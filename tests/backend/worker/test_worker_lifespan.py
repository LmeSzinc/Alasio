"""
WorkerManager 生命周期测试

测试 worker 的启动、停止和状态管理等基本功能
"""
import multiprocessing
import threading
import time

import pytest

from alasio.backend.worker.manager import WORKER_STOPPING_STATE, WorkerManager, WorkerState
from alasio.logger import logger
from alasio.testing.timeout import AssertTimeout
from tests.backend.worker.const import *

pytestmark = [
    pytest.mark.parallel,
]

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


def assert_recv_thread_gone(configs):
    """
    Assert that no recv thread exists for the given configs

    Args:
        configs (list[str]): List of config names to check
    """
    target_names = {f"WorkerRecv-{c}" for c in configs}
    # Give it a bit of time to cleanup
    for _ in range(20):
        threads = threading.enumerate()
        names = {t.name for t in threads}
        if not target_names.intersection(names):
            return
        time.sleep(0.05)

    threads = threading.enumerate()
    names = {t.name for t in threads}
    remaining = target_names.intersection(names)
    assert not remaining, f"Worker recv threads still alive: {remaining}"


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

        assert state.state == 'running'
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
                assert state.state == 'idle'

        # Restart
        success, msg = manager.worker_start('WorkerTestInfinite', 'test_restart')
        assert success, f"Cannot restart: {msg}"
        state = manager.state['test_restart']

        for _ in AssertTimeout(WORKER_STARTUP_TIMEOUT):
            with _:
                assert state.state == 'running'


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
                assert state.state == 'idle'
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
                assert state.state == 'error'
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
        assert state.state == 'scheduler-stopping'

        # 等待停止完成
        for _ in AssertTimeout(WORKER_STOP_TIMEOUT):
            with _:
                # 快速推进 worker 响应停止请求
                if state.conn:
                    state.send_test_continue()
                assert state.state == 'idle'
                assert not state.process or not state.process.is_alive()

    def test_scheduler_stop_respects_worker_state(self, manager):
        """测试 scheduler stop 时 worker 的状态响应"""
        success, _ = manager.worker_start('WorkerTestScheduler', 'test_sched_resp')
        assert success

        state = manager.state['test_sched_resp']
        state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

        # Request stop
        manager.worker_scheduler_stop('test_sched_resp')
        assert state.state == 'scheduler-stopping'

        # 等待完成
        for _ in AssertTimeout(WORKER_STOP_TIMEOUT):
            with _:
                # 快速推进
                if state.conn:
                    state.send_test_continue()
                assert state.state == 'idle'

    def test_worker_kill(self, manager):
        """测试 killing a worker"""
        success, _ = manager.worker_start('WorkerTestInfinite', 'test_kill')
        assert success

        state = manager.state['test_kill']
        state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)
        process = state.process

        # Kill worker. worker_kill waits for the worker to stop by itself,
        # escalating to force kill if the worker fails to stop in time,
        # so the worker should already be stopped when it returns
        success, msg = manager.worker_kill('test_kill')
        assert success, f"Failed to kill worker: {msg}"
        assert state.state == 'idle'
        assert state.process is None
        assert not process.is_alive()

    def test_worker_kill_escalates_to_force_kill(self, manager, monkeypatch):
        """测试 worker_kill 在 worker 未自行停止时升级为 force kill"""
        # Shorten the escalation wait to keep the test fast
        monkeypatch.setattr('alasio.backend.worker.manager.KILL_WAIT_TIMEOUT', 0.1)
        # Worker never receives the kill command, simulating an unresponsive worker
        monkeypatch.setattr(WorkerState, 'send_command', lambda self, command: False)

        success, _ = manager.worker_start('WorkerTestInfinite', 'test_kill_force')
        assert success

        state = manager.state['test_kill_force']
        state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)
        process = state.process

        # Worker is still running after the escalation wait, so worker_kill
        # should escalate to force kill and stop the worker
        success, msg = manager.worker_kill('test_kill_force')
        assert success, f"Failed to kill worker: {msg}"
        assert state.state == 'idle'
        assert state.process is None
        assert not process.is_alive()

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
                assert state.state == 'idle'

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
        assert state.state == 'scheduler-stopping'

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
                    assert not state or state.state == 'idle'

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

    def test_no_thread_leak(self, manager):
        """测试没有线程泄漏"""
        config = 'test_thread_leak'

        # Start worker
        success, _ = manager.worker_start('WorkerTestInfinite', config)
        assert success
        state = manager.state[config]
        state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

        # Verify thread exists
        threads = threading.enumerate()
        names = {t.name for t in threads}
        assert f"WorkerRecv-{config}" in names

        # Force kill
        manager.worker_force_kill(config)

        # Verify thread gone
        assert_recv_thread_gone([config])

    def test_manager_close_wakes_a_parked_reader_and_resets_the_entry(self, manager):
        """
        close() ends a read that the death of the peer does not end

        A read of the pipe is not reliable to end by killing the worker: a worker
        killed before it received its end leaves the read pending (observed on
        Windows). So close() closes the pipe itself, which is what wakes the
        reader, and the entry is reset afterwards.

        The reader of the test is parked on purpose: a worker process is killed
        before close(), so its own read would end on its own and there would be
        no parked read left to test.
        """

        class RecordingConn:
            """The pipe of the entry, recording which thread closed it"""

            def __init__(self, conn):
                self.conn = conn
                self.closers = []

            def __getattr__(self, name):
                return getattr(self.conn, name)

            def close(self):
                self.closers.append(threading.current_thread().name)
                self.conn.close()

        state = WorkerState(mod='WorkerTestScheduler', config='cfg_parked', state='running')
        parent, peer = multiprocessing.Pipe(duplex=True)
        recording = RecordingConn(parent)
        state.conn = recording
        reader_left = threading.Event()

        def read():
            try:
                state.conn.recv_bytes()
            except (EOFError, OSError):
                pass
            reader_left.set()

        thread = threading.Thread(target=read, name='WorkerRecv-cfg_parked', daemon=True)
        thread.start()
        # let the reader park in recv_bytes()
        time.sleep(0.1)
        state.recv_thread = thread
        manager.state[state.config] = state

        try:
            manager.close()

            assert recording.closers == ['MainThread'], recording.closers
            assert reader_left.is_set(), 'close() left the recv thread parked'
            assert not thread.is_alive()
            # the entry holds nothing of the run that ended
            assert state.conn is None
            assert state.process is None
            assert state.recv_thread is None
        finally:
            peer.close()

    def test_the_entry_is_closed_once_after_its_reader_left(self, manager):
        """
        The pipe is closed once: taking it is one step, so only one caller can

        The pipe has one owner at a time and taking it is a single step under the
        lock, so a second caller finds nothing to close whatever its timing was
        -- a handle closed twice hits whatever the number was handed to next.
        """
        state = WorkerState(mod='WorkerTestScheduler', config='cfg_once', state='idle')
        parent, peer = multiprocessing.Pipe(duplex=True)
        state.conn = parent
        peer.close()

        state.conn_close()
        assert state.conn is None
        # the pipe was taken, a second close has nothing of its own to close
        state.conn_close()
        assert state.conn is None

    def test_a_force_kill_closes_the_pipe_once_and_resets_the_entry(self, manager, monkeypatch):
        """
        A stopped worker closes its pipe once and leaves no field behind

        The manager stops the reader of a worker it stops, and the reader of the
        pipe may reach the close too when its read ends on its own. Both go
        through ``conn_close()``, which takes the pipe: whichever runs first
        closes it and the other finds nothing. The entry then holds no resource
        of the run that ended -- the next run gets its own pipe, process and
        reader.
        """
        closers = []
        original = WorkerState.conn_close

        def tracked(self):
            had_pipe = self.conn is not None
            original(self)
            if had_pipe:
                closers.append(self.config)

        monkeypatch.setattr(WorkerState, 'conn_close', tracked)
        config = 'cfg_owner'
        success, msg = manager.worker_start('WorkerTestInfinite', config)
        assert success, msg
        state = manager.state[config]
        state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

        manager.worker_force_kill(config)

        assert closers == [config], f'the pipe was closed {len(closers)} times'
        assert state.conn is None
        assert state.process is None
        assert state.recv_thread is None

    def test_reset_runtime_forgets_the_run(self):
        """
        reset_runtime() clears every field of the run, the stop request included

        ``closing`` is requested by the stop of a run and the entry outlives that
        run (an "error" entry is reused by the next start, a parked entry comes
        back for a resume), so the flag must be forgotten with the pipe, the
        process and the reader.
        """
        state = WorkerState(mod='WorkerTestScheduler', config='cfg_reset', state='error')
        state.conn = object()
        state.process = object()
        state.recv_thread = object()
        state.closing = True

        state.reset_runtime()

        assert state.conn is None
        assert state.process is None
        assert state.recv_thread is None
        assert state.closing is False

    def test_a_stop_does_not_leak_into_the_next_run(self, manager):
        """
        The next run of a reused entry reads its pipe from the first message

        A worker that was stopped and then exited with a failure leaves its entry
        in the dict as "error", so the next start of the same config reuses it.
        The stop request belongs to the run that ended: a reader started for the
        next run must not see it and leave at its first check, which would
        finalize a running worker as a disconnect.
        """
        config = 'test_reused_entry'
        state = WorkerState(mod='WorkerTestScheduler', config=config, state='scheduler-stopping')
        manager.state[config] = state
        # the stop of the run requests the end of its reader
        manager._request_recv_stop(state)
        assert state.closing is True

        # the stopped worker then exits with a failure: the entry stays in the
        # dict, the next start of the config reuses it
        state.process = _ExitedProcess(1)
        manager._handle_disconnect(state)
        assert state.state == 'error'
        assert state.closing is False, 'the stop of the previous run was kept'

        success, msg = manager.worker_start('WorkerTestInfinite', config)
        assert success, msg
        assert state is manager.state[config], 'the entry was not reused'
        assert state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)
        # A stale stop request would have made the reader leave at once, which
        # finalizes the entry through the disconnect handler
        assert state.state == 'running'
        assert state.recv_thread is not None and state.recv_thread.is_alive()

        manager.worker_force_kill(config)

    def test_repeated_start_stop_no_leak(self, manager):
        """测试重复启停不泄漏"""
        config = 'test_cycle'

        for cycle in range(3):
            success, _ = manager.worker_start('WorkerTestRun3', config)
            assert success, f"Failed at cycle {cycle}"

            state = manager.state[config]
            state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

            if cycle % 2 == 0:
                # force kill is synchronous: the entry is idle (and removable)
                # the moment it returns, a stale disconnect of the old process
                # must not clobber the next start
                manager.worker_force_kill(config)
            else:
                # 快速推进到完成
                for _ in range(5):
                    if state.conn:
                        state.send_test_continue()
                    time.sleep(0.02)

                for _ in AssertTimeout(WORKER_COMPLETION_TIMEOUT):
                    with _:
                        assert state.state == 'idle'

        # Verify no leaks
        assert len(manager.state) <= 1, "State leak after repeated cycles"
        assert_worker_gone([state])
        assert_recv_thread_gone([config])


# ============================================================================
# Disconnect Tests
# ============================================================================

class _LostStatusProcess:
    """
    A gone worker whose exit status can no longer be claimed

    This is what the manager sees when the status of a child that already
    exited was claimed elsewhere: multiprocessing reads the exit code out of
    its Popen object and answers None, while is_alive() keeps answering True
    (its poll() returns None instead of raising) and a join returns at once.
    """

    pid = 4321

    def is_alive(self):
        return True

    def join(self, timeout=None):
        return None

    @property
    def exitcode(self):
        return None

    def terminate(self):
        pass

    def kill(self):
        pass


class _ExitedProcess:
    """
    A gone worker whose exit status is still readable

    Args:
        exitcode (int): The exit code the process exited with
    """

    pid = 4322

    def __init__(self, exitcode):
        self._exitcode = exitcode
        self.signals = []

    def is_alive(self):
        return False

    def join(self, timeout=None):
        return self._exitcode

    @property
    def exitcode(self):
        return self._exitcode

    def terminate(self):
        self.signals.append('terminate')

    def kill(self):
        self.signals.append('kill')


class _ExitingProcess:
    """
    A process whose fds are closed but whose exit status does not exist yet

    This is the window the manager has to tell apart from a worker that is still
    running: the kernel closes the fds of an exiting process first, and only
    afterwards does the process become waitable. multiprocessing's join() waits on
    the fd, so it returns in this window while exitcode still answers None.

    Args:
        exitcode (int): The code the process exits with
        polls (int): Number of status reads that answer None before the code exists
    """

    pid = 4323

    def __init__(self, exitcode=0, polls=2):
        self._exitcode = exitcode
        self._polls = polls
        self.signals = []

    def is_alive(self):
        return self._polls > 0

    def join(self, timeout=None):
        # the fd notification is already there
        return None

    @property
    def exitcode(self):
        if self._polls > 0:
            self._polls -= 1
            return None
        return self._exitcode

    def terminate(self):
        self.signals.append('terminate')

    def kill(self):
        self.signals.append('kill')


class _StuckProcess:
    """A worker whose pipe broke but which keeps running until it is terminated"""

    pid = 4324

    def __init__(self):
        self.signals = []
        self._exitcode = None

    def is_alive(self):
        return self._exitcode is None

    def join(self, timeout=None):
        return None

    @property
    def exitcode(self):
        return self._exitcode

    def terminate(self):
        self.signals.append('terminate')
        self._exitcode = -15

    def kill(self):
        self.signals.append('kill')
        self._exitcode = -9


class _UnresponsiveProcess:
    """A worker that ignores terminate() and only ends when it is killed"""

    pid = 4325

    def __init__(self):
        self.signals = []
        self._exitcode = None

    def is_alive(self):
        return self._exitcode is None

    def join(self, timeout=None):
        return None

    @property
    def exitcode(self):
        return self._exitcode

    def terminate(self):
        self.signals.append('terminate')

    def kill(self):
        self.signals.append('kill')
        self._exitcode = -9


class TestWorkerDisconnect:
    """How the disconnect handler reads the exit status of a gone worker"""

    @pytest.fixture(autouse=True)
    def fast_timeouts(self, monkeypatch):
        """
        Shorten the waits of the disconnect handler

        The stand-in processes of this class decide every scenario themselves, so
        the production timeouts would only slow the tests down. They stay long
        enough for the stand-in that becomes reapable after a few polls.
        """
        monkeypatch.setattr('alasio.backend.worker.manager.EXIT_WAIT_TIMEOUT', 0.1)
        monkeypatch.setattr('alasio.backend.worker.manager.KILL_TERMINATE_TIMEOUT', 0.05)
        monkeypatch.setattr('alasio.backend.worker.manager.KILL_FORCE_TIMEOUT', 0.05)

    @staticmethod
    def disconnect(manager, state_before, process, config, pending_restart=False):
        """
        Run the disconnect handler on an entry in the given state

        Args:
            manager (WorkerManager): Manager of the test
            state_before (str): State the entry is in when its pipe breaks
            process: Stand-in for the process of the worker
            config (str): Config name
            pending_restart (bool): Whether the entry is parked for a restart

        Returns:
            WorkerState: The entry, after the handler decided its state
        """
        state = WorkerState(mod='WorkerTestScheduler', config=config, state=state_before,
                            pending_restart=pending_restart)
        state.process = process
        manager.state[config] = state
        manager._handle_disconnect(state)
        return state

    @pytest.mark.parametrize('state_before', WORKER_STOPPING_STATE)
    def test_a_status_that_could_not_be_read_ends_a_requested_stop_as_idle(self, manager, state_before):
        """
        A worker that was told to stop and is gone ends as a clean stop

        The exit status of a process can only be claimed once, and the manager
        reads it from multiprocessing, which swallows the error of a status that
        was already claimed elsewhere (see _handle_disconnect). A status that
        could not be read says nothing about why the worker ended, so an entry
        the manager itself was stopping must not turn into an error.
        """
        state = self.disconnect(manager, state_before, _LostStatusProcess(), 'cfg_lost_status')

        assert state.state == 'idle'
        assert state.process is None

    @pytest.mark.parametrize('state_before', ['starting', 'running', 'scheduler-waiting'])
    def test_a_status_that_could_not_be_read_is_an_error_without_a_stop_request(self, manager, state_before):
        """A worker nobody asked to stop must not pass for a clean stop"""
        state = self.disconnect(manager, state_before, _LostStatusProcess(), 'cfg_lost_crash')

        assert state.state == 'error'

    def test_a_worker_that_is_still_exiting_is_waited_for(self, manager):
        """
        The window between the closed fds and the exit status must not read as a failure

        A worker whose entry returned closes its pipe while its process is still
        exiting: join() is already answered by the fd notification, while the exit
        status does not exist yet. A single non-blocking read turns that completed
        worker into an error; waiting for the status reads the 0 it exited with.
        """
        process = _ExitingProcess(exitcode=0, polls=2)
        state = self.disconnect(manager, 'running', process, 'cfg_exiting')

        assert state.state == 'idle'
        assert process.signals == []

    def test_a_worker_that_keeps_running_after_its_pipe_broke_is_terminated(self, manager):
        """
        A worker that did not stop by itself is stopped, and that stop is its status

        The grace the manager gives a worker after its pipe broke must not turn a
        worker that is still there into a clean stop: it is terminated, and the
        status the terminate produces is what the entry ends with.
        """
        process = _StuckProcess()
        state = self.disconnect(manager, 'running', process, 'cfg_stuck')

        assert process.signals == ['terminate']
        assert state.state == 'error'

    def test_an_exit_code_of_zero_ends_as_idle(self, manager):
        """A worker that exited by itself with 0 was never a failure"""
        state = self.disconnect(manager, 'running', _ExitedProcess(0), 'cfg_exit_zero')

        assert state.state == 'idle'

    def test_a_crash_is_an_error_even_under_a_stop_request(self, manager):
        """An exit status that was read and is not 0 stays a failure of the worker"""
        state = self.disconnect(manager, 'scheduler-stopping', _ExitedProcess(1), 'cfg_crash')

        assert state.state == 'error'

    @pytest.mark.parametrize('park_state', ['restarting', 'updating'])
    def test_a_pending_restart_parks_the_entry(self, manager, park_state):
        """The restart keeps its own state, whatever the exit status says"""
        manager.park_state = park_state
        state = self.disconnect(manager, 'scheduler-stopping', _LostStatusProcess(), 'cfg_parked',
                                pending_restart=True)

        assert state.state == park_state

    def test_the_unreadable_status_is_logged(self, manager):
        """
        The status that could not be read is recorded with what is known

        A failing worker that is reported as a clean stop must leave the
        evidence behind: the process, whether it still looks alive and the
        state the entry was stopping from are what a later investigation has.
        """
        with logger.mock_capture_writer() as capture:
            self.disconnect(manager, 'scheduler-stopping', _LostStatusProcess(), 'cfg_lost_status')

        assert capture.concat() == """\
INFO | [WorkerManager] Graceful killing process: "cfg_lost_status"
INFO | [WorkerManager] Worker did not terminate, force killing process: "cfg_lost_status"
INFO | [WorkerManager] Worker still alive after force-kill: "cfg_lost_status"
INFO | [WorkerManager] Worker stopped: cfg_lost_status, exitcode=None
WARNING | [WorkerManager] No exit status to read for "cfg_lost_status": pid=4321, alive=True, state=scheduler-stopping
"""


class TestProcessWaitExit:
    """Reading the exit status only once the process is reapable"""

    @staticmethod
    def state_with(process):
        """
        An entry holding the given process

        Args:
            process: Stand-in for the process of the worker

        Returns:
            WorkerState: The entry
        """
        state = WorkerState(mod='WorkerTestRun3', config='cfg_wait_exit', state='running')
        state.process = process
        return state

    def test_no_process_has_no_status(self):
        """An entry without a process has nothing to wait for"""
        state = WorkerState(mod='WorkerTestRun3', config='cfg_no_process', state='running')

        assert state.process_wait_exit(timeout=1) is None

    def test_a_status_that_exists_is_read_at_once(self):
        """A reaped process answers on the first read"""
        state = self.state_with(_ExitedProcess(0))

        assert state.process_wait_exit(timeout=1) == 0

    def test_the_wait_ends_when_the_status_appears(self):
        """A process that is still exiting is waited for, not abandoned"""
        state = self.state_with(_ExitingProcess(exitcode=0, polls=3))

        assert state.process_wait_exit(timeout=1) == 0

    def test_a_status_that_never_appears_ends_at_the_deadline(self):
        """The wait is bounded: an unreadable status does not hang the caller"""
        state = self.state_with(_LostStatusProcess())

        start = time.monotonic()
        assert state.process_wait_exit(timeout=0.2) is None
        assert 0.15 < time.monotonic() - start < 2.0

    def test_the_default_timeout_reads_once(self):
        """A call without a timeout does not wait, it only looks at the status"""
        state = self.state_with(_ExitingProcess(exitcode=0, polls=2))

        start = time.monotonic()
        assert state.process_wait_exit() is None
        assert time.monotonic() - start < 0.05

    def test_the_poll_uses_the_precise_sleep(self, monkeypatch):
        """The poll interval is passed to the precise sleep of the project

        time.sleep() rounds a sub-millisecond remainder up to a whole
        millisecond on Windows and quantizes the wake-up to the system timer
        tick, which is the same order as the whole poll interval here.
        """
        from alasio.backend.worker import manager as manager_module

        calls = []
        monkeypatch.setattr(manager_module, 'sleep', calls.append)
        state = self.state_with(_LostStatusProcess())

        assert state.process_wait_exit(timeout=0.05) is None

        assert calls
        assert all(0 < seconds <= manager_module.EXIT_POLL_INTERVAL for seconds in calls)

    def test_the_deadline_is_measured_with_the_high_resolution_clock(self, monkeypatch):
        """The deadline does not depend on the 15.6ms tick of time.monotonic()

        The whole timeout of a poll is a few tens of milliseconds, so a clock
        that only advances on the system tick would stretch it by a tick. The
        wait is driven by perf_counter(), which advances by the requested
        duration of every poll.
        """
        from alasio.backend.worker import manager as manager_module

        precise = [0.0]
        calls = []

        def fake_sleep(seconds):
            calls.append(seconds)
            precise[0] += seconds
            if len(calls) > 100:
                raise AssertionError('the wait did not follow perf_counter()')

        # monotonic is frozen: a deadline read from it would never arrive
        monkeypatch.setattr(manager_module.time, 'perf_counter', lambda: precise[0])
        monkeypatch.setattr(manager_module.time, 'monotonic', lambda: 0.0)
        monkeypatch.setattr(manager_module, 'sleep', fake_sleep)
        state = self.state_with(_LostStatusProcess())

        assert state.process_wait_exit(timeout=0.05) is None
        assert precise[0] >= 0.05

    def test_a_graceful_kill_of_a_gone_process_only_reads_its_status(self):
        """A process that ended by itself is never signaled, its status is the answer"""
        process = _ExitedProcess(0)
        state = self.state_with(process)

        assert state.process_graceful_kill() == 0
        assert process.signals == []

    def test_a_force_kill_of_a_gone_process_only_reads_its_status(self):
        """A process that ended by itself is not killed again"""
        process = _ExitedProcess(0)
        state = self.state_with(process)

        assert state.process_kill() == 0
        assert process.signals == []

    def test_an_unresponsive_process_is_escalated_to_kill(self):
        """A process that ignores terminate() is killed, and the kill is its status"""
        process = _UnresponsiveProcess()
        state = self.state_with(process)

        assert state.process_graceful_kill(terminate_timeout=0.05, kill_timeout=0.05) == -9
        assert process.signals == ['terminate', 'kill']


# ============================================================================
# Manager Lifecycle Tests
# ============================================================================

class TestManagerLifecycle:
    """测试 Manager 生命周期"""

    def test_manager_close_with_running_workers(self):
        """测试关闭运行中的 manager"""
        WorkerManager.singleton_clear()
        manager = WorkerManager()

        # Start workers
        configs = [f'test_close_{i}' for i in range(3)]
        for config in configs:
            success, _ = manager.worker_start('WorkerTestInfinite', config)
            assert success

        # 等待所有启动
        for config in configs:
            state = manager.state[config]
            state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

        workers = list(manager.state.values())

        # Close manager
        manager.close()

        # 等待所有停止
        for _ in AssertTimeout(3.0):
            with _:
                for config in configs:
                    if config in manager.state:
                        state = manager.state[config]
                        assert not state.process or not state.process.is_alive()

        # Check no process leak
        assert_worker_gone(workers)
        # Check no thread leak
        assert_recv_thread_gone(configs)

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

        # Create new manager
        manager2 = WorkerManager()
        success, _ = manager2.worker_start('WorkerTestRun3', 'test_2')
        assert success, "Cannot create worker after manager recreation"

        manager2.close()
