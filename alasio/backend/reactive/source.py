import threading
import time
from collections import deque
from typing import TYPE_CHECKING, Any, Optional

import trio
from msgspec.json import Encoder

from alasio.backend.reactive.event import ResponseEvent
from alasio.ext.singleton import Singleton, SingletonNamed
from alasio.logger import logger

if TYPE_CHECKING:
    from trio._core import TrioToken

    from alasio.backend.ws.ws_topic import BaseTopic

ENCODER = Encoder()

# Classes whose instances may be garbage-collected when idle
# (see BaseSource.gc_idle). Collected on class definition via
# __init_subclass__; only classes with a numeric IDLE_TTL are listed.
_GC_CLASSES: "set[type]" = set()


class BaseSource:
    """
    Source protocol: event stream + subscriber set of a topic.

    Thread model:
    - on_event()    can be called from any thread (worker recv thread /
                    Trio thread / any backend thread), thread safe.
    - subscribe()   can only be called in the Trio thread, async
                    (contains no blocking await).
    - unsubscribe() can only be called in the Trio thread, sync.
    - reinit() / fetch_init() are Trio-only, default empty.

    Iron rule: never await inside a lock. All critical sections are
    synchronous in-memory operations.

    Subclasses implement the three methods (or their hooks). Two families:
    - EventSource: cache source with full data (subscribe returns the
      encoded snapshot), events apply into data and broadcast increments;
    - ViewportEventSource: view source without cached data (subscribe
      builds the full view single-flight and returns the encoded payload),
      events are filtered / converted and forwarded.
    """

    # Inbox is the cross-thread entry buffer (payload objects).
    # When it overflows, the oldest payload is dropped.
    INBOX_MAXLEN = 1024
    # Per-subscriber retry queue (encoded payloads) for slow connections.
    # When it overflows, the oldest payload is dropped and an error is logged.
    RETRY_MAXLEN = 128
    # Idle GC: None = resident (never collected); a number = the instance is
    # removed when it has no subscriber for that many seconds.
    IDLE_TTL = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Collect idle-gc candidate classes. Instances are enumerated through
        # the singleton metaclass or the keyed registry of the class.
        if cls.IDLE_TTL is not None:
            _GC_CLASSES.add(cls)
        else:
            _GC_CLASSES.discard(cls)

    def __init__(self):
        # One lock covers data (cache sources), the inbox and the subscriber
        # set. The subscriber set and the retry queues are only touched from
        # the Trio thread; the lock just keeps them atomic with the snapshot.
        self._lock = threading.Lock()
        self._subscribers: "set[BaseTopic]" = set()
        # Cross-thread entry buffer written by on_event (any thread) and
        # reinit (Trio), drained by _sync_to_trio in one batch.
        self._inbox: "deque[ResponseEvent]" = deque(maxlen=self.INBOX_MAXLEN)
        # Per-subscriber backpressure buffer of encoded payloads
        self._retry: "dict[BaseTopic, deque[bytes]]" = {}
        # Set on the first subscribe (Trio thread); _ring relies on it.
        self._trio_token: "Optional[TrioToken]" = None
        # Timestamp of the moment the last subscriber left, for idle GC.
        self._last_unsub = 0.

    # ---------------- subscribe / unsubscribe ----------------

    async def subscribe(self, sub) -> "Optional[bytes]":
        """
        [Trio] Register a subscriber and return the encoded full snapshot.

        - Cache sources: register first, then snapshot the current data
          under the lock and encode the full event.
        - Empty data returns None (no full event, matching the previous
          EventCache behavior).

        Ordering contract: after subscribe() returns, the caller must send
        the returned bytes with send_nowait() without any await in between,
        so the full event is enqueued before any later increment.
        """
        with self._lock:
            if self._trio_token is None:
                self._trio_token = trio.lowlevel.current_trio_token()
            self._subscribers.add(sub)
            self._retry.setdefault(sub, deque())
            data = self._snapshot()
            if not data:
                return None
            # Encode under the lock: the snapshot may reference data, and
            # data cannot be modified while the lock is held.
            return ENCODER.encode(ResponseEvent(t=self.TOPIC, o='full', v=data))

    def unsubscribe(self, sub):
        """
        [Trio] Unsubscribe. Idempotent.

        When the last subscriber leaves, the idle timestamp is recorded
        (for idle GC, see gc_idle()).
        """
        with self._lock:
            self._subscribers.discard(sub)
            # Retried messages die with the subscription: the connection is
            # gone or no longer interested.
            self._retry.pop(sub, None)
            empty = not self._subscribers
        if empty:
            self._last_unsub = time.monotonic()

    # ---------------- sub-class hooks ----------------

    def _snapshot(self):
        """
        [锁内] Snapshot hook of subscribe(). Default: no data.
        Cache sources return the data to be sent as the full event.
        """
        return None

    async def reinit(self, force=False):
        """
        [Trio] Refresh the full data and broadcast a full event.
        Default empty implementation: sources without a full data source
        (Log / Worker / viewport sources) never call it.
        """
        pass

    async def fetch_init(self, force=False):
        """
        [Trio] Sub-class hook of reinit(): read the latest full data.
        Default empty implementation. May carry a freshness check; returns
        the new data, or None when no update is needed.
        """
        pass

    # ---------------- doorbell and batch delivery ----------------

    def _push(self, payload):
        """
        [任意线程，锁内调用] Enqueue one payload and ring the doorbell.
        """
        was_empty = not self._inbox
        self._inbox.append(payload)
        if was_empty:
            self._ring()

    def _ring(self):
        """
        [任意线程，锁内调用] Wake up the batch delivery on the Trio thread.

        The doorbell rings on "inbox transitions from empty to non-empty",
        so pushing several payloads in one critical section wakes Trio once.
        """
        if self._trio_token is None:
            # No subscriber yet: the token is only set by the first
            # subscribe (Trio thread). Sources without subscribers only
            # apply events, never schedule anything.
            return
        try:
            self._trio_token.run_sync_soon(self._sync_to_trio)
        except trio.RunFinishedError:
            pass  # event loop already shut down

    def _sync_to_trio(self):
        """
        [Trio 线程] run_sync_soon callback: drain the inbox in one batch,
        encode once, deliver to every subscriber. Not a coroutine.
        """
        with self._lock:
            if not self._inbox:
                # Several doorbells may be scheduled while the first one is
                # running; empty runs are safe no-ops.
                return
            batch = [self._inbox.popleft() for _ in range(len(self._inbox))]
        # Encode outside the lock: payloads are frozen inside the lock
        # (see the freeze rules of EventSource), so encoding is safe.
        if len(batch) == 1:
            payload = ENCODER.encode(batch[0])
        else:
            # Merge the batch into one array message, encoded once.
            payload = ENCODER.encode(batch)
        self._deliver(payload)

    def _deliver(self, payload):
        """
        [Trio 线程] Deliver one encoded payload to all subscribers.

        - Send through send_nowait (send_buffer, priority channel);
        - on WouldBlock (slow connection) the payload is queued in the
          per-subscriber retry queue and sent on the next event;
        - retry queues are FIFO: retried messages are flushed first, then
          the new payload, so ordering is strictly preserved;
        - the retry queue is bounded: when it is full the oldest payload is
          dropped and an error is logged.
        """
        for sub in self._subscribers:
            retry = self._retry[sub]
            # 1. flush retried payloads first (stop at the first WouldBlock)
            while retry:
                try:
                    sub.server.send_nowait(retry[0])
                except trio.WouldBlock:
                    break
                retry.popleft()
            if retry:
                # still blocked: the new payload goes after the retried ones
                # (the buffer is bounded in every append path)
                self._retry_append(retry, payload)
                continue
            # 2. send the new payload
            try:
                sub.server.send_nowait(payload)
            except trio.WouldBlock:
                self._retry_append(retry, payload)

    def _retry_append(self, retry, payload):
        """
        Queue one payload into a subscriber retry buffer.

        The buffer is bounded (RETRY_MAXLEN): on overflow the oldest payload
        is dropped and an error is logged. Every append path goes through
        here, so a permanently stuck subscriber can never grow memory
        without bound.
        """
        if len(retry) >= self.RETRY_MAXLEN:
            retry.popleft()
            logger.error(f'{self} retry buffer full, drop oldest message')
        retry.append(payload)

    # ---------------- idle GC ----------------

    @classmethod
    def gc_idle(cls):
        """
        [任意线程] Idle garbage collection over all collectable source
        classes. Mounted in sync_task_gc (app.py, one round every 8s).

        An instance is removed when it has no subscriber and has been idle
        for IDLE_TTL seconds. Thread safety: instance enumeration goes
        through a registry / singleton snapshot (never mutate the dict while
        iterating), subscriber count and the idle timestamp are read under
        the instance lock.
        """
        for src_cls in list(_GC_CLASSES):
            try:
                cls._gc_idle_class(src_cls)
            except RuntimeError:
                # registry mutated concurrently by the Trio thread;
                # skip this class and retry on the next round
                continue

    @classmethod
    def _gc_idle_class(cls, src_cls):
        now = time.monotonic()
        for inst in src_cls._iter_idle_instances():
            if type(inst) is not src_cls:
                # intermediate class in the collection (e.g. the viewport
                # base class): instances are collected under their own class
                continue
            with inst._lock:
                if inst._subscribers:
                    continue
                if now - inst._last_unsub < src_cls.IDLE_TTL:
                    continue
            inst._remove()

    @classmethod
    def _iter_idle_instances(cls):
        """
        Snapshot the live instances of this class for idle GC.
        Only implemented by classes with a numeric IDLE_TTL.
        """
        return []

    def _remove(self):
        """
        Unregister this instance from its singleton / registry.
        Called by idle GC.
        """
        pass


class EventSource(BaseSource):
    """
    Cache source: a full data dict kept fresh by either an event stream
    (on_event) or a full-data source (fetch_init / reinit).

    Sub-class hooks:
    - on_init() / on_init_async(): read the full data;
    - _apply(event): apply an event under the lock, return whether data
      changed;
    - _make_response(event): build the incremental response under the lock;
    - _snapshot(): take the full-event value under the lock (default: the
      data itself; sources whose data is mutated in place by event threads
      must shallow-copy, see the freeze rules below).

    Payload freeze rules: the values referenced by incremental responses
    and reinit full events may only be sub-objects with "replace wholesale"
    semantics. Sources with sub-thread writers must shallow-copy
    (e.g. dict(self.data)); sources whose data is only replaced wholesale by
    the Trio thread (ConfigScan / DevAssets / one-shot sources) may
    reference the data directly.
    """

    TOPIC = ''
    # Freshness window of fetch_init: None = read every time.
    TTL = None

    def __init__(self):
        super().__init__()
        self._fetch_lock = trio.Lock()
        self.data: Any = {}
        # Timestamp of the last data refresh (event or full read)
        self._lastrun = 0.
        # True when an event producer is alive: its data is trusted over the
        # full-data source (fetch_init running trust of TaskQueueSource).
        self._running = False
        if not self.TOPIC:
            logger.warning(f'{self.__class__.__name__}.TOPIC is not set')

    # ---------------- sub-class hooks ----------------

    def on_init(self):
        """
        Synchronous full-data source. Default returns {}.
        """
        return {}

    async def on_init_async(self):
        """
        Default implementation: run on_init() in a worker thread.
        One-shot sources and DevAssets must keep their reads in a thread
        (file / import IO must not block the event loop).
        """
        return await trio.to_thread.run_sync(self.on_init)

    def _apply(self, event):
        """
        [锁内] Apply an event into data. Return whether data changed.
        """
        return False

    def _make_response(self, event):
        """
        [锁内] Build the incremental response and freeze its payload.
        """
        raise NotImplementedError

    def _snapshot(self):
        """
        [锁内] Full-event payload. Default: reference data directly.
        Sources with sub-thread writers that mutate data in place must
        shallow-copy (see the freeze rules above).
        """
        return self.data

    # ---------------- data refresh ----------------

    async def fetch_init(self, force=False):
        """
        [Trio] Read the latest full data.

        Args:
            force (bool): Ignore the freshness window.

        Returns:
            Any | None: The new data, or None when the current data is
                still fresh (reinit skips the broadcast).
        """
        if not force and self.TTL is not None:
            with self._lock:
                if time.monotonic() - self._lastrun < self.TTL:
                    return None
        new = await self.on_init_async()
        with self._lock:
            self._lastrun = time.monotonic()
        return new

    async def reinit(self, force=False):
        """
        [Trio] Refresh the full data and broadcast a full event when the
        data changed.

        - fetch_lock (double-checked with the freshness window of
          fetch_init) serializes concurrent refreshes;
        - identical data (old == new) does not broadcast;
        - without subscribers only the data is refreshed (no broadcast).

        Args:
            force (bool): Ignore the freshness window. RPC handlers that
                just modified the disk must pass force=True.
        """
        async with self._fetch_lock:
            new = await self.fetch_init(force)
            if new is None:
                return
            with self._lock:
                old = self.data
                if old == new:
                    return
                self.data = new
                if not self._subscribers:
                    return
                # Freeze the payload before queueing: it is encoded later,
                # outside the lock, by _sync_to_trio.
                payload = ResponseEvent(t=self.TOPIC, o='full', v=self._snapshot())
                self._push(payload)

    # ---------------- event stream ----------------

    def on_event(self, event):
        """
        [任意线程] Event entry: apply under the lock and broadcast.

        - No actual change: nothing is broadcast;
        - no subscriber: the event is only applied (data stays fresh),
          nothing is scheduled.
        """
        with self._lock:
            if not self._apply(event):
                return
            # the event stream is alive and its data is up to date
            self._lastrun = time.monotonic()
            self._running = True
            if not self._subscribers:
                return
            payload = self._make_response(event)
            self._push(payload)


class GlobalEventSource(EventSource, metaclass=Singleton):
    """Global singleton cache source (e.g. ConfigScanSource)."""

    @classmethod
    def _iter_idle_instances(cls):
        inst = cls.singleton_instance()
        return (inst,) if inst is not None else ()

    def _remove(self):
        type(self).singleton_clear()


class ConfigEventSource(EventSource, metaclass=SingletonNamed):
    """Config-keyed named-singleton cache source (e.g. TaskQueueSource)."""

    def __init__(self, config_name):
        self.config_name = config_name
        super().__init__()

    @classmethod
    def _iter_idle_instances(cls):
        return list(cls.singleton_instances().values())

    def _remove(self):
        type(self).singleton_remove(self.config_name)


class KeyedEventSource(EventSource):
    """
    Keyed cache source: registry of composite-key -> instance.

    Each subclass owns its own registry table (created on class definition,
    see __init_subclass__), so subclasses with different key semantics never
    see each other's instances. Idle GC enumerates through the registry.
    """
    # registry of the base class; replaced by a fresh per-subclass table
    _registry: "dict[tuple, KeyedEventSource]" = {}
    _reg_lock = threading.Lock()

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # each subclass gets its own table (key semantics differ per class)
        cls._registry = {}
        cls._reg_lock = threading.Lock()

    @classmethod
    def get(cls, *key):
        """
        [Trio] Get or create the instance of the composite key.

        Returns:
            KeyedEventSource | None: None when the construction raised
                KeyError (e.g. the referenced mod is gone).
        """
        with cls._reg_lock:
            try:
                return cls._registry[key]
            except KeyError:
                pass
            try:
                instance = cls(*key)
            except KeyError:
                return None
            instance._reg_key = key
            cls._registry[key] = instance
            return instance

    @classmethod
    def _iter_idle_instances(cls):
        with cls._reg_lock:
            return list(cls._registry.values())

    def _remove(self):
        with self._reg_lock:
            try:
                if self._registry.get(self._reg_key) is self:
                    del self._registry[self._reg_key]
            except KeyError:
                pass


class ViewportEventSource(BaseSource):
    """
    View source: no data cached in memory, full view built on subscribe.

    - subscribe() builds the full view (subclass hook _build_full, runs in
      a thread) and returns the encoded full payload. Concurrent
      subscribers of the same instance -- same key, i.e. same
      (mod, config, view, lang) -- share ONE build single-flight: while a
      build runs, later subscribers wait and reuse its payload (same key
      -> same view; the stale window equals the private-build window).
      The payload expires when the last waiter took it, so later
      subscribers always rebuild (freshness is never cached);
    - on_event() forwards events filtered / converted by the subclass
      (_convert), unrelated ones are dropped;
    - the mapping between events and the view is subclass business: the
      base class only provides the registry, the subscribe / unsubscribe /
      on_event machinery and idle GC.

    Lifecycle: instances are created by get() and removed by idle GC after
    IDLE_TTL seconds without subscribers (fast navigation round trips
    reuse the instance).
    """

    TOPIC = ''
    IDLE_TTL = 8
    # Shared registry of the base class: config_name -> {key: instance}.
    # One table for every subclass (ConfigArgSource / DashboardSource), so
    # a single dispatch(config_name, event) call covers all viewport
    # sources of a config. Keys start with the source class to keep
    # sibling subclasses apart (their key shapes differ).
    _by_config: "dict[str, dict[tuple, ViewportEventSource]]" = {}
    _reg_lock = threading.Lock()
    # Sentinel: no reusable build payload
    _NO_PAYLOAD = object()

    def __init__(self, config_name):
        super().__init__()
        self.config_name = config_name
        # Single-flight full build (Trio thread only): plain flags + one
        # event, no lock needed.
        self._building = False
        self._build_event = None          # completion signal of the running build
        self._build_payload = self._NO_PAYLOAD  # bytes | None | sentinel
        self._build_waiters = 0
        if not self.TOPIC:
            logger.warning(f'{self.__class__.__name__}.TOPIC is not set')

    # ---------------- instance management and routing ----------------

    @classmethod
    def get(cls, config_name, *key):
        """
        [Trio] Get or create the instance of (config_name, key).

        key[0] is the source class (registry isolation between sibling
        subclasses), the rest is passed to the constructor. The mapping /
        structure build happens in the constructor and may raise KeyError
        (config / mod / view deleted): get() converts that into None.

        Returns:
            ViewportEventSource | None:
        """
        with cls._reg_lock:
            sources = cls._by_config.get(config_name, None)
            if sources is not None:
                source = sources.get(key, None)
                if source is not None:
                    return source
            try:
                source = cls(config_name, *key[1:])
            except KeyError:
                # structure read failed (config / mod / view just deleted)
                return None
            if sources is None:
                sources = {}
                cls._by_config[config_name] = sources
            source._reg_key = key
            sources[key] = source
            return source

    @classmethod
    def dispatch(cls, config_name, event):
        """
        [任意线程] Event entry of a config-granularity event (worker recv /
        RPC broadcast): route it to every viewport source of the config,
        each source filters by its own view.
        """
        with cls._reg_lock:
            sources = list(cls._by_config.get(config_name, {}).values())
        for source in sources:
            source.on_event(event)

    def _remove(self):
        """
        [Trio] Unregister this instance (called by idle GC; the next
        subscription recreates it).
        """
        with self._reg_lock:
            sources = self._by_config.get(self.config_name, None)
            if sources is not None and sources.get(self._reg_key) is self:
                del sources[self._reg_key]
                if not sources:
                    del self._by_config[self.config_name]

    @classmethod
    def _iter_idle_instances(cls):
        with cls._reg_lock:
            return [source for sources in cls._by_config.values() for source in sources.values()]

    # ---------------- subscribe: single-flight full build ----------------

    async def subscribe(self, sub):
        """
        [Trio] Register and return the encoded full payload.

        The build happens BEFORE registration: the returned payload must
        be sent without any await in between, so the full event precedes
        every later increment (events falling into the build window are
        dropped, same as the private-build window of the previous design).
        A falsy view returns None: the subscriber is registered but no
        full is sent (empty-view semantics, same as empty data of cache
        sources).
        """
        payload = await self._get_payload()
        with self._lock:
            if self._trio_token is None:
                self._trio_token = trio.lowlevel.current_trio_token()
            self._subscribers.add(sub)
            self._retry.setdefault(sub, deque())
        return payload

    async def _get_payload(self):
        """
        [Trio] Single-flight full build shared by concurrent subscribers.

        While a build is running, subscribers wait for it and reuse its
        payload (same key -> same view). The payload is published only to
        the waiters of that build and expires when the last one took it:
        a later subscriber always rebuilds, so the full view is never
        stale by more than one build window. On build failure the waiters
        receive None (registered, no full) and the builder re-raises.
        """
        if self._building:
            # a concurrent build is running: wait and reuse its payload
            self._build_waiters += 1
            try:
                event = self._build_event
                await event.wait()
                # the builder published bytes | None before setting the
                # event: never the sentinel here
                return self._build_payload
            finally:
                self._build_waiters -= 1
                if self._build_waiters == 0:
                    # last waiter took the payload: expire it
                    self._build_payload = self._NO_PAYLOAD
        payload = self._build_payload
        if payload is not self._NO_PAYLOAD:
            # a build just finished and its last waiter is not awake yet:
            # reuse it (fresh within one scheduling window)
            return payload
        # no build running: build ourselves and publish to the waiters
        self._building = True
        event = trio.Event()
        self._build_event = event   # fresh event per build: stale signals never wake new waiters
        try:
            view = await self._build_full()
            payload = (
                ENCODER.encode(ResponseEvent(t=self.TOPIC, o='full', v=view))
                if view else None
            )
            if self._build_waiters:
                self._build_payload = payload
                event.set()
            return payload
        except BaseException:
            # wake the waiters even on failure: they must not hang forever
            if self._build_waiters:
                self._build_payload = None
                event.set()
            raise
        finally:
            self._building = False

    async def _build_full(self):
        """
        [Trio, 锁外] Build the full view. Subclass hook, runs inside the
        single-flight path of subscribe (one build per key at a time).
        Return the view data; falsy means an empty view (subscriber is
        registered, no full is sent).
        """
        raise NotImplementedError

    # ---------------- on_event forwarding ----------------

    def _convert(self, event):
        """
        [锁内] One event -> a response, or None when the event is not
        displayed by this view (dropped). Subclass hook.
        """
        raise NotImplementedError

    def on_event(self, event):
        """
        [任意线程] Locked: filter / convert by the subclass, broadcast the
        responses. A list payload (RPC responses) is expanded into single
        events inside the lock.
        """
        with self._lock:
            if not self._subscribers:
                # race window of the registry: no subscriber any more, drop
                return
            events = event if isinstance(event, list) else [event]
            was_empty = not self._inbox
            for e in events:
                resp = self._convert(e)
                if resp is not None:
                    self._inbox.append(resp)
            if was_empty and self._inbox:
                self._ring()
