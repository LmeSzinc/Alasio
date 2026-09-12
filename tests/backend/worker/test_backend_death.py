"""
Real process tests: a worker must not survive the process that started it

The child process plays the backend role (it owns the WorkerManager), the worker
runs a sleeping mod and never checks anything. Killing that backend process
without any cleanup must take the worker down right away, by one of two means:

- a worker sleeping in short chunks follows the injected KeyboardInterrupt
- a worker blocked in one long sleep never sees that exception (it is only
  delivered when the blocking call returns), it has to be terminated directly

In both cases a worker that is still there after a few seconds is waiting for its
sleep to finish, i.e. it became an orphan that keeps the device busy with nobody
watching it.
"""
import multiprocessing
import time

import psutil

from alasio.backend.worker.bridge import BACKEND_LOST_KILL_WAIT_TIMEOUT
from alasio.backend.worker.manager import WorkerManager
from alasio.logger import logger

# The workers sleep for worker_mods.WORKER_TEST_SLEEP_SECONDS (60s), waiting for
# that is exactly the failure these tests look for
WORKER_STOP_DEADLINE = 5.0
WORKER_START_TIMEOUT = 30.0
CONFIG = 'test_backend_death'


def backend_child(conn, config, mod):
    """
    Runs in the child process that plays the backend role

    Starts a real worker and reports its pid, then idles until the test kills
    this process. Nothing here runs on the way out: the test kills the process
    the same way a force kill from outside would.

    Args:
        conn (Connection): Pipe to report the worker pid to the test
        config (str): Config name to run
        mod (str): Worker mod to run
    """
    # this process only exists for the test, keep it from writing a real log file
    logger.mute(all=True)

    manager = WorkerManager()
    success, msg = manager.worker_start(mod, config)
    if not success:
        conn.send(('error', msg))
        return

    state = manager.state[config]
    state.wait_running(timeout=WORKER_START_TIMEOUT)
    # the worker reports "scheduler-waiting" once it is inside its sleep, so the
    # test only kills this process when the worker is really sleeping
    deadline = time.perf_counter() + WORKER_START_TIMEOUT
    while state.state != 'scheduler-waiting':
        if time.perf_counter() > deadline:
            conn.send(('error', f'worker did not start sleeping, state={state.state}'))
            return
        time.sleep(0.05)
    conn.send(('pid', state.process.pid))

    while 1:
        time.sleep(0.5)


def wait_worker_gone(pid, deadline):
    """
    Wait for a process to disappear

    Args:
        pid (int): Process id to watch
        deadline (float): Seconds to wait

    Returns:
        float: Seconds the process took, or None if it is still there
    """
    start = time.perf_counter()
    while time.perf_counter() - start < deadline:
        if not psutil.pid_exists(pid):
            return time.perf_counter() - start
        time.sleep(0.1)
    return None


def run_backend_death(mod):
    """
    Start a worker through a backend child process, kill the backend without any
    cleanup and wait for the worker to disappear

    Args:
        mod (str): Worker mod to run

    Returns:
        float: Seconds the worker took to disappear

    Raises:
        AssertionError: If the worker did not start, or outlived the deadline
    """
    ctx = multiprocessing.get_context('spawn')
    parent_conn, child_conn = ctx.Pipe()
    backend = ctx.Process(
        # daemon=False: a daemonic process is not allowed to have children, and
        # this child has to start a worker (the supervisor spawns the backend
        # the same way for the same reason)
        target=backend_child, args=(child_conn, CONFIG, mod),
        name='TestBackend', daemon=False
    )
    backend.start()
    child_conn.close()

    worker_pid = None
    try:
        assert parent_conn.poll(WORKER_START_TIMEOUT), 'backend child reported nothing'
        message = parent_conn.recv()
        assert message[0] == 'pid', f'worker did not start: {message}'
        worker_pid = message[1]

        # the worker is alive and sleeping (it never checks anything)
        assert psutil.Process(worker_pid).is_running()

        # kill the backend without any cleanup, like a force kill from outside
        backend.kill()
        backend.join(timeout=5)

        elapsed = wait_worker_gone(worker_pid, WORKER_STOP_DEADLINE)
        assert elapsed is not None, (
            f'worker {worker_pid} is still running {WORKER_STOP_DEADLINE}s after its backend was '
            f'killed: it waits for its sleep to return instead of following the backend'
        )
        return elapsed
    finally:
        if backend.is_alive():
            backend.kill()
            backend.join(timeout=5)
        if worker_pid and psutil.pid_exists(worker_pid):
            psutil.Process(worker_pid).kill()
        parent_conn.close()


class TestWorkerFollowsBackendDeath:
    """杀掉启动 worker 的进程后，worker 必须马上消失"""

    def test_chunked_sleep_worker_is_stopped_by_injection(self):
        """连续 0.1s 小 sleep 的 worker：注入的 KeyboardInterrupt 就够，不用硬杀"""
        elapsed = run_backend_death('WorkerTestSleep')

        assert elapsed < BACKEND_LOST_KILL_WAIT_TIMEOUT * 0.5, (
            f'worker stopped after {elapsed:.2f}s, which is the hard exit deadline '
            f'({BACKEND_LOST_KILL_WAIT_TIMEOUT}s): the injected KeyboardInterrupt did not stop it'
        )

    def test_long_sleep_worker_is_terminated(self):
        """一次长 sleep 的 worker：注入的异常到不了，必须靠硬杀"""
        elapsed = run_backend_death('WorkerTestSleepLong')

        assert elapsed >= BACKEND_LOST_KILL_WAIT_TIMEOUT * 0.5, (
            f'worker stopped after {elapsed:.2f}s, before the hard exit deadline '
            f'({BACKEND_LOST_KILL_WAIT_TIMEOUT}s): a worker blocked in one long sleep should not '
            f'see the injected KeyboardInterrupt'
        )
