"""
Tests for the BaseSource common machinery
(alasio/backend/reactive/source.py): subscription, doorbell batching,
delivery through the subscriber contract, concurrent producers and idle GC.

Driven through minimal EventSource subclasses.
"""

import threading
import time
from typing import List
from unittest.mock import MagicMock

import msgspec
import pytest
import trio

from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.reactive.source import BaseSource, EventSource, KeyedEventSource


class MockServer:
    """
    Mock ws server surface used by the mock subscriber
    """

    def __init__(self):
        self.sent = []

    def send_nowait(self, data):
        self.sent.append(data)

    async def send(self, data):
        self.sent.append(data)

    def send_lossy(self, data):
        self.sent.append(data)


class MockTopic:
    """
    Mock subscriber: deliver() records the payloads the source broadcasts
    (the BaseTopic.deliver contract). The mock always accepts: backpressure
    handling is a topic concern, tested at the ws layer.
    """

    def __init__(self, topic_name='T'):
        self.topic_name_value = topic_name
        self.conn_id = f'conn_{id(self)}'
        self.server = MockServer()

    def topic_name(self):
        return self.topic_name_value

    @property
    def sent(self):
        return self.server.sent

    def deliver(self, payload):
        """
        [Trio 线程] Source increments land here (BaseTopic.deliver contract)
        """
        self.server.sent.append(payload)


DECODER = msgspec.json.Decoder(ResponseEvent)
LIST_DECODER = msgspec.json.Decoder(List[ResponseEvent])


def decode(payload):
    """
    Decode one payload into ResponseEvent or list[ResponseEvent]

    Args:
        payload (bytes):

    Returns:
        ResponseEvent | list[ResponseEvent]:
    """
    data = msgspec.json.decode(payload)
    if isinstance(data, list):
        return LIST_DECODER.decode(payload)
    return DECODER.decode(payload)


class FakeSource(EventSource):
    """
    A minimal event source for testing the base machinery: data starts at
    {'x': 0}, on_event({'x': n}) replaces the x value, `_apply` returns
    whether the value changed.
    """
    TOPIC_NAME = 'Fake'

    def __init__(self):
        super().__init__()
        self.data = {'x': 0}

    def _apply(self, event):
        key = list(event.keys())[0] if isinstance(event, dict) else event[0]
        value = event[key] if isinstance(event, dict) else event[1]
        if self.data.get(key) == value:
            return False
        self.data[key] = value
        return True

    def _convert(self, event):
        key = event[0]
        return ResponseEvent(t=self.TOPIC_NAME, o='set', k=(key,), v=self.data[key])


class KeyedFakeSource(KeyedEventSource):
    """
    A keyed source to verify per-class registries and idle GC of keyed
    instances.
    """
    TOPIC_NAME = 'KeyedFake'
    IDLE_TTL = 8

    def __init__(self, name):
        super().__init__()
        self.name = name
        self.data = {'name': name}


@pytest.fixture(autouse=True)
def cleanup_sources():
    """Clean up keyed registries and singleton caches after each test"""
    yield
    KeyedFakeSource._registry.clear()


class TestSubscribe:
    @pytest.mark.trio
    async def test_subscribe_returns_encoded_full(self):
        """subscribing returns encoded bytes decoding to {o:'full', v:data}"""
        source = FakeSource()
        topic = MockTopic()
        data = await source.subscribe(topic)
        assert isinstance(data, bytes)
        event = decode(data)
        assert event.t == 'Fake'
        assert event.o == 'full'
        assert event.v == {'x': 0}
        assert topic in source._subscribers

    @pytest.mark.trio
    async def test_subscribe_empty_data_returns_none(self):
        """empty data returns None (no full event)"""
        source = FakeSource()
        source.data = {}
        topic = MockTopic()
        assert await source.subscribe(topic) is None
        # still registered: later events reach the topic
        assert topic in source._subscribers

    @pytest.mark.trio
    async def test_subscribe_records_trio_token(self):
        """the trio token is recorded on the first subscribe"""
        source = FakeSource()
        assert source._trio_token is None
        await source.subscribe(MockTopic())
        assert source._trio_token is not None

    @pytest.mark.trio
    async def test_subscribe_idempotent_for_same_topic(self):
        """subscribing the same topic twice is idempotent (set semantics)"""
        source = FakeSource()
        topic = MockTopic()
        await source.subscribe(topic)
        await source.subscribe(topic)
        assert len(source._subscribers) == 1


class TestUnsubscribe:
    @pytest.mark.trio
    async def test_unsubscribe_idempotent(self):
        """unsubscribing a topic that is not subscribed does not raise"""
        source = FakeSource()
        source.unsubscribe(MockTopic())

    @pytest.mark.trio
    async def test_unsubscribed_topic_gets_no_events(self):
        """after unsubscribe new events no longer reach the topic"""
        source = FakeSource()
        topic = MockTopic()
        await source.subscribe(topic)
        source.unsubscribe(topic)
        source.on_event(('x', 1))
        await trio.testing.wait_all_tasks_blocked()
        assert topic.sent == []

    @pytest.mark.trio
    async def test_unsubscribe_records_idle_time(self, monkeypatch):
        """the last subscriber leaving records the idle timestamp"""
        monkeypatch.setattr(time, 'monotonic', lambda: 100.)
        source = FakeSource()
        topic = MockTopic()
        await source.subscribe(topic)
        # a fresh instance records its creation time: idle GC keeps it
        # for IDLE_TTL even without subscribers (get_source -> subscribe
        # window)
        assert source._last_unsub == 100.
        monkeypatch.setattr(time, 'monotonic', lambda: 200.)
        source.unsubscribe(topic)
        assert source._last_unsub == 200.


class TestDoorbellBatching:
    @pytest.mark.trio
    async def test_doorbell_rings_once_per_batch(self):
        """inbox transitions from empty to non-empty ring exactly once"""
        source = FakeSource()
        topic = MockTopic()
        await source.subscribe(topic)
        calls = [0]
        original = source._trio_token

        class MockToken:
            def run_sync_soon(self, func):
                calls[0] += 1
                original.run_sync_soon(func)

        source._trio_token = MockToken()
        # burst of events in one thread batch: only the first rings
        for n in range(1, 6):
            source.on_event(('x', n))
        assert calls[0] == 1
        await trio.testing.wait_all_tasks_blocked()
        # a new burst after the inbox drained rings again
        source.on_event(('x', 6))
        assert calls[0] == 2

    @pytest.mark.trio
    async def test_batch_events_merged_into_array_message(self):
        """multiple queued events are delivered as one encoded array message"""
        source = FakeSource()
        topic = MockTopic()
        await source.subscribe(topic)
        for n in range(1, 4):
            source.on_event(('x', n))
        await trio.testing.wait_all_tasks_blocked()
        # exactly one delivery for the burst
        assert len(topic.sent) == 1
        events = decode(topic.sent[0])
        assert isinstance(events, list)
        assert [e.v for e in events] == [1, 2, 3]

    @pytest.mark.trio
    async def test_sync_to_trio_empty_inbox_noop(self):
        """_sync_to_trio with an empty inbox does nothing (scheduled race)"""
        source = FakeSource()
        await source.subscribe(MockTopic())
        source._sync_to_trio()


class TestDeliverContract:
    @pytest.mark.trio
    async def test_deliver_called_per_subscriber(self):
        """each subscriber receives the encoded payload through deliver()"""
        source = FakeSource()
        t1 = MockTopic()
        t2 = MockTopic()
        await source.subscribe(t1)
        await source.subscribe(t2)
        source.on_event(('x', 1))
        await trio.testing.wait_all_tasks_blocked()
        # one delivery per subscriber (no arrays: single-event batch)
        assert len(t1.sent) == 1
        assert decode(t1.sent[0]).o == 'set'
        assert decode(t1.sent[0]).v == 1
        assert len(t2.sent) == 1
        assert decode(t2.sent[0]).v == 1


class TestConcurrentProducers:
    @pytest.mark.trio
    async def test_thread_events_reach_topic_after_subscribe(self):
        """
        Events produced by a worker thread after registration are never
        lost: the final value of the subscriber equals the last event value
        (values may merge into array messages, per the FIFO contract).
        """
        source = FakeSource()
        topic = MockTopic()
        await source.subscribe(topic)

        def produce():
            for n in range(1, 101):
                source.on_event(('x', n))
                if n % 10 == 0:
                    time.sleep(0.001)

        await trio.to_thread.run_sync(produce)
        await trio.testing.wait_all_tasks_blocked()
        # decode all deliveries, flatten arrays, check value coverage
        values = []
        for payload in topic.sent:
            events = decode(payload)
            if not isinstance(events, list):
                events = [events]
            values.extend(e.v for e in events)
        assert values[-1] == 100
        # every intermediate value appeared at least once (no lost updates)
        assert len(set(values)) == 100

    @pytest.mark.trio
    async def test_unsubscribe_during_thread_events(self):
        """unsubscribing while a thread produces does not raise, no delivery after"""
        source = FakeSource()
        topic = MockTopic()
        await source.subscribe(topic)
        stop = threading.Event()

        def produce():
            n = 0
            while not stop.is_set():
                n += 1
                source.on_event(('x', n))

        thread = threading.Thread(target=produce, daemon=True)
        thread.start()
        try:
            await trio.sleep(0.01)
            source.unsubscribe(topic)
            await trio.testing.wait_all_tasks_blocked()
            count = len(topic.sent)
            await trio.sleep(0.01)
            await trio.testing.wait_all_tasks_blocked()
            # nothing delivered after the unsubscribe
            assert len(topic.sent) == count
        finally:
            stop.set()
            thread.join(timeout=2)


class TestGcIdle:
    @pytest.mark.trio
    async def test_idle_ttl_none_not_collected(self):
        """sources with IDLE_TTL=None are never collected"""
        source = FakeSource()
        await source.subscribe(MockTopic())
        source.unsubscribe(*list(source._subscribers))
        BaseSource.gc_idle()
        # FakeSource is not registered in the gc class list at all

    def test_keyed_source_gc_removes_idle_instance(self, monkeypatch):
        """an idle keyed instance is removed from its registry after IDLE_TTL"""
        instance = KeyedFakeSource.get('a')
        assert KeyedFakeSource._registry == {('a',): instance}
        # simulate: idle for longer than IDLE_TTL
        monkeypatch.setattr(time, 'monotonic', lambda: 100.)
        instance._last_unsub = 80.  # idle for 20s > 8s
        KeyedFakeSource.gc_idle()
        assert KeyedFakeSource._registry == {}

    def test_keyed_source_gc_keeps_active_instance(self, monkeypatch):
        """instances with subscribers or inside the idle window are kept"""
        instance = KeyedFakeSource.get('a')
        monkeypatch.setattr(time, 'monotonic', lambda: 100.)
        instance._last_unsub = 95.  # idle for 5s < 8s
        KeyedFakeSource.gc_idle()
        assert KeyedFakeSource._registry != {}
        # subscriber present: never collected even when idle is long
        topic = MagicMock()
        instance._subscribers.add(topic)
        instance._last_unsub = 1.
        KeyedFakeSource.gc_idle()
        assert KeyedFakeSource._registry != {}

    def test_keyed_source_get_isolation(self):
        """each keyed subclass owns its own registry table"""
        class OtherKeyedSource(KeyedEventSource):
            TOPIC_NAME = 'Other'
            IDLE_TTL = 8

            def __init__(self, name):
                super().__init__()
                self.name = name

        try:
            a = KeyedFakeSource.get('a')
            b = OtherKeyedSource.get('b')
            assert KeyedFakeSource._registry is not OtherKeyedSource._registry
            assert KeyedFakeSource._registry == {('a',): a}
            assert OtherKeyedSource._registry == {('b',): b}
        finally:
            OtherKeyedSource._registry.clear()

    def test_keyed_source_get_same_key_same_instance(self):
        """get with the same key returns the same instance"""
        a1 = KeyedFakeSource.get('a')
        a2 = KeyedFakeSource.get('a')
        b = KeyedFakeSource.get('b')
        assert a1 is a2
        assert a1 is not b
