"""
WorkerManager 生命周期测试

测试 worker 的启动、停止和状态管理等基本功能
"""
import multiprocessing
import time

import pytest

from alasio.backend.worker.manager import WorkerManager
from alasio.testing.timeout import AssertTimeout
from tests.backend.worker.const import *


# ============================================================================
# Fixtures
# ============================================================================

def assert_worker_gone(workers):
    """
    Assert that no worker process exists for the given workers

    Args:
        workers (list[WorkerState]): List of worker states to check
    """
    target_names = {f"Worker-{w.mod}-{w.config}" for w in workers}
    # Give it a bit of time to cleanup
    for _ in range(20):
        children = multiprocessing.active_children()
        names = {p.name for p in children}
        if not target_names.intersection(names):
            return
        time.sleep(0.05)

    children = multiprocessing.active_children()
    names = {p.name for p in children}
    remaining = target_names.intersection(names)
    assert not remaining, f"Worker processes still alive: {remaining}. Active: {names}"


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


# ============================================================================
# Basic Start Tests
# ============================================================================

class TestWorkerStart:
    """测试 worker 启动功能"""

    def test_start_success(self, manager):
        """测试成功启动 worker"""
        success, msg = manager.worker_start('WorkerTestRun3', 'test_start')

        assert success, f"Failed to start worker: {msg}"
        assert 'test_start' in manager.state

        state = manager.state['test_start']
        assert state.mod == 'WorkerTestRun3'
        assert state.config == 'test_start'

        state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

        assert state.status == 'running'
        assert state.process is not None
        assert state.conn is not None
        assert state.process.is_alive()

    def test_start_duplicate_rejected(self, manager):
        """测试重复启动被拒绝"""
        success1, _ = manager.worker_start('WorkerTestRun3', 'test_dup')
        assert success1

        # 等待启动完成
        state = manager.state['test_dup']
        state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

        success2, msg = manager.worker_start('WorkerTestRun3', 'test_dup')
        assert not success2
        assert 'already running' in msg.lower()

    def test_start_multiple_workers(self, manager):
        """测试启动多个 workers"""
        configs = ['worker_1', 'worker_2', 'worker_3']

        for config in configs:
            success, msg = manager.worker_start('WorkerTestInfinite', config)
            assert success, f"Failed to start {config}: {msg}"

        # 等待所有 workers 启动
        for config in configs:
            state = manager.state[config]
            state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

        # Cleanup
        for config in configs:
            manager.worker_force_kill(config)

    def test_restart_after_completion(self, manager):
        """测试 worker 完成后可以重新启动"""
        # Start and let it complete
        success, _ = manager.worker_start('WorkerTestRun3', 'test_restart')
        assert success

        state = manager.state['test_restart']
        state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

        # 等待完成
        for _ in AssertTimeout(WORKER_COMPLETION_TIMEOUT):
            with _:
                # 快速推进到完成
                if state.conn:
                    state.send_test_continue()
                assert state.status == 'idle'

        # Restart
        success, msg = manager.worker_start('WorkerTestInfinite', 'test_restart')
        assert success, f"Cannot restart: {msg}"
        state = manager.state['test_restart']

        for _ in AssertTimeout(WORKER_STARTUP_TIMEOUT):
            with _:
                assert state.status == 'running'


# ============================================================================
# Worker State Tests
# ============================================================================

class TestWorkerState:
    """测试 worker 状态管理"""

    def test_worker_run_to_completion(self, manager):
        """测试 worker 正常完成"""
        success, _ = manager.worker_start('WorkerTestRun3', 'test_complete')
        assert success

        state = manager.state['test_complete']
        state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

        # 等待完成
        for _ in AssertTimeout(WORKER_COMPLETION_TIMEOUT):
            with _:
                # 快速推进 worker 执行
                if state.conn:
                    state.send_test_continue()
                assert state.status == 'idle'
                assert not state.process or not state.process.is_alive()

        # 验证资源已释放
        for _ in AssertTimeout(1.0):
            with _:
                # Process should be dead
                assert not state.process or not state.process.is_alive()

                # Connection should be closed
                if state.conn:
                    try:
                        state.conn.fileno()
                        assert False, "Connection still open"
                    except (ValueError, OSError):
                        pass  # Expected

    def test_worker_error_state(self, manager):
        """测试 worker 错误状态"""
        success, _ = manager.worker_start('WorkerTestError', 'test_error')
        assert success

        state = manager.state['test_error']
        state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

        # 快速推进到错误发生
        state.send_test_continue()

        # 等待进入错误状态
        for _ in AssertTimeout(2.0):
            with _:
                assert state.status == 'error'
                assert not state.process or not state.process.is_alive()


# ============================================================================
# Stop Methods Tests
# ============================================================================

class TestWorkerStop:
    """测试 worker 停止方法"""

    def test_scheduler_stop(self, manager):
        """测试 scheduler 优雅停止"""
        success, _ = manager.worker_start('WorkerTestScheduler', 'test_sched_stop')
        assert success

        state = manager.state['test_sched_stop']
        state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

        # Request scheduler stop
        success, msg = manager.worker_scheduler_stop('test_sched_stop')
        assert success, f"Failed to request stop: {msg}"
        assert state.status == 'scheduler-stopping'

        # 等待停止完成
        for _ in AssertTimeout(WORKER_STOP_TIMEOUT):
            with _:
                # 快速推进 worker 响应停止请求
                if state.conn:
                    state.send_test_continue()
                assert state.status == 'idle'
                assert not state.process or not state.process.is_alive()

    def test_scheduler_stop_respects_worker_state(self, manager):
        """测试 scheduler stop 时 worker 的状态响应"""
        success, _ = manager.worker_start('WorkerTestScheduler', 'test_sched_resp')
        assert success

        state = manager.state['test_sched_resp']
        state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

        # Request stop
        manager.worker_scheduler_stop('test_sched_resp')
        assert state.status == 'scheduler-stopping'

        # 等待完成
        for _ in AssertTimeout(WORKER_STOP_TIMEOUT):
            with _:
                # 快速推进
                if state.conn:
                    state.send_test_continue()
                assert state.status == 'idle'

    def test_worker_kill(self, manager):
        """测试 killing a worker"""
        success, _ = manager.worker_start('WorkerTestInfinite', 'test_kill')
        assert success

        state = manager.state['test_kill']
        state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

        # Kill worker
        success, msg = manager.worker_kill('test_kill')
        assert success, f"Failed to kill worker: {msg}"
        assert state.status == 'killing'

        # 等待清理
        for _ in AssertTimeout(WORKER_STOP_TIMEOUT):
            with _:
                assert state.status in ['idle', 'error']
                assert not state.process or not state.process.is_alive()

    def test_worker_force_kill(self, manager):
        """测试 force killing a worker"""
        success, _ = manager.worker_start('WorkerTestInfinite', 'test_force')
        assert success

        state = manager.state['test_force']
        state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

        # Force kill
        success, msg = manager.worker_force_kill('test_force')
        assert success, f"Failed to force kill: {msg}"

        # 等待清理（force kill 应该很快）
        for _ in AssertTimeout(1.0):
            with _:
                # State should be removed from manager
                assert 'test_force' not in manager.state

    def test_stop_nonexistent_worker(self, manager):
        """测试停止不存在的 worker"""
        success, msg = manager.worker_scheduler_stop('nonexistent')
        assert not success
        assert 'no such worker' in msg.lower()

        success, msg = manager.worker_kill('nonexistent')
        assert not success
        assert 'no such worker' in msg.lower()

        success, msg = manager.worker_force_kill('nonexistent')
        assert not success
        assert 'no such worker' in msg.lower()

    def test_stop_idle_worker(self, manager):
        """测试停止已经空闲的 worker"""
        # Start and let it complete
        success, _ = manager.worker_start('WorkerTestRun3', 'test_idle_stop')
        assert success

        state = manager.state['test_idle_stop']
        state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

        # 等待完成
        for _ in AssertTimeout(WORKER_COMPLETION_TIMEOUT):
            with _:
                # 快速推进到完成
                if state.conn:
                    state.send_test_continue()
                assert state.status == 'idle'

        # Try to stop idle worker
        success, msg = manager.worker_scheduler_stop('test_idle_stop')
        assert not success
        msg = msg.lower()
        assert 'not running' in msg or 'no such worker' in msg

    # Mark skip because worker_kill is so fast that process is dead before force-kill
    @pytest.mark.skip
    def test_stop_sequence(self, manager):
        """测试停止命令的优先级"""
        success, _ = manager.worker_start('WorkerTestInfinite', 'test_seq')
        assert success

        state = manager.state['test_seq']
        state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

        # Request scheduler stop
        success1, _ = manager.worker_scheduler_stop('test_seq')
        assert success1
        assert state.status == 'scheduler-stopping'

        # Try to request kill (should fail, already stopping)
        success2, msg = manager.worker_kill('test_seq')
        assert not success2
        assert 'already stopping' in msg.lower()

        # But force kill should work
        success3, _ = manager.worker_force_kill('test_seq')
        assert success3


# ============================================================================
# Resource Management Tests
# ============================================================================

class TestResourceManagement:
    """测试资源管理"""

    def test_no_process_leak_on_normal_completion(self, manager):
        """测试正常完成不泄漏进程"""

        # Start and let complete
        for i in range(3):
            success, _ = manager.worker_start('WorkerTestRun3', f'test_leak_{i}')
            assert success

            state = manager.state[f'test_leak_{i}']
            state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

            # 快速推进到完成
            for _ in range(5):
                state.send_test_continue()
                time.sleep(0.02)

        # 等待所有完成
        for _ in AssertTimeout(WORKER_COMPLETION_TIMEOUT):
            with _:
                for i in range(3):
                    state = manager.state.get(f'test_leak_{i}', None)
                    assert not state or state.status == 'idle'

        # Check no process leak
        assert_worker_gone(list(manager.state.values()))

    def test_no_state_leak_on_force_kill(self, manager):
        """测试 force kill 不泄漏状态"""

        # Start workers
        for i in range(3):
            success, _ = manager.worker_start('WorkerTestInfinite', f'test_state_{i}')
            assert success

        # 等待所有启动
        for i in range(3):
            state = manager.state[f'test_state_{i}']
            state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

        workers = list(manager.state.values())

        # Force kill all
        for i in range(3):
            manager.worker_force_kill(f'test_state_{i}')

        # 等待清理
        for _ in AssertTimeout(2.0):
            with _:
                # All states should be removed
                assert len(manager.state) == 0

        # Check no process leak
        assert_worker_gone(workers)

    def test_repeated_start_stop_no_leak(self, manager):
        """测试重复启停不泄漏"""
        for cycle in range(3):
            success, _ = manager.worker_start('WorkerTestRun3', 'test_cycle')
            assert success, f"Failed at cycle {cycle}"

            state = manager.state['test_cycle']
            state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

            if cycle % 2 == 0:
                manager.worker_force_kill('test_cycle')
                time.sleep(0.2)
            else:
                # 快速推进到完成
                for _ in range(5):
                    if state.conn:
                        state.send_test_continue()
                    time.sleep(0.02)

                for _ in AssertTimeout(WORKER_COMPLETION_TIMEOUT):
                    with _:
                        assert state.status == 'idle'

        # Verify no leaks
        assert len(manager.state) <= 1, "State leak after repeated cycles"
        assert_worker_gone([state])


# ============================================================================
# Manager Lifecycle Tests
# ============================================================================

class TestManagerLifecycle:
    """测试 Manager 生命周期"""

    def test_manager_close_with_running_workers(self):
        """测试关闭运行中的 manager"""
        manager = WorkerManager()

        # Start workers
        for i in range(3):
            success, _ = manager.worker_start('WorkerTestInfinite', f'test_close_{i}')
            assert success

        # 等待所有启动
        for i in range(3):
            state = manager.state[f'test_close_{i}']
            state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

        workers = list(manager.state.values())

        # Close manager
        manager.close()

        # 等待所有停止
        for _ in AssertTimeout(3.0):
            with _:
                for i in range(3):
                    if f'test_close_{i}' in manager.state:
                        state = manager.state[f'test_close_{i}']
                        assert not state.process or not state.process.is_alive()

        # Check no process leak
        assert_worker_gone(workers)

    def test_manager_close_idempotent(self):
        """测试 close 可以多次调用"""
        manager = WorkerManager()

        success, _ = manager.worker_start('WorkerTestRun3', 'test_idempotent')
        assert success

        state = manager.state['test_idempotent']
        state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

        # Close multiple times should not error
        manager.close()
        manager.close()
        manager.close()

    def test_manager_recreation_after_close(self):
        """测试 close 后可以重新创建"""
        manager1 = WorkerManager()
        manager1.worker_start('WorkerTestRun3', 'test_1')

        state = manager1.state['test_1']
        state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

        manager1.close()
        time.sleep(0.3)

        # Create new manager
        manager2 = WorkerManager()
        success, _ = manager2.worker_start('WorkerTestRun3', 'test_2')
        assert success, "Cannot create worker after manager recreation"

        manager2.close()
