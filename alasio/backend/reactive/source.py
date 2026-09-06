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

# Framework layers of the source hierarchy (known, fixed set): never
# instantiated on their own, they leave TOPIC_NAME to the concrete
# business sources below them. The class-level TOPIC_NAME check in
# __init_subclass__ exempts this list by name; add any new framework
# layer here (a missing entry surfaces as a class-definition warning).
_FRAMEWORK_LAYERS = frozenset((
    'EventSource',
    'GlobalEventSource',
    'ConfigEventSource',
    'KeyedEventSource',
    'ViewportEventSource',
))


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

    Subscriber contract: every subscriber (a ws BaseTopic) must implement
    deliver(payload) -- the sync entry the source broadcasts increments
    through. Delivery backpressure is the subscriber's own business (the
    topic queues what its connection cannot take and sends it with await
    send, see BaseTopic.deliver); the source itself never blocks.
    """

    # Topic name this source feeds: tagged into every full / incremental
    # event (ResponseEvent.t). Every concrete source class must set it;
    # the check runs at class definition time (see __init_subclass__), so
    # a missing TOPIC_NAME is reported on import instead of on the first
    # subscribe.
    TOPIC_NAME = ''
    # Inbox is the cross-thread entry buffer (payload objects).
    # When it overflows, the oldest payload is dropped.
    INBOX_MAXLEN = 1024
    # Idle GC: None = resident (never collected); a number = the instance is
    # removed when it has no subscriber for that many seconds.
    IDLE_TTL = None
    # Full data of cache sources (EventSource initializes it per instance).
    # The default subscribe() encodes it under the lock; a source without
    # data (None) registers without a full event. ViewportEventSource
    # overrides subscribe() entirely and has no data.
    data = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Collect idle-gc candidate classes. Instances are enumerated through
        # the singleton metaclass or the keyed registry of the class.
        if cls.IDLE_TTL is not None:
            _GC_CLASSES.add(cls)
        else:
            _GC_CLASSES.discard(cls)
        # TOPIC_NAME must be set on every concrete source class (checked
        # at class definition time); the framework layers of this module
        # are exempt by name (see _FRAMEWORK_LAYERS above).
        if cls.__name__ not in _FRAMEWORK_LAYERS and not cls.TOPIC_NAME:
            logger.warning(f'{cls.__name__}.TOPIC_NAME is not set')

    def __init__(self):
        # One lock covers data (cache sources) and the inbox. The
        # subscriber set is only touched from the Trio thread; the lock
        # just keeps it atomic with the snapshot.
        self._lock = threading.Lock()
        self._subscribers: "set[BaseTopic]" = set()
        # Cross-thread entry buffer written by on_event (any thread) and
        # reinit (Trio), drained by _sync_to_trio in one batch.
        self._inbox: "deque[ResponseEvent]" = deque(maxlen=self.INBOX_MAXLEN)
        # Set on the first subscribe (Trio thread); _ring relies on it.
        self._trio_token: "Optional[TrioToken]" = None
        # Timestamp of the moment the last subscriber left (or the
        # instance was created): idle GC only removes an instance that
        # has been subscriber-free for IDLE_TTL, so a fresh instance is
        # protected while get_source() -> subscribe() is still in flight.
        self._last_unsub = time.monotonic()

    # ---------------- subscribe / unsubscribe ----------------

    async def subscribe(self, sub) -> "Optional[bytes]":
        """
        [Trio] Register a subscriber and return the encoded full snapshot.

        - Cache sources: register first, then encode the current data
          under the lock (the payload may reference data, frozen while
          the lock is held).
        - Empty data returns None (no full event, matching the previous
          EventCache behavior).
        - Sources without data (self.data is None) register silently.

        Ordering contract: after subscribe() returns, the caller must send
        the returned bytes with send_nowait() without any await in between,
        so the full event is enqueued before any later increment.
        """
        with self._lock:
            if self._trio_token is None:
                self._trio_token = trio.lowlevel.current_trio_token()
            self._subscribers.add(sub)
            data = self.data
            if not data:
                return None
            # Encode under the lock: the payload references data, and data
            # cannot be modified while the lock is held.
            return ENCODER.encode(ResponseEvent(t=self.TOPIC_NAME, o='full', v=data))

    def unsubscribe(self, sub):
        """
        [Trio] Unsubscribe. Idempotent.

        When the last subscriber leaves, the idle timestamp is recorded
        (for idle GC, see gc_idle()).
        """
        with self._lock:
            self._subscribers.discard(sub)
            if not self._subscribers:
                # Record atomically with the discard: gc_idle (worker
                # thread) reads both under the lock and must never see
                # an empty set with a stale timestamp.
                self._last_unsub = time.monotonic()

    # ---------------- sub-class hooks ----------------

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
            # Copy the batch under the lock, then clear: every append path
            # (on_event / reinit) holds the same lock, so no producer can
            # interleave between the copy and the clear -- the batch is
            # exactly the queued events and nothing is left for a
            # duplicate delivery.
            batch = list(self._inbox)
            self._inbox.clear()
        # Encode outside the lock: payloads are frozen inside the lock
        # (see the freeze rules of EventSource), so encoding is safe.
        if len(batch) == 1:
            payload = ENCODER.encode(batch[0])
        else:
            # Merge the batch into one array message, encoded once.
            payload = ENCODER.encode(batch)
        # Deliver to every subscriber. The backpressure strategy is the
        # subscriber's own business: each topic buffers what its
        # connection cannot take yet (per-subscription outbox,
        # BaseTopic.deliver) and sends it with await send when the send
        # buffer frees a slot. The source never blocks: send_nowait
        # fails fast and the topic takes over, so a slow connection never
        # delays the delivery to fast ones.
        self._deliver_all(payload)

    def _deliver_all(self, payload):
        """
        [Trio 线程] Deliver one encoded payload to every current
        subscriber. No lock: the subscriber set is only touched from the
        Trio thread, so it cannot change while this runs. Also the
        run_sync_soon target of reinit full deliveries: the FIFO order of
        the callback queue keeps a full event behind the increments
        queued before it.
        """
        for sub in self._subscribers:
            sub.deliver(payload)

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
      changed (default False: no event stream);
    - _convert(event): [锁内] build the incremental response(s) of an
      event _apply accepted. Default raises: unreachable without an event
      stream, a missing override with one is a class bug.

    Freeze contract: full events reference data and are encoded INSIDE the
    critical section (subscribe / reinit) -- data may be mutated in place
    by event threads as soon as the lock is released, so a full payload
    never outlives the lock unencoded. Incremental responses are queued as
    objects and encoded later, outside the lock (batch, _sync_to_trio);
    their values must therefore never be mutated after the response is
    built. Referencing the event payload itself is safe: worker events are
    decoded objects bound into data by replacement -- an apply must
    replace a bound container wholesale, never mutate it in place
    afterwards.
    """

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
        # Untrusted disk changes (mark_dirty) not yet consumed by a read:
        # >0 = stale, the next fetch_init bypasses the TTL window. Counting
        # (not bool) lets a read tell "its own mark" from "a mark set while
        # it was reading" (see fetch_init).
        self._dirty = 0

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

    def _convert(self, event):
        """
        [锁内] Event -> incremental response(s). Called by on_event only
        after _apply accepted the event (data changed).

        Returns:
            ResponseEvent | list[ResponseEvent]: A single response, or a
                list for multi-key events. May reference the event payload
                (bound by replacement, never mutated afterwards): it is
                encoded later, outside the lock.

        Raises:
            NotImplementedError: A source that accepts events (overrides
                _apply) must convert them. Unreachable for sources without
                an event stream (_apply rejects every event beforehand).
        """
        raise NotImplementedError

    # ---------------- data refresh ----------------

    def mark_dirty(self):
        """
        [任意线程] Mark the data stale: an external change (e.g. a config
        save outside the event stream) may have made the current data
        outdated. The next fetch_init bypasses trust / TTL and re-reads.

        Sources without a full-data source never call it. The mark is
        consumed by a successful read (fetch_init); a read that fails
        keeps it, so the next read retries.
        """
        with self._lock:
            self._dirty += 1

    async def fetch_init(self, force=False):
        """
        [Trio] Read the latest full data.

        - force: ignore the freshness window;
        - dirty (mark_dirty): ignore the freshness window, the disk may
          have changed outside the event stream;
        - the read consumes the dirty marks that existed when it started;
          marks set while the read runs survive it (the result may
          predate them), so they trigger another read.

        Args:
            force (bool): Ignore the freshness window.

        Returns:
            Any | None: The new data, or None when the current data is
                still fresh (reinit skips the broadcast).
        """
        with self._lock:
            if not force and not self._dirty:
                if self.TTL is not None:
                    if time.monotonic() - self._lastrun < self.TTL:
                        return None
            dirty_before = self._dirty
        new = await self.on_init_async()
        with self._lock:
            self._lastrun = time.monotonic()
            if self._dirty == dirty_before:
                # consumed the marks that existed when the read started
                self._dirty = 0
        return new

    async def reinit(self, force=False):
        """
        [Trio] Refresh the full data and broadcast a full event when the
        data changed.

        - fetch_lock (double-checked with the freshness window of
          fetch_init) serializes concurrent refreshes;
        - identical data (old == new) does not broadcast;
        - without subscribers only the data is refreshed (no broadcast);
        - the full event is encoded and its delivery queued under the
          lock (it references data, which event threads may mutate as
          soon as the lock is released; the run_sync_soon queueing
          shares the FIFO of the inbox doorbells, so the full event is
          ordered against increments by the lock itself).

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
                # Encode under the lock: the payload references data, and
                # data may be mutated in place as soon as the lock is
                # released. bytes are frozen.
                payload = ENCODER.encode(
                    ResponseEvent(t=self.TOPIC_NAME, o='full', v=self.data)
                )
                # Queue the delivery under the lock, exactly like the
                # doorbells of _ring: the run_sync_soon queue is the
                # delivery order, and the lock serializes every queueing
                # against the worker-thread on_event. A full event is
                # therefore always queued before any increment whose event
                # applied after its data snapshot -- never after (queueing
                # outside the lock would race the doorbells and could let
                # the full event trail a newer increment). Subscribers
                # that leave in between are skipped (delivery reads the
                # current set).
                try:
                    self._trio_token.run_sync_soon(self._deliver_all, payload)
                except trio.RunFinishedError:
                    pass

    # ---------------- event stream ----------------

    def on_event(self, event):
        """
        [任意线程] Event entry: apply under the lock and broadcast.

        - No actual change: nothing is broadcast;
        - no subscriber: the event is only applied (data stays fresh),
          nothing is scheduled;
        - _convert returns a single response or a list (multi-key
          events); everything is pushed inside one critical section.
        """
        with self._lock:
            if not self._apply(event):
                return
            # the event stream is alive and its data is up to date
            self._lastrun = time.monotonic()
            self._running = True
            if not self._subscribers:
                return
            payload = self._convert(event)
            if type(payload) is list:
                # multi-key events: several responses pushed inside the
                # same critical section (the doorbell rings at most once)
                for p in payload:
                    self._push(p)
            else:
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
    # Sentinel: the build failed. Waiters translate it to None (registered,
    # no full); subscribers arriving after the failure must rebuild, never
    # reuse it as an empty-view result.
    _FAILED = object()

    def __init__(self, config_name):
        super().__init__()
        self.config_name = config_name
        # Single-flight full build (Trio thread only): plain flags + one
        # event, no lock needed.
        self._building = False
        self._build_event = None  # completion signal of the running build
        self._build_payload = self._NO_PAYLOAD  # bytes | None | sentinel
        self._build_waiters = 0

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
        return payload

    async def _get_payload(self):
        """
        [Trio] Single-flight full build shared by concurrent subscribers.

        While a build is running, subscribers wait for it and reuse its
        payload (same key -> same view). The payload is published only to
        the waiters of that build and expires when the last one took it:
        a later subscriber always rebuilds, so the full view is never
        stale by more than one build window. On build failure the waiters
        receive None (registered, no full) and the builder re-raises;
        subscribers arriving after the failure rebuild (the failure
        sentinel is never reused as a result).
        """
        if self._building:
            # a concurrent build is running: wait and reuse its payload
            self._build_waiters += 1
            try:
                event = self._build_event
                await event.wait()
                payload = self._build_payload
                if payload is self._FAILED:
                    # the build failed: register without a full (same
                    # semantics as the failing builder's own path)
                    return None
                # the builder published bytes | None before setting the
                # event: never the sentinel here
                return payload
            finally:
                self._build_waiters -= 1
                if self._build_waiters == 0:
                    # last waiter took the payload: expire it
                    self._build_payload = self._NO_PAYLOAD
        payload = self._build_payload
        if payload is not self._NO_PAYLOAD and payload is not self._FAILED:
            # a build just finished and its last waiter is not awake yet:
            # reuse it (fresh within one scheduling window); a failed
            # build leaves the failure sentinel, which is never reused --
            # later subscribers rebuild
            return payload
        # no build running: build ourselves and publish to the waiters
        self._building = True
        event = trio.Event()
        self._build_event = event  # fresh event per build: stale signals never wake new waiters
        try:
            view = await self._build_full()
            payload = (
                ENCODER.encode(ResponseEvent(t=self.TOPIC_NAME, o='full', v=view))
                if view else None
            )
            if self._build_waiters:
                self._build_payload = payload
                event.set()
            else:
                # no waiter to take it: leave no stale result behind
                # (a leftover failure sentinel must not survive a
                # successful rebuild)
                self._build_payload = self._NO_PAYLOAD
            return payload
        except BaseException:
            # wake the waiters even on failure: they must not hang forever.
            # Publish the failure sentinel (not None): waiters translate it
            # to None, and subscribers arriving after the failure rebuild
            # instead of reusing it as an empty-view result.
            if self._build_waiters:
                self._build_payload = self._FAILED
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

        Default: no forwarding (every event dropped) -- a view source
        without increments (static / one-shot view) is legal; override to
        filter / convert the events of the view.
        """
        return None

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
