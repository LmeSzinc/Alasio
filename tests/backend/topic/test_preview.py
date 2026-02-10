import trio
import trio.testing
import pytest
from unittest.mock import MagicMock, patch
from alasio.backend.topic.preview import PreviewTask
from alasio.backend.ws.context import GLOBAL_CONTEXT


class MockWorker:
    def __init__(self, config_name, status='running'):
        self.config = config_name
        self.status = status
        self.commands = []

    def send_command(self, command):
        self.commands.append(command)


class MockTopic:
    def __init__(self):
        self.server = MagicMock()


@pytest.fixture
async def mock_env():
    # Mock GLOBAL_CONTEXT.global_nursery
    async with trio.open_nursery() as nursery:
        with patch.object(GLOBAL_CONTEXT, 'global_nursery', nursery):
            # Mock BackendWorkerManager
            mock_manager = MagicMock()
            mock_manager.state = {}
            with patch('alasio.backend.topic.preview.get_worker_manager', return_value=mock_manager):
                yield mock_manager, nursery


@pytest.fixture
def config_name():
    return "test_config"


@pytest.fixture
def worker(mock_env, config_name):
    mock_manager, _ = mock_env
    w = MockWorker(config_name)
    mock_manager.state[config_name] = w
    return w


@pytest.fixture
async def preview_task(mock_env, config_name) -> PreviewTask:
    PreviewTask.singleton_clear()
    task = PreviewTask(config_name)
    yield task
    # Shutdown background task
    task.task_shutdown()


@pytest.mark.trio
async def test_preview_task_subscribe_starts_task(preview_task, worker, autojump_clock):
    """
    Test that subscribing to a PreviewTask starts the background task 
    and sends a preview command to the worker.
    """
    topic = MockTopic()

    # Subscribe should start task and send command
    preview_task.subscribe(topic, 'normal')
    await trio.testing.wait_all_tasks_blocked()

    assert len(worker.commands) == 1
    assert worker.commands[0].c == 'preview'

    # Wait for recurrence (default 2s)
    await trio.sleep(preview_task.recurrence)
    await trio.testing.wait_all_tasks_blocked()
    assert len(worker.commands) == 2


@pytest.mark.trio
async def test_preview_task_unsubscribe_stops_task(preview_task, worker, autojump_clock):
    """
    Test that unsubscribing the last subscriber stops the background task.
    """
    topic = MockTopic()

    preview_task.subscribe(topic, 'normal')
    await trio.testing.wait_all_tasks_blocked()
    assert len(worker.commands) == 1

    # Unsubscribe last subscriber should stop task
    preview_task.unsubscribe(topic)
    await trio.testing.wait_all_tasks_blocked()

    # Reset commands to check if any new ones arrive
    worker.commands = []
    await trio.sleep(preview_task.recurrence * 2)
    assert len(worker.commands) == 0


@pytest.mark.trio
async def test_preview_task_on_preview_broadcast(preview_task, worker, autojump_clock):
    """
    Test broadcasting of preview images to subscribers based on their requested speed.
    """
    topic_normal = MockTopic()
    topic_realtime = MockTopic()

    preview_task.subscribe(topic_normal, 'normal')
    preview_task.subscribe(topic_realtime, 'realtime')
    await trio.testing.wait_all_tasks_blocked()

    preview_data = b'fake_image'

    # First preview
    preview_task.on_preview(preview_data)
    topic_normal.server.send_lossy.assert_called_with(preview_data)
    topic_realtime.server.send_lossy.assert_called_with(preview_data)

    topic_normal.server.send_lossy.reset_mock()
    topic_realtime.server.send_lossy.reset_mock()

    # Second preview immediately (less than recurrence=2s)
    await trio.sleep(0.5)
    preview_task.on_preview(preview_data)
    topic_normal.server.send_lossy.assert_not_called()
    # Realtime should still receive it
    topic_realtime.server.send_lossy.assert_called_with(preview_data)


@pytest.mark.trio
async def test_preview_task_realtime_infinite_trigger(preview_task, worker, autojump_clock):
    """
    Test that 'realtime' mode triggers a new preview request immediately after receiving one.
    """
    topic = MockTopic()

    preview_task.subscribe(topic, 'realtime')
    await trio.testing.wait_all_tasks_blocked()
    assert len(worker.commands) == 1  # Initial trigger

    # Receiving a preview should trigger another one immediately in realtime
    worker.commands = []
    preview_task.on_preview(b'data')
    await trio.testing.wait_all_tasks_blocked()
    assert len(worker.commands) == 1
    assert worker.commands[0].c == 'preview'


@pytest.mark.trio
async def test_preview_task_on_worker_state_handling(preview_task, worker, autojump_clock):
    """
    Test how PreviewTask handles worker status changes (e.g., waiting for worker to start).
    """
    # Override worker status to idle for this test
    worker.status = 'idle'
    topic = MockTopic()

    # Subscribe when worker is idle
    preview_task.subscribe(topic, 'normal')
    await trio.testing.wait_all_tasks_blocked()
    assert len(worker.commands) == 0
    assert preview_task._trigger_on_running is True

    # Worker starts running
    worker.status = 'running'
    preview_task.on_worker_status('running')
    await trio.testing.wait_all_tasks_blocked()
    assert len(worker.commands) == 1
    assert preview_task._trigger_on_running is False

    # Worker stops
    worker.status = 'idle'
    preview_task.on_worker_status('idle')
    await trio.testing.wait_all_tasks_blocked()
    # Task should stop and not send more commands
    worker.commands = []
    await trio.sleep(preview_task.recurrence * 2)
    assert len(worker.commands) == 0


@pytest.mark.trio
async def test_preview_task_speed_change(preview_task, worker, autojump_clock):
    """
    Test changing subscription speed between normal and realtime.
    """
    topic = MockTopic()

    # Start with normal
    preview_task.subscribe(topic, 'normal')
    await trio.testing.wait_all_tasks_blocked()
    assert preview_task._speed == 'normal'
    assert len(worker.commands) == 1

    # Change to realtime (speed increase)
    await trio.sleep(preview_task.drop_threshold)
    worker.commands = []
    preview_task.subscribe(topic, 'realtime')
    await trio.testing.wait_all_tasks_blocked()
    assert preview_task._speed == 'realtime'
    assert len(worker.commands) == 1  # Should trigger again

    # Change back to normal (speed decrease)
    await trio.sleep(preview_task.drop_threshold)
    worker.commands = []
    preview_task.subscribe(topic, 'normal')
    await trio.testing.wait_all_tasks_blocked()
    assert preview_task._speed == 'normal'
    assert len(worker.commands) == 0  # Should NOT trigger


@pytest.mark.trio
async def test_preview_task_multiple_subscribers(preview_task, worker, autojump_clock):
    """
    Test PreviewTask with multiple subscribers.
    """
    topic1 = MockTopic()
    topic2 = MockTopic()

    preview_task.subscribe(topic1, 'normal')
    preview_task.subscribe(topic2, 'normal')
    await trio.testing.wait_all_tasks_blocked()

    # Broadcast
    preview_data = b'data'
    preview_task.on_preview(preview_data)
    topic1.server.send_lossy.assert_called_with(preview_data)
    topic2.server.send_lossy.assert_called_with(preview_data)

    # Unsubscribe one
    preview_task.unsubscribe(topic1)
    topic1.server.send_lossy.reset_mock()
    topic2.server.send_lossy.reset_mock()

    # Still running, and should send to topic2
    await trio.sleep(preview_task.recurrence)
    preview_task.on_preview(preview_data)
    topic1.server.send_lossy.assert_not_called()
    topic2.server.send_lossy.assert_called_with(preview_data)


@pytest.mark.trio
async def test_preview_task_mixed_speeds(preview_task, worker, autojump_clock):
    """
    Test that 'normal' and 'realtime' subscribers receive previews at their respective requested speeds.
    """
    topic_normal = MockTopic()
    topic_realtime = MockTopic()

    preview_task.subscribe(topic_normal, 'normal')
    # Use a small sleep to ensure drop_threshold doesn't block the next trigger if needed,
    # though subscribe internally handles speed change logic.
    await trio.sleep(preview_task.drop_threshold)
    preview_task.subscribe(topic_realtime, 'realtime')
    await trio.testing.wait_all_tasks_blocked()

    # Worker speed should be 'realtime' because one subscriber is 'realtime'
    assert preview_task._speed == 'realtime'

    preview_data = b'data'

    # 1. First preview: both should receive
    preview_task.on_preview(preview_data)
    topic_normal.server.send_lossy.assert_called_once_with(preview_data)
    topic_realtime.server.send_lossy.assert_called_once_with(preview_data)

    topic_normal.server.send_lossy.reset_mock()
    topic_realtime.server.send_lossy.reset_mock()

    # 2. Second preview after 1s (half of recurrence=2s): only realtime should receive
    await trio.sleep(1)
    preview_task.on_preview(preview_data)
    topic_normal.server.send_lossy.assert_not_called()
    topic_realtime.server.send_lossy.assert_called_once_with(preview_data)

    topic_normal.server.send_lossy.reset_mock()
    topic_realtime.server.send_lossy.reset_mock()

    # 3. Third preview after 1.1s (total 2.1s > recurrence): both should receive
    await trio.sleep(1.1)
    preview_task.on_preview(preview_data)
    topic_normal.server.send_lossy.assert_called_once_with(preview_data)
    topic_realtime.server.send_lossy.assert_called_once_with(preview_data)
