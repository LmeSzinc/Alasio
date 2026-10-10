"""
Mod update orchestration: the process-level manager.

The UpdateManager is a per-process singleton running as a lifespan
background task (alasio.backend.app.app, next to the auto-resume and the
daily restart tasks):

1. the startup convergence finishes the updates a killed process left
   behind (job.pack of every mounted mod), a completed convergence ends
   in one graceful restart;
2. one check sequence per mounted mod, one CheckLoop object per full
   multi-round sequence: the first check runs immediately, the second one
   after a random 5-10 minutes (the clients started together re-phase
   themselves, their later checks never form a global wave), the following
   ones every Deploy.Update.CheckUpdateInterval minutes. A manual check
   (rpc update_check) starts a new sequence (its round is the first check
   of it, run immediately); an update transaction drops the sequence of its
   mod (no check is needed while the mod is updated) and a fresh sequence
   starts when the transaction ends without a backend restart.

The per-mod state and flow live in alasio.backend.app.update_mod
(ModUpdateManager); the UpdateManager owns the pieces shared by every mod:
the http client, the task nursery and the instance-level window of the single
update transaction (rpc update_apply / update_cancel).

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
from typing import Optional

import trio

from alasio.backend.app import restart as restart_app
from alasio.backend.app.update_mod import CHECK_INTERVAL_FALLBACK, FIRST_CHECK_DELAY, ModUpdateManager
from alasio.backend.app.update_startup import UPDATE_STARTUP
from alasio.backend.mpipe.mpipe_backend import mpipe_backend
from alasio.backend.topic._worker import BACKEND_WORKER_MANAGER
from alasio.backend.topic.worker import get_mod
from alasio.config.entry.loader import MOD_LOADER
from alasio.logger import logger


class UpdateError(RuntimeError):
    """
    User-facing refusal of an update rpc: the mod is unknown / unmanaged,
    no update is available, an update or a restart is already in progress,
    the backend runs without a supervisor. The topic translates it into an
    RpcValueError.
    """


class UpdateTransaction:
    """
    The instance-level window of the single update transaction:
    the mod being updated and the cancel handles of the cancellable phase
    of its task (ModUpdateManager.run_transaction).

    The rpc side (UpdateManager.apply / cancel) opens the window and
    cancels through it; the task registers the cancel scope of its running
    phase here, so that update_cancel interrupts the in-flight request. A
    cancel arriving before that registration (the task was just scheduled)
    is latched and served at it; the latch also answers the fallback check
    of the task (ModUpdateManager._cancelled) when the cancellation could
    not be delivered at an interruption point.

    Attributes (managed by the protocol methods):
        mod (str): The mod being updated
        scope (trio.CancelScope): The scope of the running phase of the
            task, None while the task is between two registrations
        cancel_requested (bool): A cancel that arrived with no scope to
            cancel: served at the next registration
    """

    def __init__(self, mod):
        """
        Args:
            mod (str): The mod being updated
        """
        self.mod = mod
        self.scope: "Optional[trio.CancelScope]" = None
        self.cancel_requested = False

    def register(self, scope):
        """
        The task registers the cancel scope of its running phase; a latched
        cancel is served here.

        Args:
            scope (trio.CancelScope): The scope wrapping the running phase
        """
        self.scope = scope
        if self.cancel_requested:
            scope.cancel()

    def unregister(self, scope):
        """
        The task leaves the cancellable section (a stale scope never unsets
        a newer one).

        Args:
            scope (trio.CancelScope): The scope registered before
        """
        if self.scope is scope:
            self.scope = None

    def cancelled(self):
        """
        Returns:
            bool: True when the transaction must stop at its next
                interruption point or abort at its next phase boundary:
                the cancel was latched, or the registered scope was
                cancelled (the cancellation may not be delivered yet)
        """
        if self.cancel_requested:
            return True
        scope = self.scope
        return scope is not None and scope.cancel_called

    def cancel(self):
        """
        Cancel the cancellable phase of the transaction (rpc update_cancel):
        the in-flight request is interrupted, nothing was changed on disk.
        Without a registered scope the cancel is latched and served at the
        next registration.
        """
        if self.scope is None:
            self.cancel_requested = True
        else:
            self.scope.cancel()


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
        self.mods: "dict[str, ModUpdateManager]" = {}
        # the shared http client of the update servers, created lazily on
        # the trio thread (a client is bound to one event loop)
        self._client: "Optional[httpx2.AsyncClient]" = None
        # the manager task state (run())
        self._nursery: "Optional[trio.Nursery]" = None
        # the instance-level window of the update transaction in flight
        # (downloading / updating), None when there is none: it holds the
        # mod being updated and the cancel handles of its task (see
        # UpdateTransaction)
        self._transaction: "Optional[UpdateTransaction]" = None
        # automatic checks on / off and the configured interval (seconds,
        # None = only the first check of the process)
        self.auto = True
        self.interval: "Optional[float]" = None

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
                external restart racing the transaction)
        """
        return self._transaction.mod if self._transaction is not None else ''

    async def run(self):
        """
        Lifespan background task of the update manager: the startup
        convergence first, then the check loop of every mounted mod, see
        _run().

        A convergence that completed ends in a graceful restart: the checks
        of this process are skipped, the new process runs them. An
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
        # opens
        UPDATE_STARTUP.update_inited.set()
        try:
            async with trio.open_nursery() as nursery:
                self._nursery = nursery
                try:
                    try:
                        restarting = await self._startup_convergence()
                    except trio.Cancelled:
                        raise
                    except Exception as e:
                        # the convergence must never stop the checks: report
                        # and continue, the failed mods are retried by their
                        # rounds
                        logger.error(f'[Update] Startup convergence failed: {e}')
                        logger.exception(e)
                        restarting = False
                    if restarting:
                        # the convergence handed the process to the supervisor:
                        # the first checks belong to the new backend
                        await trio.sleep_forever()
                    for mod in self.mods.values():
                        if mod.mod.entry.mirrors:
                            mod.start_check_loop(delay=0.0 if self.auto else None)
                    await trio.sleep_forever()
                finally:
                    # the manager is going down: from here on it schedules
                    # no new task (start_check_loop reads this through
                    # closed), and the sequences in flight die with the
                    # nursery cancellation -- they detach themselves on the
                    # way out
                    self._nursery = None
        finally:
            for mod in self.mods.values():
                mod.stop_check_loop()
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

    async def _startup_convergence(self) -> bool:
        """
        Finish the updates a killed process left behind.

        Every mod manager checks its target ledger for an unfinished job
        (job.pack, written before the real files are changed and only left
        behind by a process killed in flight) and finishes the found ones
        (missing pieces are downloaded from the mod server). A convergence
        failure is reported on the topic of the mod and retried before its
        next check round.

        Returns:
            bool: True when a convergence restart was handed to the supervisor
                (the process is going down, the checks belong to the new one),
                False when there was nothing to converge or the restart could
                not be handed over
        """
        converged = [mod for mod in self.mods.values() if await mod.finish_convergence()]
        if not converged:
            return False
        return await self.request_convergence_restart(converged)

    async def request_convergence_restart(self, mods) -> bool:
        """
        Apply a finished convergence: one graceful restart, the states of the
        converged mods move to 'updating' (the disk changed under the modules
        this process loaded).

        The restart is driven here (the internal window, an empty critical
        section): the workers are stopped, the resume intent published and the
        backend handed to the supervisor. A restart that cannot be handed over
        (no supervisor, another owner, a cancelled wait) is reported on the
        topic of the mods and the caller continues without it.

        Args:
            mods (list[ModUpdateManager]): The mods whose disk was converged

        Returns:
            bool: True when the restart was handed to the supervisor (the
                process is going down), False when it could not be
        """
        names = ', '.join(mod.name for mod in mods)
        async with self.transaction_window(mods[0].name):
            for mod in mods:
                mod.set_state('updating')
            try:
                window = await restart_app.open_restart_window(
                    reason=f'finished the interrupted update of {names}')
            except Exception as e:
                self._report_convergence_failure(mods, e)
                return False
            try:
                success, resume_list = await window.wait_stopped(
                    restart_app.GRACEFUL_STOP_TIMEOUT)
                if not success:
                    raise RuntimeError('the worker wait was cancelled')
                await window.publish(resume_list)
                return await window.shutdown()
            except Exception as e:
                self._report_convergence_failure(mods, e)
                try:
                    await window.cancel(f'the convergence restart failed: {e}')
                except Exception as cancel_error:
                    logger.error(
                        f'[Update] Failed to withdraw the convergence restart: {cancel_error}')
                return False

    def _report_convergence_failure(self, mods, error):
        """
        Report a convergence restart that could not be handed over

        Args:
            mods (list[ModUpdateManager]): The converged mods
            error (Exception): Why the restart failed
        """
        # no supervisor (a development run), a conflict (another updater took
        # the instance meanwhile) or a failed step: the disk is converged, the
        # running process keeps the old modules, report and move on
        logger.warning(f'[Update] The convergence cannot restart the backend: {error}')
        for mod in mods:
            mod.set_state('error', error=f'Restart required to apply the finished update: {error}')

    # =========================================================================
    # Manual check (rpc update_check)
    # =========================================================================

    async def check(self, name=''):
        """
        Check the updates now (rpc update_check).

        ``name`` selects one mod, an empty name every managed mod. The check
        of a mod runs in its check sequence (the only place the mod is
        checked): this interrupts the sequence in flight (its request is
        interrupted too) and starts a new one - the manual round is the
        first check of the new sequence, the next automatic one follows the
        standard schedule (random 5-10 minutes, then the configured
        interval). Returns once the new sequences are started, the progress
        flows through the Update topic.

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
            mod.start_check_loop()
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
            if self._busy(mod):
                raise UpdateError(f'The mod is busy with an update: "{name}"')
            return [mod]
        return [
            mod for mod in self.mods.values()
            if mod.mod.entry.mirrors and not self._busy(mod)
        ]

    def _busy(self, mod):
        """
        Whether a mod is inside its update transaction

        The transaction is the judge, not its state: the pre-flight of a
        transaction is 'checking' (the state of a plain check too, the state
        alone cannot tell them apart), and the applying phase outlives the
        transaction scope on the success path (the process is restarting, the
        state stays 'updating').

        Args:
            mod (ModUpdateManager): The mod to test

        Returns:
            bool: True when no check may be started on the mod
        """
        transaction = self._transaction
        if transaction is not None and transaction.mod == mod.name:
            return True
        return mod.info.state in ('downloading', 'updating')

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
                cancellable, the update_cancel / cancel_restart rpcs
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

        The transaction runs the version pre-flight and the pack download with
        the workers still running ('checking' then 'downloading', cancellable
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
            raise UpdateError(f'An update is already in progress: "{self._transaction.mod}"')
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
        self._open_transaction(name)
        # the mod enters its update transaction: the check sequence of the
        # mod is dropped (no check is needed while the mod is updated, and a
        # check must not overwrite the transaction state); a fresh sequence
        # starts when the transaction ends without a backend restart (see
        # ModUpdateManager.run_transaction)
        mod.stop_check_loop()
        # the state of the mod follows the phases the job reports (see
        # on_job_phase): the acceptance only opens the transaction, the
        # transaction task reports 'checking' when its flow starts
        logger.info(f'[Update] Update transaction accepted: "{name}"')
        self._nursery.start_soon(mod.run_transaction)

    async def cancel(self):
        """
        Cancel the cancellable phase of the update flow (rpc update_cancel).

        A check in flight: the request is interrupted and the check window
        closes, the recorded state of the last check stays. The pre-flight and
        downloading phase of an update transaction ('checking' / 'downloading',
        the pack download of the job): the flow is interrupted, nothing was
        changed on disk, the state returns to 'available'. The applying phase
        of a transaction (the workers are stopping or stopped, the files are
        about to change) is not cancellable: an explicit refusal.

        Raises:
            UpdateError: Nothing to cancel, or the update is being applied
        """
        if self.applying:
            raise UpdateError('The update is being applied and cannot be cancelled')
        if self._transaction:
            # cancel the pre-flight / the download of the transaction: the
            # in-flight request is interrupted, or the cancel is latched for
            # the task that just started (see UpdateTransaction.cancel)
            self._transaction.cancel()
            return
        cancelled = [mod.cancel_check() for mod in list(self.mods.values())]
        if not any(cancelled):
            raise UpdateError('Nothing to cancel')

    def _open_transaction(self, mod):
        """
        Open the instance-level window of an update transaction (synchronous)

        The window of the manager and the holder registration of the graceful
        restart are opened in one synchronous section (no await in between):
        an external restart requested right after sees the transaction, and
        _close_transaction() below is the only place that clears them.

        Args:
            mod (str): The mod being updated
        """
        self._transaction = UpdateTransaction(mod)
        BACKEND_WORKER_MANAGER.update_begin_transaction(mod)
        # the restart registry: the public entry of the graceful restart
        # refuses while the holder is set, the transaction itself drives its
        # restart through the internal entry (open_restart_window)
        restart_app.GRACEFUL_RESTART.set_holder(f'update of "{mod}"')

    def _close_transaction(self, transaction, mod):
        """
        Close the instance-level window of an update transaction (synchronous)

        A stale close (another transaction took the window over) leaves both
        registrations alone.

        Args:
            transaction (UpdateTransaction): The window opened before
            mod (str): The mod of that window
        """
        if self._transaction is not transaction:
            return
        self._transaction = None
        BACKEND_WORKER_MANAGER.update_end_transaction(mod)
        restart_app.GRACEFUL_RESTART.clear_holder(f'update of "{mod}"')

    @contextlib.asynccontextmanager
    async def transaction_window(self, mod):
        """
        The instance-level window of an update transaction, as a scope: the
        window is opened on entry -- or adopted when the
        acceptance of the rpc opened it already -- and closed on exit, every
        path included (a failure or a cancellation cannot leave it open).
        The transaction handle is yielded: the task registers the cancel
        scope of its running phase there (see UpdateTransaction).

        The close is paired with a best-effort release of the queued starts:
        the close itself is synchronous and always runs; the release is async
        and skips what a restart took over in between (release_queued).
        """
        transaction = self._transaction
        if transaction is not None and transaction.mod != mod:
            raise UpdateError(f'An update is already in progress: "{transaction.mod}"')
        if transaction is None:
            self._open_transaction(mod)
            transaction = self._transaction
        try:
            yield transaction
        finally:
            self._close_transaction(transaction, mod)
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
        if self._transaction is not None or restart_app.GRACEFUL_RESTART.running:
            return
        if not UPDATE_STARTUP.startup_over():
            # the first update checks are still running: the queued starts
            # belong to the startup orchestration, which releases them when
            # the mods are ready
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
