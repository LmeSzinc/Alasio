"""
Tests of the warmup manager, see alasio/backend/pyc_warm/manager.py.

The spawning is real: a stand-in target sends a msgpack report through the
pipe and exits, so the whole lifecycle of the manager -- the pipe, the
"starting" window, the recv thread, the disconnect handler, close() -- is
the one a warmup process meets in production.
"""
import multiprocessing
import threading
import time

import pytest

from alasio.backend.pyc_warm.manager import WARMUP_NAME, PycWarmManager
from alasio.backend.worker.entry import mod_entry
from alasio.backend.worker.manager import WorkerManager
from alasio.logger import logger
from tests.backend.pyc_warm import warm_targets

pytestmark = [
    pytest.mark.parallel,
]

# The line the manager logs when the child of a run that finished exits
STOPPED = 'INFO | [WorkerManager] Worker stopped: pyc_warm, exitcode={}\n'


def wait_for(predicate, timeout=30):
    """
    Poll a predicate until it holds.

    Args:
        predicate (callable): The condition to wait for
        timeout (float): Seconds to wait

    Returns:
        bool: True when the predicate held before the timeout
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def manager():
    """
    A fresh manager, closed on the way out.

    Yields:
        PycWarmManager: The manager of the test
    """
    PycWarmManager.singleton_clear()
    mgr = PycWarmManager()
    yield mgr
    mgr.close()


@pytest.fixture
def worker_manager():
    """
    A fresh worker manager, the base the seams live on.

    Yields:
        WorkerManager: The manager of the test
    """
    WorkerManager.singleton_clear()
    mgr = WorkerManager()
    yield mgr
    mgr.close()


@pytest.fixture
def capture():
    """
    Collect the lines the manager logs.

    Yields:
        CaptureWriter: The writer that holds the lines
    """
    with logger.mock_capture_writer() as capture:
        yield capture


def use_target(monkeypatch, target):
    """
    Make the manager spawn a stand-in target instead of the warmup.

    The arguments stay the ones of the manager: a stand-in is only another
    entry, and every entry takes the same six arguments.

    Args:
        monkeypatch: The pytest monkeypatch fixture
        target (callable): The spawn target
    """
    def spawn_process(self, mod, config, child_conn, project_root='', mod_root='', path_main=''):
        return self._ctx.Process(
            target=target,
            args=(mod, config, child_conn, project_root, mod_root, path_main),
            name=WARMUP_NAME,
            daemon=True,
        )

    monkeypatch.setattr(PycWarmManager, '_spawn_process', spawn_process)


class TestStart:
    """
    Tests for PycWarmManager.start().
    """

    def test_logs_the_report_of_the_child(self, manager, monkeypatch, capture):
        """The report becomes lines of the backend, and goes no further."""
        use_target(monkeypatch, warm_targets.report_target)
        assert manager.start() is True
        assert wait_for(lambda: WARMUP_NAME not in manager.state)
        assert capture.concat() == (
            'INFO | [PycWarm] 12 stale, 11 compiled, 1 failed in 0.50s, 3 units, 1 found nothing\n'
            'WARNING | [PycWarm] mod/alas: 1 of 10 failed: module/main.py: OSError: file is locked\n'
            + STOPPED.format(0)
        )

    def test_the_report_does_not_outlive_the_run(self, manager, monkeypatch):
        """The manager keeps no copy of a finished report."""
        use_target(monkeypatch, warm_targets.report_target)
        assert manager.start() is True
        assert wait_for(lambda: WARMUP_NAME not in manager.state)
        assert not hasattr(manager, 'report')
        assert manager.state == {}

    def test_a_run_that_raised(self, manager, monkeypatch, capture):
        """A warmup that could not scope the release says so, and no more."""
        use_target(monkeypatch, warm_targets.error_target)
        assert manager.start() is True
        assert wait_for(lambda: WARMUP_NAME not in manager.state)
        assert capture.concat() == (
            'WARNING | [PycWarm] Warmup failed: RuntimeError: the scope could not be read\n'
            + STOPPED.format(0)
        )

    def test_an_unreadable_report(self, manager, monkeypatch, capture):
        """Bytes that are not a report are a warning, never a crash."""
        use_target(monkeypatch, warm_targets.garbage_target)
        assert manager.start() is True
        assert wait_for(lambda: WARMUP_NAME not in manager.state)
        lines = capture.concat().splitlines()
        assert lines[0].startswith('WARNING | [PycWarm] Unreadable report: ')
        assert lines[1] == STOPPED.format(0).strip()

    def test_a_second_start_is_refused_while_running(self, manager, monkeypatch):
        """The warmup runs once per backend process."""
        use_target(monkeypatch, warm_targets.silent_target)
        assert manager.start() is True
        assert manager.start() is False
        # One child, not two
        names = [process.name for process in multiprocessing.active_children()]
        assert names.count(WARMUP_NAME) == 1

    def test_a_crash_is_not_an_error_of_the_backend(self, manager, monkeypatch, capture):
        """A warmup that exits non-zero still delivered its report."""
        use_target(monkeypatch, warm_targets.failing_target)
        assert manager.start() is True
        # The entry lands in "error", which start() accepts for a retry: a
        # warmup that could not run is retried on the next backend start
        assert wait_for(lambda: manager.state.get(WARMUP_NAME, None) is not None
                        and manager.state[WARMUP_NAME].state == 'error')
        assert capture.concat().endswith(STOPPED.format(1))
        assert manager.start() is True

    def test_start_passes_the_root_of_the_run(self, manager, monkeypatch):
        """The child is told the project root, and no mod of its own."""
        from alasio.ext import env

        monkeypatch.setattr(env, 'PROJECT_ROOT', '/the/root')
        seen = {}

        class Probe(Exception):
            """Raised where the process would have been started."""

        def spawn_process(self, mod, config, child_conn, project_root='', mod_root='', path_main=''):
            seen.update(
                mod=mod, config=config, project_root=project_root,
                mod_root=mod_root, path_main=path_main,
            )
            raise Probe()

        monkeypatch.setattr(PycWarmManager, '_spawn_process', spawn_process)
        # Best effort: a spawn that raises is a warning, and the entry is not
        # left behind as "starting"
        assert manager.start() is False
        assert seen == {
            'mod': WARMUP_NAME, 'config': WARMUP_NAME, 'project_root': '/the/root',
            'mod_root': '', 'path_main': '',
        }
        assert manager.state[WARMUP_NAME].state == 'error'


class TestSpawnProcess:
    """
    Tests of the process the warmup asks the manager for.
    """

    def test_the_warmup_process_is_not_started(self, manager):
        """The seam answers a process, the manager starts it."""
        from alasio.backend.pyc_warm.entry import pyc_warm_entry

        process = manager._spawn_process(
            WARMUP_NAME, WARMUP_NAME, 'CONN', '/the/root',
        )
        try:
            # Named after the run: a warmup is not a mod of a config, its
            # name does not follow the Worker-{mod}-{config} format
            assert process.name == WARMUP_NAME
            assert process.daemon is True
            # Not started: no pid, and the target is never resolved yet
            assert process.pid is None
            assert process._target is pyc_warm_entry
            assert process._args == (WARMUP_NAME, WARMUP_NAME, 'CONN', '/the/root', '', '')
        finally:
            process.close()

    def test_the_warmup_recv_thread(self, manager):
        """The recv thread of the warmup is named after the run too."""
        assert manager._recv_thread_name(WARMUP_NAME) == WARMUP_NAME

    def test_the_worker_recv_thread(self, worker_manager):
        """A worker keeps the thread name its lifecycle already used."""
        assert worker_manager._recv_thread_name('cfg') == 'WorkerRecv-cfg'

    def test_the_worker_process_is_not_started(self, worker_manager):
        """A worker answers the same shape, with its own entry and name."""
        process = worker_manager._spawn_process(
            'alas', 'cfg', 'CONN', '/root', '/mod', 'module/main.py',
        )
        try:
            assert process.name == 'Worker-alas-cfg'
            assert process.daemon is True
            assert process.pid is None
            assert process._target is mod_entry
            assert process._args == ('alas', 'cfg', 'CONN', '/root', '/mod', 'module/main.py')
        finally:
            process.close()


class TestClose:
    """
    Tests of the inherited close() on the warmup.
    """

    def test_close_stops_the_child(self, manager, monkeypatch):
        """A running warmup does not outlive the backend."""
        use_target(monkeypatch, warm_targets.silent_target)
        assert manager.start() is True
        assert wait_for(lambda: WARMUP_NAME in {p.name for p in multiprocessing.active_children()})
        manager.close()

        assert not [p for p in multiprocessing.active_children() if p.name == WARMUP_NAME]
        assert manager.state == {}
        # close() joins the recv thread with a timeout: a thread that is in
        # the disconnect handler when the timeout expires finishes on its
        # own, the way the worker tests wait for theirs
        assert wait_for(lambda: not [t for t in threading.enumerate() if t.name == WARMUP_NAME])

    def test_close_without_a_start(self, manager):
        """A backend that never warmed anything closes cleanly."""
        manager.close()
        assert manager.state == {}


class TestSingleton:
    """
    Tests of the process-wide instance.
    """

    def test_one_instance(self):
        """The backend and every caller share one manager."""
        PycWarmManager.singleton_clear()
        assert PycWarmManager() is PycWarmManager()

    def test_a_manager_of_its_own(self, manager):
        """The subclass does not share the slot of the worker manager."""
        assert isinstance(manager, WorkerManager)
        assert manager is not WorkerManager()
