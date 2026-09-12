"""
BackendBridge tests for the "backend died" path

backend 被外部强杀（TerminateProcess / taskkill / SIGKILL）时 pipe 会断开，
worker 必须自己停下来，而不是变成无人管理的孤儿进程继续跑：

1. 先给 scheduler 线程注入 KeyboardInterrupt（与 killing 命令同一条链路），
   正在跑 python 代码的 scheduler 会自己收尾退出
2. 注入到不了（scheduler 卡在原生调用里，异常要等那个调用返回才抛）时，
   BACKEND_LOST_KILL_WAIT_TIMEOUT 之后直接结束进程
3. send() 不再阻塞在已经死掉的 pipe 上（否则退出路径自己会卡死）
"""
import time
from multiprocessing import Pipe
from threading import Lock

import pytest

from alasio.backend.worker import bridge as bridge_module
from alasio.backend.worker.bridge import BackendBridge
from alasio.backend.worker.event import ConfigEvent
from alasio.logger import logger


class DeadThread:
    """Stands in for the scheduler thread after it unwound"""

    @staticmethod
    def is_alive():
        return False


def wait_for(predicate, timeout=2.0):
    """
    Poll a predicate until it is true

    Args:
        predicate (Callable): Predicate to poll
        timeout (float): Seconds to wait

    Returns:
        bool: True if the predicate became true
    """
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def injected(monkeypatch):
    """
    Capture KeyboardInterrupt injections instead of performing them

    The bridge injects into the thread that called init(), which is the pytest
    main thread here, a real injection would abort the test run.

    Returns:
        list[int]: Thread ids that would have been interrupted
    """
    injected_tids = []
    monkeypatch.setattr(bridge_module, '_async_raise', injected_tids.append)
    return injected_tids


@pytest.fixture
def exited(monkeypatch):
    """
    Capture process termination instead of performing it

    The bridge ends the worker with os._exit when the scheduler did not stop in
    time, which would kill the test runner if it ran for real. The wait before
    that exit is shortened to keep the tests fast.

    Returns:
        list[int]: Exit codes that would have been used
    """
    exit_codes = []
    monkeypatch.setattr(bridge_module, '_exit_process', exit_codes.append)
    monkeypatch.setattr(bridge_module, 'BACKEND_LOST_KILL_WAIT_TIMEOUT', 0.1)
    return exit_codes


@pytest.fixture
def bridge_env(injected, exited):
    """
    BackendBridge inited on a real pipe, the peer end is closed by the test

    Logging is captured (logger.mock_capture_writer) for the whole test: the
    bridge logs on pipe EOF, and that must not land in the real log file.
    """
    BackendBridge.singleton_clear()
    parent_conn, child_conn = Pipe()

    with logger.mock_capture_writer():
        bridge = BackendBridge()
        bridge.init('TestMod', 'test_backend_lost', child_conn)

        # drop the initial WorkerState event
        time.sleep(0.05)
        if parent_conn.poll(0.1):
            parent_conn.recv_bytes()

        yield bridge, parent_conn

        bridge.close()
        for conn in (parent_conn, child_conn):
            try:
                conn.close()
            except Exception:
                pass

    BackendBridge.singleton_clear()


class TestPipeBreak:
    """Pipe EOF: 先注入异常，注入没停就硬杀，send 不再阻塞"""

    def test_eof_stops_worker(self, bridge_env, injected, exited):
        """peer 关闭 pipe 后：清 running + 注入异常 + 到点硬杀"""
        bridge, parent_conn = bridge_env
        assert bridge.running
        assert injected == []
        assert exited == []

        parent_conn.close()

        assert wait_for(lambda: exited), 'worker was not terminated'
        assert not bridge.running
        assert injected == [bridge.main_tid]
        assert exited == [bridge_module.BACKEND_LOST_EXIT_CODE]

    def test_send_returns_immediately_after_eof(self, bridge_env):
        """断开后 send() 必须立刻返回，不能阻塞调用者"""
        bridge, parent_conn = bridge_env
        parent_conn.close()
        assert wait_for(lambda: not bridge.running)

        start = time.perf_counter()
        job = bridge.send(ConfigEvent(t='Log', v='after backend died'))
        assert job.acquire(timeout=1.0), 'send job was never released'
        assert time.perf_counter() - start < 0.5

    def test_send_loop_survives_pipe_break(self, bridge_env):
        """pipe 报错不能直接退出发送线程：还在等锁的调用者要靠它解锁"""
        bridge, parent_conn = bridge_env
        parent_conn.close()
        assert wait_for(lambda: not bridge.running)

        time.sleep(0.2)
        assert bridge._send_thread.is_alive()


class TestHandleBackendLost:
    """_handle_backend_lost(): recv loop 调用的入口"""

    def test_handle_backend_lost_injects_then_terminates(self, bridge_env, injected, exited):
        """先注入异常，scheduler 没退就硬杀"""
        bridge, parent_conn = bridge_env
        assert bridge.running

        bridge._handle_backend_lost()

        assert not bridge.running
        assert injected == [bridge.main_tid]
        assert exited == [bridge_module.BACKEND_LOST_EXIT_CODE]

    def test_handle_backend_lost_skips_exit_when_scheduler_unwound(self, bridge_env, injected, exited,
                                                                  monkeypatch):
        """注入生效（scheduler 线程已经退出）时不再硬杀"""
        bridge, parent_conn = bridge_env
        monkeypatch.setattr(bridge_module, 'main_thread', lambda: DeadThread())

        bridge._handle_backend_lost()

        assert injected == [bridge.main_tid]
        assert exited == []

    def test_handle_backend_lost_second_call_is_noop(self, bridge_env, injected, exited):
        """重复调用不会重复停进程"""
        bridge, parent_conn = bridge_env

        bridge._handle_backend_lost()
        bridge._handle_backend_lost()

        assert injected == [bridge.main_tid]
        assert exited == [bridge_module.BACKEND_LOST_EXIT_CODE]

    def test_handle_backend_lost_after_close_is_noop(self, bridge_env, injected, exited):
        """close() 之后不再当作 backend 丢了"""
        bridge, parent_conn = bridge_env

        bridge.close()
        bridge._handle_backend_lost()

        assert injected == []
        assert exited == []


class TestReleasePendingTask:
    """_release_pending_task(): never leave a caller blocked on a dead loop"""

    def test_release_pending_task_unlocks_waiter(self, bridge_env):
        """停止时残留的槽位必须解锁，否则调用者永远阻塞"""
        bridge, parent_conn = bridge_env

        # a task slot as send() leaves it: the caller waits on that lock
        task_lock = Lock()
        task_lock.acquire()
        bridge._task_slot = (b'pending', task_lock)

        bridge._release_pending_task()

        assert not task_lock.locked()

    def test_release_pending_task_without_task(self, bridge_env):
        """没有待发送任务时是空操作"""
        bridge, parent_conn = bridge_env

        bridge._task_slot = None
        bridge._release_pending_task()


class TestBackendLostLog:
    """断开时的日志：定位孤儿 worker 现场用的，进 mock 捕获而不是真实日志文件"""

    def test_backend_lost_is_logged(self, bridge_env):
        """停 worker 之前先留一行日志"""
        bridge, parent_conn = bridge_env

        with logger.mock_capture_writer() as capture:
            bridge._handle_backend_lost()

        assert capture.fd.any_contains('Backend disconnected'), capture.fd.logs
        assert capture.backend.any_contains('Backend disconnected'), capture.backend.logs


class TestBridgeClose:
    """close() 是我们自己发起的关闭，不能被当成 backend 丢了"""

    def test_close_does_not_stop_worker(self, bridge_env, injected, exited):
        """close() 后不应注入异常，也不应结束进程"""
        bridge, parent_conn = bridge_env

        bridge.close()
        time.sleep(0.2)

        assert injected == []
        assert exited == []
        assert not bridge.running
