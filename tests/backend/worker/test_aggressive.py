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
    WorkerManager.singleton_clear()
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
