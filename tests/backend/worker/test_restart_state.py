"""
Graceful backend restart tests on the manager layer

Covers the restarting / resuming states, the global restart gate, the
restart_begin / restart_wait / restart_cancel orchestration primitives, the
stop / cancel routing of the stop functions and the auto-resume queue
(mark_resume / worker_resume).
"""
import threading
import time

import pytest

from alasio.backend.worker.manager import WORKER_STOPPED_STATE, WorkerManager, WorkerState
from tests.backend.worker.const import *
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
def no_send(monkeypatch):
    """Silence command sending for tests that fabricate process-less states"""
    monkeypatch.setattr(WorkerState, 'send_command', lambda self, command: True)


def add_state(manager, config, state, pending_restart=False, mod='WorkerTestInfinite'):
    """
    Fabricate a worker entry in the given state (no process)

    Args:
        manager (WorkerManager): Manager to fill
        config (str): Config name
        state (WORKER_STATE): State to put the entry in
        pending_restart (bool): Value of the restart mark
        mod (str): Mod name

    Returns:
        WorkerState: The fabricated entry
    """
    worker = WorkerState(mod=mod, config=config, state='idle')
    worker.pending_restart = pending_restart
    manager.state[config] = worker
    manager._set_state(worker, state)
    return worker


def start_worker(manager, mod, config, timeout=WORKER_STARTUP_TIMEOUT):
    """
    Start a real worker subprocess and wait until it is running

    Args:
        manager (WorkerManager): Manager to start on
        mod (str): Test mod name
        config (str): Config name
        timeout (float): Startup timeout

    Returns:
        WorkerState: The running worker entry
    """
    success, msg = manager.worker_start(mod, config)
    assert success, f'Failed to start worker: {msg}'
    state = manager.state[config]
    assert state.wait_running(timeout=timeout), f'Worker did not start: {config}'
    return state


# ============================================================================
# State semantics
# ============================================================================

class TestRestartStateSemantics:
    """restarting / resuming are stopped states that stay in the state dict"""

    @pytest.mark.parametrize('state', ['restarting', 'resuming'])
    def test_state_is_stopped_and_kept(self, manager, state):
        worker = add_state(manager, 'cfg', 'running')
        manager._set_state(worker, state)

        assert worker.state == state
        assert state in WORKER_STOPPED_STATE
        assert worker.stopped_event.is_set()
        assert not worker.running_event.is_set()
        # the entry is kept (only idle removes it): it occupies the config
        assert manager.state['cfg'] is worker

    def test_pending_restart_default_false(self):
        worker = WorkerState(mod='m', config='c', state='idle')
        assert worker.pending_restart is False


# ============================================================================
# restart_begin / restart_cancel
# ============================================================================

class TestRestartBegin:
    """restart_begin snapshots, marks and requests the stops"""

    def test_snapshot_marks_running_workers(self, manager, no_send):
        a = add_state(manager, 'cfg_a', 'running')
        b = add_state(manager, 'cfg_b', 'scheduler-waiting')
        c = add_state(manager, 'cfg_c', 'starting')

        manager.restart_begin()

        for worker in (a, b, c):
            assert worker.pending_restart is True
            # the graceful stop was requested
            assert worker.state == 'scheduler-stopping'

    def test_non_running_states_are_not_marked(self, manager, no_send):
        # a worker the user already asked to stop: the old intent wins
        stopping = add_state(manager, 'cfg_stop', 'scheduler-stopping')
        # a crashing worker and a stopping one are only waited for
        error = add_state(manager, 'cfg_err', 'error')
        kill = add_state(manager, 'cfg_kill', 'killing')

        manager.restart_begin()

        assert stopping.pending_restart is False
        assert stopping.state == 'scheduler-stopping'
        assert error.pending_restart is False
        assert kill.pending_restart is False

    def test_begin_twice_raises(self, manager, no_send):
        manager.restart_begin()
        with pytest.raises(RuntimeError, match='Restart already in progress'):
            manager.restart_begin()

    def test_gate_blocks_start(self, manager, no_send):
        manager.restart_begin()
        success, msg = manager.worker_start('WorkerTestInfinite', 'cfg_new')
        assert not success
        assert 'gracefully restarting' in msg
        assert 'cfg_new' not in manager.state

    def test_cancel_clears_gate_marks_and_parked_entries(self, manager, no_send):
        running = add_state(manager, 'cfg_run', 'running')
        parked = add_state(manager, 'cfg_park', 'restarting', pending_restart=True)

        manager.restart_begin()
        assert running.pending_restart is True
        assert running.state == 'scheduler-stopping'
        assert parked.pending_restart is True
        # a queue entry created in the race window (after begin)
        queued = add_state(manager, 'cfg_queued', 'resuming')

        manager.restart_cancel()

        # the running worker only loses its mark: it stops through its own path
        assert running.pending_restart is False
        assert running.state == 'scheduler-stopping'
        # the process-less entries return to idle and leave the dict
        assert parked.state == 'idle'
        assert queued.state == 'idle'
        assert 'cfg_park' not in manager.state
        assert 'cfg_queued' not in manager.state
        assert manager.restart_aborted() is True

        # the gate is open again
        success, msg = manager.worker_start('WorkerTestInfinite', 'cfg_after_cancel')
        assert success, msg

    def test_continue_rejected_for_marked_worker(self, manager, no_send):
        add_state(manager, 'cfg', 'scheduler-stopping', pending_restart=True)
        success, msg = manager.worker_scheduler_continue('cfg')
        assert not success
        assert 'cannot continue' in msg


# ============================================================================
# restart_wait
# ============================================================================

class TestRestartWait:
    """restart_wait blocks for the stops and returns the final resume list"""

    def test_all_stopped_returns_resume_list(self, manager):
        start_worker(manager, 'WorkerTestScheduler', 'cfg_b')
        start_worker(manager, 'WorkerTestScheduler', 'cfg_a')

        manager.restart_begin()
        result = {}
        thread = threading.Thread(target=lambda: result.update(resume=manager.restart_wait(10)))
        thread.start()
        thread.join(timeout=15)

        assert not thread.is_alive(), 'restart_wait did not return'
        assert result['resume'] == ['cfg_a', 'cfg_b']
        for config in ('cfg_a', 'cfg_b'):
            assert manager.state[config].state == 'restarting'
            assert manager.state[config].process is None
        # no residual process / recv thread
        assert_worker_gone(list(manager.state.values()))
        assert_recv_thread_gone(['cfg_a', 'cfg_b'])

    def test_unresponsive_worker_escalates_to_kill(self, manager):
        # WorkerTestInfinite ignores scheduler-stopping: the timeout escalation
        # must kill it (restart_resume=True keeps the resume intent)
        state = start_worker(manager, 'WorkerTestInfinite', 'cfg_inf')

        manager.restart_begin()
        assert state.state == 'scheduler-stopping'

        result = {}
        thread = threading.Thread(target=lambda: result.update(resume=manager.restart_wait(0.5)))
        thread.start()
        thread.join(timeout=15)

        assert not thread.is_alive(), 'restart_wait did not return'
        assert result['resume'] == ['cfg_inf']
        assert state.state == 'restarting'
        assert state.pending_restart is True
        assert state.process is None

    def test_user_stop_removes_config_from_resume_list(self, manager):
        """
        A default kill during the wait means "stop and do not resume": the
        config must not appear in the final resume list
        """
        start_worker(manager, 'WorkerTestScheduler', 'cfg_a')
        self_kill = start_worker(manager, 'WorkerTestInfinite', 'cfg_b')

        manager.restart_begin()
        # user stops cfg_b for good (default restart_resume=False)
        success, msg = manager.worker_kill('cfg_b')
        assert success, msg
        assert self_kill.state == 'idle'

        result = {}
        thread = threading.Thread(target=lambda: result.update(resume=manager.restart_wait(10)))
        thread.start()
        thread.join(timeout=15)

        assert result['resume'] == ['cfg_a']

    def test_abort_returns_early(self, manager):
        # a worker that never stops gracefully: only the abort can end the wait
        add_state(manager, 'cfg_inf', 'scheduler-stopping')

        manager.restart_begin()
        # restart_begin sends a stop request to a fabricated entry; re-arm it
        with manager._lock:
            manager.state['cfg_inf'].state = 'scheduler-stopping'

        result = {}
        thread = threading.Thread(target=lambda: result.update(resume=manager.restart_wait(600)))
        thread.start()
        # give the waiting thread time to enter the loop
        time.sleep(0.3)
        assert thread.is_alive()

        manager.restart_cancel()
        thread.join(timeout=5)

        assert not thread.is_alive(), 'restart_wait did not return on abort'
        # cancelled: no config is parked in "restarting"
        assert result['resume'] == []

    def test_begin_converts_resuming_to_restarting(self, manager, no_send):
        """
        A resume queue left by a previous restart (not drained yet) is
        collected into the new resume list instead of being started under
        the gate
        """
        queued = add_state(manager, 'cfg_queued', 'resuming')

        manager.restart_begin()

        assert queued.state == 'restarting'
        assert manager.restart_wait(1) == ['cfg_queued']

    def test_wait_without_workers(self, manager, no_send):
        """No worker at all: the wait returns an empty list immediately"""
        started = time.monotonic()
        manager.restart_begin()
        assert manager.restart_wait(10) == []
        assert time.monotonic() - started < 1


# ============================================================================
# Stop / cancel routing (three stop functions)
# ============================================================================

class TestStopRouting:
    """The stop functions on parked / queued workers cancel (or keep) the resume"""

    def test_kill_keep_resume_on_running_marked_worker(self, manager):
        state = start_worker(manager, 'WorkerTestInfinite', 'cfg')
        manager.restart_begin()

        success, msg = manager.worker_kill('cfg', restart_resume=True)
        assert success, msg
        assert state.state == 'restarting'
        assert state.pending_restart is True
        assert state.process is None

    def test_kill_default_on_running_marked_worker_cancels(self, manager):
        state = start_worker(manager, 'WorkerTestInfinite', 'cfg')
        manager.restart_begin()

        success, msg = manager.worker_kill('cfg')
        assert success, msg
        assert state.state == 'idle'
        assert state.pending_restart is False
        assert 'cfg' not in manager.state

    def test_force_kill_keep_resume_on_running_marked_worker(self, manager):
        state = start_worker(manager, 'WorkerTestInfinite', 'cfg')
        manager.restart_begin()

        success, msg = manager.worker_force_kill('cfg', restart_resume=True)
        assert success, msg
        assert state.state == 'restarting'
        assert state.pending_restart is True

    def test_kill_unmarked_worker_unchanged(self, manager):
        """A normal kill outside a restart is not affected"""
        state = start_worker(manager, 'WorkerTestInfinite', 'cfg')
        success, msg = manager.worker_kill('cfg')
        assert success, msg
        assert state.state == 'idle'
        assert 'cfg' not in manager.state

    @pytest.mark.parametrize('method', ['worker_scheduler_stop', 'worker_kill', 'worker_force_kill'])
    def test_restarting_default_cancels_resume(self, manager, no_send, method):
        state = add_state(manager, 'cfg', 'restarting', pending_restart=True)

        success, msg = getattr(manager, method)('cfg')

        assert success, msg
        assert state.state == 'idle'
        assert state.pending_restart is False
        assert 'cfg' not in manager.state

    @pytest.mark.parametrize('method', ['worker_scheduler_stop', 'worker_kill', 'worker_force_kill'])
    def test_restarting_keep_resume(self, manager, no_send, method):
        state = add_state(manager, 'cfg', 'restarting', pending_restart=True)

        success, msg = getattr(manager, method)('cfg', restart_resume=True)

        assert success, msg
        assert state.state == 'restarting'
        assert 'cfg' in manager.state

    @pytest.mark.parametrize('method', ['worker_scheduler_stop', 'worker_kill', 'worker_force_kill'])
    def test_resuming_cancels_regardless_of_restart_resume(self, manager, no_send, method):
        """A queued entry has no process: any stop means cancel"""
        state = add_state(manager, 'cfg', 'resuming')

        success, msg = getattr(manager, method)('cfg', restart_resume=True)

        assert success, msg
        assert state.state == 'idle'
        assert 'cfg' not in manager.state

    def test_stop_unknown_still_rejected(self, manager):
        success, msg = manager.worker_kill('nonexistent')
        assert not success
        assert 'no such worker' in msg.lower()


# ============================================================================
# Auto-resume queue
# ============================================================================

class TestResumeQueue:
    """mark_resume / worker_resume primitives of the new backend"""

    def test_mark_resume_creates_entries(self, manager):
        queued = manager.mark_resume(['cfg_a', 'cfg_b'])

        assert queued == ['cfg_a', 'cfg_b']
        for config in ('cfg_a', 'cfg_b'):
            state = manager.state[config]
            assert state.state == 'resuming'
            assert state.stopped_event.is_set()
            assert not state.running_event.is_set()

    def test_mark_resume_skips_started_configs(self, manager):
        state = start_worker(manager, 'WorkerTestInfinite', 'cfg_a')

        queued = manager.mark_resume(['cfg_a', 'cfg_b'])

        assert queued == ['cfg_b']
        assert state.state == 'running'

    def test_mark_resume_skips_parked_configs(self, manager, no_send):
        parked = add_state(manager, 'cfg_a', 'restarting', pending_restart=True)

        queued = manager.mark_resume(['cfg_a', 'cfg_b'])

        assert queued == ['cfg_b']
        assert parked.state == 'restarting'

    def test_worker_resume_starts_queued(self, manager):
        manager.mark_resume(['cfg_a'])
        state = manager.state['cfg_a']

        success, msg = manager.worker_resume('WorkerTestInfinite', 'cfg_a')
        assert success, msg
        assert state.state == 'starting'
        assert state.mod == 'WorkerTestInfinite'
        assert state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)

    def test_worker_resume_after_cancel_returns_false(self, manager):
        manager.mark_resume(['cfg_a'])
        # user cancels while queued
        success, msg = manager.worker_kill('cfg_a')
        assert success, msg

        success, msg = manager.worker_resume('WorkerTestInfinite', 'cfg_a')
        assert not success
        assert 'not awaiting auto-resume' in msg

    def test_worker_resume_after_manual_start_returns_false(self, manager):
        manager.mark_resume(['cfg_a'])
        # cancel then start manually: the queue entry is gone
        manager.worker_kill('cfg_a')
        start_worker(manager, 'WorkerTestInfinite', 'cfg_a')

        success, msg = manager.worker_resume('WorkerTestInfinite', 'cfg_a')
        assert not success
        assert 'not awaiting auto-resume' in msg

    def test_start_rejected_for_resuming_entry(self, manager):
        manager.mark_resume(['cfg_a'])
        success, msg = manager.worker_start('WorkerTestInfinite', 'cfg_a')
        assert not success
        assert 'queued for auto-resume' in msg
        assert manager.state['cfg_a'].state == 'resuming'

    def test_worker_resume_blocked_by_new_restart_gate(self, manager, no_send):
        """
        The race between mark_resume and a new restart_begin: starting is
        refused, the entry was already converted to "restarting" by begin
        """
        # fabricate the race window: a resuming entry created after begin
        manager.restart_begin()
        racing = add_state(manager, 'cfg_race', 'resuming')

        success, msg = manager.worker_resume('WorkerTestInfinite', 'cfg_race')

        assert not success
        assert 'gracefully restarting' in msg
        assert racing.state == 'resuming'
        assert racing.pending_restart is False


# ============================================================================
# Disconnect handling / close
# ============================================================================

class TestRestartDisconnect:
    """A marked worker parks in "restarting" whatever its exit code"""

    def test_marked_worker_error_exit_parks_restarting(self, manager):
        start_worker(manager, 'WorkerTestError', 'cfg_err')
        manager.restart_begin()

        state = manager.state['cfg_err']
        # let the worker crash by itself (exitcode != 0)
        state.send_test_continue()

        for _ in range(40):
            if state.state == 'restarting':
                break
            time.sleep(0.05)

        assert state.state == 'restarting'
        assert state.pending_restart is True
        assert state.process is None

    def test_unmarked_worker_error_exit_stays_error(self, manager):
        start_worker(manager, 'WorkerTestError', 'cfg_err')
        state = manager.state['cfg_err']
        state.send_test_continue()

        for _ in range(40):
            if state.state == 'error':
                break
            time.sleep(0.05)

        assert state.state == 'error'

    def test_close_with_parked_entries_does_not_hang(self, manager, no_send):
        add_state(manager, 'cfg_park', 'restarting', pending_restart=True)
        manager.mark_resume(['cfg_queued', 'cfg_queued_2'])

        started = time.monotonic()
        manager.close()
        assert time.monotonic() - started < 5

        assert manager.state == {}
        for config in ('cfg_park', 'cfg_queued', 'cfg_queued_2'):
            assert config not in manager.state
