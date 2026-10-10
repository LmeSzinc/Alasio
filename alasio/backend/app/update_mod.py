"""
Mod update orchestration: one mod's state, check sequence and update
transaction.

One ModUpdateManager holds the state and the flow of ONE mod (its check
sequence, its update server, its state on the Update topic); the process-wide
pieces -- the http client, the task nursery, the instance-level window of the
single update transaction and the startup convergence -- live in
alasio.backend.app.update_manager.

The check of one mod is read-only: the local version of the mod ledger
against latest.pack of its update source, one small request per round.
The update transaction (rpc update_apply) has three phases, one per step of
the flow (they are the phases the deploy job reports, see DeployJob.update):

- 'checking': the version check - the transaction pre-flight (DeployJob.check()
  before the lock) and the check inside the job flow read the local version
  against latest.pack, read-only and cancellable through update_cancel
  (nothing was changed, the same flow can be retried). The state of the mod
  follows the phases the job reports, this one included;
- 'downloading': the update pack downloads into memory, still cancellable
  through update_cancel and with nothing changed on disk;
- 'updating': the update transaction opens and drives its own graceful restart
  window (restart.RestartWindow): every worker is stopped and the resume intent
  published before DeployJob.update() - the whole download and replace flow of
  the target under the one exclusive lock of the job - replaces the files while
  the backend is still alive, then the window hands the process to the
  supervisor and the new backend resumes the recorded configs. A failure after
  the window opened withdraws it again: the backend keeps running and the
  half-updated tree converges on the next update.

A separate download phase was rejected: it would run outside the lock of
update() and could race a second updater of the same target (the lock is
held for the whole download-and-replace flow of one caller). Only the
read-only version comparison of check() is shared, which takes no lock
(the index pack of a target being updated is read atomically).
"""

import time
from typing import Optional

import trio

from alasio.backend.app import restart as restart_app
from alasio.backend.app.update_startup import UPDATE_STARTUP
from alasio.backend.topic.update import UpdateInfo, UpdateSource
from alasio.deploy.httpclient.probe import Mirrors
from alasio.deploy.pack.job import DeployJob, UpdateAborted
from alasio.deploy.pack.server_file import ServerFile
from alasio.logger import logger


class CheckLoop:
    """
    One full multi-round check sequence of one mod: the first check (as soon
    as the given delay is over), the random follow-up and the following
    rounds (the schedule belongs to UpdateManager.next_delay). One task of
    the manager nursery serves the sequence (run(), started by
    ModUpdateManager.start_check_loop).

    A sequence is never rewound: its callers replace it (a manual check
    restarts the schedule, the manual round being the first check of the new
    sequence) or drop it (an update transaction owns the mod: no check is
    needed while the mod is updated, and a check must not overwrite the
    transaction state). The sequence also ends by itself when a graceful
    restart of this process takes over (the new backend checks from
    scratch); it always detaches itself from the mod on the way out.

    The cancel scope of the wait / round in flight is registered here
    (scope), so interrupt() reaches it; the check request of a round runs
    under request_check, which registers the request scope, so update_cancel
    interrupts the request alone (cancel_check) and the sequence continues.
    """

    def __init__(self, mod, delay, rounds=0):
        """
        Args:
            mod (ModUpdateManager): The mod this sequence checks
            delay (float | None): Seconds to the first check, None when no
                automatic check follows (the sequence serves only manual
                checks, see UpdateManager.next_delay)
            rounds (int): Rounds the sequence starts with, so the schedule
                continues from there (the end of an update transaction
                resumes the sequence as if its first round was done).
                Defaults to 0
        """
        self.mod = mod
        self.delay = delay
        self.rounds = rounds
        # the cancel scope of the wait / round in flight, None between two
        # registrations; interrupt() cancels it
        self.scope: "Optional[trio.CancelScope]" = None
        # the cancel scope of the in-flight check request, None when there
        # is none; cancel_check() cancels it
        self.check_scope: "Optional[trio.CancelScope]" = None
        # the sequence was interrupted (a replacement or a drop comes
        # after): run() must end
        self.interrupted = False

    def interrupt(self):
        """
        Interrupt the sequence: run() ends at its next checkpoint (the
        callers install the successor, or None, themselves).
        """
        self.interrupted = True
        scope = self.scope
        if scope is not None:
            scope.cancel()

    async def request_check(self):
        """
        Run one check request of the mod (DeployJob.check()) under the request
        cancel scope: update_cancel interrupts this request alone (see
        cancel_check), the sequence continues.

        Returns:
            DeployCheck | None: The result, None when the request was
                cancelled (the caller keeps the state of the last check)
        """
        with trio.CancelScope() as scope:
            self.check_scope = scope
            try:
                check = await self.mod.job().check()
            finally:
                if self.check_scope is scope:
                    self.check_scope = None
        if scope.cancelled_caught:
            # update_cancel: the request alone was interrupted
            return None
        return check

    def cancel_check(self):
        """
        Interrupt the in-flight check of this sequence (update_cancel), see
        UpdateManager.cancel().

        Returns:
            bool: True when a check was in flight
        """
        scope = self.check_scope
        if scope is None:
            return False
        scope.cancel()
        return True

    async def run(self):
        """
        Serve the sequence: wait, round, compute the next delay, repeat.
        Returns when the sequence is over: interrupted (see interrupt()), or
        a graceful restart of this process took over (the new backend
        checks from scratch).
        """
        mod = self.mod
        if self.delay is None:
            # no automatic check follows: the startup round is over by
            # definition (the first manual check waits for nothing)
            mod.first_update_checked.set()
        try:
            while not self.interrupted:
                with trio.CancelScope() as scope:
                    self.scope = scope
                    try:
                        if self.delay is not None:
                            if self.delay > 0:
                                await trio.sleep(self.delay)
                            await mod.check_round()
                            if restart_app.GRACEFUL_RESTART.running:
                                # a restart (an update transaction or a
                                # finished convergence) is in flight: no
                                # further check of this process, the new
                                # backend checks
                                return
                            # the first check of this mod is over
                            # (idempotent): its startup gate opens, the
                            # resume queue may release it
                            mod.first_update_checked.set()
                            self.rounds += 1
                            self.delay = mod.manager.next_delay(self.rounds)
                        else:
                            # no automatic check pending: wait for a manual
                            # one (which starts a new sequence)
                            await trio.sleep_forever()
                    finally:
                        if self.scope is scope:
                            self.scope = None
                if scope.cancelled_caught:
                    # interrupted: the sequence is over (replaced or dropped)
                    return
        finally:
            # the sequence ended: a later interrupt / cancel finds nothing
            self.scope = None
            self.check_scope = None
            if mod.check_loop is self:
                # it ended by itself (a restart of this process took over)
                mod.check_loop = None


class ModUpdateManager:
    """
    Update state and flow of one mounted mod.

    One instance per mounted mod, built by UpdateManager.bind_mods(): it
    holds the state pushed to the Update topic, the update server of the mod
    and the check sequence of the mod (one CheckLoop per full multi-round
    sequence, see start_check_loop), plus the per-mod parts of the update
    transaction. The pieces shared by every mod (the http client, the
    instance window of the single update transaction, the queue release)
    stay on the UpdateManager, see it.

    Attributes (read-only for the outside):
        info (UpdateInfo): The state of the mod, also the value bound on the
            Update topic
        convergence_pending (bool): The startup convergence of the mod
            failed and is retried before its next check round
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
        self._server: "Optional[ServerFile]" = None
        # the check sequence of this mod in flight, None when no sequence
        # runs (an update transaction owns the mod, or the manager stopped):
        # one CheckLoop per full multi-round sequence, replaced by
        # start_check_loop and dropped by stop_check_loop
        self.check_loop: "Optional[CheckLoop]" = None
        # the startup event of this mod: set when the first update
        # check is over (a check ran, failed or was skipped); the resume queue
        # and the starts of the mod wait behind it. Registered in the
        # process-wide UPDATE_STARTUP so the sync world (the worker manager)
        # reads its is_set() without importing this module
        self.first_update_checked = UPDATE_STARTUP.mod_event(self.name)
        # the startup convergence of the mod failed: retried before the next
        # check round until it succeeds
        self.convergence_pending = False
        # the restart window of the applying phase of the transaction in flight
        # (restart.RestartWindow): the job's 'updating' phase opens and drives
        # it (see on_job_phase), the transaction task closes it (shutdown or
        # withdraw). None when no apply is in flight
        self._window = None

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
    # Check sequence (one task per sequence)
    # =========================================================================

    def start_check_loop(self, delay=0.0, rounds=0):
        """
        Start (or restart) the check sequence of this mod: the sequence in
        flight, if any, is interrupted and replaced. A manual check restarts
        the schedule this way (its round is the first check of the new
        sequence); the startup starts it with the first check at once; the
        end of an update transaction resumes it where the update
        interrupted it (the standard follow-up spacing, then the interval,
        see UpdateManager.next_delay).

        Args:
            delay (float | None): Seconds to the first check of the new
                sequence, None when no automatic check follows. Defaults
                to 0.0 (immediately)
            rounds (int): Rounds the new sequence is considered to have
                completed (its schedule continues from there). Defaults
                to 0
        """
        nursery = self.manager._nursery
        if nursery is None:
            # the manager is not running (or is going down): no sequence
            return
        loop = CheckLoop(self, delay, rounds)
        old = self.check_loop
        self.check_loop = loop
        if old is not None:
            old.interrupt()
        nursery.start_soon(loop.run)

    def stop_check_loop(self):
        """
        Drop the check sequence of this mod (an update transaction owns the
        mod, or the manager is going down): no check runs until a new
        sequence is started.
        """
        loop = self.check_loop
        self.check_loop = None
        if loop is not None:
            loop.interrupt()

    def cancel_check(self):
        """
        Interrupt the in-flight check of this mod (update_cancel), see
        UpdateManager.cancel().

        Returns:
            bool: True when a check was in flight
        """
        loop = self.check_loop
        return loop is not None and loop.cancel_check()

    async def check_round(self):
        """
        One round of the check sequence: retry a failed convergence, then
        check.
        """
        if self.manager.transaction == self.name:
            # the mod is inside an instance-level transaction window (a
            # convergence restart; an update transaction drops the whole
            # sequence instead, see stop_check_loop): the check of this mod
            # is skipped, it must not overwrite the transaction state
            #
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
        Finish the interrupted update this mod may hold: the
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
        One check of this mod: the state flow around one check request of
        the sequence.

        Read-only (the local ledger version against latest.pack) and
        cancellable: the request runs under the request scope of the
        sequence (CheckLoop.request_check), update_cancel interrupts it and
        the recorded state of the last check stays. A check locks nothing:
        the starts of the configs are not gated by it.
        """
        previous = self.info
        loop = self.check_loop
        if loop is None:
            raise RuntimeError('The check sequence of the mod is not running')
        self.set_state('checking')
        try:
            try:
                check = await loop.request_check()
            except Exception as e:
                logger.warning(f'[Update] Failed to check "{self.name}": {e}')
                self.set_state('error', error=str(e))
                return
            if check is None:
                # update_cancel: the request was interrupted, nothing is
                # recorded (the previous state and its versions stay, the
                # timing of the sequence is kept)
                logger.info(f'[Update] The check of "{self.name}" was cancelled')
                self.info = previous
                self.push(previous)
                return
            if check.uptodate():
                self.set_state('uptodate', current=check.local, latest=check.latest,
                               checked_at=time.time())
            else:
                # the verdict line is logged by DeployJob.check() itself
                self.set_state('available', current=check.local, latest=check.latest,
                               checked_at=time.time())
        except trio.Cancelled:
            raise

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
           exclusive lock) with this manager as its phase listener: the
           phases of the flow drive the state ('checking' / 'downloading'
           while the files on disk are untouched, then 'updating'), and at
           the 'updating'
           phase boundary the transaction opens its own restart window --
           every worker stopped, the resume intent published -- before the
           job changes the real files (see on_job_phase);
        3. the job replaced the files: the window is shut down and the
           supervisor restarts the backend with the new files on disk, the
           new process resumes the recorded configs;
        4. anything else withdraws the window again (the resume file removed,
           the gate released): the backend keeps running, the stopped configs
           stay stopped and the half-updated tree converges on the next
           update (E5).

        Never raises: the task lives in the manager nursery, where a raised
        error would cancel every other task; every failure ends in the error
        state, and the transaction scope closes the window and releases the
        queued configs on the way out.
        """
        manager = self.manager
        # whether the transaction handed the backend to the supervisor: the
        # check sequence only resumes when it did not (the new process checks
        # from scratch)
        restarting = False
        try:
            async with manager.transaction_window(self.name) as transaction:
                # phase 1: the pre-flight, the clean cancellation window
                # before anything is stopped or changed
                with trio.CancelScope() as scope:
                    transaction.register(scope)
                    try:
                        check = await self.job().check()
                    finally:
                        transaction.unregister(scope)
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
                state_error = ''
                cancelled = False
                try:
                    with trio.CancelScope() as scope:
                        transaction.register(scope)
                        try:
                            ok = await self.job().update(self.on_job_phase)
                        finally:
                            transaction.unregister(scope)
                    if scope.cancelled_caught:
                        # update_cancel: the in-flight request was interrupted
                        # (the common cancel path of the downloading phase), the
                        # real files were never changed
                        cancelled = True
                    elif not ok:
                        # some records stay in error: the tree did not converge
                        state_error = 'Some files failed to update, retry the update'
                except UpdateAborted as e:
                    # the fallback check of the phase callback: the transaction
                    # was cancelled while the job had no request to interrupt
                    logger.info(f'[Update] The update of "{self.name}" was aborted: {e}')
                    cancelled = True
                except trio.Cancelled:
                    # the manager is going down (its nursery was cancelled): the
                    # window must not let the restart continue
                    await self._withdraw_window('the update manager stopped')
                    raise
                except Exception as e:
                    logger.error(f'[Update] Failed to apply the update of "{self.name}": {e}')
                    logger.exception(e)
                    state_error = str(e)
                # the restart window of the applying phase (opened by
                # on_job_phase at its 'updating' boundary)
                if cancelled or state_error:
                    # nothing was applied (or the tree did not converge): the
                    # published intent is withdrawn, the backend keeps running
                    await self._withdraw_window(f'the update of "{self.name}" did not apply')
                else:
                    # the job replaced the files: hand the process over to the
                    # supervisor, the new backend resumes the recorded configs
                    window = self._take_window()
                    if window is not None:
                        try:
                            restarting = await window.shutdown()
                        except Exception as e:
                            # the supervisor pipe is gone: withdraw the published
                            # intent, the backend stays alive
                            logger.error(f'[Update] Failed to restart the backend: {e}')
                            logger.exception(e)
                            state_error = str(e)
                            try:
                                await window.cancel(
                                    f'the restart of the update of "{self.name}" failed')
                            except Exception as cancel_error:
                                logger.error(f'[Update] Failed to withdraw the restart: {cancel_error}')
                if cancelled:
                    # zero side effect: nothing was changed on disk
                    self.set_state('available')
                    return
                if state_error:
                    self.set_state('error', error=state_error)
                # the success path has nothing to do here: the window handed the
                # process to the supervisor, the new backend takes over
        except trio.Cancelled:
            raise
        except Exception as e:
            logger.error(f'[Update] Failed to prepare the update of "{self.name}": {e}')
            logger.exception(e)
            self.set_state('error', error=str(e))
        finally:
            # the transaction is over: the check sequence resumes where the
            # update interrupted it -- not immediately (the state was just
            # checked, and a failure would likely repeat), the standard
            # follow-up spacing first, then the interval; None (no automatic
            # check) parks the sequence with AutoUpdate off.
            # The restarted backend is the exception: its process takes the
            # mods over and the first checks belong to it
            if not restarting:
                self.start_check_loop(delay=manager.next_delay(1), rounds=1)

    # =========================================================================
    # The applying phase: the phase listener contract of DeployJob.update()
    # =========================================================================

    async def on_job_phase(self, phase):
        """
        A phase of the deploy job of this transaction (DeployJob.update() runs
        with this bound method as its on_job_phase callback, so the state and
        the cancellability of the transaction follow what the job is really
        doing):

        - 'checking': the version check of the flow runs (the transaction is
          still cancellable through update_cancel, nothing on the real files
          was changed yet);
        - 'downloading': the update pack downloads (still cancellable through
          update_cancel, nothing on the real files was changed yet);
        - 'updating': the local changes start. This opens the restart window of
          the transaction and drives it to its publication: the gate, the
          graceful stop of every worker, the freeze of the resume list and the
          resume file. The call returns True only once the window is open --
          the backend still runs, the files may be replaced from here on -- and
          only the transaction task closes it (see run_transaction); the
          transaction is not cancellable any more.

        The call is also the fallback check of a cancellation: a cancel
        interrupts an in-flight request itself, but one that arrived while the
        job had no checkpoint to be interrupted at is answered here with
        False, and the job aborts before changing anything (it removes its
        temporary files and raises UpdateAborted).

        Args:
            phase (str): 'checking', 'downloading' or 'updating'

        Returns:
            bool: True to let the job continue, False when the transaction was
                cancelled meanwhile

        Raises:
            ValueError: The phase is unknown
            RuntimeError: The applying phase cannot start or was aborted
        """
        if self._cancelled():
            return False
        if phase == 'checking':
            self.set_state('checking')
            return True
        if phase == 'downloading':
            self.set_state('downloading')
            return True
        if phase != 'updating':
            raise ValueError(f'Unknown update job phase: {phase!r}')
        # the phase boundary: the state is the not cancellable one from here
        # on, and every worker is stopped before the job changes the files
        self.set_state('updating')
        try:
            window = await restart_app.open_restart_window(
                reason=f'update of mod "{self.name}"')
        except restart_app.RestartInProgress:
            raise RuntimeError('A graceful restart is in progress') from None
        self._window = window
        try:
            success, resume_list = await window.wait_stopped(restart_app.GRACEFUL_STOP_TIMEOUT)
        except BaseException:
            # the wait was interrupted (the manager is going down): the window
            # wrote nothing yet, withdraw it and let the job abort
            await self._withdraw_window('the restart of the update was interrupted')
            raise
        if not success:
            # a force restart / a backend stop cancelled the wait: nothing was
            # published, the resume list is not frozen and the job aborts
            # before changing anything (E5)
            await self._withdraw_window('the worker wait was cancelled')
            raise RuntimeError(
                'The restart of the update was aborted: the worker wait was cancelled')
        # the publication (the resume file and its credential): the configs of
        # the list are resumed by the new backend whatever happens from here on,
        # and the caller's critical section (the file replacement) runs in the
        # job after this returns
        await window.publish(resume_list)
        return True

    def _cancelled(self):
        """
        Whether the transaction of this mod was cancelled (update_cancel):
        the registered scope of its running phase was cancelled (the
        cancellation may not be delivered yet), the cancel was latched
        before the phase registered (see UpdateTransaction), or the
        transaction ended.

        Returns:
            bool: True when the job must abort (see on_job_phase)
        """
        manager = self.manager
        transaction = manager._transaction
        if transaction is None or transaction.mod != self.name:
            return True
        return transaction.cancelled()

    def _take_window(self):
        """
        Take the restart window of the applying phase out of the manager.

        Returns:
            restart.RestartWindow: The window opened by on_job_phase, None when
                the job never reached its applying phase
        """
        window = self._window
        self._window = None
        return window

    async def _withdraw_window(self, reason):
        """
        Withdraw the restart window of the applying phase (best effort)

        The published resume file is removed and the gate released: the backend
        keeps running whatever the caller does next.

        Args:
            reason (str): Log message
        """
        window = self._take_window()
        if window is None:
            return
        try:
            await window.cancel(reason)
        except Exception as e:
            logger.error(f'[Update] Failed to withdraw the restart of "{self.name}": {e}')
