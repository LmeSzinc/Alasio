"""
Update check module of the mods

The module owns the update *checks* of every mounted mod and the states the
frontend reads on the Update topic. One class holds both sides of one mod --
the state and its check sequence (``ModUpdateCheck``): the sequence calls back
into the state it serves instead of a second object that owns the mod, there is
no loop object to keep in sync. The module (``UpdateCheck``) holds one per
mounted mod, the shared http client, and the lifespan task (``run``) that owns
the nursery the sequences run in.

The module runs as one task of the lifespan (``run``): the sequences are
children of the nursery that task owns, so the process shutdown cancels and
joins them before the teardown -- nothing of the module is in flight when
``close`` closes the shared http client.

A check is read-only (the local ledger against latest.pack) and never applies
anything: the apply flows belong to ``alasio.backend.app.restart.restart_step``
(their steps drive the states here and run the deploy jobs).

Deliberately independent of the restart manager: it does not read the flow
state, it never pauses or ends a sequence by itself. The maintenance flows
pause the sequences they do not need (``pause``), restore them on their way out
(``restore``) and the startup flow starts them (``start``):

- the first check of a sequence waits ``random(CHECK_INTERVAL,
  CHECK_INTERVAL * 2)`` seconds -- the process startup just ran a full update,
  a check right after would repeat it, and the one-interval-wide spread keeps
  every backend started at the same moment spread out (see
  ``UpdateCheck.first_delay``);
- a sequence never follows another one: a caller replaces it (``start`` /
  ``update_check``) or drops it (``pause``);
- an exception of a check ends that sequence (reported, never raised: the
  module runs in the lifespan, a raised error would take the backend down) --
  the flow that paused the mod restores the sequence later.

The states of one mod (Update topic):

    unmanaged / no-supervisor / disabled / idle / checking / uptodate / available / error

plus the states the apply steps set: 'queued', 'stopping', 'downloading',
'updating' and 'restart-required'.

A mod is classified once, at the startup: 'no-supervisor' when the backend
runs without a live supervisor (only a supervisor can spawn the new process
that makes an applied update effective, see MPipeBackend), 'unmanaged' when it
declares no usable update source (the source is parsed once), 'disabled' when
the mod updates are off for this process (``auto``, i.e.
Deploy.Update.AutoUpdate = false), 'idle' otherwise. None of them is ever
checked: no sequence is started for it and the manual entry answers a message
instead of checking.
"""
import random
import time
from typing import Dict, List, Optional

import trio

from alasio.backend.mpipe.mpipe_backend import has_supervisor
from alasio.backend.topic.update import UpdateInfo, UpdateSource
from alasio.deploy.httpclient.client_async import AsyncHttpClient
from alasio.deploy.httpclient.probe_mirrors import Mirrors
from alasio.deploy.pack.job import DeployJob
from alasio.deploy.pack.server_file import ServerFile
from alasio.logger import logger

# Seconds between two automatic checks of one running sequence. The first check
# of a sequence waits CHECK_INTERVAL to CHECK_INTERVAL * 2 (see
# UpdateCheck.first_delay), the following ones are CHECK_INTERVAL apart
CHECK_INTERVAL = 300.0
# A check is discarded when the last one finished less than this many seconds
# ago: the result of that check is the answer already
FRESH_WINDOW = 5.0
# The messages of a discarded check (the caller, the rpc layer, shows them to
# the user)
FRESH_MESSAGE = 'Just checked a moment ago'
BUSY_MESSAGE = 'A check is already in progress'
# The message of a check requested for a mod without a usable update source
# (the entries answer it instead of raising: a mod without a source is a normal
# state, the startup flow publishes it as 'unmanaged')
UNMANAGED_MESSAGE = 'The mod has no update source'
# The message of a check requested while the mod updates are off for this
# process (Deploy.Update.AutoUpdate = false): no mod is ever checked then (the
# entries answer it instead of raising: turning the updates off is a normal
# state, the startup flow publishes it as 'disabled')
DISABLED_MESSAGE = 'The mod updates are disabled'
# The message of a check requested for a mod of a backend running without a
# live supervisor: only a supervisor can spawn the new process that makes an
# applied update effective (this one has the mod packages imported already), so
# the updates are off -- no sequence, no manual check, no full update (the
# entries answer it instead of raising: running without a supervisor is a
# normal state, the startup flow publishes it as 'no-supervisor')
NO_SUPERVISOR_MESSAGE = 'The backend runs without a supervisor'
# The states the apply steps of a flow set on a mod while the flow runs
# ('queued' / 'stopping' / 'downloading'): they describe the flow in flight, not
# the update itself -- the wrap-up of a flow that does not hand over releases
# them back to 'available' (see UpdateCheck.release_states)
FLOW_STATES = ('queued', 'stopping', 'downloading')


class ModUpdateCheck:
    """
    The update check of one mounted mod: its state and its check sequence

    The state (``info``, the update source of the mod) and the sequence that
    refreshes it live in one class: the sequence calls back into the state it
    serves instead of a second object that owns the mod.

    The check of a mod is one operation with two entries and one lock:

    - ``run_check_loop()``: the automatic sequence, a task of the module
      nursery (started by ``start_check_loop``, dropped by
      ``stop_check_loop``): wait the spread (CHECK_INTERVAL to
      CHECK_INTERVAL * 2), check, wait CHECK_INTERVAL, check, ...;
    - ``update_check()``: the manual check (the user entry): the automatic
      sequence is interrupted, the check runs in the caller's task and the
      sequence starts over afterwards.

    Both run the real check (``_run_update_check``) under ``_check_lock`` and
    both discard it when the last check finished less than FRESH_WINDOW seconds
    ago. Two callers arriving together therefore leave exactly one check
    running: the loser is answered with FRESH_MESSAGE or BUSY_MESSAGE (the two
    checks before the lock only exist for those messages, the arbitration is
    the re-check inside the lock). ``cancel_check()`` interrupts the
    request in flight alone: nothing is recorded, the state of the last check
    stays.

    Attributes (read-only for the outside):
        info (UpdateInfo): The state of the mod, also the value bound on the
            Update topic
    """

    def __init__(self, update_check, mod):
        """
        Args:
            update_check (UpdateCheck): The module of the process
            mod (Mod): The mounted mod to check, its entry carries the update
                source (mirrors)
        """
        # the module of the process (the shared client and nursery, the
        # schedule); it is not called ``update_check`` -- that name is the
        # manual check of this mod
        self.check_module = update_check
        self.mod = mod
        self.name = mod.name
        # the update source of the mod, parsed once here: the mount of a mod
        # never changes while the process runs, so a malformed declaration is
        # not retried on every check -- the mod is unmanaged for good (None)
        self.mirrors = self._parse_mirrors()
        # the state of the mod, the value pushed to the Update topic: a new
        # instance per change (the topic binds the instance, it is never
        # mutated in place)
        self.info = UpdateInfo(state='idle')
        # the lock the real check runs under: one check at a time per mod, the
        # discard of a check that arrives too late happens inside it
        self._check_lock = trio.Lock()
        # the cancel scope of the sequence task in flight, None when no
        # sequence runs (the module starts the task, the scope is created here
        # so a stop landing before the task starts is not lost)
        self._loop_scope: "Optional[trio.CancelScope]" = None
        # the cancel scope of the check request in flight, None when there is
        # none; cancel_check() cancels it
        self._request_scope: "Optional[trio.CancelScope]" = None
        # monotonic timestamp (time.monotonic) of the last check that ran (any
        # outcome, a cancelled request does not count): a check younger than
        # FRESH_WINDOW is not repeated. Monotonic on purpose: the wall clock
        # can jump (NTP, the user, DST) and a jump must neither make a check
        # look fresh nor stale (the checked_at of the topic stays the wall
        # clock timestamp, it is what the frontend shows)
        self._checked_mono = 0.0

    # ------------------------------------------------------------------
    # State on the Update topic
    # ------------------------------------------------------------------

    def set_state(self, state, current=None, latest=None, checked_at=None, error=''):
        """
        Set the state of the mod and push it to the Update topic.

        Args:
            state (str): One of UPDATE_STATE
            current (str): Local version, None keeps the known one
            latest (str): Latest version, None keeps the known one
            checked_at (int): Check timestamp (unix seconds), None keeps the
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

    # ------------------------------------------------------------------
    # Update source of the mod
    # ------------------------------------------------------------------

    def _parse_mirrors(self):
        """
        Parse the update source the mod declares (once, at construction)

        The mount of a mod never changes while the process runs, so the source
        is parsed once instead of on every check: a mod without usable mirrors
        is unmanaged from then on -- no check ever runs on it (the startup flow
        publishes the state once, see UpdateCheck.set_initial_states).

        Returns:
            Mirrors | None: The mirrors, None when the mod declares none (it is
                not updated on purpose) or a malformed one (a warning says so)
        """
        value = self.mod.entry.mirrors
        if not value:
            return None
        try:
            return Mirrors.from_input(value)
        except (ValueError, TypeError) as e:
            logger.warning(f'[Update] Invalid update source of "{self.name}": {e}, '
                           f'the mod is unmanaged')
            return None

    @property
    def disabled(self) -> bool:
        """
        Whether the mod updates are off for this process

        The switch (``auto``, Deploy.Update.AutoUpdate) is read once at the
        startup, so the classification is final like the update source: a
        disabled mod is never checked -- no sequence is started for it and the
        manual entry answers DISABLED_MESSAGE (the state published at the
        startup is 'disabled'). A mod without a usable update source is
        'unmanaged' whatever the switch says: the source is the classification
        that outlives the switch.

        Returns:
            bool: True when the mod updates are off for this process
                (Deploy.Update.AutoUpdate = false) and the mod has an update
                source to check (a mod without one is unmanaged, whatever the
                switch says)
        """
        return self.mirrors is not None and not self.check_module.auto

    @property
    def no_supervisor(self) -> bool:
        """
        Whether the mod updates are off because the backend has no live supervisor

        A supervisor is the only one that can replace the backend process, and
        an applied update only takes effect in the new process (this one has
        the mod packages imported already). Without a live supervisor nothing
        is checked -- no sequence, no manual check, no full update -- and the
        state published at the startup is 'no-supervisor'. Read live: the
        supervisor can die while the backend runs (the recv loop of the
        lifespan clears the pipe), the backend shuts itself down right after.
        A mod without a usable update source is 'unmanaged' whatever the
        environment says.

        Returns:
            bool: True when the backend runs without a live supervisor and the
                mod has an update source to check
        """
        return self.mirrors is not None and not has_supervisor()

    async def job(self):
        """
        Build the deploy job of this mod, bound to a fresh update server

        The server is created per use: a ServerFile serves one flow (see its
        class docstring -- it caches the mirror selection and the probe of that
        flow, one instance for the process would freeze the selection of a
        long-running backend), and it is warmed up here (the record read of the
        mirror selection is blocking sqlite IO, it runs in a worker thread
        instead of on the request path; see ServerFile.warmup).

        Returns:
            DeployJob: The job, bound to the warmed server

        Raises:
            ValueError: The mod is unmanaged: the entries of the mod (the
                check, the sequence) refuse an unmanaged mod before this point
                and the steps of the flows skip it, so this is the last line
                of defence
            AllMirrorsFailedError: If the warmup probe finds no usable mirror
        """
        if self.mirrors is None:
            raise ValueError(f'The mod declares no update source: "{self.name}"')
        server = ServerFile(self.mirrors, name=self.name, client=self.check_module.client())
        await server.warmup()
        return DeployJob(root=self.mod.root, server=server)

    # ------------------------------------------------------------------
    # The automatic sequence
    # ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        """
        Returns:
            bool: Whether the automatic sequence of this mod is running (a mod
                that is never checked -- unmanaged, disabled -- has none)
        """
        return self._loop_scope is not None

    def start_check_loop(self):
        """
        Start the automatic sequence of this mod (the module's entry: the
        startup flow, a restore, the end of the manual check)

        The sequence in flight, if any, is stopped first: one sequence per mod.
        The new task waits the standard spread before its first check -- the
        startup flow just ran a full update, a check right now would repeat it.

        A mod that is never checked has no sequence: nothing is started for an
        unmanaged, disabled or no-supervisor one (their states are published
        once by the startup flow).
        """
        if self.mirrors is None or self.disabled or self.no_supervisor:
            return
        nursery = self.check_module.nursery
        if nursery is None:
            # the module is not attached (the backend is going down): no
            # sequence
            return
        self.stop_check_loop()
        scope = trio.CancelScope()
        self._loop_scope = scope
        nursery.start_soon(self.run_check_loop)

    def stop_check_loop(self):
        """
        Drop the automatic sequence of this mod (a maintenance flow pauses the
        mod, the manual check restarts it)

        A sequence whose task did not start yet is dropped all the same: the
        task finds no scope of its own and returns without checking.
        """
        scope = self._loop_scope
        self._loop_scope = None
        if scope is not None:
            scope.cancel()

    async def run_check_loop(self):
        """
        Serve the automatic sequence of this mod: wait the spread, check, wait
        CHECK_INTERVAL, check, ...

        Returns when the sequence is dropped (a pause, a replacement) or when a
        check raised (reported: the flow that paused the mod restores a fresh
        sequence).
        """
        scope = self._loop_scope
        if scope is None:
            # dropped before this task started
            return
        if self.mirrors is None or self.disabled or self.no_supervisor:
            # defensive: the sequence is never started for a mod that is never
            # checked (see start_check_loop), a sequence that finds itself
            # without a source, with the mod updates turned off, or on a
            # backend that lost its supervisor, ends here instead of checking
            return
        try:
            with scope:
                delay = self.check_module.first_delay()
                while True:
                    if delay > 0:
                        await trio.sleep(delay)
                    async with self._check_lock:
                        await self._run_update_check()
                    delay = self.check_module.interval()
        except trio.Cancelled:
            raise
        except Exception as e:
            logger.error(f'[Update] The check sequence of "{self.name}" stopped: {e}')
            logger.exception(e)
        finally:
            # the sequence ended: a later stop finds nothing
            if self._loop_scope is scope:
                self._loop_scope = None

    # ------------------------------------------------------------------
    # The manual check
    # ------------------------------------------------------------------

    async def update_check(self) -> str:
        """
        Check the updates of this mod now (the manual entry)

        The check runs in the caller's task: the automatic sequence is dropped
        first (the check takes its place in the rhythm) and started over
        afterwards. The two checks before the lock only serve the message the
        caller gets -- the arbitration is the re-check inside the lock, so two
        callers arriving together leave exactly one check running.

        Returns:
            str: The message of the check: '' when it ran, the reason it was
                discarded otherwise (FRESH_MESSAGE / BUSY_MESSAGE /
                UNMANAGED_MESSAGE / DISABLED_MESSAGE / NO_SUPERVISOR_MESSAGE)
        """
        if self.no_supervisor:
            # the backend cannot replace itself (no live supervisor): an
            # applied update could not take effect, the mods are never checked
            return NO_SUPERVISOR_MESSAGE
        if self.mirrors is None:
            # the entry of the manual check: a mod without a usable update
            # source is never checked (its state is 'unmanaged', published once
            # by the startup flow); nothing is an error here
            return UNMANAGED_MESSAGE
        if self.disabled:
            # the mod updates are off for this process (Deploy.Update.AutoUpdate
            # = false): a disabled mod is never checked, not even by hand -- the
            # state 'disabled' published at the startup is final
            return DISABLED_MESSAGE
        if time.monotonic() - self._checked_mono < FRESH_WINDOW:
            # the check that just ran is the answer already
            return FRESH_MESSAGE
        if self._check_lock.locked():
            # a check (the automatic sequence or another manual one) is running
            return BUSY_MESSAGE
        async with self._check_lock:
            self.stop_check_loop()
            message = await self._run_update_check()
            self.start_check_loop()
            return message

    def cancel_check(self) -> bool:
        """
        Interrupt the check request in flight of this mod (the manual cancel).

        Only the request is interrupted: the sequence continues (or the manual
        check of the caller returns), the state of the last check stays.

        Returns:
            bool: True when a request was in flight
        """
        scope = self._request_scope
        if scope is None:
            return False
        scope.cancel()
        return True

    # ------------------------------------------------------------------
    # The real check (the body under the lock)
    # ------------------------------------------------------------------

    async def _run_update_check(self) -> str:
        """
        Run the real check of this mod and record its verdict (the body inside
        ``_check_lock``)

        Read-only (the local ledger version against latest.pack). A failure is
        recorded as the state of the mod; an interrupted check (the manual
        cancel, the sequence dropped mid-request) records nothing: the state of
        the last check goes back, the topic is never left on 'checking'.

        Returns:
            str: '' when the check ran, FRESH_MESSAGE when it was discarded
                (the second check of the fresh window, inside the lock),
                UNMANAGED_MESSAGE for an unmanaged mod (defensive: the entry
                refuses it and its sequence is never started), DISABLED_MESSAGE
                for a disabled one, NO_SUPERVISOR_MESSAGE for a mod of a
                backend without a live supervisor (the same, the mod updates
                are off)
        """
        if self.no_supervisor:
            # defensive: the last line of defence of the check path
            return NO_SUPERVISOR_MESSAGE
        if self.mirrors is None:
            # defensive: the last line of defence of the check path
            return UNMANAGED_MESSAGE
        if self.disabled:
            # defensive: the same line for a disabled mod (see the manual entry)
            return DISABLED_MESSAGE
        if time.monotonic() - self._checked_mono < FRESH_WINDOW:
            # two callers raced to the lock: the first one checked a moment ago
            return FRESH_MESSAGE
        previous = self.info
        checking = self.set_state('checking')
        try:
            check = await self._request_check()
        except trio.Cancelled:
            # the sequence was dropped while the request was in flight (a pause
            # of a maintenance flow, a replacement of the sequence, the
            # teardown): the interrupted check records nothing and the state of
            # the last check goes back -- the topic must not stay on 'checking'
            # until the restored sequence runs its next check (5-10 minutes)
            self._rollback_check(checking, previous)
            raise
        except Exception as e:
            logger.warning(f'[Update] Failed to check "{self.name}": {e}')
            self.set_state('error', error=str(e))
            self._checked_mono = time.monotonic()
            return ''
        if check is None:
            # the manual cancel: the request was interrupted, nothing is
            # recorded (the previous state and its versions stay)
            logger.info(f'[Update] The check of "{self.name}" was cancelled')
            self._rollback_check(checking, previous)
            return ''
        if check.uptodate():
            self.set_state('uptodate', current=check.local, latest=check.latest,
                           checked_at=int(time.time()))
        else:
            # the verdict line is logged by DeployJob.check() itself
            self.set_state('available', current=check.local, latest=check.latest,
                           checked_at=int(time.time()))
        self._checked_mono = time.monotonic()
        return ''

    def _rollback_check(self, checking, previous):
        """
        Put the state of the last check back after an interrupted request

        Called by an interrupted check (a dropped sequence, the manual cancel):
        no verdict exists, so nothing is recorded. Only when the state of the
        mod is still the ``'checking'`` instance this request pushed -- a writer
        that moved the state on meanwhile (a maintenance flow marking its scope
        'queued' / 'stopping') did it knowingly and its state is the newer truth
        (the interrupted check must not resurrect the verdict it replaced).
        Binding the previous instance as-is is enough: the topic drops a push
        whose value is already bound by identity.

        Args:
            checking (UpdateInfo): The 'checking' state this request pushed
            previous (UpdateInfo): The state it replaced
        """
        if self.info is not checking:
            return
        self.info = previous
        self.push(previous)

    async def _request_check(self):
        """
        Run one check request of the mod (DeployJob.check()) under the request
        cancel scope: cancel_check() interrupts this request alone.

        Returns:
            DeployCheck | None: The result, None when the request was cancelled
        """
        with trio.CancelScope() as scope:
            self._request_scope = scope
            try:
                job = await self.job()
                check = await job.check()
            finally:
                if self._request_scope is scope:
                    self._request_scope = None
        if scope.cancelled_caught:
            # the manual cancel: the request alone was interrupted
            return None
        return check


class UpdateCheck:
    """
    The check module of the process: one ModUpdateCheck per mounted mod, the
    shared http client and the pause bookkeeping of the maintenance flows.

    The module runs as one task of the lifespan (``run``): the task owns the
    nursery of the check sequences and tears them down, with the shared http
    client, when the process shuts down (``close``).

    Attributes (read-only for the outside):
        mods (dict[str, ModUpdateCheck]): The managed mods, keyed by mod name,
            built by bind()
    """

    def __init__(self):
        self.mods: "Dict[str, ModUpdateCheck]" = {}
        self._client = None
        self._nursery: "Optional[trio.Nursery]" = None
        # the master switch of the mod updates (Deploy.Update.AutoUpdate), read
        # once per process by load_schedule(): with it off every managed mod is
        # 'disabled' -- no sequence, no manual check, no full update (see
        # ModUpdateCheck.disabled)
        self.auto = True

    # ------------------------------------------------------------------
    # Wiring (the lifespan task and the nursery of the sequences)
    # ------------------------------------------------------------------

    async def run(self):
        """
        Lifespan background task: own the nursery the check sequences run in

        One task per backend process, started by the lifespan before the
        restart manager: the manager's startup flow starts the check sequences
        (``start``) and finds the nursery set here -- this task sets it in its
        first synchronous section, and the startup flow, spawned by the task
        of the manager, only runs after the pass both tasks were queued in (see
        the comment at the entry of the lifespan).

        The sequences are children of the nursery opened here, never of the
        lifespan nursery: a failing sequence ends alone (its body reports and
        never raises), and the module can join every sequence before it tears
        down. The task runs until the process shuts down (the lifespan nursery
        is cancelled): the sequences are cancelled and joined with this
        nursery, then ``close`` closes the shared http client (shielded: the
        teardown must complete under the cancellation of the shutdown).

        The nursery field is cleared the moment the teardown starts, before
        the sequences are cancelled and joined: from then on the module starts
        no sequence (the field is what the entries read). A restore landing in
        that window -- the wrap-up of a flow during the shutdown -- starts
        nothing instead of hitting a closing nursery.
        """
        try:
            async with trio.open_nursery() as nursery:
                self._nursery = nursery
                try:
                    await trio.sleep_forever()
                finally:
                    # the teardown: the sequences are about to be cancelled and
                    # joined, no sequence may start from here on (the field is
                    # cleared before the nursery closes for new arrivals)
                    self._nursery = None
        except trio.Cancelled:
            raise
        except Exception as e:
            # a check sequence must never take the backend down: report and end
            # the module (the lifespan nursery keeps running)
            logger.error(f'[Update] The check module stopped: {e}')
            logger.exception(e)
        finally:
            await self.close()

    @property
    def nursery(self):
        """
        Returns:
            trio.Nursery | None: The nursery of the check sequences, owned by
                ``run``; None when the module does not run (the process startup
                has not reached the task yet, or the process is shutting down:
                no sequence is started then)
        """
        return self._nursery

    def reset(self):
        """
        Return to the initial state (tests: a fresh process has a fresh module)
        """
        self.mods = {}
        self._nursery = None
        self.auto = True

    def bind(self, mods=None):
        """
        Build one ModUpdateCheck per mounted mod and set the initial states.

        Args:
            mods (dict[str, Mod]): The mount to manage, defaults to the
                MOD_LOADER mount (imported here: the loader is bound to the
                project root resolved at the backend startup, see
                asgi.create_config)

        Returns:
            dict[str, ModUpdateCheck]: The per-mod checks, keyed by mod name
        """
        if mods is None:
            from alasio.config.entry.loader import MOD_LOADER
            mods = dict(MOD_LOADER.dict_mod)
        self.mods = {name: ModUpdateCheck(self, mod) for name, mod in mods.items()}
        self.load_schedule()
        return self.mods

    def load_schedule(self):
        """
        Read the update schedule from the deploy config (once per process, like
        the daily restart time: changing it takes effect with the next
        restart). A broken config falls back to the defaults: the checks of a
        broken installation must not stop.

        Sets `auto`. Read once: the classification of the mods ('disabled', see
        ModUpdateCheck.disabled) is as final as their update source.
        """
        from alasio.deploy.config.model import DeployConfig

        try:
            update = DeployConfig().config.data.Update
            auto = bool(update.AutoUpdate)
        except Exception as e:
            logger.warning(f'[Update] Failed to read the update schedule: {e}, using the defaults')
            auto = True
        self.auto = auto

    def first_delay(self) -> float:
        """
        Seconds to the first automatic check of a fresh sequence

        Always ``random(CHECK_INTERVAL, CHECK_INTERVAL * 2)``. The window is a
        fixed mathematical design, not a tunable:

        - the spread is exactly CHECK_INTERVAL wide, and the checks that follow
          are CHECK_INTERVAL apart, so the sequence stays evenly spread --
          backends started at the same moment (a fleet restarted together, the
          daily maintenance) never hit the mirrors together, at any time;
        - the lower bound CHECK_INTERVAL covers the startup full update: the
          process just checked every mod, a check right after would repeat it.

        Returns:
            float: The delay
        """
        return random.uniform(CHECK_INTERVAL, CHECK_INTERVAL * 2)

    def interval(self) -> float:
        """
        Seconds between two checks of a running sequence

        Returns:
            float: CHECK_INTERVAL
        """
        return CHECK_INTERVAL

    def client(self):
        """
        The shared http client of the update servers, created lazily (a client
        is bound to the event loop it is used in). Never replaced: a client
        ``close`` closed stays the client, a further request fails through it
        instead of a second client being built.

        Returns:
            httpx2.AsyncClient: The client
        """
        if self._client is None:
            # the client of the deploy domain, see AsyncHttpClient.create_client()
            self._client = AsyncHttpClient.create_client()
        return self._client

    async def close(self):
        """
        Tear the module down: stop every sequence and close the shared http
        client

        The teardown of ``run`` (its ``finally``); also callable on its own (a
        test). The sequences are stopped (a task still in flight is cancelled;
        ``run`` joins them with its nursery before this point, so no request of
        the module is in flight here) and the client is closed.

        The closed client is kept: the close is final -- a request that arrives
        after it goes to the closed client and fails with "Cannot send a
        request, as the client has been closed" instead of silently building a
        second client that nothing would ever close (a foreign task may still
        be in flight while the process goes down: its request fails, the
        teardown does not wait for it). Idempotent, and shielded: the process
        may be shutting down with the cancellation already in flight, the
        closing itself must complete.
        """
        self._nursery = None
        for mod in self.mods.values():
            mod.stop_check_loop()
        client = self._client
        if client is None:
            return
        try:
            with trio.CancelScope(shield=True):
                await client.aclose()
        except trio.Cancelled:
            raise
        except Exception as e:
            logger.warning(f'[Update] Failed to close the http client: {e}')

    def set_initial_states(self):
        """
        Publish the initial state of every mod (the startup flow, before the
        first check): 'no-supervisor' for a mod of a backend without a live
        supervisor, 'unmanaged' for a mod without a usable update source (no
        mirrors, or a malformed declaration: the mod is never checked),
        'disabled' for a mod whose updates are off (``auto``), 'idle'
        otherwise. The classification is final for the update source and the
        switch (parsed and read once, see ModUpdateCheck.mirrors and
        ModUpdateCheck.disabled); the supervisor gate is read live
        (ModUpdateCheck.no_supervisor).
        """
        for mod in self.mods.values():
            if mod.no_supervisor:
                mod.set_state('no-supervisor')
            elif mod.mirrors is None:
                mod.set_state('unmanaged')
            elif mod.disabled:
                mod.set_state('disabled')
            else:
                mod.set_state('idle')

    # ------------------------------------------------------------------
    # The flows' API: start / pause / restore / check / cancel
    # ------------------------------------------------------------------

    def names(self, managed=True) -> "List[str]":
        """
        Args:
            managed (bool): Only the mods with a usable update source (a
                disabled mod still has one: the update of a mod and the switch
                of the process are two different things)

        Returns:
            List[str]: The mod names (the order of the mount), sorted
        """
        if not managed:
            return sorted(self.mods)
        return sorted(name for name, mod in self.mods.items() if mod.mirrors is not None)

    def start(self, names=None):
        """
        Start the check sequences of the mods (the startup flow): every
        sequence waits the standard spread before its first check -- the
        startup full update just checked everything.

        Args:
            names (Iterable[str]): Mod names, defaults to every managed mod
        """
        for name in self._resolve(names):
            self.mods[name].start_check_loop()

    def pause(self, names=None):
        """
        Pause the check sequences of the mods (a maintenance flow owns them):
        the sequences in flight are dropped, the states stay.

        Args:
            names (Iterable[str]): Mod names, defaults to every managed mod
        """
        for name in self._resolve(names):
            self.mods[name].stop_check_loop()

    def restore(self, names=None):
        """
        Restore the check sequences a flow paused (its wrap-up): the sequences
        that are not running start again with the standard spread.

        A mod whose sequence runs is left alone: the flows are mutually
        exclusive, so the flow wrapping up is the one that paused it (if it did
        not, the sequence must not be restarted -- the spread would be drawn
        again for nothing). A sequence that ended by itself (a check raised) is
        restored the same way, it is not running either.

        Args:
            names (Iterable[str]): Mod names, defaults to every managed mod
        """
        for name in self._resolve(names):
            mod = self.mods[name]
            if mod.running:
                continue
            mod.start_check_loop()

    async def update_check(self, name: str) -> str:
        """
        Check the updates of one mod now (the manual check of the rpc layer)

        Args:
            name (str): Mod name

        Returns:
            str: The message of the check: '' when it ran, the reason it was
                discarded otherwise (see ModUpdateCheck.update_check)

        Raises:
            ValueError: The mod is unknown (the entry of the module): an
                unmanaged mod answers UNMANAGED_MESSAGE and a disabled one
                DISABLED_MESSAGE, like their own entries
        """
        mod = self.mods.get(name, None)
        if mod is None:
            raise ValueError(f'No such mod: "{name}"')
        return await mod.update_check()

    def cancel_check(self, name: str) -> bool:
        """
        Interrupt the check request in flight of one mod.

        Args:
            name (str): Mod name

        Returns:
            bool: True when a request was in flight
        """
        mod = self.mods.get(name, None)
        return mod is not None and mod.cancel_check()

    # ------------------------------------------------------------------
    # States (the rpc precondition and the apply steps)
    # ------------------------------------------------------------------

    def state_of(self, name: str) -> str:
        """
        Args:
            name (str): Mod name

        Returns:
            str: The state of the mod, '' for an unknown mod
        """
        mod = self.mods.get(name, None)
        return mod.info.state if mod is not None else ''

    def available(self) -> "List[str]":
        """
        Returns:
            List[str]: The mods whose update is available (the update flow
                applies them), sorted
        """
        return sorted(name for name, mod in self.mods.items() if mod.info.state == 'available')

    def set_state(self, name: str, state: str, error=''):
        """
        Set the state of one mod (the apply steps drive it).

        Args:
            name (str): Mod name
            state (str): One of UPDATE_STATE
            error (str): Failure message, '' when there is none
        """
        mod = self.mods.get(name, None)
        if mod is None:
            return
        mod.set_state(state, error=error)

    def mark_restart_required(self, names, error=''):
        """
        Mark the mods whose files were already written by a flow whose handover
        failed: the disk holds the new version, the process still runs the old
        one, a restart applies it.

        Args:
            names (Iterable[str]): Mod names
            error (str): Failure message shown on the cards
        """
        for name in names:
            mod = self.mods.get(name, None)
            if mod is None:
                continue
            logger.warning(f'[Update] The update of "{name}" needs a restart to be applied')
            mod.set_state('restart-required', error=error)

    def release_states(self):
        """
        Release the transient states of the flow in flight (the wrap-up of a
        flow that does not hand over)

        The pending states the apply steps set (``FLOW_STATES``: 'queued',
        'stopping', 'downloading') describe the flow that set them: once it
        wrapped up they are stale -- the update of the last check is still
        pending (nothing on disk changed) and the wrapped-up flow restored the
        check sequences, so the mod returns to 'available' (the versions of the
        last check are kept).

        The states of the files ('updating', 'restart-required') and the
        failures ('error') do not describe the flow in flight and stay (the next
        process start converges the tree); 'checking' is the state of a check
        request, not of the flow: the check module puts the state of the last
        check back itself when the request is interrupted (see
        ``ModUpdateCheck._rollback_check``). A mod the flow never touched is
        left alone.
        """
        for name, mod in self.mods.items():
            if mod.info.state in FLOW_STATES:
                self.set_state(name, 'available')

    def _resolve(self, names):
        """
        Resolve the mod names of a flow call against the managed mods

        Args:
            names (Iterable[str] | None): Given names, None means every mod

        Returns:
            Iterator[str]: The names actually managed
        """
        if names is None:
            return list(self.names())
        return [name for name in names if name in self.mods]


# The process-wide check module, attached by the lifespan
UPDATE_CHECK = UpdateCheck()
