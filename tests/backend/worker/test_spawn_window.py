"""
Stop / cleanup requests that land while the worker process is still spawning

The spawn window is the interval between the "starting" mark (its pipe is
already registered by then, see WorkerManager._mark_starting_locked) and the
moment process.start() returned. Two races used to live in it:

- a graceful stop request had no pipe to write to, so the command was dropped
  and the restart waited out the whole timeout escalation before killing the
  worker: the fixed behavior is pinned by TestStopRequestDuringSpawn in
  test_restart_state.py;
- a kill / close could leave a live worker process that the manager no longer
  tracked (F13): worker_force_kill() ran its bookkeeping while state.process
  was still None (the spawn was in flight), so process_graceful_kill() was a
  no-op and the entry returned to idle -- the process was created afterwards
  and used to keep running with nobody managing it.

The guarantee pinned here: a cleanup landing in the spawn window does not
finalize the entry itself while the process does not exist yet. The kill
request travels through the pipe (a "starting" worker always has one), the
worker stops itself the moment it boots and its own disconnect performs the
final transition; close() closes the pipe, which the process created afterwards
observes as EOF. Either way no process survives, and a disconnect of an entry
that is not registered any more publishes no state.

The spawn is held by a SpawnGate (tests/backend/worker/spawn_gate.py): the tests
send their request while the spawn is parked in the window and release it
afterwards, so "the request lands in the window" is an event, not a delay.

Running these tests against manager.py @737d30c8 (the spawn-window pipe fix is
in, the cleanup fix is not) fails them for the pinned reasons: the entries end
in "error" instead of "idle" -- the disconnect of an entry that is already
final was still published -- and a spawn that fails in the parent leaves its
entry "starting" forever (TestSpawnFailure).
"""
import multiprocessing
import threading
import time

import pytest

from alasio.backend.worker.manager import WorkerManager, WorkerState
from tests.backend.worker.const import *
from tests.backend.worker.spawn_gate import SpawnGate
from tests.backend.worker.test_worker_lifespan import assert_recv_thread_gone, assert_worker_gone


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


@pytest.fixture
def state_events(manager, monkeypatch):
    """
    Record the worker state events the manager broadcasts

    The Worker topic (and with it the frontend) is driven by these events: a
    cleanup that lands in the spawn window must not leave a spurious
    "disconnected" / "error" state behind for an entry that is already final.

    Returns:
        list: (config, state) tuples in broadcast order
    """
    events = []
    original = manager.on_worker_state
    monkeypatch.setattr(
        manager, 'on_worker_state',
        lambda config, state: (events.append((config, state)), original(config, state))[1])
    return events


def states_of(events, config):
    """States broadcast for one config, in order"""
    return [state for event_config, state in events if event_config == config]


def wait_until(predicate, timeout=5.0, description='condition'):
    """
    Wait until the predicate holds (poll based, no fixed sleep)

    Args:
        predicate (callable): Returns True once the expected state is there
        timeout (float): Seconds to wait at most
        description (str): What is being waited for, used in the error
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(f'Timeout waiting for {description}')


def start_into_window(manager, monkeypatch, config, mod='WorkerTestScheduler'):
    """
    Start a worker in a thread and return once it is parked in its spawn window

    The spawn is held by a SpawnGate: the window is open and stays open until
    the test releases it, so a request sent now is inside the window by
    construction (no delay, no race).

    Args:
        manager (WorkerManager): Manager to start on
        monkeypatch: pytest monkeypatch fixture (installs the gate)
        config (str): Config name
        mod (str): Mod name to start

    Returns:
        tuple: (threading.Thread, dict, WorkerState, SpawnGate) the starting
            thread, the dict it fills with the worker_start() return value, the
            worker entry of the window and the gate that holds it
    """
    gate = SpawnGate(monkeypatch)
    result = {}

    def start():
        result['start'] = manager.worker_start(mod, config)

    thread = threading.Thread(target=start)
    thread.start()
    gate.wait_entered()

    state = manager.state.get(config, None)
    assert state is not None, f'The started worker "{config}" has no state entry'
    assert state.state == 'starting', f'"{config}" is not starting: {state.state}'
    assert state.process is None, f'The spawn of "{config}" already created its process'
    assert state.conn is not None, \
        f'The spawn window of "{config}" has no pipe: a stop command would be dropped'
    return thread, result, state, gate


def assert_spawned(thread, result, config):
    """
    Wait for the spawn thread and assert the process was really created

    Without it "no process left" could pass because nothing was launched.

    Args:
        thread (threading.Thread): Thread of start_into_window()
        result (dict): Result dict of start_into_window()
        config (str): Config name, for the error message
    """
    thread.join(timeout=10)
    assert not thread.is_alive(), f'The spawn thread of "{config}" did not finish'
    assert result.get('start') == (True, 'Success'), f'The spawn of "{config}" failed: {result}'


def kill_in_thread(manager, config, restart_resume=False):
    """
    Run worker_kill() in a thread and return the handle to join it

    worker_kill() blocks until the worker stopped (up to KILL_WAIT_TIMEOUT
    before it escalates): with the spawn still parked the wait has nothing to
    observe, so it runs in a thread and the tests release the window while it
    waits -- the wait then ends through the real stop instead of the timeout.

    Args:
        manager (WorkerManager): Manager to kill on
        config (str): Config name
        restart_resume (bool): Passed through to worker_kill()

    Returns:
        tuple: (threading.Thread, dict) the killing thread and the dict it
            fills with the worker_kill() return value
    """
    killed = {}

    def kill():
        killed['kill'] = manager.worker_kill(config, restart_resume=restart_resume)

    thread = threading.Thread(target=kill)
    thread.start()
    return thread, killed


def join_kill(thread, killed, timeout=10):
    """
    Wait for the killing thread and return what worker_kill() returned

    Args:
        thread (threading.Thread): Thread of kill_in_thread()
        killed (dict): Result dict of kill_in_thread()
        timeout (float): Seconds to wait at most

    Returns:
        tuple: (bool, str) the worker_kill() return value
    """
    thread.join(timeout=timeout)
    assert not thread.is_alive(), 'worker_kill() did not return'
    return killed['kill']


def wait_worker_stopped(config, timeout=5.0, mod='WorkerTestScheduler'):
    """
    Wait until the worker of the config has no process and no recv thread left

    assert_worker_gone() / assert_recv_thread_gone() only poll one second: the
    workers of this file stop themselves after their boot (the kill request is
    buffered by the pipe), which can outlast that window on a slow machine. Both
    helpers are still called afterwards, so the failure message stays the
    familiar one.

    Args:
        config (str): Config name
        timeout (float): Seconds to wait at most
        mod (str): Mod name the worker was started with (process / thread name)
    """
    process_name = f'Worker-{mod}-{config}'
    thread_name = f'WorkerRecv-{config}'

    def stopped():
        if thread_name in {t.name for t in threading.enumerate()}:
            return False
        return process_name not in {p.name for p in multiprocessing.active_children()}

    wait_until(stopped, timeout=timeout, description=f'the worker of "{config}" to stop')


def assert_no_orphan(config, mod='WorkerTestScheduler'):
    """
    Assert no worker process / recv thread is left for the config

    Args:
        config (str): Config name
        mod (str): Mod name the worker was started with (process name)
    """
    assert_worker_gone([WorkerState(mod=mod, config=config, state='idle')])
    assert_recv_thread_gone([config])


class TestCleanupDuringSpawn:
    """A cleanup path landing in the spawn window must not leave a process"""

    def test_kill_in_window_leaves_no_process(self, manager, state_events, monkeypatch):
        """Default kill (= stop, no resume) during the spawn window"""
        # the spawn is parked, so the wait of worker_kill() cannot be satisfied
        # and escalates to a force kill: with no process to wait for, a timeout
        # of 0 takes the same branch as the production 1.0s (and immediately)
        monkeypatch.setattr('alasio.backend.worker.manager.KILL_WAIT_TIMEOUT', 0)
        thread, result, state, gate = start_into_window(manager, monkeypatch, 'cfg_kill')

        # worker_kill() escalates to a force kill while state.process is still
        # None: this is the window that used to leave a live process behind
        success, msg = manager.worker_kill('cfg_kill')
        assert success, msg
        gate.release()
        assert_spawned(thread, result, 'cfg_kill')

        # the kill requests were buffered by the pipe: the worker stops itself
        # as soon as it boots, and its own disconnect finalizes the entry (idle
        # removes it)
        wait_until(lambda: 'cfg_kill' not in manager.state,
                   description='the finalized entry of "cfg_kill"')
        wait_worker_stopped('cfg_kill')
        assert_no_orphan('cfg_kill')
        assert states_of(state_events, 'cfg_kill')[-1] == 'idle'

    def test_force_kill_in_window_leaves_no_process(self, manager, state_events, monkeypatch):
        """Force kill during the spawn window (the request travels through the pipe too)"""
        thread, result, state, gate = start_into_window(manager, monkeypatch, 'cfg_force')

        # a process-less force kill returns right after asking the worker to die
        success, msg = manager.worker_force_kill('cfg_force')
        assert success, msg
        gate.release()
        assert_spawned(thread, result, 'cfg_force')

        wait_until(lambda: 'cfg_force' not in manager.state,
                   description='the finalized entry of "cfg_force"')
        wait_worker_stopped('cfg_force')
        assert_no_orphan('cfg_force')
        assert states_of(state_events, 'cfg_force')[-1] == 'idle'

    def test_kill_keep_resume_in_window_parks_restarting(self, manager, state_events, monkeypatch):
        """
        "Force stop, keep resume" during the spawn window: the config must be
        parked in "restarting" (collected by restart_wait) without a process
        """
        thread, result, state, gate = start_into_window(manager, monkeypatch, 'cfg_keep')

        # parked spawn: the kill cannot wait for a process, it escalates at once
        monkeypatch.setattr('alasio.backend.worker.manager.KILL_WAIT_TIMEOUT', 0)
        assert manager.restart_begin() == ['cfg_keep']
        success, msg = manager.worker_kill('cfg_keep', restart_resume=True)
        assert success, msg
        gate.release()
        assert_spawned(thread, result, 'cfg_keep')

        # the worker is parked for the resume, no process survives it and the
        # last thing the frontend hears is the parked state
        wait_until(lambda: state.state == 'restarting',
                   description='the parked entry of "cfg_keep"')
        wait_worker_stopped('cfg_keep')
        assert_no_orphan('cfg_keep')
        assert state.pending_restart is True
        assert state.process is None
        assert manager.restart_wait(2.0) == ['cfg_keep']
        assert states_of(state_events, 'cfg_keep')[-1] == 'restarting'

    def test_close_in_window_leaves_no_process(self, manager, state_events, monkeypatch):
        """Manager close (backend exit) during the spawn window"""
        thread, result, state, gate = start_into_window(manager, monkeypatch, 'cfg_close')

        manager.close()
        # close() returns while the spawn is still in flight: the process is
        # created afterwards, sees its pipe closed and is terminated by the
        # disconnect; the closed entry publishes no state any more
        assert manager.state == {}
        gate.release()
        assert_spawned(thread, result, 'cfg_close')
        wait_worker_stopped('cfg_close')
        assert_no_orphan('cfg_close')
        assert states_of(state_events, 'cfg_close')[-1] == 'idle'


class TestWorkerFinishesWithPendingStop:
    """
    The worker's entry function may complete while a stop request is still
    pending in its pipe (recv thread scheduling): the worker then exits by
    itself, the late read has nothing to stop (or never happens at all). The
    entry must still be finalized through the disconnect, with no orphan.
    """

    def test_kill_then_entry_finishes_by_itself(self, manager, state_events, monkeypatch):
        """Kill requested in the spawn window, the worker finishes on its own"""
        thread, result, state, gate = start_into_window(
            manager, monkeypatch, 'cfg_done', mod='WorkerTestExit')

        killer, killed = kill_in_thread(manager, 'cfg_done')
        wait_until(lambda: state.state == 'killing', description='the kill request of "cfg_done"')
        gate.release()
        assert_spawned(thread, result, 'cfg_done')
        assert join_kill(killer, killed) == (True, 'Success')

        # the worker is already done when its recv thread reads the kill: the
        # child exits (exitcode 0, or 1 when the injected interrupt landed in
        # its shutdown), the disconnect finalizes the entry as idle
        wait_until(lambda: 'cfg_done' not in manager.state,
                   description='the finalized entry of "cfg_done"')
        wait_worker_stopped('cfg_done', mod='WorkerTestExit')
        assert_no_orphan('cfg_done', mod='WorkerTestExit')
        assert states_of(state_events, 'cfg_done')[-1] == 'idle'

    def test_keep_resume_then_entry_finishes_by_itself(self, manager, state_events, monkeypatch):
        """
        Same as above with the resume kept: a worker that finished on its own is
        still parked in "restarting" (the restart promised to bring it back)
        """
        thread, result, state, gate = start_into_window(
            manager, monkeypatch, 'cfg_done_keep', mod='WorkerTestExit')

        assert manager.restart_begin() == ['cfg_done_keep']
        killer, killed = kill_in_thread(manager, 'cfg_done_keep', restart_resume=True)
        wait_until(lambda: state.state == 'killing',
                   description='the kill request of "cfg_done_keep"')
        gate.release()
        assert_spawned(thread, result, 'cfg_done_keep')
        assert join_kill(killer, killed) == (True, 'Success')

        wait_until(lambda: state.state == 'restarting',
                   description='the parked entry of "cfg_done_keep"')
        wait_worker_stopped('cfg_done_keep', mod='WorkerTestExit')
        assert_no_orphan('cfg_done_keep', mod='WorkerTestExit')
        assert state.pending_restart is True
        assert manager.restart_wait(2.0) == ['cfg_done_keep']
        assert states_of(state_events, 'cfg_done_keep')[-1] == 'restarting'


class TestSpawnFailure:
    """A spawn that fails in the parent must not leave the entry "starting" forever"""

    def test_spawn_failure_returns_to_error(self, manager, monkeypatch):
        """
        The interpreter cannot be launched: a parent-side spawn failure, so no
        process is created at all and process.start() raises in the backend.
        No process will ever report for the entry, so the spawn drops its pipe
        and returns the entry to error -- otherwise the config could never be
        started again and no disconnect would ever finalize it.
        """
        import multiprocessing.spawn as mp_spawn

        with monkeypatch.context() as patch:
            # only this process is affected: the patch is undone before the
            # "starts again" check below
            patch.setattr(mp_spawn, 'get_executable', lambda: 'no/such/python')
            with pytest.raises(OSError):
                manager.worker_start('WorkerTestScheduler', 'cfg_fail')

        state = manager.state.get('cfg_fail', None)
        assert state is not None
        assert state.state == 'error'
        assert state.conn is None
        assert state.process is None

        # the entry is not stuck: with the interpreter back the config starts again
        success, msg = manager.worker_start('WorkerTestScheduler', 'cfg_fail')
        assert success, msg
        assert manager.state['cfg_fail'].wait_running(timeout=WORKER_STARTUP_TIMEOUT)
