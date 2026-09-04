"""
Tests for LogCache (alasio/backend/topic/log.py).

LogCache bridges blocking worker-thread log events to the async websocket
with a lock-free inbox/cache design:
- events go to the inbox (live stream) first, then to the cache (history)
- the same ResponseEvent object lives in both, dedup uses identity
- the doorbell (run_sync_soon) only rings when the inbox turns non-empty
- subscribe returns the encoded full snapshot bytes (no internal send),
  live batches go through send_lossy
"""

import time
from unittest.mock import MagicMock

import msgspec
import pytest
import trio

from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.topic.log import LOG_ENCODER, LogCache
from alasio.backend.worker.event import ConfigEvent
from alasio.logger import logger


class MockTopic:
    """
    Mock BaseTopic for testing LogCache
    """

    def __init__(self, topic_name='Log'):
        self.topic_name_value = topic_name
        self.server = MagicMock()
        self.conn_id = f'conn_{id(self)}'

    def topic_name(self):
        return self.topic_name_value


def make_event(n):
    """
    A ConfigEvent with timestamp n

    Args:
        n (int):

    Returns:
        ConfigEvent:
    """
    return ConfigEvent(t='Log', v={'t': float(n), 'l': 'INFO', 'm': f'message {n}', 'e': None})


def decode_full(data):
    """
    Decode the full snapshot bytes returned by LogCache.subscribe

    Args:
        data (bytes):

    Returns:
        ResponseEvent:
    """
    return msgspec.json.Decoder(ResponseEvent).decode(data)


def send_events_from_thread(cache, events, delay=0.001, batch_size=1):
    """
    Send events from a worker thread, simulating real pipe reading

    Args:
        cache (LogCache):
        events (list[ConfigEvent]):
        delay (float):
        batch_size (int):
    """
    for i, event in enumerate(events):
        cache.on_event(event)
        if delay > 0 and (i + 1) % batch_size == 0:
            time.sleep(delay)


@pytest.fixture(autouse=True)
def cleanup_logcache():
    """Clean up LogCache singletons after each test"""
    yield
    LogCache.singleton_clear()


class TestLogCacheOnEvent:
    @pytest.mark.trio
    async def test_on_event_no_subscribers_caches_only(self):
        """Events only go to the cache when nobody subscribes (zero idle overhead)"""
        cache = await LogCache.get_instance('test_config')
        cache.on_event(make_event(1))
        assert len(cache._cache) == 1
        assert cache._cache[0].v == make_event(1).v
        # no live stream, no doorbell
        assert len(cache._inbox) == 0

    @pytest.mark.trio
    async def test_on_event_with_subscriber_goes_to_inbox(self):
        """Events go to both the inbox and the cache when a subscriber exists"""
        cache = await LogCache.get_instance('test_config')
        topic = MockTopic()
        await cache.subscribe(topic)
        cache.on_event(make_event(1))
        # the message is pending in the inbox, not yet consumed
        assert len(cache._inbox) == 1
        assert len(cache._cache) == 1
        # the doorbell schedules _sync_to_trio, which flushes the inbox
        await trio.testing.wait_all_tasks_blocked()
        assert len(cache._inbox) == 0
        topic.server.send_lossy.assert_called_once()

    @pytest.mark.trio
    async def test_sync_to_trio_sends_encoded_batch(self):
        """_sync_to_trio encodes the whole batch once and sends via send_lossy"""
        cache = await LogCache.get_instance('test_config')
        topic = MockTopic()
        await cache.subscribe(topic)
        cache.on_event(make_event(1))
        cache.on_event(make_event(2))
        await trio.testing.wait_all_tasks_blocked()
        # exactly one batch for the two messages
        topic.server.send_lossy.assert_called_once()
        data = topic.server.send_lossy.call_args[0][0]
        assert isinstance(data, bytes)
        decoded = msgspec.json.decode(data)
        assert [entry['v']['m'] for entry in decoded] == ['message 1', 'message 2']

    @pytest.mark.trio
    async def test_on_event_without_trio_token_warns(self):
        """without a trio_token (direct construction) the live stream degrades with a warning"""
        cache = LogCache('test_config')
        topic = MockTopic()
        await cache.subscribe(topic)
        with logger.mock_capture_writer() as capture:
            cache.on_event(make_event(1))
            assert capture.fd.any_contains('trio_token not initialized')
        # the message still reaches the cache, the inbox keeps it pending
        assert len(cache._cache) == 1
        assert len(cache._inbox) == 1
        assert topic.server.send_lossy.call_count == 0

    @pytest.mark.trio
    async def test_sync_to_trio_empty_inbox_noop(self):
        """_sync_to_trio with an empty inbox does nothing"""
        cache = await LogCache.get_instance('test_config')
        topic = MockTopic()
        await cache.subscribe(topic)
        cache._sync_to_trio()
        assert topic.server.send_lossy.call_count == 0


class TestLogCacheSubscribe:
    @pytest.mark.trio
    async def test_subscribe_returns_full_snapshot(self):
        """Subscribing returns an encoded full snapshot of the cached history"""
        cache = await LogCache.get_instance('test_config')
        for n in range(5):
            cache.on_event(make_event(n))
        topic = MockTopic()
        data = await cache.subscribe(topic)
        assert isinstance(data, bytes)
        event = decode_full(data)
        assert event.o == 'full'
        assert [v['m'] for v in event.v] == [f'message {n}' for n in range(5)]
        # subscribe does not send internally
        assert topic.server.send_nowait.call_count == 0

    @pytest.mark.trio
    async def test_subscribe_empty_cache_returns_empty_full(self):
        """Subscribing on an empty cache still returns an empty full event (clears UI on config switch)"""
        cache = await LogCache.get_instance('test_config')
        topic = MockTopic()
        event = decode_full(await cache.subscribe(topic))
        assert event.o == 'full'
        assert event.v == []

    @pytest.mark.trio
    async def test_subscribe_deduplicates_overlap(self):
        """
        Messages pending in the inbox are not duplicated in the snapshot:
        the snapshot is cut at the overlap, the pending messages arrive as live batches
        """
        cache = await LogCache.get_instance('test_config')
        # pre-populate the cache
        for n in range(5):
            cache.on_event(make_event(n))
        topic1 = MockTopic()
        await cache.subscribe(topic1)
        # events 5..7 go to both cache and inbox, still pending (no await in between)
        for n in range(5, 8):
            cache.on_event(make_event(n))
        # a second subscriber joins before trio consumes the inbox
        topic2 = MockTopic()
        snapshot = decode_full(await cache.subscribe(topic2))
        # overlapping 5..7 are cut from the snapshot
        assert [v['m'] for v in snapshot.v] == [f'message {n}' for n in range(5)]
        # the pending 5..7 arrive as live batches to both subscribers
        await trio.testing.wait_all_tasks_blocked()
        assert topic1.server.send_lossy.call_count > 0
        assert topic2.server.send_lossy.call_count > 0

    @pytest.mark.trio
    async def test_subscribe_registers_subscriber(self):
        """Subscribing registers the topic in the subscriber set"""
        cache = await LogCache.get_instance('test_config')
        topic = MockTopic()
        await cache.subscribe(topic)
        assert topic in cache._subscribers


class TestLogCacheUnsubscribe:
    @pytest.mark.trio
    async def test_unsubscribe_removes_subscriber(self):
        """Unsubscribing removes the topic from the subscriber set"""
        cache = await LogCache.get_instance('test_config')
        topic = MockTopic()
        await cache.subscribe(topic)
        assert topic in cache._subscribers
        cache.unsubscribe(topic)
        assert topic not in cache._subscribers

    @pytest.mark.trio
    async def test_unsubscribe_not_subscribed_no_error(self):
        """Unsubscribing a topic that is not subscribed does not raise"""
        cache = await LogCache.get_instance('test_config')
        cache.unsubscribe(MockTopic())

    @pytest.mark.trio
    async def test_unsubscribe_last_subscriber_clears_inbox(self):
        """Unsubscribing the last subscriber clears the pending inbox"""
        cache = await LogCache.get_instance('test_config')
        topic = MockTopic()
        await cache.subscribe(topic)
        cache.on_event(make_event(1))
        assert len(cache._inbox) == 1
        cache.unsubscribe(topic)
        assert len(cache._inbox) == 0
        assert len(cache._subscribers) == 0


class TestLogCacheBatching:
    @pytest.mark.trio
    async def test_high_frequency_batching(self):
        """High-frequency events from a worker thread are batched into few send_lossy calls"""
        cache = await LogCache.get_instance('test_config')
        topic = MockTopic()
        await cache.subscribe(topic)
        topic.server.send_lossy.reset_mock()

        num_events = 1000
        events = [make_event(i) for i in range(num_events)]
        await trio.to_thread.run_sync(send_events_from_thread, cache, events, 0.01, 50)
        await trio.testing.wait_all_tasks_blocked()

        assert topic.server.send_lossy.call_count < num_events
        assert topic.server.send_lossy.call_count > 0

    @pytest.mark.trio
    async def test_doorbell_rings_once_per_batch(self):
        """The doorbell only rings when the inbox transitions from empty to non-empty"""
        cache = await LogCache.get_instance('test_config')
        topic = MockTopic()
        await cache.subscribe(topic)
        run_sync_soon_count = [0]
        original_token = cache.trio_token

        class MockTrioToken:
            def run_sync_soon(self, func):
                run_sync_soon_count[0] += 1
                if original_token:
                    original_token.run_sync_soon(func)

        cache.trio_token = MockTrioToken()
        topic.server.send_lossy.reset_mock()
        run_sync_soon_count[0] = 0

        # first event rings the doorbell once
        cache.on_event(make_event(1))
        assert run_sync_soon_count[0] == 1
        # flush, inbox becomes empty again
        # note: wait_all_tasks_blocked() is required, a single sleep(0) does not
        # guarantee the run_sync_soon callback has been processed
        await trio.testing.wait_all_tasks_blocked()
        # rapid events: only the first one rings, the rest accumulate in the inbox
        for i in range(2, 10):
            cache.on_event(make_event(i))
        assert run_sync_soon_count[0] == 2
        await trio.testing.wait_all_tasks_blocked()
        # the batch reaches the subscriber
        assert topic.server.send_lossy.call_count > 0


class TestLogCacheMultipleSubscribers:
    @pytest.mark.trio
    async def test_all_subscribers_receive(self):
        """All subscribers receive the same live batches"""
        cache = await LogCache.get_instance('test_config')
        topic1 = MockTopic()
        topic2 = MockTopic()
        await cache.subscribe(topic1)
        await cache.subscribe(topic2)
        topic1.server.send_lossy.reset_mock()
        topic2.server.send_lossy.reset_mock()

        cache.on_event(make_event(1))
        await trio.testing.wait_all_tasks_blocked()
        assert topic1.server.send_lossy.call_count > 0
        assert topic2.server.send_lossy.call_count > 0

    @pytest.mark.trio
    async def test_subscriber_join_leave(self):
        """Subscribers joining/leaving mid-stream don't interfere with others"""
        cache = await LogCache.get_instance('test_config')
        topic1 = MockTopic()
        await cache.subscribe(topic1)
        topic1.server.send_lossy.reset_mock()

        # subscriber 2 joins mid-stream
        topic2 = MockTopic()
        await cache.subscribe(topic2)
        topic2.server.send_lossy.reset_mock()

        cache.on_event(make_event(1))
        await trio.testing.wait_all_tasks_blocked()
        assert topic1.server.send_lossy.call_count > 0
        assert topic2.server.send_lossy.call_count > 0

        # subscriber 2 leaves, only subscriber 1 receives new events
        cache.unsubscribe(topic2)
        topic1.server.send_lossy.reset_mock()
        topic2.server.send_lossy.reset_mock()
        cache.on_event(make_event(2))
        await trio.testing.wait_all_tasks_blocked()
        assert topic1.server.send_lossy.call_count > 0
        assert topic2.server.send_lossy.call_count == 0


class TestLogCacheMemorySafety:
    @pytest.mark.trio
    async def test_cache_limited_to_maxlen(self):
        """The cache is bounded, oldest messages are dropped"""
        cache = await LogCache.get_instance('test_config')
        num_events = 2000
        events = [make_event(i) for i in range(num_events)]
        await trio.to_thread.run_sync(send_events_from_thread, cache, events, 0)
        assert len(cache._cache) == 500
        # the newest messages are retained
        assert cache._cache[-1].v['t'] == float(num_events - 1)
        assert cache._cache[0].v['t'] == float(num_events - 500)


class TestLogCacheLifecycle:
    @pytest.mark.trio
    async def test_full_lifecycle(self):
        """subscribe -> snapshot -> live logs -> unsubscribe"""
        cache = await LogCache.get_instance('test_config')
        for n in range(5):
            cache.on_event(make_event(n))

        # 1. subscribe, snapshot of history is returned
        topic = MockTopic()
        snapshot = decode_full(await cache.subscribe(topic))
        assert snapshot.o == 'full'
        assert len(snapshot.v) == 5
        topic.server.send_lossy.reset_mock()

        # 2. live logs flow
        for n in range(5, 10):
            cache.on_event(make_event(n))
        await trio.testing.wait_all_tasks_blocked()
        assert topic.server.send_lossy.call_count > 0

        # 3. unsubscribe, no more delivery
        cache.unsubscribe(topic)
        topic.server.send_lossy.reset_mock()
        cache.on_event(make_event(10))
        await trio.testing.wait_all_tasks_blocked()
        assert topic.server.send_lossy.call_count == 0

    @pytest.mark.trio
    async def test_config_switch(self):
        """Switching configs re-subscribes to a fresh cache with an empty snapshot"""
        cache1 = await LogCache.get_instance('config1')
        topic = MockTopic()
        await cache1.subscribe(topic)
        for n in range(3):
            cache1.on_event(make_event(n))
        await trio.testing.wait_all_tasks_blocked()

        # switch to config2
        cache1.unsubscribe(topic)
        cache2 = await LogCache.get_instance('config2')
        snapshot = decode_full(await cache2.subscribe(topic))
        assert snapshot.o == 'full'
        assert snapshot.v == []

        # events on config2 reach the subscriber
        topic.server.send_lossy.reset_mock()
        cache2.on_event(make_event(100))
        await trio.testing.wait_all_tasks_blocked()
        assert topic.server.send_lossy.call_count > 0


class TestLogCacheEncoder:
    def test_log_encoder_encodes_batch(self):
        """LOG_ENCODER encodes a batch of ResponseEvents into a JSON array"""
        events = [ResponseEvent(t='Log', v={'t': 1.0}), ResponseEvent(t='Log', v={'t': 2.0})]
        data = LOG_ENCODER.encode(events)
        decoded = msgspec.json.decode(data)
        assert [entry['t'] for entry in decoded] == ['Log', 'Log']
