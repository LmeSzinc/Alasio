"""
The single steps of the maintenance flows

Every flow sequences these steps (the order is the flow's own, the steps know
nothing about each other; ``restart_flow`` composes them, ``restart_manager``
adds the entries and the lifespan run):

- ``stop_all``: stop every running worker gracefully and freeze the resume list
  (the worker manager keeps the list, the handover writes it into the resume
  file);
- ``update_mods`` / ``full_update``: apply the deploy jobs of the mods (one job
  per mod, the phases drive the Update topic, a failure of one mod is isolated);
- ``maybe_upgrade_to_daily``: turn a flow in flight into the daily maintenance
  when it crosses the target time;
- ``handover``: publish the resume intent and ask the supervisor to replace the
  process. Never returns on the success path (the process shuts down);
- ``read_resume_intent`` / ``resume_queue``: the other side of the handover --
  the startup flow consumes the file and this task starts the configs of the
  new process;
- ``wrap_up``: the release path of a flow that did not hand over (release the
  transient update states of the mods, restore the checks, resume the workers
  stopped for it);
- ``force_restart`` / ``abort``: leave the flows behind and end the process.

The steps read the state of ``restart_base`` (the slot, the frozen flag) and
the collaborators of the manager (the worker manager, the check module, the
resume files, the process restart), all as attributes of the manager instance:
a test builds a manager with stand-ins and calls the step directly.

The Restart topic phases are pushed by the flows and by the resume queue; the
queue must never overwrite a phase of a flow that took the backend over in
between, so both go through one lock here (PHASE_LOCK) rather than reading the
gate and pushing in two steps.
"""
from typing import Callable, Dict, List, Optional

import trio

from alasio.backend.app.lifespan import SHUTDOWN_EVENT, lifespan_restart
from alasio.backend.app.restart.daily import DAILY, DailyState
from alasio.backend.app.restart.restart_base import Flow, RestartBase
from alasio.backend.app.restart.resume_file import OWNER_RESTART, RESUME_FILES, ResumeFiles, ResumeRecord
from alasio.backend.app.restart.update_check import UPDATE_CHECK, UpdateCheck
from alasio.backend.topic.restart import RestartSource
from alasio.backend.topic.scan import ConfigScanSource
from alasio.deploy.pack.job import UpdateAborted
from alasio.logger import logger

# Seconds to wait for the workers to stop gracefully before killing the
# remaining ones
GRACEFUL_STOP_TIMEOUT = 600.0
# Interval between two worker starts of the resume queue (measured from the
# previous worker_resume return; the queue exists to avoid spawning dozens of
# processes and device connections in the same instant)
WORKER_START_INTERVAL = 1.0
# Orders the phase pushes of the resume queue and of the flows: a queue that
# checks "did a flow take the backend over" and pushes inside one section can
# never overwrite the phase a flow pushed in the meantime
PHASE_LOCK = trio.Lock()


class HandoverFailed(RuntimeError):
    """
    Raised by handover() when the process restart cannot be requested (no
    supervisor, broken pipe): the publication was withdrawn and the flow must
    wrap up. The mods it already wrote are marked restart-required; the next
    process start converges them.
    """


# =============================================================================
# Restart topic phases
# =============================================================================

def _push_phase_blocking(phase: str):
    """
    Push the restart phase to the Restart topic (blocking, thread pool only)

    Args:
        phase (str): '' clears the phase, otherwise 'stopping' /
            'shutting-down' / 'resuming' / 'done'
    """
    RestartSource().on_event(phase)


def _push_view_blocking(view):
    """
    Push the flow view to the Restart topic (blocking, thread pool only)

    Args:
        view (dict): {'flow', 'cancellable', 'upgraded'}
    """
    RestartSource().on_event(view)


# =============================================================================
# Resume actions (carried by the resume file, run before the resume)
# =============================================================================

# Action tag -> runner. No shell: an action is a cleanup the new backend runs
# before the auto-resume (a flow injects tags through handover(actions=[...])
# and registers their runners here). No builtin action is registered: replacing
# the .py files of an updated package needs no bytecode cleanup, the
# interpreter drops a stale .pyc on its own (the .pyc header records the source
# mtime and size, a changed source is recompiled).
RESUME_ACTIONS: "Dict[str, Callable]" = {}


def run_resume_actions(actions):
    """
    Run the actions recorded in the resume file (best effort, no shell)

    Args:
        actions (list[str]): Action tags
    """
    for action in actions:
        runner = RESUME_ACTIONS.get(action, None)
        if runner is None:
            logger.warning(f'[Restart] Unknown resume action ignored: {action}')
            continue
        logger.info(f'[Restart] Running resume action: {action}')
        try:
            runner()
        except Exception as e:
            logger.error(f'[Restart] Resume action failed: {action}: {e}')


class RestartStep(RestartBase):
    """
    The single steps of a flow, bound to the collaborators of one manager

    Attributes (the injected collaborators; a manager resolves the process
    singletons when they are not given):
        worker_manager (WorkerManager): The worker manager the stops and the
            resume queue drive
        update_check (UpdateCheck): The check module the apply steps and the
            pause / restore discipline drive
        daily (DailyState): The daily bookkeeping
        files (ResumeFiles): The resume file IO
        restart_fn (Callable): Coroutine asking the supervisor to replace the
            process
    """

    def __init__(self, worker_manager=None, update_check: "Optional[UpdateCheck]" = None,
                 daily: "Optional[DailyState]" = None, files: "Optional[ResumeFiles]" = None,
                 restart_fn=None):
        """
        Args:
            worker_manager (WorkerManager): Defaults to the process singleton
                (resolved lazily: importing the topic package here would be a
                cycle, the worker manager imports this package)
            update_check (UpdateCheck): Defaults to the process singleton
                UPDATE_CHECK
            daily (DailyState): Defaults to the process singleton DAILY
            files (ResumeFiles): Defaults to the process singleton RESUME_FILES
            restart_fn (Callable): Defaults to lifespan_restart
        """
        super().__init__()
        self._worker_manager = worker_manager
        self.update_check = update_check if update_check is not None else UPDATE_CHECK
        self.daily = daily if daily is not None else DAILY
        self.files = files if files is not None else RESUME_FILES
        self.restart_fn = restart_fn if restart_fn is not None else lifespan_restart

    @property
    def worker_manager(self):
        """
        Returns:
            WorkerManager: The worker manager of this backend process, or the
                injected one (resolved on first use)
        """
        if self._worker_manager is None:
            from alasio.backend.topic._worker import BACKEND_WORKER_MANAGER
            self._worker_manager = BACKEND_WORKER_MANAGER
        return self._worker_manager

    # ------------------------------------------------------------------
    # Restart topic phases
    # ------------------------------------------------------------------

    def publish_view(self):
        """
        Publish the flow view on the Restart topic through a task of the
        nursery (the entry points of the manager are synchronous, the push is
        a thread-pool hop)
        """
        nursery = self.nursery
        if nursery is None:
            # the manager does not run (no flow can own the slot then)
            return
        nursery.start_soon(self.push_view)

    async def push_view(self):
        """
        Push the flow view: which flow owns the backend, whether it may still
        be cancelled, whether it was upgraded into the daily maintenance

        The frontend disables the update / restart entries from it and shows the
        cancel entry of a cancellable flow.
        """
        view = self.view()
        async with PHASE_LOCK:
            try:
                await trio.to_thread.run_sync(_push_view_blocking, view)
            except Exception as e:
                logger.warning(f'[Restart] Failed to push the flow view: {e}')

    async def push_restart_phase(self, phase: str):
        """
        Push the restart phase to the Restart topic

        The source event entry is a sync function (it locks the source and
        queues the delivery), so it runs in the trio thread pool -- the event
        loop never executes it.

        Args:
            phase (str): '' clears the topic, otherwise 'stopping' /
                'shutting-down' / 'resuming' / 'done'
        """
        try:
            await trio.to_thread.run_sync(_push_phase_blocking, phase)
        except Exception as e:
            # the phase is a progress hint, never let it break the flow
            logger.warning(f'[Restart] Failed to push phase "{phase}": {e}')

    async def push_flow_phase(self, phase: str):
        """
        Push a phase of a flow (the phases of a flow are the last word)

        Args:
            phase (str): The phase, see push_restart_phase
        """
        async with PHASE_LOCK:
            await self.push_restart_phase(phase)

    async def push_queue_phase(self, phase: str) -> bool:
        """
        Push a phase of the resume queue, unless a flow took the backend over

        The guard and the push are one critical section of PHASE_LOCK: either
        the phase lands before the first phase of the new flow, or it is
        dropped. The queue's phases only ever describe an idle backend (the
        resume queue runs outside the slot).

        Args:
            phase (str): 'resuming' / 'done' / ''

        Returns:
            bool: True when the phase was pushed, False when a flow owns the
                backend and the queue must stop here
        """
        async with PHASE_LOCK:
            if self.worker_manager.restarting or self.frozen:
                return False
            await self.push_restart_phase(phase)
            return True

    # ------------------------------------------------------------------
    # Stopping the workers
    # ------------------------------------------------------------------

    async def stop_all(self, flow: "Optional[Flow]" = None, timeout: "float | None" = GRACEFUL_STOP_TIMEOUT):
        """
        Stop every running worker gracefully (the first step of every flow)

        The stop requests go out immediately (a worker finishes its current task
        first), the wait escalates to a kill after `timeout` seconds (the worker
        manager owns both). The wait is also what a cancel interrupts: the abort
        event of the manager ends it early and its result reports the
        cancellation.

        The workers are parked in the state of the flow ('updating' for an
        update / daily / startup flow, 'restarting' for a restart): the frontend
        shows what the maintenance is doing.

        Args:
            flow (Flow): The flow stopping the workers (its name picks the
                parked state), None keeps the default ('restarting')
            timeout (float): Seconds to wait for the graceful stop before the
                kill escalation, None waits forever

        Returns:
            tuple[bool, List[str]]: whether every worker stopped (False = the
                wait was cancelled, or the manager was already restarting) and
                the frozen resume list -- the configs the handover records for
                the new backend, empty when the wait was cancelled
        """
        manager = self.worker_manager
        park_state = flow.PARK_STATE if flow is not None else 'restarting'
        try:
            await trio.to_thread.run_sync(manager.set_park_state, park_state)
            waiting = await trio.to_thread.run_sync(manager.restart_begin)
        except RuntimeError as e:
            logger.error(f'[Restart] Cannot stop the workers: {e}')
            return False, []
        logger.info(f'[Restart] Stopping the workers, waiting up to {timeout}s for: {waiting}')
        await self.push_flow_phase('stopping')
        return await trio.to_thread.run_sync(manager.restart_wait, timeout)

    async def queued_resume_list(self) -> "List[str]":
        """
        The configs queued for auto-resume right now (the resume list of a flow
        that stops no worker, e.g. the startup convergence)

        Returns:
            List[str]: Sorted config names
        """
        return await trio.to_thread.run_sync(self.worker_manager.resume_intent)

    # ------------------------------------------------------------------
    # Applying the updates
    # ------------------------------------------------------------------

    async def update_mods(self, names, flow=None) -> "List[str]":
        """
        Update the given mods: one deploy job per mod, run to the end.

        The job reports the phases of its flow ('checking' / 'downloading' /
        'updating'), this step binds them to the Update topic and closes the
        cancellation window of the flow at its point of no return (the
        'updating' phase: the real files are about to be replaced). A failure
        of one mod is isolated: it is reported on the topic of that mod, the
        other mods and the caller's handover are not affected.

        Args:
            names (Iterable[str]): Mod names to update
            flow (Flow): The flow running this step, its cancellation window
                closes at the first write; None keeps the caller's own handling

        Returns:
            List[str]: The mods whose real files changed (a restart applies them)
        """
        changed = []
        for name in names:
            mod = self.update_check.mods.get(name, None)
            if mod is None or mod.mirrors is None:
                # unknown mod, or unmanaged (no usable update source): nothing
                # to apply
                continue
            logger.info(f'[Update] Updating mod "{name}"')

            async def on_phase(phase, name=name):
                if phase == 'updating' and flow is not None:
                    # the real files are about to be replaced: the flow cannot
                    # be cancelled any more, its handover ends it
                    flow.close_window()
                self.update_check.set_state(name, phase)
                return True

            try:
                job = await mod.job()
                completed = await job.update(on_phase)
            except trio.Cancelled:
                raise
            except UpdateAborted as e:
                # the flow was cancelled: the job removed its temporary files
                # and the real files were not changed
                logger.info(f'[Update] The update of "{name}" was aborted: {e}')
                mod.set_state('available')
                continue
            except Exception as e:
                logger.error(f'[Update] Failed to update "{name}": {e}')
                logger.exception(e)
                mod.set_state('error', error=str(e))
                continue
            if not completed:
                mod.set_state('error', error='Some files failed to update, retry the update')
                continue
            if job.changed:
                changed.append(name)
                logger.info(f'[Update] Applied the update of "{name}"')
            mod.set_state('uptodate')
        return changed

    async def full_update(self, flow=None) -> "List[str]":
        """
        Update every managed mod and record the completion (the daily
        bookkeeping)

        The mods without an update source are skipped (they stay 'unmanaged'), a
        mod that is already up to date is validated and repaired by its job
        instead of being applied -- the tree converges either way.

        Args:
            flow (Flow): The flow running this step, see update_mods

        Returns:
            List[str]: The mods whose real files changed
        """
        changed = await self.update_mods(self.update_check.names(), flow=flow)
        self.daily.note_full_update()
        logger.info(f'[Update] Full update finished, changed mods: {changed}')
        return changed

    async def maybe_upgrade_to_daily(self, flow) -> bool:
        """
        Upgrade a flow in flight into the daily maintenance when it crossed the
        target time of a day whose action is not served yet

        The flow already stopped the workers and (for an update) applied what
        the user asked; the daily action only adds the full update, which the
        handover of the flow then serves (the worker restart is the same one).
        The upgrade closes the cancellation window: the day is served in one go.

        Args:
            flow (Flow): The flow reaching its handover

        Returns:
            bool: True when the flow was upgraded
        """
        if flow.upgraded:
            # only the first crossing of a flow upgrades it
            return False
        if not self.daily.should_upgrade():
            return False
        logger.info(f'[Restart] The "{flow.name}" flow is upgraded into the daily maintenance '
                    f'(the daily action of today was not served yet)')
        flow.upgrade_to_daily()
        await self.push_view()
        # a full update of this process that already covers today (a failed
        # daily handover, an update that ran one) is not repeated
        if not self.daily.covered_by(self.daily.updated_all_at):
            await self.full_update(flow)
        return True

    # ------------------------------------------------------------------
    # Handover (old process)
    # ------------------------------------------------------------------

    async def handover(self, flow, reason: str, resume_list, actions=None):
        """
        Hand the backend over to a new process (the last step of a flow)

        1. freeze the state: the resume list is final, no start is accepted any
           more;
        2. publish the resume intent: the file is written and its one-shot
           credential is announced to the supervisor in the same step (a cancel
           cannot interleave the two);
        3. mark the daily action done when the last full update of this process
           already covers it (the handover is the daily action of a crossed
           target);
        4. push the 'shutting-down' phase;
        5. ask the supervisor for the process restart (the shutdown event set
           inside ends the backend).

        Never returns on the success path. A failure (no supervisor, broken
        pipe) withdraws the publication and unfreezes the state, then raises
        HandoverFailed: the caller wraps up, the mods already written are marked
        restart-required and the next process start converges them.

        Args:
            flow (Flow): The flow handing over (only its name is used: the log
                line and the meaning of the resume list)
            reason (str): Log message of the handover (e.g. 'user request')
            resume_list (list[str]): Configs the new backend resumes (the frozen
                list of stop_all, or the queued configs of a flow that stopped
                no worker)
            actions (list[str]): Optional resume action tags, see RESUME_ACTIONS

        Raises:
            HandoverFailed: When the process restart cannot be requested
        """
        with self.handover_intent():
            # 1) the file and its credential are one step (see the resume_file
            #    module): a file whose credential was never announced is never
            #    consumed
            await self.files.write(
                resume_list, owner=OWNER_RESTART, actions=actions,
                updated_all_at=self.daily.updated_all_at)
            # 2) the daily action of a crossed target is served by this
            #    handover: the full update of this process completed after the
            #    target moment
            if self.daily.mark_done_if_covered():
                logger.info('[Restart] Daily action covered by the full update of this process')
            logger.info(f'[Restart] Handover of the "{flow.name}" flow ({reason}): '
                        f'{len(resume_list)} configs to resume')
            # 3) the frontend phase
            await self.push_flow_phase('shutting-down')
            # 4) the process restart: the supervisor stops this backend and
            #    starts a new one with the credential in its environment
            try:
                await self.restart_fn()
            except Exception as e:
                # the process is not replaced: a published intent would be
                # consumed by nothing and the frozen state would refuse every
                # start forever (the context manager clears it on the way out)
                logger.error(f'[Restart] Handover failed, the backend keeps running: {e}')
                try:
                    await self.files.withdraw()
                except Exception as withdraw_error:
                    # best effort: the credential is one-shot and the file is
                    # stale-cleaned, a failed removal must not mask the
                    # handover error
                    logger.error(f'[Restart] Failed to withdraw the resume file: {withdraw_error}')
                raise HandoverFailed(f'Cannot restart the backend: {e}') from e

    async def wrap_up(self, flow, resume_list=None):
        """
        Wrap up a flow that did not hand over (cancelled, failed, refused
        restart)

        1. the transient states the apply steps set return to 'available' (the
           flow in flight is gone, the update of the last check is still
           pending);
        2. the check sequences the flow paused are restored (a mod that was
           never paused is left alone);
        3. the workers stopped for the flow are resumed: the restart run of the
           worker manager is released (the cancel path may have done it already)
           and the frozen list is queued for the resume task;
        4. the resume queue is spawned as an external task: it drains outside
           the slot, so a new flow may take the backend over while it still runs
           (the queue stops there and its entries are collected by the new
           flow).

        The flow was either cancelled before publishing anything or its
        publication was withdrawn by the failure path, so nothing else is
        undone.

        Args:
            flow (Flow): The flow wrapping up
            resume_list (list[str]): The workers stopped for this flow (the
                frozen resume list of stop_all), None/empty when the flow
                stopped none
        """
        worker_manager = self.worker_manager
        self.update_check.release_states()
        self.update_check.restore()
        try:
            # every worker this flow took must come back: the frozen list of
            # its stop, the entries parked right now (a cancelled stop leaves
            # some) and the starts queued while it owned the backend
            resume = set(resume_list or ())
            resume.update(await trio.to_thread.run_sync(worker_manager.parked_configs))
            resume.update(await trio.to_thread.run_sync(worker_manager.resume_intent))
            # release the run: the parked entries return to idle, the queued
            # ones keep waiting
            await trio.to_thread.run_sync(worker_manager.restart_cancel)
            if resume:
                await trio.to_thread.run_sync(worker_manager.mark_resume, sorted(resume))
        except Exception as e:
            logger.warning(f'[Restart] Failed to queue the resume of the stopped workers: {e}')
        logger.info(f'[Restart] The "{flow.name}" flow wraps up: releasing the queued workers')
        await self.push_flow_phase('')
        self.spawn(self.resume_queue)

    # ------------------------------------------------------------------
    # Resume (new process)
    # ------------------------------------------------------------------

    async def read_resume_intent(self) -> "Optional[ResumeRecord]":
        """
        Consume the resume file of the previous process (the startup flow, step
        1)

        The file is read once (the read deletes it, whatever the verification
        result), its actions run, the recorded configs are marked as queued
        resumes (they wait, they are not started yet) and the stale leftovers of
        dead sessions are cleaned -- the cleanup runs in this task, right after
        the read, it must never race the file this process consumes.

        Without a resume credential (no supervisor, a cold start, a killed
        session) nothing is read and the configs the frontend asks for while the
        startup flow runs are the only ones queued.

        Returns:
            ResumeRecord | None: The record, None when there was none
        """
        manager = self.worker_manager
        files = self.files
        record = None
        try:
            record = await trio.to_thread.run_sync(files.read)
        except Exception as e:
            logger.error(f'[Restart] Resume file read failed: {e}')
            logger.exception(e)
        await trio.to_thread.run_sync(files.cleanup)
        if record is None:
            return None
        logger.info(f'[Restart] Resume intent accepted: {len(record.configs)} configs, '
                    f'{len(record.actions)} actions, owner={record.owner}, '
                    f'updated_all_at={record.updated_all_at:.0f}')
        if record.actions:
            await trio.to_thread.run_sync(run_resume_actions, record.actions)
        if record.configs:
            queued = await trio.to_thread.run_sync(manager.mark_resume, record.configs)
            logger.info(f'[Restart] Resume queue recorded: {queued}')
        return record

    def _resume_one(self, config: str):
        """
        Resolve one queued config and start it (blocking, thread pool only)

        Failures are logged, not raised: one broken config must not stop the
        queue.

        Args:
            config (str): Config name
        """
        manager = self.worker_manager
        try:
            # local import: the topic package imports the worker manager, which
            # imports this package
            from alasio.backend.topic.worker import get_mod
            mod = get_mod(config)
        except Exception as e:
            # config / mod gone: drop the queue entry (it must not block a
            # manual start), log and continue with the rest. drop_resume only
            # touches an entry still waiting: one a flow collected is not ours
            # to drop
            logger.error(f'[Restart] Resume dropped, cannot load config "{config}": {e}')
            manager.drop_resume([config])
            return
        try:
            success, msg = manager.worker_resume(mod, config)
        except Exception as e:
            # the spawn failed: the manager finalized the entry on the way out
            # (error / restarting), so the cleanup only ends what this attempt
            # created. restart_resume=True: an entry a new flow already
            # collected is not this attempt's to cancel -- the crashed startup
            # stays in the new resume list and the new backend retries it
            logger.error(f'[Restart] Resume failed: "{config}": {e}')
            manager.worker_force_kill(config, restart_resume=True)
            return
        if not success:
            # cancelled by the user, started by someone else, or collected by a
            # new flow (the entry belongs to its resume list from then on)
            logger.info(f'[Restart] Resume skipped: "{config}": {msg}')
            return
        logger.info(f'[Restart] Worker resumed: {config}')

    async def resume_queue(self):
        """
        Release the starts queued while a flow owned the backend (external task)

        The queue lives in the worker manager: the configs the resume file
        recorded and the starts the frontend requested while a flow owned the
        backend (both are "resuming" entries). Each config is resolved against
        the config scan (one forced refresh): a config the scan does not expose
        is dropped, never started -- starting it would recreate its file with
        the default settings, which the user never asked for. The configs start
        one by one (WORKER_START_INTERVAL between two starts) with the phases
        'resuming' / 'done' / '' on the Restart topic.

        The task runs outside the slot of the manager (the startup flow spawns
        it right before releasing the slot, the wrap-up of a cancelled flow
        spawns it the same way), so a new flow can take the backend over while
        the queue still drains: the queue then stops (its entries are collected
        by the new flow's stop_all and recorded in its resume file). The queue
        also ends at the shutdown of the process. Failures are best effort: the
        entries left queued stay visible in the Worker topic and a manual start
        is possible.
        """
        manager = self.worker_manager
        if manager.restarting or self.frozen:
            # a flow is stopping the workers right now, or a handover already
            # published the resume intent (the file owns the queue)
            return
        queued = await trio.to_thread.run_sync(manager.resume_intent)
        if not queued:
            logger.info('[Restart] The resume queue is empty, nothing to start')
            return
        source = ConfigScanSource()
        try:
            # the disk read runs in the thread pool (inside reinit)
            await source.reinit(force=True)
        except Exception as e:
            # the scan decides whether a config exists: a failing refresh must
            # not abandon the whole queue, the resolution below falls back to
            # the data the source currently holds
            logger.error(f'[Restart] Config scan refresh failed: {e}')
        data = source.data
        resolved = [config for config in queued if config in data]
        # drop the still queued marks of the missing configs (an entry a new
        # flow already collected is not ours to drop, drop_resume leaves it
        # alone)
        missing = [config for config in queued if config not in data]
        if missing:
            logger.warning(f'[Restart] Resume entries abandoned, configs not found: {missing}')
            await trio.to_thread.run_sync(manager.drop_resume, missing)
        if not resolved:
            logger.info('[Restart] The resume queue is empty, nothing to start')
            return
        if not await self.push_queue_phase('resuming'):
            logger.info('[Restart] The resume queue was interrupted by a new flow')
            return
        logger.info(f'[Restart] Resume queue: {len(resolved)} configs, '
                    f'starting them with a {WORKER_START_INTERVAL}s interval')
        for config in resolved:
            if SHUTDOWN_EVENT.is_set():
                logger.info(f'[Restart] The resume queue was interrupted by the shutdown: {config}')
                return
            if manager.restarting or self.frozen:
                # the new flow owns the queue from here on (its stop_all
                # collects the entries and its handover records them)
                logger.info('[Restart] The resume queue was interrupted by a new flow')
                return
            # blocking (mod resolution + process spawn) -> thread pool
            await trio.to_thread.run_sync(self._resume_one, config)
            # interval between two starts, measured from the worker_resume
            # return (do not wait for the worker to reach "running")
            await trio.sleep(WORKER_START_INTERVAL)
        # the terminal phases, refused together when a flow took the backend
        # over while the last config was starting: 'done' is transient, "phase
        # present" means "a restart is in progress" for the frontend
        if not await self.push_queue_phase('done'):
            return
        await self.push_queue_phase('')

    # ------------------------------------------------------------------
    # Force restart / abort (the backend goes down)
    # ------------------------------------------------------------------

    async def _release_in_flight(self):
        """
        Interrupt the flow in flight, release the worker manager and drop the
        published resume intent (the shared body of the force restart and of
        the backend stop)
        """
        flow = self.current
        if flow is not None:
            logger.info(f'[Restart] Interrupting the "{flow.name}" flow')
            # no validation: the force restart and the shutdown interrupt
            # whatever is in flight. The wait of the flow lives in a worker
            # thread, the flow object wakes it (and cancels the scope of its
            # cancellable window)
            await flow.interrupt()
        try:
            await trio.to_thread.run_sync(self.worker_manager.restart_cancel)
        except Exception as e:
            logger.warning(f'[Restart] Cannot reset the worker manager: {e}')
        # no start is accepted any more and nothing may consume a published
        # intent
        self.frozen = True
        try:
            await self.files.withdraw()
        except Exception as e:
            logger.warning(f'[Restart] Cannot withdraw the resume file: {e}')
        await self.push_view()

    async def abort(self):
        """
        Abort the maintenance flow in flight and drop the published resume intent

        The backend is going down (the host closed it, see
        ``alasio.backend.app.lifespan``): nothing may restart it, and the next
        process must not resume what this one was about to record.
        """
        await self._release_in_flight()
        await self.push_flow_phase('')

    async def force_restart(self):
        """
        Restart the backend immediately, whatever is in flight (the emergency
        exit)

        The flow in flight is interrupted (its wrap-up task ends on its own, the
        workers die with the process), no resume intent survives (the
        publication is withdrawn: a cancelled handover must not be resumed by
        the next process), no daily upgrade is attempted. The starts are refused
        from here on (the frontend reconnects to the new process).

        The worker processes are not waited for: the process teardown ends them
        (the graceful stop of the flows is exactly what this path skips).
        Without a supervisor the restart cannot be requested -- the backend
        keeps running and the error reaches the caller.

        Raises:
            PermissionError: When the backend runs without a supervisor (the
                process can only be stopped by the host)
        """
        await self._release_in_flight()
        await self.push_flow_phase('shutting-down')
        await self.restart_fn()
