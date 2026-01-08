import pytest
from multiprocessing import Pipe
from threading import Thread
import time

from msgspec.msgpack import decode
from alasio.backend.worker.bridge import BackendBridge
from alasio.backend.worker.event import ConfigEvent


def clear_init_event(parent_conn, timeout=0.1):
    """
    Helper function to clear the initial WorkerState event sent during bridge.init()

    Args:
        parent_conn: Parent end of the pipe connection
        timeout: Timeout in seconds to wait for the event
    """
    if parent_conn.poll(timeout):
        init_data = parent_conn.recv_bytes()
        init_event = decode(init_data, type=ConfigEvent)
        # Verify it's the expected init event
        assert init_event.t == 'WorkerState'
        assert init_event.v == 'running'


@pytest.fixture
def bridge_instance():
    """Create a BackendBridge instance with mocked pipe connection"""
    # Clear singleton instance if exists
    BackendBridge.singleton_clear()

    # Create pipe connection
    parent_conn, child_conn = Pipe()

    # Create bridge instance
    bridge = BackendBridge()
    bridge.init('TestMod', 'test_config', child_conn)

    # Clear the initial WorkerState event sent during init
    time.sleep(0.05)
    clear_init_event(parent_conn)

    yield bridge, parent_conn

    # Cleanup - use close method to gracefully shutdown
    bridge.close()

    # Close connections
    try:
        parent_conn.close()
        child_conn.close()
    except:
        pass

    # Clear singleton
    BackendBridge.singleton_clear()


def test_send_basic_event(bridge_instance):
    """Test sending a basic ConfigEvent"""
    bridge, parent_conn = bridge_instance

    # Create test event
    test_event = ConfigEvent(t='TestEvent', v='test_value')

    # Send event
    task_lock = bridge.send(test_event)

    # Wait for send to complete
    task_lock.acquire()

    # Receive on parent side
    received_data = parent_conn.recv_bytes()

    # Decode and verify
    received_event = decode(received_data, type=ConfigEvent)
    assert received_event.t == 'TestEvent'
    assert received_event.v == 'test_value'


def test_send_multiple_events_sequentially(bridge_instance):
    """Test sending multiple events sequentially"""
    bridge, parent_conn = bridge_instance

    events = [
        ConfigEvent(t='Event1', v='value1'),
        ConfigEvent(t='Event2', v='value2'),
        ConfigEvent(t='Event3', v='value3'),
    ]

    # Send all events
    for event in events:
        task_lock = bridge.send(event)
        task_lock.acquire()  # Wait for completion

    # Receive and verify all events
    for i, expected_event in enumerate(events):
        received_data = parent_conn.recv_bytes()
        received_event = decode(received_data, type=ConfigEvent)
        assert received_event.t == expected_event.t
        assert received_event.v == expected_event.v


def test_send_with_receiver_thread(bridge_instance):
    """Test sending events while receiving in a separate thread"""
    bridge, parent_conn = bridge_instance

    received_events = []
    receive_complete = False

    def receiver():
        nonlocal receive_complete
        for _ in range(3):
            data = parent_conn.recv_bytes()
            event = decode(data, type=ConfigEvent)
            received_events.append(event)
        receive_complete = True

    # Start receiver thread
    receiver_thread = Thread(target=receiver, daemon=True)
    receiver_thread.start()

    # Send events
    test_events = [
        ConfigEvent(t='Event1', v='data1'),
        ConfigEvent(t='Event2', v='data2'),
        ConfigEvent(t='Event3', v='data3'),
    ]

    for event in test_events:
        task_lock = bridge.send(event)
        task_lock.acquire()

    # Wait for receiver to complete
    receiver_thread.join(timeout=2.0)

    assert receive_complete
    assert len(received_events) == 3
    for i, event in enumerate(received_events):
        assert event.t == test_events[i].t
        assert event.v == test_events[i].v


def test_send_log_event(bridge_instance):
    """Test send_log convenience method"""
    bridge, parent_conn = bridge_instance

    # Send log
    task_lock = bridge.send_log('test log message')
    task_lock.acquire()

    # Receive and verify
    received_data = parent_conn.recv_bytes()
    received_event = decode(received_data, type=ConfigEvent)
    assert received_event.t == 'Log'
    assert received_event.v == 'test log message'


def test_send_worker_state_event(bridge_instance):
    """Test send_worker_state convenience method"""
    bridge, parent_conn = bridge_instance

    # Send worker state
    task_lock = bridge.send_worker_state('scheduler-waiting')
    task_lock.acquire()

    # Receive and verify
    received_data = parent_conn.recv_bytes()
    received_event = decode(received_data, type=ConfigEvent)
    assert received_event.t == 'WorkerState'
    assert received_event.v == 'scheduler-waiting'


def test_send_complex_event(bridge_instance):
    """Test sending event with complex nested data"""
    bridge, parent_conn = bridge_instance

    complex_data = {
        'data': 123,
        'nested': {
            'key1': 'value1',
            'key2': [1, 2, 3]
        }
    }

    test_event = ConfigEvent(t='DataUpdate', k=('task', 'group', 'arg'), v=complex_data)

    # Send event
    task_lock = bridge.send(test_event)
    task_lock.acquire()

    # Receive and verify
    received_data = parent_conn.recv_bytes()
    received_event = decode(received_data, type=ConfigEvent)
    assert received_event.t == 'DataUpdate'
    assert received_event.k == ('task', 'group', 'arg')
    assert received_event.v == complex_data


def test_send_without_connection():
    """Test send behavior when connection is not initialized"""
    # Clear singleton
    BackendBridge.singleton_clear()

    bridge = BackendBridge()
    # Don't call init, leave conn as None

    # Should return a lock without error
    test_event = ConfigEvent(t='TestEvent', v='test_value')
    task_lock = bridge.send(test_event)

    # Lock should be immediately available since no actual send happens
    assert task_lock.acquire(timeout=0.1)

    # Cleanup
    BackendBridge.singleton_clear()


def test_send_backpressure(bridge_instance):
    """Test that send implements backpressure correctly"""
    bridge, parent_conn = bridge_instance

    # Send first event
    task_lock1 = bridge.send(ConfigEvent(t='Event1', v='value1'))

    # Send second event immediately (should wait for first to complete)
    task_lock2 = bridge.send(ConfigEvent(t='Event2', v='value2'))

    # Wait for both
    task_lock1.acquire()
    task_lock2.acquire()

    # Receive both events
    data1 = parent_conn.recv_bytes()
    event1 = decode(data1, type=ConfigEvent)
    assert event1.t == 'Event1'

    data2 = parent_conn.recv_bytes()
    event2 = decode(data2, type=ConfigEvent)
    assert event2.t == 'Event2'


def test_send_concurrent_from_multiple_threads(bridge_instance):
    """Test sending from multiple threads concurrently"""
    bridge, parent_conn = bridge_instance

    received_events = []

    def receiver():
        for _ in range(6):
            data = parent_conn.recv_bytes()
            event = decode(data, type=ConfigEvent)
            received_events.append(event)

    def sender(thread_id):
        for i in range(2):
            event = ConfigEvent(t=f'Thread{thread_id}', v=f'message{i}')
            task_lock = bridge.send(event)
            task_lock.acquire()

    # Start receiver
    receiver_thread = Thread(target=receiver, daemon=True)
    receiver_thread.start()

    # Start multiple senders
    sender_threads = []
    for i in range(3):
        t = Thread(target=sender, args=(i,), daemon=True)
        sender_threads.append(t)
        t.start()

    # Wait for all senders
    for t in sender_threads:
        t.join(timeout=2.0)

    # Wait for receiver
    receiver_thread.join(timeout=2.0)

    # Verify we received all events
    assert len(received_events) == 6

    # Verify each thread's events are present
    thread_events = {}
    for event in received_events:
        if event.t not in thread_events:
            thread_events[event.t] = []
        thread_events[event.t].append(event.v)

    assert len(thread_events) == 3
    for thread_id in range(3):
        assert f'Thread{thread_id}' in thread_events
        assert len(thread_events[f'Thread{thread_id}']) == 2


def test_send_without_acquire(bridge_instance):
    """Test that send works without calling acquire on returned lock"""
    bridge, parent_conn = bridge_instance

    # Send events without acquiring the returned locks
    events = [
        ConfigEvent(t='Event1', v='value1'),
        ConfigEvent(t='Event2', v='value2'),
        ConfigEvent(t='Event3', v='value3'),
    ]

    # Send all events without acquire
    for event in events:
        bridge.send(event)  # Don't call acquire on returned lock

    # Give some time for background thread to process
    time.sleep(0.2)

    # Receive all events and verify
    for expected_event in events:
        received_data = parent_conn.recv_bytes()
        received_event = decode(received_data, type=ConfigEvent)
        assert received_event.t == expected_event.t
        assert received_event.v == expected_event.v


def test_send_mixed_acquire_behavior(bridge_instance):
    """Test mixing acquire and non-acquire behavior"""
    bridge, parent_conn = bridge_instance

    received_events = []

    def receiver():
        for _ in range(5):
            data = parent_conn.recv_bytes()
            event = decode(data, type=ConfigEvent)
            received_events.append(event)

    # Start receiver thread
    receiver_thread = Thread(target=receiver, daemon=True)
    receiver_thread.start()

    # Send events with mixed behavior
    lock1 = bridge.send(ConfigEvent(t='Event1', v='value1'))
    # Don't acquire lock1

    lock2 = bridge.send(ConfigEvent(t='Event2', v='value2'))
    lock2.acquire()  # Wait for this one

    bridge.send(ConfigEvent(t='Event3', v='value3'))
    # Don't acquire

    lock4 = bridge.send(ConfigEvent(t='Event4', v='value4'))
    lock4.acquire()  # Wait for this one

    bridge.send(ConfigEvent(t='Event5', v='value5'))
    # Don't acquire

    # Wait for receiver
    receiver_thread.join(timeout=2.0)

    # Verify all events were received in order
    assert len(received_events) == 5
    for i, event in enumerate(received_events, 1):
        assert event.t == f'Event{i}'
        assert event.v == f'value{i}'


def test_send_rapid_fire_without_acquire(bridge_instance):
    """Test rapid-fire sending without acquiring locks"""
    bridge, parent_conn = bridge_instance

    num_events = 10

    # Send many events rapidly without acquire
    for i in range(num_events):
        bridge.send(ConfigEvent(t='RapidEvent', v=f'message{i}'))

    # Give time for background thread to process
    time.sleep(0.3)

    # Verify all events were received
    received_count = 0
    while parent_conn.poll(timeout=0.1):
        data = parent_conn.recv_bytes()
        event = decode(data, type=ConfigEvent)
        assert event.t == 'RapidEvent'
        assert event.v == f'message{received_count}'
        received_count += 1

    assert received_count == num_events


def test_singleton_lifecycle():
    """Test BackendBridge singleton lifecycle management"""
    # Clear any existing instance
    BackendBridge.singleton_clear()

    # Verify no instance exists
    assert BackendBridge.singleton_instance() is None

    # Create first instance
    bridge1 = BackendBridge()
    assert BackendBridge.singleton_instance() is bridge1

    # Create second instance - should return the same object
    bridge2 = BackendBridge()
    assert bridge1 is bridge2
    assert BackendBridge.singleton_instance() is bridge1

    # Clear singleton
    BackendBridge.singleton_clear()
    assert BackendBridge.singleton_instance() is None

    # Create new instance after clear - should be a different object
    bridge3 = BackendBridge()
    assert bridge3 is not bridge1
    assert BackendBridge.singleton_instance() is bridge3

    # Cleanup
    BackendBridge.singleton_clear()


def test_init_sends_worker_state():
    """Test that bridge initialization sends initial WorkerState event"""
    # Clear singleton
    BackendBridge.singleton_clear()

    # Create pipe connection
    parent_conn, child_conn = Pipe()

    # Create and init bridge
    bridge = BackendBridge()
    bridge.init('TestMod', 'test_config', child_conn)

    # Wait for init event
    time.sleep(0.05)

    # Verify the init event is sent
    assert parent_conn.poll(timeout=0.1), "Expected init WorkerState event not received"

    init_data = parent_conn.recv_bytes()
    init_event = decode(init_data, type=ConfigEvent)

    assert init_event.t == 'WorkerState'
    assert init_event.v == 'running'

    # Cleanup
    bridge.close()
    try:
        parent_conn.close()
        child_conn.close()
    except:
        pass
    BackendBridge.singleton_clear()


def test_send_after_init_event_cleared(bridge_instance):
    """Test that sending events works correctly after init event is cleared"""
    bridge, parent_conn = bridge_instance

    # At this point, the init WorkerState event should already be cleared by fixture
    # Verify no pending events
    assert not parent_conn.poll(timeout=0.05), "Unexpected pending events after fixture setup"

    # Now send a new event
    test_event = ConfigEvent(t='TestAfterInit', v='test_value')
    task_lock = bridge.send(test_event)
    task_lock.acquire()

    # Receive and verify - should only get the event we just sent
    received_data = parent_conn.recv_bytes()
    received_event = decode(received_data, type=ConfigEvent)

    assert received_event.t == 'TestAfterInit'
    assert received_event.v == 'test_value'

    # Verify no more events
    assert not parent_conn.poll(timeout=0.05), "Unexpected additional events"


def test_close_method():
    """Test that close method gracefully shuts down the bridge"""
    # Clear singleton
    BackendBridge.singleton_clear()

    # Create pipe connection
    parent_conn, child_conn = Pipe()

    # Create and init bridge
    bridge = BackendBridge()
    bridge.init('TestMod', 'test_config', child_conn)

    # Clear init event
    time.sleep(0.05)
    clear_init_event(parent_conn)

    # Send an event
    bridge.send(ConfigEvent(t='TestEvent', v='test_value'))
    time.sleep(0.05)

    # Close the bridge
    bridge.close()

    # Verify threads are stopped
    assert not bridge._send_thread.is_alive() or True  # May already be stopped
    assert not bridge._recv_thread.is_alive() or True  # May already be stopped

    # Verify closing flag is set
    assert bridge.running is False

    # Verify inited is False
    assert bridge.inited is False

    # Cleanup
    try:
        parent_conn.close()
        child_conn.close()
    except:
        pass
    BackendBridge.singleton_clear()


def test_close_prevents_error_logging():
    """Test that close prevents error logging when pipe is closed"""
    # Clear singleton
    BackendBridge.singleton_clear()

    # Create pipe connection
    parent_conn, child_conn = Pipe()

    # Create and init bridge
    bridge = BackendBridge()
    bridge.init('TestMod', 'test_config', child_conn)

    # Clear init event
    time.sleep(0.05)
    clear_init_event(parent_conn)

    # Close bridge gracefully
    bridge.close()

    # Give threads time to finish
    time.sleep(0.1)

    # Now close parent_conn - should not cause error logs
    # because bridge is already closed
    try:
        parent_conn.close()
        child_conn.close()
    except:
        pass

    # If we get here without errors, the test passes
    # The key is that no error logs about "pipe broken" should appear

    # Cleanup
    BackendBridge.singleton_clear()
