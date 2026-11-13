"""
Tests for BackgroundManager: scheduling, drop policies, counts and shutdown
"""
import gc
import threading
import time
import weakref
from collections import deque

import pytest

from alasio.base.background import BackgroundClosed, BackgroundManager, DropPolicy
from alasio.logger import logger


@pytest.fixture(autouse=True)
def _mute_logs():
    """Capture all logger output during tests to avoid writing to real files."""
    with logger.mock_capture_writer():
        yield


@pytest.fixture
def manager():
    """A fresh singleton, shut down and dropped after the test."""
    BackgroundManager.singleton_clear()
    manager = BackgroundManager(name='test-bg')
    yield manager
    manager.shutdown(wait=True, timeout=5)
    BackgroundManager.singleton_clear()


def wait_until(predicate, timeout=5.0):
    """
    Wait until a predicate turns true

    Args:
        predicate (callable):
        timeout (float): Seconds to wait

    Returns:
        bool: True if the predicate turned true
    """
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class Gate:
    """
    A task the test can hold on to and release

    Records what ran and on which thread, so a test can check the execution
    order without sleeping a guessed amount of time.
    """

    def __init__(self, tag='task'):
        self.tag = tag
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.thread_names = []
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            self.thread_names.append(threading.current_thread().name)
        self.entered.set()
        self.release.wait(5)
        self.finished.set()

    def wait(self, timeout=5.0):
        """Wait for the task to finish, raising when it never started."""
        assert self.entered.wait(timeout), f'{self.tag} never started'
        return self.finished.wait(timeout)


def record(calls, tag, done=None):
    """
    Build a task that appends a tag to a shared list

    Args:
        calls (list):
        tag (str):
        done (threading.Event | None): Set when the task ends

    Returns:
        callable: The task
    """
    def task():
        calls.append(tag)
        if done is not None:
            done.set()
    return task


class TestSubmitRun:
    """Tests for the basic submit/execute behaviour."""

    def test_submit_returns_true(self, manager):
        """A queued task is answered with True."""
        assert manager.submit(lambda: None, name='ch') is True

    def test_runs_in_another_thread(self, manager):
        """The task runs on a thread of the manager, not on the caller."""
        threads = []
        done = threading.Event()

        def task():
            threads.append(threading.current_thread())
            done.set()

        manager.submit(task, name='ch')
        assert done.wait(5)
        assert threads[0] is not threading.current_thread()

    def test_submit_does_not_wait(self, manager):
        """submit() returns while the task is still running."""
        gate = Gate()
        manager.submit(gate, name='ch')
        assert gate.entered.wait(5)
        assert manager.is_running('ch')
        gate.release.set()
        assert gate.finished.wait(5)

    def test_return_value_is_discarded(self, manager):
        """A task may return a value, the manager does not keep it."""
        done = threading.Event()

        def task():
            done.set()
            return 'ignored'

        assert manager.submit(task, name='ch') is True
        assert done.wait(5)

    def test_thread_name_carries_manager_and_name(self, manager):
        """The thread is named after the manager and the channel."""
        gate = Gate()
        manager.submit(gate, name='oil')
        gate.wait()
        gate.release.set()
        assert gate.thread_names == ['test-bg-oil']


class TestThreadLifecycleExit:
    """Tests for the thread exiting when the queue is empty."""

    def test_thread_exits_when_idle(self, manager):
        """The thread of a name ends after draining its queue."""
        done = threading.Event()
        manager.submit(lambda: done.set(), name='ch')
        assert done.wait(5)
        assert wait_until(lambda: manager.thread_count == 0)

    def test_new_thread_after_exit(self, manager):
        """A submission after the thread ended runs on a fresh thread."""
        names = []
        done = threading.Event()

        def task():
            names.append(threading.current_thread().name)
            done.set()

        manager.submit(task, name='ch')
        assert done.wait(5)
        assert wait_until(lambda: manager.thread_count == 0)

        done.clear()
        manager.submit(task, name='ch')
        assert done.wait(5)
        assert names == ['test-bg-ch', 'test-bg-ch']

    def test_one_thread_drains_the_queue(self, manager):
        """Multiple pending tasks of a name run on the same thread, in order."""
        gate = Gate()
        calls = []
        done = threading.Event()

        manager.submit(gate, name='ch')
        assert gate.entered.wait(5)
        manager.submit(record(calls, 'A2'), name='ch')
        manager.submit(record(calls, 'A3', done=done), name='ch')
        gate.release.set()

        assert done.wait(5)
        assert calls == ['A2', 'A3']
        assert manager.thread_count <= 1

    def test_submit_repairs_a_queue_without_a_thread(self, manager):
        """A queue whose thread died is drained by the next submission."""
        done = threading.Event()
        dead = threading.Thread(target=lambda: None)
        dead.start()
        dead.join()

        # only reachable after a framework error killed the thread
        with manager._lock:
            manager._threads['ch'] = dead
            manager._pending['ch'] = deque([(lambda: None, (), {})])

        assert manager.submit(lambda: done.set(), name='ch') is True
        assert done.wait(5)


class TestAnonymous:
    """Tests for submissions without a name."""

    def test_default_name_is_anonymous(self, manager):
        """submit(func) runs without joining any queue."""
        done = threading.Event()
        manager.submit(lambda: done.set())
        assert done.wait(5)
        assert wait_until(lambda: manager.thread_count == 0)

    def test_each_submission_has_its_own_thread(self, manager):
        """Anonymous tasks are not serialized by a shared channel."""
        names = []
        lock = threading.Lock()
        barrier = threading.Barrier(2, timeout=5)

        def task():
            with lock:
                names.append(threading.current_thread().name)
            barrier.wait()

        manager.submit(task)
        manager.submit(task)

        assert wait_until(lambda: len(names) == 2)
        assert len(set(names)) == 2

    def test_drop_is_ignored(self, manager):
        """An anonymous task has no queue, a drop policy has nothing to drop."""
        done = threading.Event()
        manager.submit(lambda: done.set(), drop=DropPolicy.DROP_PENDING)
        assert done.wait(5)

    def test_anonymous_is_not_queryable(self, manager):
        """The empty name is not a channel, a query of it finds nothing."""
        assert manager.is_running('') is False
        assert manager.pending_count('') == 0

    def test_anonymous_counts_as_running(self, manager):
        """An anonymous task is part of running_count while it runs."""
        gate = Gate()
        manager.submit(gate)
        assert gate.entered.wait(5)
        assert manager.running_count == 1
        gate.release.set()
        assert gate.finished.wait(5)


class TestDropPolicyQueue:
    """Tests for the default policy, which keeps every submission."""

    def test_all_tasks_run_in_order(self, manager):
        """The default policy runs the submissions in submission order."""
        gate = Gate()
        calls = []
        done = threading.Event()

        manager.submit(gate, name='ch')
        assert gate.entered.wait(5)
        manager.submit(record(calls, 'A2'), name='ch')
        manager.submit(record(calls, 'A3', done=done), name='ch')
        assert manager.pending_count('ch') == 2
        gate.release.set()

        assert done.wait(5)
        assert calls == ['A2', 'A3']


class TestDropPolicyDropPending:
    """Tests for DropPolicy.DROP_PENDING, the latest-wins policy."""

    def test_pending_is_replaced(self, manager):
        """A newer submission replaces the pending one, the running one is kept."""
        gate = Gate()
        calls = []
        done = threading.Event()

        manager.submit(gate, name='ch')
        assert gate.entered.wait(5)
        assert manager.submit(record(calls, 'A2'), name='ch', drop=DropPolicy.DROP_PENDING) is True
        assert manager.submit(record(calls, 'A3', done=done), name='ch',
                              drop=DropPolicy.DROP_PENDING) is True
        assert manager.pending_count('ch') == 1
        gate.release.set()

        assert done.wait(5)
        assert calls == ['A3']

    def test_multiple_pending_are_cleared(self, manager):
        """One DROP_PENDING submission clears the whole queue of the name."""
        gate = Gate()
        calls = []
        done = threading.Event()

        manager.submit(gate, name='ch')
        assert gate.entered.wait(5)
        manager.submit(record(calls, 'A2'), name='ch')
        manager.submit(record(calls, 'A3'), name='ch')
        assert manager.pending_count('ch') == 2

        manager.submit(record(calls, 'A4', done=done), name='ch', drop=DropPolicy.DROP_PENDING)
        assert manager.pending_count('ch') == 1
        gate.release.set()

        assert done.wait(5)
        assert calls == ['A4']

    def test_running_task_is_not_replaced(self, manager):
        """The running task completes even when a newer submission arrives."""
        gate = Gate()
        manager.submit(gate, name='ch')
        assert gate.entered.wait(5)

        manager.submit(lambda: None, name='ch', drop=DropPolicy.DROP_PENDING)
        assert manager.is_running('ch')
        assert not gate.finished.is_set()

        gate.release.set()
        assert gate.finished.wait(5)

    def test_idle_name_accepts(self, manager):
        """A DROP_PENDING submission to an idle name runs normally."""
        done = threading.Event()
        assert manager.submit(lambda: done.set(), name='ch', drop=DropPolicy.DROP_PENDING) is True
        assert done.wait(5)


class TestDropPolicyDropNew:
    """Tests for DropPolicy.DROP_NEW, which refuses a submission while pending."""

    def test_new_submission_is_dropped(self, manager):
        """A submission is refused while another one waits, the older one runs."""
        gate = Gate()
        calls = []
        done = threading.Event()

        manager.submit(gate, name='ch')
        assert gate.entered.wait(5)
        assert manager.submit(record(calls, 'A2', done=done), name='ch') is True
        assert manager.submit(record(calls, 'A3'), name='ch', drop=DropPolicy.DROP_NEW) is False
        gate.release.set()

        assert done.wait(5)
        assert calls == ['A2']

    def test_pending_queue_is_not_compacted(self, manager):
        """DROP_NEW refuses the submission, it does not shorten the queue."""
        gate = Gate()
        calls = []

        manager.submit(gate, name='ch')
        assert gate.entered.wait(5)
        manager.submit(record(calls, 'A2'), name='ch')
        manager.submit(record(calls, 'A3'), name='ch')
        assert manager.submit(record(calls, 'A4'), name='ch', drop=DropPolicy.DROP_NEW) is False
        assert manager.pending_count('ch') == 2
        gate.release.set()

        assert wait_until(lambda: len(calls) == 2)
        assert calls == ['A2', 'A3']

    def test_idle_name_accepts(self, manager):
        """DROP_NEW only refuses while a pending task exists, running is fine."""
        done = threading.Event()
        gate = Gate()

        manager.submit(gate, name='ch')
        assert gate.entered.wait(5)
        assert manager.submit(lambda: done.set(), name='ch', drop=DropPolicy.DROP_NEW) is True
        gate.release.set()
        assert done.wait(5)


class TestCounts:
    """Tests for running_count, pending_total and snapshot."""

    def test_running_count(self, manager):
        """A running task is counted, the count returns to zero afterwards."""
        gate = Gate()
        manager.submit(gate, name='a')
        assert gate.entered.wait(5)
        assert manager.running_count == 1
        gate.release.set()
        assert gate.finished.wait(5)
        assert wait_until(lambda: manager.running_count == 0)

    def test_running_count_of_several_names(self, manager):
        """Every running task is counted, named and anonymous."""
        first = Gate()
        second = Gate()
        manager.submit(first, name='a')
        manager.submit(second)
        assert first.entered.wait(5)
        assert second.entered.wait(5)
        assert manager.running_count == 2
        first.release.set()
        second.release.set()
        assert first.finished.wait(5)
        assert second.finished.wait(5)

    def test_pending_total(self, manager):
        """The pending count sums every name and drops to zero when drained."""
        first = Gate()
        second = Gate()
        done = threading.Event()

        manager.submit(first, name='a')
        manager.submit(second, name='b')
        assert first.entered.wait(5)
        assert second.entered.wait(5)

        manager.submit(record([], 'x'), name='a')
        manager.submit(record([], 'y'), name='b')
        manager.submit(lambda: done.set(), name='a')
        assert manager.pending_total == 3

        first.release.set()
        second.release.set()
        assert done.wait(5)

    def test_snapshot_is_one_instant(self, manager):
        """snapshot() reports both counts of the same critical section."""
        gate = Gate()
        manager.submit(gate, name='a')
        assert gate.entered.wait(5)
        manager.submit(lambda: None, name='a')
        assert manager.snapshot() == (1, 1)
        gate.release.set()
        assert gate.finished.wait(5)


class TestTaskErrors:
    """Tests for the task raising, the thread must survive."""

    def test_worker_survives_exception(self, manager):
        """A failing task is logged and the next task of the name still runs."""
        done = threading.Event()

        def fail():
            raise ValueError('boom')

        manager.submit(fail, name='ch')
        manager.submit(lambda: done.set(), name='ch')
        assert done.wait(5)

    def test_worker_survives_base_exception(self, manager):
        """SystemExit from a task does not kill the queue of the name."""
        done = threading.Event()

        def fail():
            raise SystemExit(1)

        manager.submit(fail, name='ch')
        manager.submit(lambda: done.set(), name='ch')
        assert done.wait(5)

    def test_error_is_logged_with_tag(self, manager):
        """The failure log carries the background tag and the channel name."""
        started = threading.Event()

        def fail():
            started.set()
            raise ZeroDivisionError('boom')

        with logger.mock_capture_writer() as capture:
            manager.submit(fail, name='oil')
            assert started.wait(5)
            manager.shutdown(wait=True, timeout=5)
        assert capture.fd.any_contains('[Background] oil failed')
        # an Exception carries its traceback
        assert 'Traceback' in capture.fd.logs[0]

    def test_base_exception_logs_one_line(self, manager):
        """An exit signal is a plain line, its traceback would be noise."""
        started = threading.Event()

        def fail():
            started.set()
            raise SystemExit(1)

        with logger.mock_capture_writer() as capture:
            manager.submit(fail, name='oil')
            assert started.wait(5)
            manager.shutdown(wait=True, timeout=5)
        assert capture.concat() == 'ERROR | [Background] oil aborted: 1\n'
        assert 'Traceback' not in capture.fd.logs[0]


class TestSelfSubmit:
    """Tests for a task submitting another task."""

    def test_same_name_does_not_start_a_second_thread(self, manager):
        """A task submitting its own name is drained by the same thread."""
        names = []
        done = threading.Event()
        lock = threading.Lock()

        def follow():
            with lock:
                names.append(threading.current_thread().name)
            done.set()

        def first():
            with lock:
                names.append(threading.current_thread().name)
            manager.submit(follow, name='ch')

        manager.submit(first, name='ch')
        assert done.wait(5)
        assert names[0] == names[1]

    def test_other_name_runs(self, manager):
        """A task submitting another name does not block on it."""
        done = threading.Event()

        def first():
            manager.submit(lambda: done.set(), name='other')

        manager.submit(first, name='ch')
        assert done.wait(5)


class TestShutdown:
    """Tests for shutdown()."""

    def test_pending_is_dropped(self, manager):
        """Shutdown drops the waiting tasks and refuses new submissions."""
        gate = Gate()
        calls = []
        manager.submit(gate, name='ch')
        assert gate.entered.wait(5)
        manager.submit(record(calls, 'dropped'), name='ch')

        assert manager.shutdown(wait=False) is True
        gate.release.set()
        assert gate.finished.wait(5)
        assert manager.shutdown(wait=True, timeout=5) is True
        assert calls == []
        with pytest.raises(BackgroundClosed):
            manager.submit(lambda: None, name='ch')

    def test_running_task_completes(self, manager):
        """A running task is not cancelled, wait=True joins its thread."""
        gate = Gate()
        manager.submit(gate, name='ch')
        assert gate.entered.wait(5)

        assert manager.shutdown(wait=False) is True
        gate.release.set()
        assert gate.finished.wait(5)
        assert manager.shutdown(wait=True, timeout=5) is True

    def test_anonymous_task_is_waited_for(self, manager):
        """wait=True joins the threads of the anonymous submissions too."""
        gate = Gate()
        manager.submit(gate)
        assert gate.entered.wait(5)
        manager.shutdown(wait=False)
        gate.release.set()
        assert manager.shutdown(wait=True, timeout=5) is True

    def test_timeout_returns_false(self, manager):
        """A stuck task makes wait=True return False after the timeout."""
        gate = Gate()
        manager.submit(gate, name='ch')
        assert gate.entered.wait(5)

        assert manager.shutdown(wait=True, timeout=0.05) is False
        gate.release.set()
        assert gate.finished.wait(5)
        assert manager.shutdown(wait=True, timeout=5) is True

    def test_is_idempotent(self, manager):
        """Shutdown can be called more than once."""
        assert manager.shutdown(wait=True, timeout=5) is True
        assert manager.shutdown(wait=True, timeout=5) is True

    def test_refuses_to_join_its_own_thread(self, manager):
        """A task cannot shut the manager down with wait=True."""
        errors = []
        done = threading.Event()

        def task():
            try:
                manager.shutdown(wait=True)
            except RuntimeError as e:
                errors.append(e)
            finally:
                done.set()

        manager.submit(task, name='ch')
        assert done.wait(5)
        assert len(errors) == 1


class TestArguments:
    """Tests for the arguments of submit()."""

    def test_args_are_forwarded(self, manager):
        """Positional and keyword arguments reach the task."""
        result = []
        done = threading.Event()

        def task(a, b, c=0):
            result.append((a, b, c))
            done.set()

        manager.submit(task, 1, 2, c=3, name='ch')
        assert done.wait(5)
        assert result == [(1, 2, 3)]


class TestMemory:
    """Tests for releasing the arguments of a task."""

    class Payload:
        """A payload a test tracks through a weak reference."""
        pass

    def test_args_released_when_dropped(self, manager):
        """A dropped submission releases its arguments."""
        gate = Gate()
        manager.submit(gate, name='ch')
        assert gate.entered.wait(5)

        payload = self.Payload()
        ref = weakref.ref(payload)
        manager.submit(lambda value: None, payload, name='ch')
        manager.submit(lambda: None, name='ch', drop=DropPolicy.DROP_PENDING)
        del payload
        gc.collect()

        assert ref() is None
        gate.release.set()
        assert gate.finished.wait(5)

    def test_args_released_after_run(self, manager):
        """A completed task releases its arguments."""
        payload = self.Payload()
        ref = weakref.ref(payload)
        done = threading.Event()

        def task(value):
            done.set()

        manager.submit(task, payload, name='ch')
        assert done.wait(5)
        del payload
        gc.collect()

        assert wait_until(lambda: ref() is None)


class TestSingleton:
    """Tests for the process-wide instance."""

    def test_every_call_returns_the_same_instance(self):
        """The manager is a singleton, the first call's arguments build it."""
        try:
            first = BackgroundManager(name='first')
            second = BackgroundManager(name='second')
            assert first is second
            assert first._name == 'first'
        finally:
            BackgroundManager.singleton_clear()

    def test_clear_creates_a_new_instance(self):
        """singleton_clear() drops the instance, the next call builds another one."""
        try:
            first = BackgroundManager()
            BackgroundManager.singleton_clear()
            second = BackgroundManager()
            assert first is not second
        finally:
            BackgroundManager.singleton_clear()

    def test_building_the_manager_starts_no_thread(self):
        """Creating the singleton only sets up the lock and the tables."""
        try:
            assert BackgroundManager().thread_count == 0
        finally:
            BackgroundManager.singleton_clear()


class TestConfigWrite:
    """Tests for a task writing the result itself, without returning it."""

    def test_task_saves_into_config(self, manager):
        """The task writes an absolute value, the caller does not collect it."""
        class FakeDashboard:
            def __init__(self):
                self.values = {}

            def set(self, value):
                self.values['Oil'] = value

        class FakeConfig:
            def __init__(self):
                self.Dashboard = FakeDashboard()

        config = FakeConfig()
        done = threading.Event()

        def read_oil(value):
            config.Dashboard.set(value)
            done.set()

        manager.submit(read_oil, 100, name='resource')
        assert done.wait(5)
        assert config.Dashboard.values == {'Oil': 100}
