"""
WorkerManager IPC 测试

测试 worker 与 manager 之间的通信功能
"""
import pytest

from alasio.backend.worker.manager import WorkerManager
from alasio.testing.timeout import AssertTimeout
from tests.backend.worker.const import *


@pytest.fixture
def manager():
    """Create a fresh WorkerManager instance"""
    WorkerManager.singleton_clear_all()
    mgr = WorkerManager()
    yield mgr
    try:
        mgr.close()
    except Exception as e:
        print(f"Warning: Error during cleanup: {e}")


class TestWorkerIPC:
    """测试 Worker IPC 通信"""

    def test_send_events(self, manager):
        """测试子进程发送 config event"""
        # Mock handle_config_event to capture events
        received_events = []

        def mock_handler(event):
            received_events.append(event)

        # 替换实例方法
        manager.on_config_event = mock_handler

        # 启动 worker
        success, msg = manager.worker_start('WorkerTestSendEvents', 'test_events')
        assert success, f"Failed to start worker: {msg}"

        state = manager.state['test_events']
        state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

        # 等待事件接收
        # worker_test_send_events 会发送:
        # 1. Log: 'worker started'
        # 2. CustomEvent: 'test_value_1'
        # 3. CustomEvent: 'test_value_2'
        # 4. DataUpdate: ...

        target_events_count = 4  # Log + 2 Custom + 1 DataUpdate

        for _ in AssertTimeout(WORKER_COMPLETION_TIMEOUT):
            with _:
                if len(received_events) >= target_events_count:
                    break
                # 加速 worker 执行
                state.send_test_continue()

                # 验证接收到的事件
                # 过滤出 CustomEvent
                custom_events = [e for e in received_events if e.t == 'CustomEvent']
                assert len(custom_events) >= 2
                assert custom_events[0].v == 'test_value_1'
                assert custom_events[1].v == 'test_value_2'
                # 验证 event.c 被正确覆盖为 config name
                assert custom_events[0].c == 'test_events'

                # 过滤出 DataUpdate
                data_updates = [e for e in received_events if e.t == 'DataUpdate']
                assert len(data_updates) >= 1
                assert data_updates[0].v == {'data': 123}
                assert data_updates[0].c == 'test_events'

    def test_scheduler_status_change(self, manager):
        """测试子进程改变 worker status"""
        # 启动 worker
        success, msg = manager.worker_start('WorkerTestScheduler', 'test_status')
        assert success, f"Failed to start worker: {msg}"

        state = manager.state['test_status']
        state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

        # 初始状态应该是 running
        assert state.status == 'running'

        # worker_test_scheduler 逻辑:
        # n=0: running
        # n=1: running
        # n=2: scheduler-waiting
        # ...

        # 推进 n=0 -> n=1
        # 推进 n=1 -> n=2 (scheduler-waiting)
        # 我们使用循环推进直到状态改变，因为 IPC 可能有延迟，或者 worker 启动时的 n 值可能不完全确定（虽然代码看起来是确定的）

        for _ in AssertTimeout(WORKER_STATE_TIMEOUT):
            with _:
                state.send_test_continue()
                assert state.status == 'scheduler-waiting', (f"Worker status did not change to scheduler-waiting. "
                                                             f"Current: {state.status}")

        # 推进 n=2 -> n=3 (running)
        # 等待状态变为 running
        for _ in AssertTimeout(WORKER_STATE_TIMEOUT):
            with _:
                state.send_test_continue()
                assert state.status == 'running', (f"Worker status did not change back to running. Current: "
                                                   f"{state.status}")

        # 清理
        manager.worker_kill('test_status')

    def test_standalone_worker(self):
        """测试 worker 在没有后端连接的情况下运行"""
        from alasio.backend.worker.bridge import worker_test_run3

        worker_test_run3()
