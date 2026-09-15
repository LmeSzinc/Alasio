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
from alasio.logger import logger
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

        waiting = manager.restart_begin()

        for worker in (a, b, c):
            assert worker.pending_restart is True
            # the graceful stop was requested
            assert worker.state == 'scheduler-stopping'
        # the return value is the waiting set of the restart (for the caller's log)
        assert waiting == ['cfg_a', 'cfg_b', 'cfg_c']

    def test_returns_the_waiting_set(self, manager, no_send):
        """
        The returned list covers every worker the wait will wait for: the ones
        this begin stopped plus the ones already stopping from an earlier
        request. Stopped / parked entries are not part of the wait.
        """
        add_state(manager, 'cfg_run', 'running')
        add_state(manager, 'cfg_stop', 'scheduler-stopping')
        add_state(manager, 'cfg_kill', 'killing')
        add_state(manager, 'cfg_err', 'error')
        add_state(manager, 'cfg_parked', 'restarting', pending_restart=True)

        waiting = manager.restart_begin()

        assert waiting == ['cfg_kill', 'cfg_run', 'cfg_stop']

    def test_returns_empty_without_workers(self, manager, no_send):
        """No worker to wait for: the list is empty"""
        assert manager.restart_begin() == []

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
    """restart_wait blocks for the stops and returns (success, resume list)"""

    def test_all_stopped_returns_resume_list(self, manager):
        start_worker(manager, 'WorkerTestScheduler', 'cfg_b')
        start_worker(manager, 'WorkerTestScheduler', 'cfg_a')

        manager.restart_begin()
        result = {}
        thread = threading.Thread(target=lambda: result.update(resume=manager.restart_wait(10)))
        thread.start()
        thread.join(timeout=15)

        assert not thread.is_alive(), 'restart_wait did not return'
        assert result['resume'] == (True, ['cfg_a', 'cfg_b'])
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
        assert result['resume'] == (True, ['cfg_inf'])
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

        assert result['resume'] == (True, ['cfg_a'])

    def test_abort_returns_early(self, manager, monkeypatch):
        # a worker that never stops gracefully: only the abort can end the wait
        add_state(manager, 'cfg_inf', 'scheduler-stopping')

        manager.restart_begin()
        # restart_begin sends a stop request to a fabricated entry; re-arm it
        with manager._lock:
            manager.state['cfg_inf'].state = 'scheduler-stopping'

        # observe the first iteration of the wait loop: the thread is provably
        # parked in restart_wait once it ran one (the hook runs under the
        # manager lock, the original it delegates to is lock free)
        entered = threading.Event()
        remaining = manager._restart_pending_configs_locked
        monkeypatch.setattr(manager, '_restart_pending_configs_locked',
                            lambda: (entered.set(), remaining())[1])

        result = {}
        thread = threading.Thread(target=lambda: result.update(resume=manager.restart_wait(600)))
        thread.start()
        assert entered.wait(5), 'restart_wait did not reach its poll loop'
        assert thread.is_alive()

        manager.restart_cancel()
        thread.join(timeout=5)

        assert not thread.is_alive(), 'restart_wait did not return on abort'
        # cancelled: the wait reports it and carries no resume list
        assert result['resume'] == (False, [])

    def test_abort_in_the_kill_phase_returns_without_freezing(self, manager, no_send, monkeypatch):
        """
        An abort landing in the second phase -- after the timeout escalation,
        while the kills are still running -- returns without freezing the list:
        the transaction is gone and the cancel path owns the state cleanup
        """
        # the escalation must not spend its KILL_WAIT_TIMEOUT on the fabricated
        # entry below (its kill only travels through the pipe of a real process)
        monkeypatch.setattr('alasio.backend.worker.manager.KILL_WAIT_TIMEOUT', 0)
        # an entry that never stops by itself and has no process to end: the
        # escalation cannot finish it, so the wait parks in the kill phase
        add_state(manager, 'cfg_park', 'scheduler-stopping')
        manager.restart_begin()

        result = {}
        thread = threading.Thread(target=lambda: result.update(resume=manager.restart_wait(0.1)))
        thread.start()

        # the graceful wait timed out and the escalation ran: the entry is being
        # killed and the second loop waits for it
        for _ in range(200):
            if manager.state['cfg_park'].state == 'force-killing':
                break
            time.sleep(0.01)
        assert manager.state['cfg_park'].state == 'force-killing', 'the escalation did not run'
        assert thread.is_alive(), 'the wait did not continue into the kill phase'

        manager.restart_cancel()
        thread.join(timeout=5)

        assert not thread.is_alive(), 'restart_wait did not return on abort'
        assert result['resume'] == (False, [])
        # nothing was frozen: a "restarting" entry of the cancel window can
        # still be stopped
        parked = add_state(manager, 'cfg_late', 'restarting', pending_restart=True)
        success, msg = manager.worker_kill('cfg_late')
        assert success, msg
        assert parked.state == 'idle'

    def test_abort_landing_at_the_freeze_wins(self, manager, no_send, monkeypatch):
        """
        The freeze re-checks the abort in its own critical section: the wait
        observes "every worker stopped" in one section, the freeze runs in the
        next, and a cancel landing in between wins -- nothing is frozen and the
        wait reports the cancellation (a (True, [...]) here would let the
        orchestration write a resume file and restart after the cancel)
        """
        add_state(manager, 'cfg_park', 'restarting', pending_restart=True)
        manager.restart_begin()

        def remaining_after_a_cancel():
            # the wait observed "every worker stopped" and a cancel landed
            # before the freeze took its section: the abort event is what
            # restart_cancel() sets (the hook runs under the manager lock, so
            # the cancel itself cannot be called from here)
            manager._restart_abort.set()
            return []

        monkeypatch.setattr(manager, '_restart_pending_configs_locked', remaining_after_a_cancel)

        # the wait must not report a completed restart after the cancel
        assert manager.restart_wait(1) == (False, [])

    def test_begin_converts_resuming_to_restarting(self, manager, no_send):
        """
        A resume queue left by a previous restart (not drained yet) is
        collected into the new resume list instead of being started under
        the gate
        """
        queued = add_state(manager, 'cfg_queued', 'resuming')

        manager.restart_begin()

        assert queued.state == 'restarting'
        assert manager.restart_wait(1) == (True, ['cfg_queued'])

    def test_wait_without_workers(self, manager, no_send):
        """No worker at all: the wait returns an empty list immediately"""
        started = time.monotonic()
        manager.restart_begin()
        assert manager.restart_wait(10) == (True, [])
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
# Frozen resume list (F4)
# ============================================================================

class TestRestartSealed:
    """
    The resume list is frozen once the wait is over

    The orchestration writes exactly the list restart_wait() returned into the
    resume file, so from that instant a cancel of a config of the list cancels
    nothing (the written file keeps resuming it): it is refused with an explicit
    error instead of silently dropping the entry from the manager. A
    "restarting" entry outside the list has no recorded resume intent and stays
    cancellable.
    """

    def seal(self, manager, config='cfg_park'):
        """Park one fabricated worker and run a whole wait over it"""
        parked = add_state(manager, config, 'restarting', pending_restart=True)
        manager.restart_begin()
        assert manager.restart_wait(1) == (True, [config])
        return parked

    def test_cancel_after_the_wait_is_refused(self, manager, no_send):
        parked = self.seal(manager)

        success, msg = manager.worker_kill('cfg_park')

        # the rpc layer turns the message into an RpcValueError (frontend toast)
        assert not success
        assert 'point of no return' in msg
        assert 'cfg_park' in msg
        # the entry did not move: it resumes after the backend restart
        assert parked.state == 'restarting'
        assert parked.pending_restart is True
        assert 'cfg_park' in manager.state

    @pytest.mark.parametrize('method', ['worker_scheduler_stop', 'worker_kill', 'worker_force_kill'])
    def test_entry_outside_the_frozen_list_is_still_cancellable(self, manager, no_send, method):
        """
        The refusal covers exactly the recorded configs: a "restarting" entry
        that is not in the frozen resume list has no resume intent, so the
        default stop keeps its plain meaning ("stop, do not resume") and is
        honoured without an error
        """
        recorded = self.seal(manager, 'cfg_a')
        # parked after the seal: not part of the recorded list
        late = add_state(manager, 'cfg_b', 'restarting', pending_restart=True)

        success, msg = getattr(manager, method)('cfg_a')
        assert not success
        assert 'point of no return' in msg
        assert recorded.state == 'restarting'

        # no conflict: nothing resumes this config, the stop is honoured
        success, msg = getattr(manager, method)('cfg_b')
        assert success, msg
        assert late.state == 'idle'
        assert 'cfg_b' not in manager.state

    @pytest.mark.parametrize('method', ['worker_scheduler_stop', 'worker_kill', 'worker_force_kill'])
    def test_refused_on_every_stop_function(self, manager, no_send, method):
        parked = self.seal(manager)

        success, msg = getattr(manager, method)('cfg_park')

        assert not success
        assert 'point of no return' in msg
        assert parked.state == 'restarting'

    @pytest.mark.parametrize('method', ['worker_scheduler_stop', 'worker_kill', 'worker_force_kill'])
    def test_keep_resume_after_the_wait_is_still_a_noop(self, manager, no_send, method):
        parked = self.seal(manager)

        success, msg = getattr(manager, method)('cfg_park', restart_resume=True)

        assert success, msg
        assert parked.state == 'restarting'

    def test_cancel_during_the_wait_is_still_honoured(self, manager):
        """
        Before the seal a cancel of an already parked entry still works (the
        wait is open, the list is not written yet): the config leaves the
        resume list
        """
        parked = add_state(manager, 'cfg_park', 'restarting', pending_restart=True)
        # a worker that ignores scheduler-stopping keeps the wait open
        start_worker(manager, 'WorkerTestInfinite', 'cfg_inf')

        manager.restart_begin()
        success, msg = manager.worker_kill('cfg_park')
        assert success, msg
        assert parked.state == 'idle'

        # the wait escalates cfg_inf and returns: the killed config is not in
        # the final resume list (it never reaches the resume file)
        assert manager.restart_wait(0.5) == (True, ['cfg_inf'])

    def test_queued_resuming_entry_is_still_cancelled(self, manager, no_send):
        """
        The seal covers the parked entries of the old backend: an entry queued
        for the auto-resume of the new backend is still cancelled by a stop
        (mark_resume runs there, the seal of the old backend is long gone)
        """
        self.seal(manager)
        queued = add_state(manager, 'cfg_queued', 'resuming')

        success, msg = manager.worker_kill('cfg_queued')

        assert success, msg
        assert queued.state == 'idle'
        assert 'cfg_queued' not in manager.state

    def test_cancelled_wait_freezes_nothing(self, manager, no_send):
        """
        restart_wait() returning after restart_cancel() (force restart /
        backend stop) freezes nothing: the transaction is gone and a worker
        parked in the race window of the cancel can still be stopped
        """
        add_state(manager, 'cfg_inf', 'scheduler-stopping')
        manager.restart_begin()
        manager.restart_cancel()

        assert manager.restart_wait(1) == (False, [])

        parked = add_state(manager, 'cfg_park', 'restarting', pending_restart=True)
        success, msg = manager.worker_kill('cfg_park')
        assert success, msg
        assert parked.state == 'idle'

    def test_next_transaction_decides_again(self, manager, no_send):
        """The seal belongs to one transaction: restart_cancel() drops it"""
        self.seal(manager, 'cfg_a')
        assert not manager.worker_kill('cfg_a')[0]

        manager.restart_cancel()
        assert 'cfg_a' not in manager.state

        # the next transaction starts unsealed
        second = add_state(manager, 'cfg_b', 'restarting', pending_restart=True)
        assert manager.restart_begin() == []
        success, msg = manager.worker_kill('cfg_b')
        assert success, msg
        assert second.state == 'idle'


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


# ============================================================================
# Stop requests during the spawn window
# ============================================================================

class TestStopRequestDuringSpawn:
    """
    Stop requests that land while the worker process is still spawning

    A "starting" worker always has its pipe (it is opened before the state
    flips): a stop request landing in the spawn window is written into the pipe
    and buffered until the worker reads its end. Without that order the command
    was dropped (send_command() found no pipe), the worker kept running while
    the manager believed it was stopping, and a graceful restart only ended
    through the timeout escalation (10 minutes in production).

    The spawn window is held open by a SpawnGate, so the request of the test is
    inside it by construction (no delay, no race).
    """

    def test_restart_begin_during_spawn_stops_the_worker(self, manager, monkeypatch):
        """
        A restart that begins in the spawn window must still stop the worker
        gracefully, not through the timeout escalation
        """
        gate = SpawnGate(monkeypatch)
        result = {}

        def start():
            result['start'] = manager.worker_start('WorkerTestScheduler', 'cfg_spawn')

        thread = threading.Thread(target=start)
        thread.start()
        gate.wait_entered()

        # the pipe is opened before the state flips: a command sent now is
        # buffered by the pipe and read when the worker starts
        state = manager.state['cfg_spawn']
        assert state.state == 'starting'
        assert state.process is None, 'the spawn already created its process'
        assert state.conn is not None, 'the spawn window has no pipe: a stop command would be dropped'

        with logger.mock_capture_writer() as capture:
            waiting = manager.restart_begin()
            assert waiting == ['cfg_spawn']
            assert state.state == 'scheduler-stopping'

            # the stop request is in the pipe: release the window so the worker
            # boots, reads the buffered command and stops by itself
            gate.release()
            started = time.monotonic()
            resume = manager.restart_wait(6.0)
            duration = time.monotonic() - started

            # the command went into the pipe of the spawning worker: no drop
            assert not capture.fd.any_contains('pipe connection not initialized')
            # the escalation must not be needed
            assert not capture.fd.any_contains('Graceful stop timeout')

        thread.join(timeout=10)
        assert not thread.is_alive()
        assert result['start'] == (True, 'Success')
        assert duration < 4.5, f'Stopped through the timeout escalation: {duration:.2f}s'
        assert resume == (True, ['cfg_spawn'])

        assert state.state == 'restarting'
        assert state.process is None
        assert_worker_gone([state])
        assert_recv_thread_gone(['cfg_spawn'])
