"""
The four maintenance flows of the backend

Every flow is a class: the manager creates its object when the flow is claimed
and runs it as the body of the flow task (``Flow.run()``). The entries name the
class itself (``restart_manager``): the class carries the flow name used by the
view and the log lines, and the ``REFUSALS`` table the two user requests answer
with. The object owns the state of the flow and, above all, the *cancellable
part* of its body: the body declares it itself with ``with
self.cancel_window(past_reason):`` -- inside that block a cancel is accepted,
leaving it (or closing it early) makes the flow irreversible. The manager asks
the object to cancel (``Flow.cancel()``) instead of binding a cancel scope of
its own.

The four flows:

- ``startup``: the process startup (consume the resume intent, full update when
  the previous process did not just run one, start the checks, release the
  resume queue). No cancellable phase;
- ``restart``: a user restart (pause the checks, stop the workers, hand over);
- ``update``: a user update (pause the checks, pre-check the available mods,
  stop the workers, apply the deploy jobs, hand over);
- ``daily``: the daily maintenance (pause the checks, stop the workers, full
  update, hand over). No cancellable phase.

The bodies drive the single steps of the manager
(``alasio.backend.app.restart.restart_step``); every flow that stops something
releases it on its way out when it does not hand over (``wrap_up``): the checks
it paused, the workers it stopped, the resume queue. A flow that hands over
does nothing else -- the process is replaced and the new one resumes what the
resume file recorded.
"""
from typing import List

from alasio.backend.app.restart.restart_base import INIT_FLOW, STARTING_UP, Flow
from alasio.backend.app.restart.restart_step import HandoverFailed
from alasio.logger import logger


class FlowStartup(Flow):
    """
    The process startup flow (the run() entry of the manager)

    1. consume the resume file of the previous process (its configs are queued,
       the stale leftovers of dead sessions are cleaned);
    2. a full update when the previous process did not just run one
       (``updated_all_at`` unset): the tree converges whatever happened before.
       When it changed real files the flow hands the process over -- the new
       process sees ``updated_all_at`` and skips its own full update, so the
       chain converges in one update and one restart;
    3. the daily coverage: a process that just ran the full update (this one or
       the previous one) after the target moment serves the daily action of the
       day, the daily task then leaves the day alone;
    4. start the check sequences (the first check waits the standard spread: the
       full update just checked everything);
    5. spawn the resume queue of the configs marked in step 1 and release the
       slot.

    No cancellable phase: the process has no checks yet, a cancel would leave
    the backend without them.
    """
    name = 'startup'

    async def run(self):
        manager = self.manager
        record = await manager.read_resume_intent()
        manager.update_check.bind()
        manager.update_check.set_initial_states()
        changed = []
        try:
            if record is None or not record.updated_all_at:
                changed = await manager.full_update(self)
                if manager.daily.mark_done_if_covered():
                    logger.info('[Restart] The daily action of today is served by the '
                                'startup full update')
            elif manager.daily.covered_by(record.updated_all_at):
                manager.daily.mark_done()
                logger.info('[Restart] The daily action of today was served by the '
                            'previous process')
            if changed:
                # the files just applied need the new process: hand over, the
                # new process sees updated_all_at and starts its checks directly
                await manager.handover(self, 'startup convergence', await manager.queued_resume_list())
                return
            manager.update_check.start()
            # the resume queue runs outside the slot: the flow releases it as
            # this task returns (a user flow may take the backend over while
            # the queue still drains, the queue stops there)
            manager.spawn(manager.resume_queue)
        except HandoverFailed as e:
            logger.error(f'[Restart] The startup convergence could not hand the backend over: {e}')
            manager.update_check.mark_restart_required(changed, str(e))
            await manager.wrap_up(self)
        except Exception as e:
            logger.error(f'[Restart] The startup flow failed: {e}')
            logger.exception(e)
            await manager.wrap_up(self)


class FlowRestart(Flow):
    """
    A user restart: pause the checks, stop every worker gracefully, cross the
    daily target, hand the process over

    The wait for the workers to stop is the cancellable phase (the long part);
    an upgrade into the daily maintenance closes the window.
    """
    name = 'restart'
    PARK_STATE = 'restarting'
    # Why this request is refused, by the name of the flow owning the backend
    # when it lands (the startup window refuses a request of the process
    # startup alike)
    REFUSALS = {
        INIT_FLOW: STARTING_UP,
        'startup': STARTING_UP,
        'restart': 'A restart is already in progress',
        'update': 'An update is in progress; the backend restarts by itself when it finishes',
        'daily': 'The daily maintenance is in progress (it includes the restart)',
    }

    async def run(self):
        manager = self.manager
        resume_list = []
        handed_over = False
        try:
            with self.cancel_window('the handover started'):
                manager.update_check.pause()
                success, resume_list = await manager.stop_all(self)
                if not success:
                    # cancelled while waiting: the wrap-up below resumes the
                    # workers it stopped and restores the checks
                    return
                await manager.maybe_upgrade_to_daily(self)
            await manager.handover(self, 'user request', resume_list)
            handed_over = True
        except HandoverFailed as e:
            logger.error(f'[Restart] The restart could not hand the backend over: {e}')
        finally:
            if not handed_over:
                await manager.wrap_up(self, resume_list)


class FlowUpdate(Flow):
    """
    A user update: pause the checks, pre-check the available mods, stop the
    workers, apply the deploy jobs, hand the process over

    The user entry is a single one (there is no per-mod update button): the mods
    with an available update at the moment of the click are the scope of this
    flow. An in-flight job that finds a mod already up to date (another flow
    applied it meanwhile) keeps its state and skips it, a failed mod is
    isolated, the other mods and the handover are not affected.

    The cancellable phase is the stop and the download: the apply step closes
    the window at the first real write (the deploy jobs report their 'updating'
    phase there), a daily upgrade closes it too.
    """
    name = 'update'
    # Why this request is refused, by the name of the flow owning the backend
    # when it lands (the startup window refuses a request of the process
    # startup alike)
    REFUSALS = {
        INIT_FLOW: STARTING_UP,
        'startup': STARTING_UP,
        'update': 'An update is already in progress',
        'restart': 'A restart is in progress; the updates are checked and applied right after it',
        'daily': 'The daily maintenance is in progress (it includes the full update)',
    }

    async def run(self):
        manager = self.manager
        names = manager.update_check.available()
        resume_list = []
        changed = []
        handed_over = False
        try:
            with self.cancel_window('the mods are being written'):
                manager.update_check.pause()
                for name in names:
                    manager.update_check.set_state(name, 'queued')
                if names and await self._all_uptodate(names):
                    # the pre-check (read-only, nothing stopped yet): every mod
                    # of the scope is up to date now, the update is a no-op
                    logger.info('[Restart] The update is already applied, nothing to do')
                    return
                success, resume_list = await manager.stop_all(self)
                if not success:
                    return
                for name in names:
                    manager.update_check.set_state(name, 'stopping')
                changed = await manager.update_mods(names, flow=self)
                await manager.maybe_upgrade_to_daily(self)
            await manager.handover(self, 'update', resume_list)
            handed_over = True
        except HandoverFailed as e:
            logger.error(f'[Restart] The update could not hand the backend over: {e}')
            if changed:
                manager.update_check.mark_restart_required(changed, 'The update is applied, a backend '
                                                                 f'restart is required: {e}')
        finally:
            if not handed_over:
                await manager.wrap_up(self, resume_list)

    async def _all_uptodate(self, names: "List[str]") -> bool:
        """
        The read-only pre-check of the update flow

        Every mod of the scope is checked (DeployJob.check(): the local ledger
        against the latest pack, no lock, nothing on disk) and its state is
        refreshed in passing; the flow only stops the workers when at least one
        mod still has an update.

        Args:
            names (List[str]): Mod names of the scope

        Returns:
            bool: True when every checked mod is up to date (a check that fails
                counts as "not up to date": a broken mirror must not turn the
                click into a no-op)
        """
        update_check = self.manager.update_check
        for name in names:
            mod = update_check.mods.get(name, None)
            if mod is None or mod.mirrors is None:
                continue
            try:
                job = await mod.job()
                check = await job.check()
            except Exception as e:
                logger.warning(f'[Update] The pre-check of "{name}" failed: {e}')
                return False
            if check.uptodate():
                update_check.set_state(name, 'uptodate')
            else:
                mod.set_state('available', current=check.local, latest=check.latest)
                return False
        return True


class FlowDaily(Flow):
    """
    The daily maintenance: pause the checks, stop the workers, run the full
    update of every mod, hand the process over

    No cancellable phase: the daily action is done in one go (the schedule would
    retrigger otherwise, with the workers already stopped), and an upgrade of a
    flow in flight (``maybe_upgrade_to_daily``) closes its window the same way.
    """
    name = 'daily'

    async def run(self):
        manager = self.manager
        resume_list = []
        changed = []
        handed_over = False
        try:
            manager.update_check.pause()
            success, resume_list = await manager.stop_all(self)
            if not success:
                return
            changed = await manager.full_update(self)
            await manager.handover(self, 'daily', resume_list)
            handed_over = True
        except HandoverFailed as e:
            logger.error(f'[Restart] The daily maintenance could not hand the backend over: {e}')
            if changed:
                manager.update_check.mark_restart_required(changed, 'The update is applied, a backend '
                                                                 f'restart is required: {e}')
        finally:
            if not handed_over:
                await manager.wrap_up(self, resume_list)
