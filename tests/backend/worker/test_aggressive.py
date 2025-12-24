"""
WorkerManager 鲁棒性测试

测试异常情况下的恢复能力
"""
import time
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


class TestWorkerResilience:
    """测试 WorkerManager 的鲁棒性"""

    def test_kill_process_directly(self, manager):
        """测试直接杀掉 worker 进程"""
        success, _ = manager.worker_start('WorkerTestInfinite', 'test_kill_direct')
        assert success

        state = manager.state['test_kill_direct']
        state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

        # 直接杀掉进程
        state.process.terminate()

        # 等待 Manager 发现并处理
        # Manager 的 _io_loop 应该会因为 pipe EOF 而触发 _handle_disconnect
        for _ in AssertTimeout(WORKER_STOP_TIMEOUT):
            with _:
                if state.status == 'error':
                    break
                time.sleep(0.1)

        assert state.status == 'error'
        assert not state.process or not state.process.is_alive()

    def test_close_worker_pipe_directly(self, manager):
        """测试直接关闭 worker pipe"""
        success, _ = manager.worker_start('WorkerTestInfinite', 'test_pipe_close')
        assert success

        state = manager.state['test_pipe_close']
        state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

        # 直接关闭 pipe
        state.conn.close()

        # Manager 应该检测到 pipe 关闭/错误，并清理 worker
        for _ in AssertTimeout(WORKER_STOP_TIMEOUT):
            with _:
                assert state.status == 'error'

        # 进程也应该被 manager 杀掉
        assert not state.process or not state.process.is_alive()

    def test_close_notify_r_before_start(self, manager):
        """测试启动前关闭 notify_r"""
        assert manager._io_thread.is_alive()

        old_r = manager._notify_r
        manager._notify_r.close()

        # Start worker should trigger rebuild
        success, _ = manager.worker_start('WorkerTestRun3', 'test_notify_r_before')
        assert success

        # Verify rebuild
        assert manager._notify_r != old_r
        try:
            manager._notify_r.fileno()
        except (ValueError, OSError):
            pytest.fail("Notify pipe read end is invalid")

        # Verify worker started
        state = manager.state['test_notify_r_before']
        state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)
        assert state.status == 'running'

    def test_close_notify_r_after_start(self, manager):
        """测试启动后关闭 notify_r"""
        success, _ = manager.worker_start('WorkerTestInfinite', 'test_notify_r_after_1')
        assert success
        state1 = manager.state['test_notify_r_after_1']
        state1.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

        old_r = manager._notify_r
        manager._notify_r.close()

        # Start another worker to trigger activity
        success, _ = manager.worker_start('WorkerTestRun3', 'test_notify_r_after_2')
        assert success

        # Verify rebuild
        assert manager._notify_r != old_r
        try:
            manager._notify_r.fileno()
        except (ValueError, OSError):
            pytest.fail("Notify pipe read end is invalid")

        # Verify both workers running
        state2 = manager.state['test_notify_r_after_2']
        state2.wait_running(timeout=WORKER_STARTUP_TIMEOUT)
        assert state2.status == 'running'
        assert state1.status == 'running'

    def test_close_notify_w_before_start(self, manager):
        """测试启动前关闭 notify_w"""
        assert manager._io_thread.is_alive()

        old_w = manager._notify_w
        manager._notify_w.close()

        # Start worker should trigger rebuild
        success, _ = manager.worker_start('WorkerTestRun3', 'test_notify_w_before')
        assert success

        # Verify rebuild
        assert manager._notify_w != old_w
        try:
            manager._notify_w.fileno()
        except (ValueError, OSError):
            pytest.fail("Notify pipe write end is invalid")

        # Verify worker started
        state = manager.state['test_notify_w_before']
        state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)
        assert state.status == 'running'

    def test_close_notify_w_after_start(self, manager):
        """测试启动后关闭 notify_w"""
        success, _ = manager.worker_start('WorkerTestInfinite', 'test_notify_w_after_1')
        assert success
        state1 = manager.state['test_notify_w_after_1']
        state1.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

        old_w = manager._notify_w
        manager._notify_w.close()

        # Start another worker to trigger activity
        success, _ = manager.worker_start('WorkerTestRun3', 'test_notify_w_after_2')
        assert success

        # Verify rebuild
        assert manager._notify_w != old_w
        try:
            manager._notify_w.fileno()
        except (ValueError, OSError):
            pytest.fail("Notify pipe write end is invalid")

        # Verify both workers running
        state2 = manager.state['test_notify_w_after_2']
        state2.wait_running(timeout=WORKER_STARTUP_TIMEOUT)
        assert state2.status == 'running'
        assert state1.status == 'running'
