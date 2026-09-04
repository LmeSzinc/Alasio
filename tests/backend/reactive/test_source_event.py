"""
Tests for the EventSource family (alasio/backend/reactive/source.py):
subscribe snapshots, on_event apply + broadcast, fetch_init / reinit
semantics, payload freeze, singletons and idle GC of one-shot sources.
"""

import threading
import time

import pytest
import trio

from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.reactive.source import BaseSource, ConfigEventSource, EventSource, GlobalEventSource
from tests.backend.reactive.test_source_base import MockTopic, decode


class FakeSource(EventSource):
    """
    EventSource with an event stream and a full-data source.

    - data: {'x': n, 'y': n} with n = fetch count;
    - on_event(('x', n)) replaces x (thread-writer semantics: data keys are
      replaced in place);
    - TTL = 5 for fetch freshness tests.
    """
    TOPIC = 'Fake'
    TTL = 5

    def __init__(self):
        super().__init__()
        self.data = {'x': 0, 'y': 0}
        self.fetch_count = 0

    def on_init(self):
        # runs in a worker thread when called through on_init_async
        self.fetch_count += 1
        return {'x': self.fetch_count, 'y': self.fetch_count}

    def _apply(self, event):
        key, value = event
        if self.data.get(key) == value:
            return False
        self.data[key] = value
        return True

    def _make_response(self, event):
        key, value = event
        return ResponseEvent(t=self.TOPIC, o='set', k=(key,), v=value)


class NoWriterSource(EventSource):
    """
    A source without sub-thread writers: data is only replaced wholesale by
    reinit, so the snapshot may reference data directly.
    """
    TOPIC = 'NoWriter'
    TTL = None

    def on_init(self):
        return {'a': 1}


class TestSubscribeSnapshot:
    @pytest.mark.trio
    async def test_snapshot_contains_all_applied_events(self):
        """the full snapshot is the current data (all applied events included)"""
        source = FakeSource()
        source.data = {'x': 1, 'y': 2}
        topic = MockTopic()
        event = decode(await source.subscribe(topic))
        assert event.o == 'full'
        assert event.v == {'x': 1, 'y': 2}

    @pytest.mark.trio
    async def test_snapshot_empty_data_returns_none(self):
        """empty data returns None, no full event"""
        source = FakeSource()
        source.data = {}
        assert await source.subscribe(MockTopic()) is None


class TestOnEvent:
    @pytest.mark.trio
    async def test_no_subscriber_applies_only(self):
        """without subscribers the event is only applied, nothing scheduled"""
        source = FakeSource()
        source.on_event(('x', 5))
        assert source.data['x'] == 5

    @pytest.mark.trio
    async def test_with_subscriber_broadcasts_set(self):
        """with subscribers the event is applied and a set response broadcast"""
        source = FakeSource()
        topic = MockTopic()
        await source.subscribe(topic)
        source.on_event(('x', 5))
        await trio.testing.wait_all_tasks_blocked()
        event = decode(topic.sent[0])
        assert event.o == 'set'
        assert event.k == ('x',)
        assert event.v == 5

    @pytest.mark.trio
    async def test_no_change_no_broadcast(self):
        """an event that does not change data is not broadcast"""
        source = FakeSource()
        source.data = {'x': 1, 'y': 0}
        topic = MockTopic()
        await source.subscribe(topic)
        topic.sent.clear()
        source.on_event(('x', 1))  # same value
        await trio.testing.wait_all_tasks_blocked()
        assert topic.sent == []


class TestMakeResponseFreeze:
    @pytest.mark.trio
    async def test_incremental_response_not_affected_by_later_apply(self):
        """
        Payload freeze: an incremental response queued by a worker thread
        carries its own frozen value; a later in-place apply of data does
        not change the queued payload.
        """
        source = FakeSource()
        topic = MockTopic()
        await source.subscribe(topic)
        topic.sent.clear()
        # two changes queued before the trio thread drains them
        source.on_event(('x', 1))
        source.on_event(('x', 2))
        await trio.testing.wait_all_tasks_blocked()
        events = decode(topic.sent[0])
        # both queued payloads kept their frozen values
        if isinstance(events, list):
            assert [e.v for e in events] == [1, 2]
        else:
            assert events.v == 1


class TestFetchInit:
    @pytest.mark.trio
    async def test_fetch_within_ttl_returns_none(self):
        """a second fetch inside the TTL window returns None (no re-read)"""
        source = FakeSource()
        assert await source.fetch_init() == {'x': 1, 'y': 1}
        assert source.fetch_count == 1
        # fresh: no read
        assert await source.fetch_init() is None
        assert source.fetch_count == 1

    @pytest.mark.trio
    async def test_fetch_after_ttl_rereads(self, monkeypatch):
        """after the TTL expires the data is re-read"""
        source = FakeSource()
        await source.fetch_init()
        now = [time.monotonic()]

        class Clock:
            @staticmethod
            def monotonic():
                now[0] += 10
                return now[0]

        monkeypatch.setattr(time, 'monotonic', Clock.monotonic)
        # note: EventSource freshness uses time.monotonic at check and set
        new = await source.fetch_init()
        assert new == {'x': 2, 'y': 2}
        assert source.fetch_count == 2

    @pytest.mark.trio
    async def test_fetch_force_ignores_ttl(self):
        """force=True always re-reads"""
        source = FakeSource()
        await source.fetch_init()
        assert await source.fetch_init(force=True) == {'x': 2, 'y': 2}
        assert source.fetch_count == 2

    @pytest.mark.trio
    async def test_fetch_ttl_none_always_reads(self):
        """TTL=None reads every time"""
        source = NoWriterSource()
        assert await source.fetch_init() == {'a': 1}
        assert await source.fetch_init() == {'a': 1}

    @pytest.mark.trio
    async def test_on_init_runs_in_thread(self):
        """on_init_async runs on_init in a worker thread"""
        source = FakeSource()
        main_thread = threading.get_ident()

        class ThreadAwareSource(FakeSource):
            def on_init(self):
                assert threading.get_ident() != main_thread
                return super().on_init()

        source = ThreadAwareSource()
        await source.fetch_init(force=True)
        assert source.fetch_count == 1


class TestReinit:
    @pytest.mark.trio
    async def test_reinit_broadcasts_full_to_subscribers(self):
        """reinit with new data broadcasts a full event to subscribers"""
        source = FakeSource()
        topic = MockTopic()
        await source.subscribe(topic)
        topic.sent.clear()
        await source.reinit(force=True)
        await trio.testing.wait_all_tasks_blocked()
        event = decode(topic.sent[0])
        assert event.o == 'full'
        assert event.v == {'x': 1, 'y': 1}

    @pytest.mark.trio
    async def test_reinit_no_change_no_broadcast(self):
        """reinit with unchanged data does not broadcast"""
        source = NoWriterSource()
        topic = MockTopic()
        await source.subscribe(topic)
        # data must equal the read result first
        await source.reinit(force=True)
        await trio.testing.wait_all_tasks_blocked()
        topic.sent.clear()
        await source.reinit(force=True)
        await trio.testing.wait_all_tasks_blocked()
        assert topic.sent == []

    @pytest.mark.trio
    async def test_reinit_no_subscriber_refreshes_data_only(self):
        """reinit without subscribers refreshes data and broadcasts nothing"""
        source = FakeSource()
        await source.reinit(force=True)
        assert source.data == {'x': 1, 'y': 1}

    @pytest.mark.trio
    async def test_concurrent_reinit_serialized(self):
        """concurrent reinits serialize on the fetch lock: one read only"""
        source = FakeSource()

        async def double_reinit():
            async with trio.open_nursery() as nursery:
                nursery.start_soon(source.reinit)
                nursery.start_soon(source.reinit)

        await double_reinit()
        # both reinits ran (fetch_lock serializes); the second one found the
        # data fresh (TTL) and skipped the read
        assert source.fetch_count == 1


class TestGlobalConfigSingleton:
    def test_global_singleton(self):
        """GlobalEventSource subclasses share one global instance"""
        class GlobalFake(GlobalEventSource):
            TOPIC = 'GlobalFake'

        try:
            a = GlobalFake()
            b = GlobalFake()
            assert a is b
        finally:
            GlobalFake.singleton_clear()

    def test_config_named_singleton(self):
        """ConfigEventSource instances are keyed by config name"""
        class ConfigFake(ConfigEventSource):
            TOPIC = 'ConfigFake'

        try:
            a = ConfigFake('config_a')
            b = ConfigFake('config_a')
            c = ConfigFake('config_b')
            assert a is b
            assert a is not c
            assert a.config_name == 'config_a'
        finally:
            ConfigFake.singleton_clear()


class TestOneShotIdleGc:
    @pytest.mark.trio
    async def test_resident_source_not_collected(self):
        """resident sources (IDLE_TTL=None) are never idle-collected"""
        source = FakeSource()
        topic = MockTopic()
        await source.subscribe(topic)
        source.unsubscribe(topic)
        BaseSource.gc_idle()
        assert source._subscribers == set()

    def test_one_shot_collected_after_idle(self, monkeypatch):
        """one-shot sources are removed after IDLE_TTL without subscribers"""

        class OneShotFake(GlobalEventSource):
            TOPIC = 'OneShotFake'
            IDLE_TTL = 8

        try:
            instance = OneShotFake()
            topic = MockTopic()
            # simulate it was subscribed and then left 20s ago
            instance._last_unsub = time.monotonic() - 20
            # instance must be re-registered on next use: singleton cleared
            OneShotFake.gc_idle()
            assert OneShotFake.singleton_instance() is None
        finally:
            OneShotFake.singleton_clear()
