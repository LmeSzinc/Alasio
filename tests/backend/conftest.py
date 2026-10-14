"""
Fixtures for the backend tests.
"""

import socket
from datetime import datetime

import pytest

from alasio.logger.writer import LogWriter
from alasio.testing.patch_time import PatchTime

# A fixed instant for the tests that assert exact wall-clock instants: the real
# clock advances between two calls and its granularity is a system detail
# (~15.6ms on Windows), so "the same instant" could only be asserted by luck.
# 2020-01-01 00:00 UTC (PatchTime reads a naive value as UTC).
FROZEN_NOW = datetime(2020, 1, 1, 0, 0, 0)


@pytest.fixture
def frozen_clock():
    """
    Freeze the wall clock of the test at a fixed instant (PatchTime)

    For the tests whose assertions compare instants derived from the wall clock
    (``time.time()`` / ``datetime.now()``): with the clock frozen the two reads
    are the same value, so the comparison is exact instead of winning or losing
    on the clock granularity between the two calls. The patched functions
    advance when the test calls ``time.sleep``.

    trio's own clock is unaffected (it captured ``time.perf_counter`` at import
    time), so the sleeps and deadlines of a trio test keep working.
    """
    with PatchTime(FROZEN_NOW) as clock:
        yield clock


@pytest.fixture(autouse=True)
def restart_state():
    """
    Reset the restart manager, the check module and the daily bookkeeping around
    every test

    The singletons are process-wide while the tests run many "backends" in one
    process: a test that claims the flow slot, freezes a handover or binds the
    check module must not leak into the next one.
    """
    from alasio.backend.app.restart.daily import DAILY
    from alasio.backend.app.restart.restart_manager import RESTART_MANAGER
    from alasio.backend.app.restart.update_check import UPDATE_CHECK

    RESTART_MANAGER.reset()
    UPDATE_CHECK.reset()
    DAILY.reset()
    yield
    RESTART_MANAGER.reset()
    UPDATE_CHECK.reset()
    DAILY.reset()


@pytest.fixture(autouse=True)
def mute_log_file():
    """
    Keep the test process from opening log files in the project log directory

    Every log line written by the test process itself (pytest's module name is
    "__main__") would create log/{date}__main__.txt in the repository; a full
    run leaves a large file of test noise behind. The file target is muted
    around every backend test.

    Log assertions keep working: the tests use logger.mock_capture_writer(),
    which swaps the whole writer and captures in front of the (muted) file
    target. Function scoped on purpose: a test that resets the logger fd cache
    (alasio.testing.filesystem) must not resurrect the real file mid-test.

    Note: this only covers the pytest process. Processes spawned by tests
    (worker mods, supervisor fakes, the real gui.py chain) mute themselves /
    keep their own logs, see worker_mods.mute_test_worker_logging and
    supervisor/backends.py.
    """
    LogWriter().mute(fd=True)
    yield
    # drop whatever fd the test left cached (real or fake) before unmuting
    LogWriter().close_fd()
    LogWriter().mute_clear()


@pytest.fixture
def free_port():
    """
    Provide a free localhost port for tests that bind a listener.

    The port is reserved by binding to 127.0.0.1:0 (the OS assigns an
    unused port) and released before the test binds it again, so tests
    never collide with a running backend or with each other.

    Note: create_config treats `--port 0` as "not given" (falsy) and
    falls back to the configured port (8000 by default), so tests of the
    backend must pass the returned port explicitly.

    Returns:
        int: An unused port on 127.0.0.1
    """
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]
