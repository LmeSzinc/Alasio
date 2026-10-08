"""
Mod update orchestration: the check loop, the update windows and the
update transaction (see doc/2026-10-05_mod-update-backend-integration.md).

The UpdateManager is a per-process singleton running as a lifespan
background task (alasio.backend.app.app, next to the auto-resume and the
daily restart tasks):

1. the startup convergence finishes the updates a killed process left
   behind (job.pack of every mounted mod), a completed convergence ends
   in one graceful restart (§2.6);
2. one check loop per mounted mod: the first check runs immediately, the
   second one after a random 5-10 minutes (the clients started together
   re-phase themselves, their later checks never form a global wave),
   the following ones every Deploy.Update.CheckUpdateInterval minutes. A
   manual check (rpc update_check) cancels the pending wait, counts as
   the first check and runs immediately.

One ModUpdateManager holds the state and the flow of ONE mod (its check
loop, its update server, its state on the Update topic); the UpdateManager
owns the pieces shared by every mod: the http client, the task nursery and
the instance-level window of the single update transaction.

The check of one mod is read-only: the local version of the mod ledger
against latest.pack of its update source, one small request per round.
The update transaction (rpc update_apply) has two phases:

- 'downloading': the version pre-flight - DeployJob.check() reads the
  local version against latest.pack, read-only and cancellable through
  update_cancel (nothing was changed, the same flow can be retried). The
  pre-flight only decides whether the update is still there: the download
  belongs to the exclusive lock of DeployJob.update(), see below;
- 'updating': the graceful restart machinery is reused, its on_all_stopped
  hook runs DeployJob.update() - the whole download and replace flow of
  the target under the one exclusive lock of the job - while every worker
  is stopped and the backend is still alive (replacing the files is safe
  there), then the supervisor restarts the backend and the new process
  resumes the recorded configs.

A separate download phase was rejected: it would run outside the lock of
update() and could race a second updater of the same target (the lock is
held for the whole download-and-replace flow of one caller). Only the
read-only version comparison of check() is shared, which takes no lock
(the index pack of a target being updated is read atomically).

While a window is open the manager accepts the starts of the affected
configs instead of refusing them (WorkerManager.update window): a click
on "start" during a check or an update window creates a resume entry and
the config starts automatically when the window ends (or after the
restart of the update transaction); the workers stopped for the restart
of an update transaction are parked in the 'updating' worker state (the
frontend shows 更新中 instead of 重启中).
"""
import contextlib
import random
import time

import trio

from alasio.backend.app import restart as restart_app
from alasio.backend.app.update_startup import UPDATE_STARTUP
from alasio.backend.mpipe.mpipe_backend import mpipe_backend
from alasio.backend.topic._worker import BACKEND_WORKER_MANAGER
from alasio.backend.topic.update import UpdateInfo, UpdateSource
from alasio.backend.topic.worker import get_mod
from alasio.config.entry.loader import MOD_LOADER
from alasio.deploy.httpclient.probe import Mirrors
from alasio.deploy.pack.job import DeployJob, UpdateAborted
from alasio.deploy.pack.server_file import ServerFile
from alasio.logger import logger

# Seconds of the second check of a mod: the first check of a process runs
# immediately, the second one waits a random delay of this range (every mod
# of every client rolls its own). The startup moment of simultaneous clients
# is synchronized, the random spread re-phases them so their later checks
# never form a wave again.
FIRST_CHECK_DELAY = (300.0, 600.0)
# Fallback interval when Deploy.Update.CheckUpdateInterval cannot be read
# (a broken config file must not stop the update manager): seconds.
CHECK_INTERVAL_FALLBACK = 300.0


class UpdateError(RuntimeError):
    """
    User-facing refusal of an update rpc: the mod is unknown / unmanaged,
    no update is available, an update or a restart is already in progress,
    the backend runs without a supervisor. The topic translates it into an
    RpcValueError.
    """


class ModUpdateManager:
    """
    Update state and flow of one mounted mod.

    One instance per mounted mod, built by UpdateManager.bind_mods(): it
    holds the state pushed to the Update topic, the update server of the mod
    and the check loop of the mod (run(), a task of the manager nursery),
    plus the per-mod parts of the update transaction. The pieces shared by
    every mod (the http client, the instance window of the single update
    transaction, the queue release) stay on the UpdateManager, see it.

    Attributes (read-only for the outside):
        info (UpdateInfo): The state of the mod, also the value bound on the
            Update topic
        convergence_pending (bool): The startup convergence of the mod
            failed and is retried before its next check round (§2.6)
    """

    def __init__(self, manager, mod):
        """
        Args:
            manager (UpdateManager): The update manager of the process
            mod (Mod): The mounted mod to manage
        """
        self.manager = manager
        # the mounted mod, its entry carries the update source (mirrors)
        self.mod = mod
        self.name = mod.name
        # the state of the mod, the value pushed to the Update topic: a new
        # instance per change (the topic binds the instance, it is never
        # mutated in place)
        self.info = UpdateInfo(state='idle')
        # the update server of the mod, built on first use; the mirror record
        # (gui.db, scope = mod name) and the resolved mirror are per mod
        self._server = None
        # the check loop wait scope: registered while the loop waits, a manual
        # check cancels it to run immediately; None otherwise
        self._wake_scope = None
        # the in-flight check scope: update_cancel cancels it (the request is
        # interrupted, the loop continues)
        self._check_scope = None
        # a manual check waits: the loop restarts its sequence and runs now
        self._manual_request = False
        # the startup event of this mod (doc §16.8): set when the first update
        # check is over (a check ran, failed or was skipped); the resume queue
        # and the starts of the mod wait behind it. Registered in the
        # process-wide UPDATE_STARTUP so the sync world (the worker manager)
        # reads its is_set() without importing this module
        self.first_update_checked = UPDATE_STARTUP.mod_event(self.name)
        # the startup convergence of the mod failed: retried before the next
        # check round until it succeeds (§2.6)
        self.convergence_pending = False
        # the rendezvous of the applying phase (see _prepare_apply): the
        # graceful restart hook releases the job's 'updating' phase when the
        # replace window is open, the transaction releases the hook when the
        # job finished (`_apply_error` carries the failure it must raise,
        # `_apply_aborted` the reason of a restart that ended before the
        # window)
        self._apply_ready = None
        self._apply_done = None
        self._apply_error = None
        self._apply_aborted = ''

    # =========================================================================
    # State on the Update topic
    # =========================================================================

    def set_state(self, state, current=None, latest=None, checked_at=None, error=''):
        """
        Set the state of the mod and push it to the Update topic.

        Args:
            state (str): One of UPDATE_STATE
            current (str, optional): Local version, None keeps the known one
            latest (str, optional): Latest version, None keeps the known one
            checked_at (float, optional): Check timestamp, None keeps the
                known one
            error (str): Failure message, '' when there is none

        Returns:
            UpdateInfo: The new state (also the object bound by the topic)
        """
        previous = self.info
        self.info = UpdateInfo(
            state=state,
            current_version=previous.current_version if current is None else current,
            latest_version=previous.latest_version if latest is None else latest,
            checked_at=previous.checked_at if checked_at is None else checked_at,
            error=error,
        )
        self.push(self.info)
        return self.info

    def push(self, info):
        """
        Bind a state to the mod on the Update topic.

        Args:
            info (UpdateInfo): The state, never mutated afterwards
        """
        UpdateSource().on_event((self.name, info))

    # =========================================================================
    # Server and job of the mod
    # =========================================================================

    def mirrors(self):
        """
        The mirror structure of the update source of this mod.

        Returns:
            Mirrors | None: The mirrors, None when the mod declares no
                update source

        Raises:
            ValueError / TypeError: The declared input is malformed
        """
        value = self.mod.entry.mirrors
        if not value:
            return None
        return Mirrors.from_input(value)

    def server_or_none(self):
        """
        Returns:
            ServerFile | None: The update server of the mod, None when the
                mod declares no (or a malformed) update source
        """
        try:
            return self.server()
        except (ValueError, TypeError):
            # a malformed update source: no server, the same treatment as no
            # source (the mount check of the state reports the reason)
            return None

    def server(self):
        """
        The update server of this mod, one instance per mod: the mirror
        record (gui.db, scope = mod name) and the resolved mirror belong to
        it. Every server shares the process http client of the manager, so
        their keep-alive connections are pooled.

        Returns:
            ServerFile: The server

        Raises:
            ValueError: The mod declares no update source
        """
        if self._server is not None:
            return self._server
        mirrors = self.mirrors()
        if mirrors is None:
            raise ValueError(f'The mod declares no update source: "{self.name}"')
        self._server = ServerFile(mirrors, scope=self.name, client=self.manager.client())
        return self._server

    def job(self):
        """
        Returns:
            DeployJob: A deploy job of the mod tree, bound to its server
        """
        return DeployJob(root=self.mod.root, server=self.server())

    # =========================================================================
    # Check loop (one task per mod)
    # =========================================================================

    async def run(self):
        """
        Check loop of this mod: the rounds follow the automatic schedule
        (startup check, random 5-10 minutes, the configured interval) and a
        manual check cancels the pending wait or the in-flight check and runs
        immediately (the manual round counts as the first check, the next
        automatic one follows the standard schedule again). A round retries a
        failed startup convergence first, see _round().

        The loop is the only place the mod is checked: the manual check of
        the rpc is a signal, never a second concurrent check.
        """
        manager = self.manager
        delay = 0.0 if manager.auto else None
        if delay is None:
            # no automatic check: the startup round is over by definition
            # (the first manual check waits for nothing)
            self.first_update_checked.set()
        rounds = 0
        while True:
            with trio.CancelScope() as scope:
                self._wake_scope = scope
                try:
                    if delay is not None:
                        if delay > 0:
                            await trio.sleep(delay)
                        await self._round()
                        if restart_app.GRACEFUL_RESTART.running:
                            # a restart (an update transaction or a finished
                            # convergence) is in flight: no further check of
                            # this process, the new backend checks
                            return
                        # the first check of this mod is over (idempotent): its
                        # startup gate opens, the resume queue may release it
                        self.first_update_checked.set()
                        rounds += 1
                        delay = manager.next_delay(rounds)
                    else:
                        # no automatic check pending: wait for a manual one
                        await trio.sleep_forever()
                finally:
                    if self._wake_scope is scope:
                        self._wake_scope = None
            if scope.cancelled_caught:
                # interrupted by a manual check, or by the manager shutdown
                if manager.closed:
                    return
                if self._manual_request:
                    self._manual_request = False
                    # the manual check restarts the sequence and runs now
                    rounds = 0
                    delay = 0.0
                    continue
                return

    async def _round(self):
        """
        One round of the loop: retry a failed convergence, then check.
        """
        if self.manager._transaction == self.name:
            # the mod is inside its update transaction (downloading /
            # updating): the check of this mod is skipped, it must not
            # overwrite the transaction state (doc §16.3, GAP-2)
            logger.info(f'[Update] Check of "{self.name}" skipped: the update is in flight')
            return
        if self.convergence_pending:
            if not await self.finish_convergence():
                # still failing: the error is on the topic, wait for the
                # next round
                return
            # the disk was converged by this process: one restart applies it
            await self.manager.request_convergence_restart([self])
            return
        await self.check()

    async def finish_convergence(self):
        """
        Finish the interrupted update this mod may hold (§2.6): the
        unfinished job recorded in job.pack of its target ledger, written
        before the real files are changed and only left behind by a process
        killed in flight.

        A found job is resumed (its missing pieces are downloaded from the
        mod server) and every change is applied in one pass; a job that does
        not finish falls back to a rebuild from the latest index, the
        fallback of update(). A failure is reported on the topic and the mod
        retries before its next check round.

        Returns:
            bool: True when the disk of the mod was converged (the caller
                restarts the backend to apply it), False when there was
                nothing to finish or the convergence failed
        """
        job = DeployJob(root=self.mod.root, name='', server=self.server_or_none())
        try:
            completed = await job.run_unfinished_job()
        except trio.Cancelled:
            raise
        except Exception as e:
            logger.error(f'[Update] Failed to finish the interrupted update of "{self.name}": {e}')
            self.convergence_pending = True
            self.set_state('error', error=str(e))
            return False
        self.convergence_pending = False
        if not completed:
            return False
        logger.info(f'[Update] Finished the interrupted update of "{self.name}"')
        return True

    async def check(self):
        """
        One check of this mod: the request and the state update.

        Read-only (the local ledger version against latest.pack) and
        cancellable: update_cancel cancels the in-flight request and the
        recorded state of the last check stays. A check locks nothing: the
        starts of the configs are not gated by it (doc §16.2 revision).
        """
        previous = self.info
        self.set_state('checking')
        try:
            try:
                with trio.CancelScope() as scope:
                    self._check_scope = scope
                    try:
                        check = await self.job().check()
                    finally:
                        if self._check_scope is scope:
                            self._check_scope = None
            except Exception as e:
                logger.warning(f'[Update] Failed to check "{self.name}": {e}')
                self.set_state('error', error=str(e))
                return
            if scope.cancelled_caught:
                # update_cancel: the in-flight request is interrupted,
                # nothing is recorded (the previous state and its versions
                # stay, the timing of the sequence is kept)
                logger.info(f'[Update] The check of "{self.name}" was cancelled')
                self.info = previous
                self.push(previous)
                return
            if check.uptodate():
                self.set_state('uptodate', current=check.local, latest=check.latest,
                               checked_at=time.time())
            else:
                # the verdict line is logged by check() itself
                self.set_state('available', current=check.local, latest=check.latest,
                               checked_at=time.time())
        except trio.Cancelled:
            raise

    def request_manual(self):
        """
        Serve a manual check request of the rpc (UpdateManager.check): cancel
        the pending wait (or the in-flight check) and mark the manual round.
        The loop restarts its sequence and runs immediately.
        """
        self._manual_request = True
        scope = self._wake_scope
        if scope is not None:
            scope.cancel()

    def cancel_check(self):
        """
        Interrupt the in-flight check of this mod (update_cancel), see
        UpdateManager.cancel().

        Returns:
            bool: True when a check was in flight
        """
        scope = self._check_scope
        if scope is None:
            return False
        scope.cancel()
        return True

    def forget_scopes(self):
        """
        Drop the loop scopes of this mod (the manager shutdown): the scopes
        are cancelled by their nursery, a later update_cancel finds nothing.
        """
        self._wake_scope = None
        self._check_scope = None

    # =========================================================================
    # Update transaction (per mod, driven by UpdateManager.apply / cancel)
    # =========================================================================

    async def run_transaction(self):
        """
        The update transaction of this mod (background task of the manager).

        1. the version pre-flight: DeployJob.check() reads the local version
           against latest.pack, read-only and cancellable through
           update_cancel (nothing is changed, the same flow can be retried);
        2. the whole update flow of the target (DeployJob.update(), its one
           exclusive lock) with this manager as its phase listener: the pack
           download stays cancellable ('downloading'), and at the 'updating'
           phase boundary every worker is stopped and the resume intent
           published (the graceful restart takes the backend over) before
           the job changes the real files;
        3. the graceful restart of the transaction restarts the backend with
           the new files on disk, the new process resumes the recorded
           configs.

        Never raises: the task lives in the manager nursery, where a raised
        error would cancel every other task; every failure ends in the error
        state, and the transaction scope closes the window and releases the
        queued configs on the way out (§16.4).
        """
        manager = self.manager
        try:
            async with manager.transaction_window(self.name):
                # phase 1: the pre-flight, the clean cancellation window
                # before anything is stopped or changed
                with trio.CancelScope() as scope:
                    manager._transaction_scope = scope
                    try:
                        if manager._cancel_requested:
                            scope.cancel()
                        check = await self.job().check()
                    finally:
                        if manager._transaction_scope is scope:
                            manager._transaction_scope = None
                if scope.cancelled_caught:
                    # update_cancel: nothing was changed, the state returns to
                    # available (the release of the queued starts belongs to
                    # the transaction scope)
                    logger.info(f'[Update] The update of "{self.name}" was cancelled while downloading')
                    self.set_state('available')
                    return
                if check.uptodate():
                    # the update is gone (another flow applied it meanwhile):
                    # nothing to apply, no restart
                    logger.info(f'[Update] The mod "{self.name}" is already up to date, '
                                f'the update is dropped')
                    self.set_state('uptodate', current=check.local, latest=check.latest)
                    return
                # phase 2: the whole flow of the target, the job reports its own
                # phases through this manager as its callback (see on_job_phase)
                self._prepare_apply()
                state_error = ''
                apply_error = None
                cancelled = False
                try:
                    with trio.CancelScope() as scope:
                        manager._transaction_scope = scope
                        try:
                            ok = await self.job().update(self.on_job_phase)
                        finally:
                            if manager._transaction_scope is scope:
                                manager._transaction_scope = None
                    if scope.cancelled_caught:
                        # update_cancel: the in-flight request was interrupted
                        # (the common cancel path of the downloading phase), the
                        # real files were never changed
                        cancelled = True
                    elif not ok:
                        # some records stay in error: the tree did not converge
                        state_error = 'Some files failed to update, retry the update'
                        apply_error = RuntimeError(f'The update of "{self.name}" did not converge')
                except UpdateAborted as e:
                    # the fallback check of the phase callback: the transaction
                    # was cancelled while the job had no request to interrupt
                    logger.info(f'[Update] The update of "{self.name}" was aborted: {e}')
                    cancelled = True
                except trio.Cancelled:
                    # the manager is going down (its nursery was cancelled): the
                    # hook must not let the restart continue
                    apply_error = RuntimeError('The update manager stopped')
                    raise
                except Exception as e:
                    logger.error(f'[Update] Failed to apply the update of "{self.name}": {e}')
                    logger.exception(e)
                    state_error = str(e)
                    apply_error = e
                finally:
                    self._release_apply(apply_error)
                if cancelled:
                    # zero side effect: nothing was changed on disk
                    self.set_state('available')
                    return
                if state_error:
                    self.set_state('error', error=state_error)
                # the success path has nothing to do here: the hook of the restart
                # lets it continue with its shutdown and the new backend takes over
        except trio.Cancelled:
            raise
        except Exception as e:
            logger.error(f'[Update] Failed to prepare the update of "{self.name}": {e}')
            logger.exception(e)
            self.set_state('error', error=str(e))

    # =========================================================================
    # The applying phase: the phase listener contract of DeployJob.update()
    # =========================================================================

    async def on_job_phase(self, phase):
        """
        A phase of the deploy job of this transaction (DeployJob.update() runs
        with this bound method as its on_job_phase callback, so the state and
        the cancellability of the transaction follow what the job is really
        doing):

        - 'downloading': the version check and the pack download run (the
          transaction is still cancellable through update_cancel, nothing on
          the real files was changed yet);
        - 'updating': the local changes start: every worker is stopped here
          (with the resume intent published) and this call returns only when
          the replace window is open; the transaction is not cancellable any
          more.

        The call is also the fallback check of a cancellation: a cancel
        interrupts an in-flight request itself, but one that arrived while the
        job had no checkpoint to be interrupted at is answered here with
        False, and the job aborts before changing anything (it removes its
        temporary files and raises UpdateAborted).

        Args:
            phase (str): 'downloading' or 'updating'

        Returns:
            bool: True to let the job continue, False when the transaction was
                cancelled meanwhile

        Raises:
            ValueError: The phase is unknown
            RuntimeError: The applying phase cannot start or was aborted
        """
        if self._cancelled():
            return False
        if phase == 'downloading':
            self.set_state('downloading')
            return True
        if phase != 'updating':
            raise ValueError(f'Unknown update job phase: {phase!r}')
        if self._apply_ready is None:
            raise RuntimeError('The update transaction is not armed for the apply')
        # the phase boundary: the state is the not cancellable one from here
        # on, and every worker is stopped before the job changes the files
        self.set_state('updating')
        try:
            await restart_app.request_graceful_restart(
                reason=f'update of mod "{self.name}"', hooks=self._apply_hooks(),
                owner=self.name)
        except restart_app.RestartInProgress:
            raise RuntimeError('A graceful restart is in progress') from None
        # wait for the hook: every worker stopped, the resume intent published
        await self._apply_ready.wait()
        if self._apply_aborted:
            raise RuntimeError(f'The restart of the update was aborted: {self._apply_aborted}')
        return True

    def _cancelled(self):
        """
        Whether the transaction of this mod was cancelled (update_cancel):
        its scope was cancelled, the cancel was requested before the scope was
        registered, or the transaction ended.

        Returns:
            bool: True when the job must abort (see on_job_phase)
        """
        manager = self.manager
        if manager._transaction != self.name or manager._cancel_requested:
            return True
        scope = manager._transaction_scope
        return scope is not None and scope.cancel_called

    def _prepare_apply(self):
        """
        Arm the rendezvous of the applying phase before the job runs, see
        on_job_phase().
        """
        self._apply_ready = trio.Event()
        self._apply_done = trio.Event()
        self._apply_error = None
        self._apply_aborted = ''

    def _release_apply(self, error):
        """
        The job of the applying phase finished (or failed): release the
        restart hook waiting for it, with the error it must raise (None when
        the apply succeeded and the restart may continue).
        """
        self._apply_error = error
        self._apply_done.set()

    async def _on_all_stopped(self):
        """
        The hook of the graceful restart of this transaction (see
        RestartHooks.on_all_stopped): every worker stopped and the resume
        intent published - the replace window. It releases the 'updating'
        phase of the job (the files may be replaced now) and waits for the
        job to finish; a failed apply is raised here, which cancels the
        restart (§10: the backend keeps running, the stopped configs stay
        stopped, the half-applied tree converges on the next update).
        """
        self._apply_ready.set()
        await self._apply_done.wait()
        if self._apply_error is not None:
            raise self._apply_error

    async def _on_aborted(self, reason):
        """
        The hook of the graceful restart of this transaction when it ended
        before the replace window (see RestartHooks.on_aborted): the job's
        'updating' phase must not go on.
        """
        self._apply_aborted = reason
        self._apply_ready.set()

    def _apply_hooks(self):
        """
        The hooks of the graceful restart of this transaction.
        """
        return restart_app.RestartHooks(
            on_all_stopped=self._on_all_stopped, on_aborted=self._on_aborted)


class UpdateManager:
    """
    Mod updates of the process: one ModUpdateManager per mounted mod, the
    shared http client and the instance-level window of the single update
    transaction.

    Attributes (read-only for the outside):
        mods (dict[str, ModUpdateManager]): The managed mods, keyed by mod
            name, built by bind_mods()
    """

    def __init__(self):
        # {mod_name: ModUpdateManager}, the mounted mods of this process
        self.mods = {}
        # the shared http client of the update servers, created lazily on
        # the trio thread (a client is bound to one event loop)
        self._client = None
        # the manager task state (run())
        self._nursery = None
        # the mod of the update transaction in flight, '' when there is
        # none: the instance-level window (downloading / updating)
        self._transaction = ''
        # CancelScope of the in-flight transaction task (its pre-flight and
        # the download phase of its job): update_cancel cancels it while the
        # transaction is still cancellable
        self._transaction_scope = None
        # True when update_cancel arrived before the download scope was
        # registered: run_transaction cancels it right after
        self._cancel_requested = False
        # automatic checks on / off and the configured interval (seconds,
        # None = only the first check of the process)
        self.auto = True
        self.interval = None

    # =========================================================================
    # Startup / lifespan task
    # =========================================================================

    def load_mods(self):
        """
        Load the mods of this process.

        Returns:
            dict[str, Mod]: {mod_name: Mod}, the MOD_LOADER mount
        """
        return dict(MOD_LOADER.dict_mod)

    def bind_mods(self, mods):
        """
        Build one ModUpdateManager per mod of the process.

        Args:
            mods (dict[str, Mod]): The mount to manage

        Returns:
            dict[str, ModUpdateManager]: The per-mod managers
        """
        self.mods = {name: ModUpdateManager(self, mod) for name, mod in mods.items()}
        return self.mods

    def load_schedule(self):
        """
        Read the update schedule from the deploy config (once per process,
        like the daily restart time: changing it takes effect with the next
        restart). A broken config falls back to the defaults, the checks of
        a broken installation must not stop.

        Sets `auto` and `interval`.
        """
        from alasio.deploy.config.model import DeployConfig

        try:
            update = DeployConfig().config.data.Update
            auto = bool(update.AutoUpdate)
            interval = int(update.CheckUpdateInterval)
        except Exception as e:
            logger.warning(f'[Update] Failed to read the update schedule: {e}, using the defaults')
            auto, interval = True, CHECK_INTERVAL_FALLBACK / 60
        self.auto = auto
        # 0 = only the first check of a sequence (the startup one, or the
        # manual one that reset the sequence)
        self.interval = max(interval, 0) * 60.0 or None

    def next_delay(self, rounds):
        """
        Seconds to the next automatic check after a completed round.

        Args:
            rounds (int): Completed rounds since the last sequence reset

        Returns:
            float | None: The delay, None when no automatic check follows
                (the loop waits for a manual check then): automatic checks
                off, or CheckUpdateInterval = 0 after the follow-up of the
                first check
        """
        if not self.auto:
            return None
        if rounds == 1:
            # the second check of a sequence: the random spread
            return random.uniform(*FIRST_CHECK_DELAY)
        if self.interval is None:
            return None
        return self.interval

    def client(self):
        """
        The shared http client of the update servers, created lazily (a
        client is bound to the event loop it is used in).

        Returns:
            httpx2.AsyncClient: The client
        """
        if self._client is None:
            import httpx2
            self._client = httpx2.AsyncClient()
        return self._client

    @property
    def closed(self):
        """
        Returns:
            bool: True when the manager task is not running (before the
                startup and after the shutdown)
        """
        return self._nursery is None

    @property
    def transaction(self):
        """
        Returns:
            str: The mod of the update transaction in flight, '' when there
                is none (the restart orchestration reads it to refuse an
                external restart racing the transaction, doc §16.3)
        """
        return self._transaction

    async def run(self):
        """
        Lifespan background task of the update manager: the startup
        convergence first, then the check loop of every mounted mod, see
        _run().

        A convergence that completed ends in a graceful restart: the checks
        of this process are skipped, the new process runs them (§2.6). An
        error never escapes this task: it lives in the lifespan nursery,
        where a raised error would cancel every other task and take the
        whole backend process down.
        """
        try:
            await self._run()
        except trio.Cancelled:
            raise
        except Exception as e:
            logger.error(f'[Update] The update manager stopped: {e}')
            logger.exception(e)
        finally:
            # the gate must never stay shut: a stopped manager releases it
            # (the checks are gone, refusing the starts forever would lock
            # the user out of every config)
            UPDATE_STARTUP.release()

    async def _run(self):
        """
        Body of run(): the convergence, the initial states and the check
        loops (the error guard belongs to run()).
        """
        self.bind_mods(self.load_mods())
        self.load_schedule()
        # the initial states are visible before the first request
        for mod in self.mods.values():
            if not mod.mod.entry.mirrors:
                mod.set_state('unmanaged')
                # no update source: the first check of this mod is over by
                # definition, nothing gates its starts
                mod.first_update_checked.set()
                continue
            try:
                mod.mirrors()
            except (ValueError, TypeError) as e:
                logger.error(f'[Update] Invalid update source of "{mod.name}": {e}')
                mod.set_state('error', error=str(e))
                continue
            mod.set_state('idle')
        # every mounted mod has its ModUpdateManager and its startup event
        # now: the startup gate of the sync world and of the resume queue
        # opens (doc §16.8)
        UPDATE_STARTUP.update_inited.set()
        try:
            async with trio.open_nursery() as nursery:
                self._nursery = nursery
                try:
                    await self._startup_convergence()
                except trio.Cancelled:
                    raise
                except Exception as e:
                    # the convergence must never stop the checks: report and
                    # continue, the failed mods are retried by their rounds
                    logger.error(f'[Update] Startup convergence failed: {e}')
                    logger.exception(e)
                if restart_app.GRACEFUL_RESTART.running:
                    # the convergence requested a restart: the first checks
                    # belong to the new process
                    await trio.sleep_forever()
                for mod in self.mods.values():
                    if mod.mod.entry.mirrors:
                        nursery.start_soon(mod.run)
                await trio.sleep_forever()
        finally:
            self._nursery = None
            for mod in self.mods.values():
                mod.forget_scopes()
            await self._aclose_client()

    async def _aclose_client(self):
        """
        Close the shared http client of the update servers (best effort,
        cancelled contexts included: the process may be going down).
        """
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            with trio.CancelScope(shield=True):
                await client.aclose()
        except trio.Cancelled:
            raise
        except Exception as e:
            logger.warning(f'[Update] Failed to close the http client: {e}')

    async def _startup_convergence(self):
        """
        Finish the updates a killed process left behind (§2.6).

        Every mod manager checks its target ledger for an unfinished job
        (job.pack, written before the real files are changed and only left
        behind by a process killed in flight) and finishes the found ones
        (missing pieces are downloaded from the mod server). A convergence
        failure is reported on the topic of the mod and retried before its
        next check round.
        """
        converged = []
        for mod in self.mods.values():
            if await mod.finish_convergence():
                converged.append(mod)
        if not converged:
            return
        await self.request_convergence_restart(converged)

    async def request_convergence_restart(self, mods):
        """
        Apply a finished convergence: one graceful restart, the states of the
        converged mods move to 'updating' (the disk changed under the modules
        this process loaded, §2.6).

        Args:
            mods (list[ModUpdateManager]): The mods whose disk was converged
        """
        names = ', '.join(mod.name for mod in mods)
        async with self.transaction_window(mods[0].name):
            for mod in mods:
                mod.set_state('updating')
            try:
                await restart_app.request_graceful_restart(
                    reason=f'finished the interrupted update of {names}',
                    owner=mods[0].name)
            except Exception as e:
                # no supervisor (a development run) or a conflict (another
                # updater took the instance meanwhile): the disk is converged,
                # the running process keeps the old modules, report and move on
                logger.warning(f'[Update] The convergence cannot restart the backend: {e}')
                for mod in mods:
                    mod.set_state('error', error=f'Restart required to apply the finished update: {e}')

    # =========================================================================
    # Manual check (rpc update_check)
    # =========================================================================

    async def check(self, name=''):
        """
        Check the updates now (rpc update_check).

        ``name`` selects one mod, an empty name every managed mod. The check
        of a mod runs in its loop (the only place a mod is checked): this
        only cancels the pending wait (or the in-flight check, the request is
        interrupted) and restarts the sequence of that mod - the manual round
        counts as the first check, the next automatic one follows the
        standard schedule (random 5-10 minutes, then the configured
        interval). Returns once the checks are requested, the progress flows
        through the Update topic.

        Args:
            name (str): Mod name, '' checks every managed mod

        Raises:
            UpdateError: Unknown / unmanaged mod, the manager is not
                running, or the mod is busy with an update transaction
        """
        if self.closed:
            raise UpdateError('The update manager is not running')
        mods = self.select(name)
        if not mods:
            raise UpdateError(f'No updatable mod: "{name}"')
        for mod in mods:
            mod.request_manual()
        logger.info(f'[Update] Update check requested: {", ".join(mod.name for mod in mods)}')

    def select(self, name):
        """
        Resolve a mod selector of the rpcs into the managed mods to act on.

        Args:
            name (str): Mod name, '' selects every managed mod

        Returns:
            list[ModUpdateManager]: The mods, in mount order

        Raises:
            UpdateError: The mod is unknown, unmanaged, or busy with an
                update transaction
        """
        if name:
            mod = self.mods.get(name, None)
            if mod is None:
                raise UpdateError(f'No such mod: "{name}"')
            if not mod.mod.entry.mirrors:
                raise UpdateError(f'The mod declares no update source: "{name}"')
            if mod.info.state in ('downloading', 'updating'):
                raise UpdateError(f'The mod is busy with an update: "{name}"')
            return [mod]
        return [
            mod for mod in self.mods.values()
            if mod.mod.entry.mirrors and mod.info.state not in ('downloading', 'updating')
        ]

    # =========================================================================
    # Update transaction (rpc update_apply / update_cancel)
    # =========================================================================

    @property
    def applying(self):
        """
        Returns:
            bool: True when the update transaction in flight is in its
                applying phase (its restart is in flight, the workers are
                being stopped or already stopped): such a transaction is not
                cancellable (§2.5), the update_cancel / cancel_restart rpcs
                refuse while it is on.

        The state of the mods is the judge (not the window of the manager):
        on the success path the transaction scope closes the window before
        the process exits, while the applying phase (and its restart) is
        still in flight.
        """
        return any(mod.info.state == 'updating' for mod in self.mods.values())

    async def apply(self, name):
        """
        Start the update transaction of one mod (rpc update_apply).

        The preconditions are checked here (the transaction is accepted and
        its task scheduled, the progress flows through the Update topic): the
        mod must be mounted and managed, have an update available, no other
        update transaction and no graceful restart may be in flight, and the
        backend must run under a supervisor (the update ends in a backend
        restart).

        The transaction first runs the version pre-flight and the pack
        download with the workers still running ('downloading', cancellable
        through update_cancel, nothing is changed on disk), then applies the
        update inside the graceful restart ('updating', irreversible): at the
        phase boundary every worker is stopped (the resume intent published),
        DeployJob.update() replaces the files and the supervisor restarts the
        backend.

        Args:
            name (str): Mod name

        Raises:
            UpdateError: Any precondition fails
        """
        if self.closed:
            raise UpdateError('The update manager is not running')
        if not mpipe_backend:
            raise UpdateError('Cannot update backend running without supervisor')
        if restart_app.GRACEFUL_RESTART.running:
            raise UpdateError('A graceful restart is in progress')
        if self._transaction:
            raise UpdateError(f'An update is already in progress: "{self._transaction}"')
        mod = self.mods.get(name, None)
        if mod is None:
            raise UpdateError(f'No such mod: "{name}"')
        if not mod.mod.entry.mirrors:
            raise UpdateError(f'The mod declares no update source: "{name}"')
        if mod.info.state != 'available':
            raise UpdateError(f'The mod has no update available: "{name}"')
        # accept: the instance-level window opens synchronously (no await in
        # between, so two concurrent applies cannot both pass the checks
        # above), the task takes over; the window is closed by the transaction
        # scope of the task (and adopted there, see transaction_window)
        self._transaction = name
        self._cancel_requested = False
        BACKEND_WORKER_MANAGER.update_begin_transaction(name)
        mod.set_state('downloading')
        logger.info(f'[Update] Update transaction accepted: "{name}" '
                    f'{mod.info.current_version or "(none)"} -> {mod.info.latest_version}')
        self._nursery.start_soon(mod.run_transaction)

    async def cancel(self):
        """
        Cancel the cancellable phase of the update flow (rpc update_cancel).

        A check in flight: the request is interrupted and the check window
        closes, the recorded state of the last check stays. The downloading
        phase of an update transaction (its pre-flight and the pack download
        of the job): the job is interrupted, nothing was changed on disk, the
        state returns to 'available'. The applying phase of a transaction
        (the workers are stopping or stopped, the files are about to change)
        is not cancellable: an explicit refusal.

        Raises:
            UpdateError: Nothing to cancel, or the update is being applied
        """
        if self.applying:
            raise UpdateError('The update is being applied and cannot be cancelled')
        if self._transaction:
            scope = self._transaction_scope
            if scope is None:
                # the transaction scope is not registered yet (the task just
                # started): the task cancels itself right after
                self._cancel_requested = True
                return
            scope.cancel()
            return
        cancelled = [mod.cancel_check() for mod in list(self.mods.values())]
        if not any(cancelled):
            raise UpdateError('Nothing to cancel')

    @contextlib.asynccontextmanager
    async def transaction_window(self, mod):
        """
        The instance-level window of an update transaction, as a scope (doc
        §16.4): the window is opened on entry -- or adopted when the
        acceptance of the rpc opened it already -- and closed on exit, every
        path included (a failure or a cancellation cannot leave it open).

        The close is paired with a best-effort release of the queued starts:
        the close itself is synchronous and always runs; the release is async
        and skips what a restart took over in between (release_queued).
        """
        if self._transaction and self._transaction != mod:
            raise UpdateError(f'An update is already in progress: "{self._transaction}"')
        if not self._transaction:
            self._transaction = mod
            BACKEND_WORKER_MANAGER.update_begin_transaction(mod)
        try:
            yield
        finally:
            if self._transaction == mod:
                self._transaction = ''
                self._cancel_requested = False
                BACKEND_WORKER_MANAGER.update_end_transaction(mod)
            try:
                await self.release_queued()
            except trio.Cancelled:
                raise
            except Exception as e:
                logger.warning(f'[Update] Failed to release the queued starts: {e}')

    # =========================================================================
    # Accepting the starts of an update window (the queue of the gate)
    # =========================================================================

    async def release_queued(self):
        """
        Start the configs whose start was accepted during a closed update
        window (their resume entries were created by the WorkerManager
        windows).

        The release is skipped while an update transaction owns the instance
        or a graceful restart is in flight: the configs stay queued and the
        restart of the transaction resumes them (or the end of the
        transaction releases them). Each config starts through
        worker_resume() with the standard interval between two starts; a
        config a restart collected in between is skipped by worker_resume()
        itself.
        """
        if self._transaction or restart_app.GRACEFUL_RESTART.running:
            return
        if not UPDATE_STARTUP.startup_over():
            # the first update checks are still running: the queued starts
            # belong to the startup orchestration, which releases them when
            # the mods are ready (doc §16.8)
            return
        released = await trio.to_thread.run_sync(
            BACKEND_WORKER_MANAGER.release_update_queue)
        if not released:
            return
        logger.info(f'[Update] Starting the configs queued during the update window: {", ".join(released)}')
        for config in released:
            try:
                mod = get_mod(config)
            except Exception as e:
                # the config is gone: drop its queue entry, a manual start
                # is possible again
                logger.error(f'[Update] Queued config dropped, cannot load it: {e}')
                await trio.to_thread.run_sync(BACKEND_WORKER_MANAGER.drop_resume, [config])
                continue
            try:
                success, msg = await trio.to_thread.run_sync(
                    BACKEND_WORKER_MANAGER.worker_resume, mod, config)
            except Exception as e:
                logger.error(f'[Update] Failed to start the queued config "{config}": {e}')
                await trio.to_thread.run_sync(
                    BACKEND_WORKER_MANAGER.worker_force_kill, config, True)
                continue
            if not success:
                logger.info(f'[Update] Queued config skipped: "{config}": {msg}')
                continue
            logger.info(f'[Update] Queued config started: {config}')
            await trio.sleep(restart_app.WORKER_START_INTERVAL)


UPDATE_MANAGER = UpdateManager()
