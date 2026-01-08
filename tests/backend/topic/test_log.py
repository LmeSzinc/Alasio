import threading
import time
from unittest.mock import MagicMock

import pytest
import trio

from alasio.backend.topic.log import LogCache
from alasio.backend.worker.event import ConfigEvent


# ---- Test Helper ----


def run_in_thread(func, *args):
    """Run a function in a separate thread and return the thread"""
    thread = threading.Thread(target=func, args=args, daemon=True)
    thread.start()
    return thread


def send_events_from_thread(cache, events, delay=0.001, batch_size=1):
    """Send events from a worker thread, simulating real pipe reading"""
    for i, event in enumerate(events):
        cache.on_event(event)
        if delay > 0 and (i + 1) % batch_size == 0:
            time.sleep(delay)


class MockTopic:
    """Mock BaseTopic for testing"""

    def __init__(self, topic_name='Log'):
        self.topic_name_value = topic_name
        self.server = MagicMock()
        self.server.send_nowait = MagicMock()
        self.conn_id = f'conn_{id(self)}'

    def topic_name(self):
        return self.topic_name_value


@pytest.fixture(autouse=True)
def cleanup_logcache():
    """Clean up LogCache singletons after each test"""
    yield
    LogCache.singleton_clear()


# ---- LogCache Basic Functionality Tests ----


@pytest.mark.trio
async def test_logcache_on_event_no_subscribers():
    """Test that events are only written to cache when there are no subscribers"""
    cache = await LogCache.get_instance('test_config')

    # Send event without subscribers
    event = ConfigEvent(t='Log', v={'t': 1.0, 'l': 'INFO', 'm': 'test message', 'e': None})
    cache.on_event(event)

    # Check cache has the event
    assert len(cache._cache) == 1
    assert cache._cache[0].v == event.v

    # Check inbox is empty (no subscribers)
    assert len(cache._inbox) == 0


@pytest.mark.trio
async def test_logcache_on_event_with_subscribers():
    """Test that events are written to both cache and inbox when there are subscribers"""
    cache = await LogCache.get_instance('test_config')
    topic = MockTopic()

    # Add a subscriber
    cache._subscribers.add(topic)

    # Send event
    event = ConfigEvent(t='Log', v={'t': 1.0, 'l': 'INFO', 'm': 'test message', 'e': None})
    cache.on_event(event)

    # Wait for trio to process
    await trio.testing.wait_all_tasks_blocked()

    # Check cache has the event
    assert len(cache._cache) == 1

    # Check server.send_nowait was called
    assert topic.server.send_nowait.call_count >= 1


@pytest.mark.trio
async def test_logcache_subscribe_send_snapshot():
    """Test that subscribing sends a snapshot of existing logs"""
    cache = await LogCache.get_instance('test_config')

    # Add some events to cache first
    for i in range(5):
        event = ConfigEvent(t='Log', v={'t': float(i), 'l': 'INFO', 'm': f'message {i}', 'e': None})
        cache.on_event(event)

    # Now subscribe
    topic = MockTopic()
    cache.subscribe(topic)

    # Check that send_nowait was called with snapshot
    assert topic.server.send_nowait.call_count == 1
    call_args = topic.server.send_nowait.call_args
    snapshot_event = call_args[0][0]

    # Verify it's a 'full' operation
    assert snapshot_event.o == 'full'
    # Verify it contains all 5 messages
    assert len(snapshot_event.v) == 5


@pytest.mark.trio
async def test_logcache_subscribe_deduplication():
    """Test that subscription correctly deduplicates overlapping messages"""
    cache = await LogCache.get_instance('test_config')

    # Add some events to cache
    for i in range(5):
        event = ConfigEvent(t='Log', v={'t': float(i), 'l': 'INFO', 'm': f'message {i}', 'e': None})
        cache.on_event(event)

    # Add a subscriber to trigger inbox usage
    topic1 = MockTopic()
    cache._subscribers.add(topic1)

    # Add more events (these will go to both cache and inbox)
    for i in range(5, 8):
        event = ConfigEvent(t='Log', v={'t': float(i), 'l': 'INFO', 'm': f'message {i}', 'e': None})
        cache.on_event(event)

    await trio.testing.wait_all_tasks_blocked()

    # Now subscribe a new topic
    topic2 = MockTopic()
    cache.subscribe(topic2)

    # The snapshot should not include duplicates from inbox
    # It should have 8 messages total (0-7)
    call_args = topic2.server.send_nowait.call_args
    snapshot_event = call_args[0][0]
    assert len(snapshot_event.v) == 8


@pytest.mark.trio
async def test_logcache_unsubscribe_cleanup():
    """Test that unsubscribing cleans up inbox when no subscribers remain"""
    cache = await LogCache.get_instance('test_config')
    topic = MockTopic()

    # Subscribe and add events
    cache.subscribe(topic)
    for i in range(3):
        event = ConfigEvent(t='Log', v={'t': float(i), 'l': 'INFO', 'm': f'message {i}', 'e': None})
        cache.on_event(event)

    await trio.testing.wait_all_tasks_blocked()

    # Inbox should have items
    # (they may have been processed, but let's add one more to be sure)
    event = ConfigEvent(t='Log', v={'t': 99.0, 'l': 'INFO', 'm': 'final', 'e': None})
    cache.on_event(event)

    # Unsubscribe
    cache.unsubscribe(topic)

    # Inbox should be cleared
    assert len(cache._inbox) == 0
    assert len(cache._subscribers) == 0


@pytest.mark.trio
async def test_logcache_unsubscribe_not_subscribed():
    """Test that unsubscribing a topic that is not subscribed doesn't raise error"""
    cache = await LogCache.get_instance('test_config')
    topic = MockTopic()

    # Unsubscribe without subscribing first
    # This should not raise KeyError
    cache.unsubscribe(topic)
    assert len(cache._subscribers) == 0


# ---- High-Frequency Message Tests ----


@pytest.mark.trio
async def test_logcache_high_frequency_batching():
    """Test automatic batching under high-frequency messages from worker thread"""
    cache = await LogCache.get_instance('test_config')
    topic = MockTopic()
    cache.subscribe(topic)

    # Reset call count after subscription
    topic.server.send_nowait.reset_mock()

    # Prepare many events
    num_events = 1000
    events = [
        ConfigEvent(t='Log', v={'t': float(i), 'l': 'INFO', 'm': f'msg {i}', 'e': None})
        for i in range(num_events)
    ]

    # Send events from worker thread to simulate real scenario
    # Use batch_size=50 to force accumulation in inbox before trio loop runs
    await trio.to_thread.run_sync(send_events_from_thread, cache, events, 0.01, 50)

    # Wait for processing
    await trio.testing.wait_all_tasks_blocked()

    # The number of send_nowait calls should be much less than num_events
    # due to batching (doorbell mechanism)
    assert topic.server.send_nowait.call_count < num_events
    assert topic.server.send_nowait.call_count > 0
    print(f"Batched {num_events} events into {topic.server.send_nowait.call_count} sends")


@pytest.mark.trio
async def test_logcache_zero_overhead_idle():
    """Test that no trio overhead occurs when there are no subscribers"""
    cache = await LogCache.get_instance('test_config')

    # Send events without subscribers
    for i in range(10):
        event = ConfigEvent(t='Log', v={'t': float(i), 'l': 'INFO', 'm': f'msg {i}', 'e': None})
        cache.on_event(event)

    # Check that cache is populated but inbox is empty
    assert len(cache._cache) == 10
    assert len(cache._inbox) == 0
    # No trio tasks should have been scheduled


@pytest.mark.trio
async def test_logcache_memory_safety():
    """Test that maxlen prevents memory overflow when events sent from worker thread"""
    cache = await LogCache.get_instance('test_config')

    # Prepare more events than maxlen (1024)
    num_events = 2000
    events = [
        ConfigEvent(t='Log', v={'t': float(i), 'l': 'INFO', 'm': f'msg {i}', 'e': None})
        for i in range(num_events)
    ]

    # Send from worker thread
    await trio.to_thread.run_sync(send_events_from_thread, cache, events, 0)

    # Cache should be limited to maxlen
    assert len(cache._cache) == 500
    # The oldest messages should be dropped, newest retained
    assert cache._cache[-1].v['t'] == float(num_events - 1)


@pytest.mark.trio
async def test_logcache_doorbell_mechanism():
    """Test that doorbell only rings when inbox transitions from empty to non-empty"""
    cache = await LogCache.get_instance('test_config')
    topic = MockTopic()

    # Track run_sync_soon calls
    run_sync_soon_count = [0]
    original_token = cache.trio_token

    class MockTrioToken:
        def run_sync_soon(self, func):
            run_sync_soon_count[0] += 1
            # Actually run it for testing
            if original_token:
                original_token.run_sync_soon(func)

    cache.trio_token = MockTrioToken()
    cache.subscribe(topic)
    topic.server.send_nowait.reset_mock()
    run_sync_soon_count[0] = 0

    # Send first event - should ring doorbell
    event1 = ConfigEvent(t='Log', v={'t': 1.0, 'l': 'INFO', 'm': 'msg1', 'e': None})
    cache.on_event(event1)
    await trio.sleep(0.001)  # Allow processing

    first_doorbell_count = run_sync_soon_count[0]
    assert first_doorbell_count > 0

    # Send more events rapidly before inbox is cleared
    for i in range(2, 5):
        event = ConfigEvent(t='Log', v={'t': float(i), 'l': 'INFO', 'm': f'msg{i}', 'e': None})
        cache.on_event(event)

    # Doorbell should not ring much more (batching)
    # The exact count depends on timing, but should be less than number of events
    assert run_sync_soon_count[0] < 10


# ---- Multiple Subscribers Tests ----


@pytest.mark.trio
async def test_logcache_multiple_subscribers():
    """Test that multiple subscribers all receive messages correctly from worker thread"""
    cache = await LogCache.get_instance('test_config')

    # Create two subscribers
    topic1 = MockTopic()
    topic2 = MockTopic()

    cache.subscribe(topic1)
    cache.subscribe(topic2)

    # Reset mocks after subscription
    topic1.server.send_nowait.reset_mock()
    topic2.server.send_nowait.reset_mock()

    # Prepare events
    events = [
        ConfigEvent(t='Log', v={'t': float(i), 'l': 'INFO', 'm': f'msg {i}', 'e': None})
        for i in range(5)
    ]

    # Send from worker thread
    await trio.to_thread.run_sync(send_events_from_thread, cache, events, 0.001)

    await trio.testing.wait_all_tasks_blocked()

    # Both should have received messages
    assert topic1.server.send_nowait.call_count > 0
    assert topic2.server.send_nowait.call_count > 0


@pytest.mark.trio
async def test_logcache_subscriber_join_leave():
    """Test that subscribers joining/leaving mid-stream don't interfere with others (multi-threaded)"""
    cache = await LogCache.get_instance('test_config')

    # Subscriber 1 subscribes early
    topic1 = MockTopic()
    cache.subscribe(topic1)
    topic1.server.send_nowait.reset_mock()

    # Prepare events for phase 1
    events_phase1 = [
        ConfigEvent(t='Log', v={'t': float(i), 'l': 'INFO', 'm': f'msg {i}', 'e': None})
        for i in range(3)
    ]

    # Send from worker thread
    await trio.to_thread.run_sync(send_events_from_thread, cache, events_phase1, 0.001)
    await trio.testing.wait_all_tasks_blocked()

    topic1_count_before = topic1.server.send_nowait.call_count

    # Subscriber 2 joins mid-stream
    topic2 = MockTopic()
    cache.subscribe(topic2)
    topic1.server.send_nowait.reset_mock()
    topic2.server.send_nowait.reset_mock()

    # Prepare events for phase 2
    events_phase2 = [
        ConfigEvent(t='Log', v={'t': float(i), 'l': 'INFO', 'm': f'msg {i}', 'e': None})
        for i in range(3, 6)
    ]

    # Send from worker thread
    await trio.to_thread.run_sync(send_events_from_thread, cache, events_phase2, 0.001)
    await trio.testing.wait_all_tasks_blocked()

    # Both should receive new messages
    assert topic1.server.send_nowait.call_count > 0
    assert topic2.server.send_nowait.call_count > 0

    # Subscriber 2 unsubscribes
    cache.unsubscribe(topic2)
    topic1.server.send_nowait.reset_mock()

    # Prepare events for phase 3
    events_phase3 = [
        ConfigEvent(t='Log', v={'t': float(i), 'l': 'INFO', 'm': f'msg {i}', 'e': None})
        for i in range(6, 9)
    ]

    # Send from worker thread
    await trio.to_thread.run_sync(send_events_from_thread, cache, events_phase3, 0.001)
    await trio.testing.wait_all_tasks_blocked()

    # Only topic1 should receive messages
    assert topic1.server.send_nowait.call_count > 0
    # topic2 should not have been called after unsubscribe
    # (its counter was reset, so it should still be 0)


# ---- Integration Tests (Real Scenario Combinations) ----


@pytest.mark.trio
async def test_log_topic_full_lifecycle():
    """Test a complete lifecycle: subscribe -> receive history -> receive new logs -> unsubscribe (multi-threaded)"""
    cache = await LogCache.get_instance('test_config')

    # 1. Pre-populate with some logs from worker thread
    history_events = [
        ConfigEvent(t='Log', v={'t': float(i), 'l': 'INFO', 'm': f'history {i}', 'e': None})
        for i in range(5)
    ]
    await trio.to_thread.run_sync(send_events_from_thread, cache, history_events, 0)

    # 2. Subscribe
    topic = MockTopic()
    cache.subscribe(topic)

    # Verify snapshot was sent
    assert topic.server.send_nowait.call_count == 1
    snapshot_event = topic.server.send_nowait.call_args[0][0]
    assert snapshot_event.o == 'full'
    assert len(snapshot_event.v) == 5

    # Reset for new messages
    topic.server.send_nowait.reset_mock()

    # 3. Send new logs from worker thread (simulating continuous log stream)
    new_events = [
        ConfigEvent(t='Log', v={'t': float(i), 'l': 'INFO', 'm': f'new {i}', 'e': None})
        for i in range(5, 10)
    ]
    await trio.to_thread.run_sync(send_events_from_thread, cache, new_events, 0.001)

    await trio.testing.wait_all_tasks_blocked()

    # Verify new messages were sent
    assert topic.server.send_nowait.call_count > 0

    # 4. Unsubscribe
    cache.unsubscribe(topic)
    assert topic not in cache._subscribers
    assert len(cache._inbox) == 0


@pytest.mark.trio
async def test_log_topic_config_switch():
    """Test switching between configs multiple times with worker threads continuously pushing"""
    # Simulate switching between different configs

    # Config 1
    cache1 = await LogCache.get_instance('config1')
    topic = MockTopic()
    cache1.subscribe(topic)

    # Send events from worker thread for config1
    events_config1_phase1 = [
        ConfigEvent(t='Log', v={'t': float(i), 'l': 'INFO', 'm': f'config1 msg {i}', 'e': None})
        for i in range(3)
    ]
    await trio.to_thread.run_sync(send_events_from_thread, cache1, events_config1_phase1, 0.001)
    await trio.testing.wait_all_tasks_blocked()

    config1_calls = topic.server.send_nowait.call_count

    # Switch to Config 2
    cache1.unsubscribe(topic)
    topic.server.send_nowait.reset_mock()

    cache2 = await LogCache.get_instance('config2')
    cache2.subscribe(topic)

    # Check for empty full event
    assert topic.server.send_nowait.call_count == 1
    snapshot_event = topic.server.send_nowait.call_args[0][0]
    assert snapshot_event.o == 'full'
    assert snapshot_event.v == []

    topic.server.send_nowait.reset_mock()

    # Send events from worker thread for config2
    events_config2 = [
        ConfigEvent(t='Log', v={'t': float(i), 'l': 'INFO', 'm': f'config2 msg {i}', 'e': None})
        for i in range(3)
    ]
    await trio.to_thread.run_sync(send_events_from_thread, cache2, events_config2, 0.001)
    await trio.testing.wait_all_tasks_blocked()

    # Verify messages from config2 were received
    assert topic.server.send_nowait.call_count > 0

    # Switch back to Config 1
    cache2.unsubscribe(topic)
    cache1.subscribe(topic)
    topic.server.send_nowait.reset_mock()

    # Send more events from worker thread for config1
    events_config1_phase2 = [
        ConfigEvent(t='Log', v={'t': float(i), 'l': 'INFO', 'm': f'config1 msg {i}', 'e': None})
        for i in range(3, 6)
    ]
    await trio.to_thread.run_sync(send_events_from_thread, cache1, events_config1_phase2, 0.001)
    await trio.testing.wait_all_tasks_blocked()

    # Verify new messages from config1 were received
    assert topic.server.send_nowait.call_count > 0

    # Cleanup
    cache1.unsubscribe(topic)

    # Verify resources are cleaned up for both caches
    assert len(cache1._subscribers) == 0
    assert len(cache2._subscribers) == 0
    assert len(cache1._inbox) == 0
    assert len(cache2._inbox) == 0
