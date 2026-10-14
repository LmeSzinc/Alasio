"""
Update check module of the mods

The module owns the update *checks* of every mounted mod and the states the
frontend reads on the Update topic. One class holds both sides of one mod --
the state and its check sequence (``ModUpdateCheck``): the sequence calls back
into the state it serves instead of a second object that owns the mod, there is
no loop object to keep in sync. The module (``UpdateCheck``) holds one per
mounted mod, the shared http client and the nursery the sequences run in.

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
  module shares the lifespan nursery, a raised error would take the backend
  down) -- the flow that paused the mod restores the sequence later.

The states of one mod (Update topic):

    unmanaged / idle / checking / uptodate / available / error

plus the states the apply steps set: 'queued', 'stopping', 'downloading',
'updating' and 'restart-required'.
"""
import random
import time
from typing import Dict, List, Optional

import trio

from alasio.backend.topic.update import UpdateInfo, UpdateSource
from alasio.deploy.httpclient.async_client import AsyncHttpClient
from alasio.deploy.httpclient.probe import Mirrors
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
        server = ServerFile(self.mirrors, scope=self.name, client=self.check_module.client())
        await server.warmup()
        return DeployJob(root=self.mod.root, server=server)

    # ------------------------------------------------------------------
    # The automatic sequence
    # ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        """
        Returns:
            bool: Whether the automatic sequence of this mod is running (a
                sequence parked because automatic checks are off counts as
                running: it is dropped and restored like any other one)
        """
        return self._loop_scope is not None

    def start_check_loop(self):
        """
        Start the automatic sequence of this mod (the module's entry: the
        startup flow, a restore, the end of the manual check)

        The sequence in flight, if any, is stopped first: one sequence per mod.
        The new task waits the standard spread before its first check -- the
        startup flow just ran a full update, a check right now would repeat it.

        An unmanaged mod has no sequence: nothing is started (the state stays
        'unmanaged', it is published once by the startup flow).
        """
        if self.mirrors is None:
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
        if self.mirrors is None:
            # defensive: the sequence is never started for an unmanaged mod
            # (see start_check_loop), a sequence that finds itself without a
            # source ends here instead of checking
            return
        try:
            with scope:
                delay = self.check_module.first_delay()
                while True:
                    if delay is None:
                        # automatic checks off: the sequence parks until it is
                        # dropped (only the manual check checks)
                        await trio.sleep_forever()
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
                UNMANAGED_MESSAGE)
        """
        if self.mirrors is None:
            # the entry of the manual check: a mod without a usable update
            # source is never checked (its state is 'unmanaged', published once
            # by the startup flow); nothing is an error here
            return UNMANAGED_MESSAGE
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
        recorded as the state of the mod, an interrupted request records
        nothing (the state of the last check stays).

        Returns:
            str: '' when the check ran, FRESH_MESSAGE when it was discarded
                (the second check of the fresh window, inside the lock),
                UNMANAGED_MESSAGE for an unmanaged mod (defensive: the entry
                refuses it and its sequence is never started)
        """
        if self.mirrors is None:
            # defensive: the last line of defence of the check path
            return UNMANAGED_MESSAGE
        if time.monotonic() - self._checked_mono < FRESH_WINDOW:
            # two callers raced to the lock: the first one checked a moment ago
            return FRESH_MESSAGE
        previous = self.info
        self.set_state('checking')
        try:
            check = await self._request_check()
        except Exception as e:
            logger.warning(f'[Update] Failed to check "{self.name}": {e}')
            self.set_state('error', error=str(e))
            self._checked_mono = time.monotonic()
            return ''
        if check is None:
            # the manual cancel: the request was interrupted, nothing is
            # recorded (the previous state and its versions stay)
            logger.info(f'[Update] The check of "{self.name}" was cancelled')
            self.info = previous
            self.push(previous)
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

    Attributes (read-only for the outside):
        mods (dict[str, ModUpdateCheck]): The managed mods, keyed by mod name,
            built by bind()
    """

    def __init__(self):
        self.mods: "Dict[str, ModUpdateCheck]" = {}
        self._client = None
        self._nursery: "Optional[trio.Nursery]" = None
        # automatic checks on / off (Deploy.Update.AutoUpdate: with the checks
        # off the sequences park and only serve the manual checks)
        self.auto = True

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------

    def attach(self, nursery):
        """
        Bind the lifespan nursery the check sequences run in

        Args:
            nursery (trio.Nursery): The lifespan nursery
        """
        self._nursery = nursery

    @property
    def nursery(self):
        """
        Returns:
            trio.Nursery | None: The attached nursery, None when the module is
                not attached (a sequence is never started then)
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

        Sets `auto`.
        """
        from alasio.deploy.config.model import DeployConfig

        try:
            update = DeployConfig().config.data.Update
            auto = bool(update.AutoUpdate)
        except Exception as e:
            logger.warning(f'[Update] Failed to read the update schedule: {e}, using the defaults')
            auto = True
        self.auto = auto

    def first_delay(self) -> "Optional[float]":
        """
        Seconds to the first automatic check of a fresh sequence

        Always ``random(CHECK_INTERVAL, CHECK_INTERVAL * 2)`` when automatic
        checks are on. The window is a fixed mathematical design, not a tunable:

        - the spread is exactly CHECK_INTERVAL wide, and the checks that follow
          are CHECK_INTERVAL apart, so the sequence stays evenly spread --
          backends started at the same moment (a fleet restarted together, the
          daily maintenance) never hit the mirrors together, at any time;
        - the lower bound CHECK_INTERVAL covers the startup full update: the
          process just checked every mod, a check right after would repeat it.

        Returns:
            float | None: The delay, None when no automatic check follows
                (AutoUpdate = false: the sequence parks and only serves the
                manual checks)
        """
        if not self.auto:
            return None
        return random.uniform(CHECK_INTERVAL, CHECK_INTERVAL * 2)

    def interval(self) -> "Optional[float]":
        """
        Seconds between two checks of a running sequence

        Returns:
            float | None: CHECK_INTERVAL, None when no automatic check follows
                (AutoUpdate = false: the sequence parks after its check)
        """
        if not self.auto:
            return None
        return CHECK_INTERVAL

    def client(self):
        """
        The shared http client of the update servers, created lazily (a client
        is bound to the event loop it is used in).

        Returns:
            httpx2.AsyncClient: The client
        """
        if self._client is None:
            # the client of the deploy domain, see AsyncHttpClient.create_client()
            self._client = AsyncHttpClient.create_client()
        return self._client

    async def close(self):
        """
        Stop every sequence and close the http client (the lifespan teardown,
        best effort: the process may be going down).
        """
        self._nursery = None
        for mod in self.mods.values():
            mod.stop_check_loop()
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

    def set_initial_states(self):
        """
        Publish the initial state of every mod (the startup flow, before the
        first check): 'unmanaged' for a mod without a usable update source (no
        mirrors, or a malformed declaration: the mod is never checked), 'idle'
        otherwise. The classification is final: the source of a mod is parsed
        once, see ModUpdateCheck.mirrors.
        """
        for mod in self.mods.values():
            mod.set_state('idle' if mod.mirrors is not None else 'unmanaged')

    # ------------------------------------------------------------------
    # The flows' API: start / pause / restore / check / cancel
    # ------------------------------------------------------------------

    def names(self, managed=True) -> "List[str]":
        """
        Args:
            managed (bool): Only the mods with a usable update source

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
                unmanaged mod answers UNMANAGED_MESSAGE like its own entry
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
