import threading
import time
from typing import List, TypedDict

import trio
from msgspec import NODEFAULT, ValidationError, convert

from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.reactive.source import ConfigSource, DiskCache, DiskCachePush, KeyedSource, ResidentCache
from alasio.backend.topic.scan import ConfigScanSource
from alasio.backend.topic.state import ConnState
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.entry.loader import MOD_LOADER
from alasio.config.entry.model import TaskItem
from alasio.ext.deep import deep_iter_depth1
from alasio.ext.singleton import Singleton
from alasio.logger import logger


class TaskQueueData(TypedDict):
    pending: List[TaskItem]
    waiting: List[TaskItem]


def next_waiting_time(waiting):
    """
    Earliest NextRun of a waiting task list, as a wall-clock epoch.

    Args:
        waiting (list | None): Waiting tasks of the table (TaskItem, the
            shape data always holds). waiting[0] is NOT trusted: only
            mod.get_task_schedule sorts the list, the topic does not
            enforce it.

    Returns:
        float | None: Epoch seconds of the earliest NextRun; None when the
            list is empty or no item carries a usable NextRun
    """
    earliest = None
    for item in waiting or ():
        try:
            deadline = item.NextRun.timestamp()
        except (AttributeError, OverflowError, OSError, ValueError):
            # not a TaskItem / out of range datetime: unusable as a deadline
            continue
        if earliest is None or deadline < earliest:
            earliest = deadline
    return earliest


class TaskQueueUpdateManager(metaclass=Singleton):
    """
    Time-driven refresh scheduler of the TaskQueue topic.

    The task table is a function of the wall clock (mod.get_task_schedule
    splits by now: NextRun <= now is pending, NextRun > now is waiting)
    but no event carries the clock: without this manager a cached table
    stays frozen when no worker event arrives, and a waiting task keeps
    being displayed as waiting long after its NextRun passed (see
    doc/2026-09-23_taskqueue-time-driven-refresh.md).

    Protocol:
    - a config registers the wall-clock instant of its next needed
      recompute (the earliest waiting NextRun, floored to MIN_LEAD from
      now) whenever its table changes or a watcher attaches;
    - the last watcher leaving unregisters the config: nothing displays
      the table, nothing must be recomputed (the loop returns to a true
      idle -- it blocks on the wake event -- when the registry is empty);
    - the loop waits for the earliest registered instant and refreshes the
      due configs through the existing forced-read path
      (TaskQueueSource.reinit(force=True): the same computation a config
      save triggers, no separate split logic);
    - a config save asks for an immediate refresh with request_refresh()
      instead of spawning a coroutine of its own, so the refresh is done
      by whoever comes first: the loop, or the worker push carrying the
      very table that save asked for (register(fresh=True) satisfies the
      request -- the worker just recomputed the whole table, the backend
      read is redundant).

    Clock jumps: the wall clock is read every loop iteration and the loop
    never sleeps longer than MAX_WAIT, so a jump is noticed within
    MAX_WAIT. Forward (now >= deadline): the deadlines become due on their
    own; backward (now < the previous iteration): every tracked config is
    refreshed at once, a pending task may have become waiting again.

    Thread model: register / request_refresh / unregister may be called
    from any thread (worker event thread, Trio thread), run() is the
    single Trio loop started by the lifespan nursery. Lock order: source
    lock -> manager lock (never the reverse; the loop never holds the
    manager lock while awaiting a refresh).
    """

    # Minimum lead of a registered deadline: waiting tasks a few seconds
    # apart accumulate into one recompute instead of one per task.
    MIN_LEAD = 5
    # Longest single wait of the loop: the wall-clock jump check runs at
    # least this often while a config is tracked.
    MAX_WAIT = 5

    def __init__(self):
        # Guards _watched + _requested + _wake + _trio_token (any thread).
        self._lock = threading.Lock()
        # config_name -> epoch seconds of the next needed recompute, or
        # None when nothing waits (still tracked: a backward wall-clock
        # jump must be able to refresh it).
        self._watched: "dict[str, float | None]" = {}
        # Configs with a pending immediate refresh request (the config-save
        # linkage moved into this manager): executed by the loop, or
        # satisfied (dropped) by a full worker push -- see register(fresh).
        # Kept apart from _watched on purpose: subscribe / unsubscribe /
        # reinit re-derive deadlines but never satisfy a request (a new
        # subscriber attaching mid-flight must not eat it).
        self._requested: "set[str]" = set()
        # The sleeper of the current wait (Trio thread only): set while the
        # loop waits, None while it is working.
        self._wake = None
        # Captured by run(); None before the lifespan starts the loop and
        # after it ends (no wake is possible then, the loop re-plans anyway).
        self._trio_token = None
        # Wall clock of the previous loop iteration (backward jump check).
        self._last_wake = None

    def register(self, config_name, deadline, fresh=False):
        """
        [任意线程] Publish the next time-driven refresh of a config.

        Args:
            config_name (str):
            deadline (float | None): Epoch seconds of the earliest waiting
                NextRun, None when nothing waits
            fresh (bool): The caller just got the WHOLE table from its
                owner (a full worker push): a pending refresh request of
                this config is satisfied, the read it asked for is not
                needed any more. subscribe / unsubscribe / reinit never
                pass it -- they do not prove the table is fresh.
        """
        if deadline is not None:
            # floor: a burst of adjacent NextRuns becomes one recompute
            deadline = max(deadline, time.time() + self.MIN_LEAD)
        with self._lock:
            self._watched[config_name] = deadline
            if fresh:
                self._requested.discard(config_name)
        self._wake_loop()

    def request_refresh(self, config_name):
        """
        [任意线程] Ask for an immediate refresh (config-save linkage).

        Deliberately NOT floored by MIN_LEAD: it is a user / worker action,
        not a time-derived deadline. The request survives until the loop
        executes it (_refresh) or a full worker push satisfies it
        (register(fresh=True)) -- it is never dropped for being "too
        early", so no save can be swallowed.
        """
        with self._lock:
            self._requested.add(config_name)
        self._wake_loop()

    def unregister(self, config_name):
        """
        [任意线程] Stop tracking a config (its last watcher left).
        """
        with self._lock:
            self._watched.pop(config_name, None)
            self._requested.discard(config_name)

    def _wake_loop(self):
        """
        [任意线程，锁外] Ring the sleeper: an early-wake hint so the loop
        re-plans at once instead of at the end of its current wait. A
        spurious wake costs one loop iteration; correctness never depends
        on it (the timed wait bounds the error by MAX_WAIT).
        """
        with self._lock:
            wake = self._wake
            token = self._trio_token
        if wake is None or token is None:
            return
        try:
            token.run_sync_soon(wake.set)
        except trio.RunFinishedError:
            pass

    async def run(self):
        """
        [Trio] The manager loop, started by the lifespan nursery (app.py).
        """
        # own token: register / request_refresh may be called from any thread
        self._trio_token = trio.lowlevel.current_trio_token()
        try:
            while True:
                now = time.time()
                # Wall clock moved backwards (the system time was adjusted):
                # every tracked table was split against a clock that no
                # longer exists -- a pending task may be waiting again --
                # so refresh them all at once.
                jumped = self._last_wake is not None and now < self._last_wake
                self._last_wake = now
                due = self._due(now, all_registered=jumped)
                for config_name in due:
                    await self._refresh(config_name)
                if due:
                    # Re-plan with a fresh clock: the refreshes republished
                    # the deadlines (and a forward jump was consumed by the
                    # due check above).
                    continue
                await self._wait(now)
        finally:
            self._trio_token = None
            self._wake = None

    def _due(self, now, all_registered=False):
        """
        Snapshot the configs whose refresh is due: an immediate request, a
        deadline that passed, or -- after a backward clock jump --
        everything tracked.

        Args:
            now (float): Wall clock of the current iteration
            all_registered (bool): True after a backward clock jump

        Returns:
            list[str]:
        """
        with self._lock:
            requested = set(self._requested)
            if all_registered:
                return list(self._watched.keys() | requested)
            passed = {
                name for name, deadline in self._watched.items()
                if deadline is not None and deadline <= now
            }
            return list(requested | passed)

    async def _refresh(self, config_name):
        """
        [Trio] Recompute one config's task table and broadcast the change.

        The read is exactly the forced read a config save triggers; the
        same computation, so no split logic is duplicated. A config whose
        last watcher left (or whose read fails) is skipped / logged -- the
        loop must survive anything but cancellation. A pending request is
        consumed by the ATTEMPT (same semantics as the config-save linkage
        this replaces: a failed read is retried by the next trigger, never
        in a hot loop).
        """
        with self._lock:
            if config_name not in self._watched and config_name not in self._requested:
                return
            self._requested.discard(config_name)
        try:
            await TaskQueueSource(config_name).reinit(force=True)
        except Exception:
            logger.exception(f'[TaskQueue] Scheduled refresh failed: {config_name}')
        # A due deadline the refresh did not move forward (failed read) must
        # not be retried at full speed: push it a MIN_LEAD ahead. This also
        # bounds the retry rate of a permanently broken config, and it keeps
        # the loop from spinning when a refresh does not re-register (a
        # successful read re-registers through _sync_update_time, so it
        # never reaches here).
        now = time.time()
        with self._lock:
            deadline = self._watched.get(config_name, None)
            if deadline is not None and deadline <= now:
                self._watched[config_name] = now + self.MIN_LEAD

    async def _wait(self, now):
        """
        [Trio] Wait for the earliest deadline, a new registration, a
        refresh request, or the jump-check tick.

        The registry snapshot and the sleeper are published in ONE
        critical section: a registration either lands before it (the loop
        sees the new deadline) or after it (the registration rings the
        sleeper) -- no lost wakeup.

        Args:
            now (float): Wall clock of the current iteration
        """
        wake = trio.Event()
        with self._lock:
            self._wake = wake
            earliest = min(
                (deadline for deadline in self._watched.values() if deadline is not None),
                default=None,
            )
            # a pending request is due at once: never fall into the "true
            # idle" branch while one exists
            watched = bool(self._watched) or bool(self._requested)
        try:
            if not watched:
                # nothing tracked: a true idle, a registration wakes us
                await wake.wait()
                return
            if earliest is None:
                timeout = self.MAX_WAIT
            else:
                timeout = min(self.MAX_WAIT, max(0.0, earliest - now))
            with trio.move_on_after(timeout):
                await wake.wait()
        finally:
            with self._lock:
                if self._wake is wake:
                    self._wake = None


TASK_QUEUE_UPDATE_MANAGER = TaskQueueUpdateManager()


class TaskQueueSource(ConfigSource, DiskCachePush):
    """
    Task queue cache source: disk cache + event push (config-keyed).

    Data = {pending, waiting} loaded from the disk schedule table
    (reinit) and kept up to date by worker TaskQueue events. Event
    protocol: ConfigEvent(t='TaskQueue') with v a dict of any subset
    of {pending, waiting} -- merged key by key. Increments are keyed set
    patches of the changed keys (the client merges them into the task
    table); full events carry the whole data at the root.

    Data shape: data is ALWAYS a TaskQueueData (TaskItem items). The read
    path builds them by construction, and a push payload is decoded into
    TaskItem before it is merged (msgspec decodes the worker bytes with
    v: Any, so the raw items are plain dicts) -- a payload that does not
    decode is dropped with a warning, never stored raw. Consumers
    (next_waiting_time, the client patches) therefore never deal with the
    dict shape, and an identical push compares equal to the read table.

    Lifecycle: DiskCachePush -- worker events / config-event refreshes
    refresh the data TTL; the data-expiry GC recycles the instance only
    when the TTL expired AND no subscriber is attached (the push channel
    is bound to the instance).

    Time-driven refresh: the table is a function of the wall clock (the
    split of mod.get_task_schedule moves a waiting task into pending at
    its NextRun), so the source republishes the next needed recompute to
    TASK_QUEUE_UPDATE_MANAGER on every data change and on every subscriber
    attach / detach (the subscribe / unsubscribe / on_event / reinit
    overrides below; see doc/2026-09-23_taskqueue-time-driven-refresh.md).
    The manager tracks watched configs only, and a full worker push
    satisfies a pending config-save refresh request instead of letting the
    backend read the disk for a table the worker just computed.
    """
    TOPIC_NAME = 'TaskQueue'
    TTL = 5
    data: TaskQueueData

    def __init__(self, config_name):
        super().__init__(config_name)

    def on_init(self):
        """
        [线程] Build the task table from the ConfigScan cache and the mod
        schedule. Runs in a worker thread (on_init_async). Never touches
        self.data (cross-thread).

        Returns:
            dict: {'pending': list, 'waiting': list} -- empty lists when
                the config / mod is gone.
        """
        # access cache directly, no rescan
        configs = ConfigScanSource().data
        try:
            info = configs[self.config_name]
        except KeyError:
            return {'pending': [], 'waiting': []}
        try:
            mod = MOD_LOADER.dict_mod[info.mod]
        except KeyError:
            return {'pending': [], 'waiting': []}

        pending_task, waiting_task = mod.get_task_schedule(self.config_name)
        return {'pending': pending_task, 'waiting': waiting_task}

    def _apply(self, event):
        """
        [锁内] Simple merge: each present key of {pending, waiting}
        replaces the corresponding key of data.

        The push payload is decoded into TaskItem items first: data is a
        TaskQueueData (see the class docstring), and a decoded list also
        compares equal to the read one, so an identical push does not
        broadcast. A key that does not decode is dropped with a warning --
        a foreign shape is a protocol bug, it must never enter data.

        Returns:
            bool: If data changed
        """
        value = event.v
        modified = False
        for key in ['pending', 'waiting']:
            if key not in value:
                continue
            after = self._decode(value[key])
            if after is None:
                continue
            before = self.data.get(key, NODEFAULT)
            # stop broadcast if not modified
            if before != after:
                self.data[key] = after
                modified = True
        return modified

    @staticmethod
    def _decode(value):
        """
        [锁内] TaskItem items of a push payload value.

        Args:
            value (list): pending / waiting value of a worker push (msgspec
                decodes the ConfigEvent payload with v: Any, so items are
                plain dicts here)

        Returns:
            list[TaskItem] | None: None when the value is not a task list
                (the caller drops it: data only ever holds TaskQueueData)
        """
        try:
            return convert(value, List[TaskItem])
        except (ValidationError, TypeError) as e:
            logger.warning(f'[TaskQueue] Malformed task table payload ignored: {e}')
            return None

    def _convert(self, event):
        """
        [锁内] Keyed set patches of the event's changed keys, referencing
        the applied values.

        The worker event v is a dict of any subset of {pending, waiting};
        each key is forwarded as a separate keyed set so the client merges
        the patch instead of replacing the whole task table. Values are the
        applied ones (data[key], set by _apply in this same critical
        section): they are bound into data by replacement and never mutated
        in place afterwards, so encoding later (outside the lock) is safe.
        Unknown payload keys are not forwarded -- _apply ignores them, the
        client table only has the two keys.

        Returns:
            ResponseEvent | list[ResponseEvent]:
        """
        value = event.v
        return [
            ResponseEvent(t=self.TOPIC_NAME, o='set', k=(key,), v=self.data[key])
            for key in ['pending', 'waiting'] if key in value
        ]

    # ---------------- config-event linkage ----------------

    @staticmethod
    def _need_reinit(responses):
        """
        Does the config-save payload touch Scheduler.Enable / NextRun?
        Handles ConfigSetEvent and dict payloads, single or list.

        Args:
            responses (ConfigSetEvent | list[ConfigSetEvent] | dict |
                list[dict]):

        Returns:
            bool:
        """
        if not isinstance(responses, list):
            responses = [responses]
        for resp in responses:
            if resp is None:
                continue
            # worker payloads are dicts (decoded from bytes)
            if type(resp) is dict:
                try:
                    group = resp['group']
                    arg = resp['arg']
                except KeyError:
                    continue
            else:
                group = resp.group
                arg = resp.arg
            if group == 'Scheduler' and (arg == 'Enable' or arg == 'NextRun'):
                return True
        return False

    def on_config_event(self, event):
        """
        [任意线程] Config-save linkage: recompute the task table when the
        scheduler settings (Scheduler.Enable / NextRun) changed.

        - subscribers present: ask the manager for an immediate refresh.
          The loop executes it unless a full worker push satisfies it
          first (the worker recomputed the table for the very same save --
          the common task_delay case), or the table is no longer watched
          by then;
        - no subscriber: mark the data dirty. Nothing is displayed, so
          nothing must be recomputed now: the next subscribe re-reads
          (fetch_init bypasses TTL while dirty), and the manager is asked
          to keep nothing warm. The data is kept as a fallback (never
          cleared: a failed read still has the old table).
        """
        if not self._need_reinit(event):
            return
        # snapshot under the lock, branch outside: mark_dirty takes the
        # lock itself (threading.Lock is not reentrant)
        with self._lock:
            has_subscribers = bool(self._subscribers)
        if has_subscribers:
            TASK_QUEUE_UPDATE_MANAGER.request_refresh(self.config_name)
        else:
            self.mark_dirty()

    # ---------------- time-driven refresh ----------------

    async def subscribe(self, sub):
        """
        [Trio] Register the subscriber and publish the next time-driven
        refresh (the manager only tracks watched configs). Never satisfies
        a pending refresh request: attaching a viewer proves nothing about
        the table.
        """
        snapshot = await super().subscribe(sub)
        self._sync_update_time()
        return snapshot

    def unsubscribe(self, sub):
        """
        [Trio] Drop the subscriber; the last one clears the config from
        the manager (nothing displays the table any more).
        """
        super().unsubscribe(sub)
        self._sync_update_time()

    def on_event(self, event):
        """
        [任意线程] Worker TaskQueue event: the table changed, so can the
        next refresh time (a task delayed / called / added).

        A push carrying the WHOLE table (both keys) is also proof that its
        owner just recomputed it: such a push satisfies a pending refresh
        request (the worker computed the table for the very save that
        asked for it), so no backend read follows.
        """
        super().on_event(event)
        value = event.v or {}
        if 'waiting' not in value:
            # the next refresh time derives from the waiting list only: a
            # push without it cannot move the deadline (and must not
            # satisfy a request -- the table is only partially fresh)
            return
        self._sync_update_time(fresh='pending' in value)

    async def reinit(self, force=False):
        """
        [Trio] Full read: the table (and with it the next refresh time)
        changed.
        """
        await super().reinit(force=force)
        self._sync_update_time()

    def _sync_update_time(self, fresh=False):
        """
        [任意线程] Republish this config's next time-driven refresh.

        Called after every data change and every subscriber attach /
        detach: the deadline is derived state of the task table, the
        manager is its only holder. Without a watcher nothing is
        registered -- nothing displays the table, and the forced disk read
        is the expensive part.

        Args:
            fresh (bool): The caller just got the WHOLE table from its
                owner (a full worker push): it also satisfies a pending
                refresh request (manager.register(fresh=True))
        """
        manager = TASK_QUEUE_UPDATE_MANAGER
        with self._lock:
            if not self._subscribers:
                watched = False
                deadline = None
            else:
                watched = True
                deadline = next_waiting_time(self.data.get('waiting', None))
        if not watched:
            manager.unregister(self.config_name)
        else:
            manager.register(self.config_name, deadline, fresh=fresh)


class TaskQueue(BaseTopic):
    TOPIC_NAME = 'TaskQueue'

    async def get_source(self):
        """
        Data preparation: reinit (TTL / dirty no-op when fresh; the first
        subscription of a config without worker events fills the task
        table through on_init).
        """
        state = ConnState(self.conn_id, self.server)
        config_name = await state.config_name
        if not config_name:
            return None
        source = TaskQueueSource(config_name)
        await source.reinit()
        return source


class TaskRunningSource(ConfigSource, ResidentCache):
    """
    Current running task of a config, resident (event stream only).

    Event protocol: ConfigEvent(t='TaskRunning') with
    v = task_name | None -- sent by the worker scheduler on every task
    switch. data is the task name itself (None = no task running). There
    is no full-data source: every change flows through events, so reinit
    is never called (the get_source of the topic does not call it).
    Runtime-produced state is kept resident: a later front-end visit
    finds the current task without events having to be replayed.
    """
    TOPIC_NAME = 'TaskRunning'

    def __init__(self, config_name):
        super().__init__(config_name)
        # no running task until the first scheduler event
        self.data = None

    def _apply(self, event):
        """
        [锁内] Replace the running task.

        Args:
            event: ConfigEvent with v = task name | None

        Returns:
            bool: If data changed
        """
        value = event.v
        if self.data == value:
            return False
        self.data = value
        return True

    def _convert(self, event):
        """
        [锁内] Event -> root set of the running task.
        """
        return ResponseEvent(t=self.TOPIC_NAME, o='set', v=self.data)


class TaskRunning(BaseTopic):
    TOPIC_NAME = 'TaskRunning'

    async def get_source(self):
        """
        Pure event-stream source: no reinit (there is no full-data read;
        the full is the in-memory data snapshot of subscribe).
        """
        state = ConnState(self.conn_id, self.server)
        config_name = await state.config_name
        if not config_name:
            return None
        return TaskRunningSource(config_name)


class TaskQueueI18nSource(KeyedSource, DiskCache):
    """
    One-shot disk-cache source of TaskQueueI18n: key (mod_name, lang).

    Static i18n content read from disk; no push after the full snapshot.
    Recycled by the data-expiry GC when the data TTL expired, regardless
    of subscribers (DiskCache semantics).
    """
    TOPIC_NAME = 'TaskQueueI18n'

    def __init__(self, mod_name, lang):
        super().__init__()
        self.mod_name = mod_name
        self.lang = lang

    def on_init(self):
        """
        Returns:
            dict[str, str]:
                key: {task_name}
                value: i18n translation
        """
        data = MOD_LOADER.get_queue_i18n(self.mod_name)
        # {task_name}.{lang}=i18n -> {task_name}=i18n
        i18n_dict = {}
        for task, i18n in deep_iter_depth1(data):
            value = i18n.get(self.lang, task)
            i18n_dict[task] = value
        return i18n_dict


class TaskQueueI18n(BaseTopic):
    TOPIC_NAME = 'TaskQueueI18n'

    async def get_source(self):
        state = ConnState(self.conn_id, self.server)
        mod_name = await state.mod_name
        lang = await state.lang
        if not mod_name or not lang:
            return None
        source = TaskQueueI18nSource.get(mod_name, lang)
        await source.reinit()
        return source
