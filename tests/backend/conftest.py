"""
Fixtures for the backend tests.
"""

import socket

import pytest

from alasio.logger.writer import LogWriter


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
