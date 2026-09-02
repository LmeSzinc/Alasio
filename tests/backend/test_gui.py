"""
Real-environment tests for the gui.py entry.

Starting `python gui.py` must bring up the whole process chain
(supervisor -> backend via multiprocessing spawn) and shut down cleanly
afterwards. The detailed behaviors of the supervisor / backend / worker
are covered by unit tests; this file only verifies the real startup and
shutdown path, talking to the process through the stdin command channel
exactly like the Electron webapp does.
"""
import os
import socket

from alasio.testing.managed_process import ManagedProcess

# Project root, gui.py sits at the top level
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GUI_PATH = os.path.join(ROOT, 'gui.py')

# The backend imports starlette / trio / hypercorn and binds the port;
# slow machines need a generous window
BACKEND_START_TIMEOUT = 30
# Supervisor graceful shutdown window (graceful_shutdown_timeout=5s default)
# plus process teardown margin
SHUTDOWN_TIMEOUT = 15


def _free_port():
    """
    Pick a free localhost port for the backend.

    Returns:
        int: An unused port
    """
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class TestGuiStartup:
    """gui.py must start the real supervisor + backend chain and shut down cleanly."""

    def test_startup_and_graceful_shutdown(self):
        """
        Start gui.py with explicit host/port, wait for the real backend
        (hypercorn prints "Running on http"), then stop the whole chain
        through the stdin command channel; the supervisor must exit 0.
        """
        port = _free_port()
        with ManagedProcess(GUI_PATH, '--host', '127.0.0.1', '--port', str(port)) as proc:
            # the real backend must come up: hypercorn announces the bind
            proc.wait_for_output('Running on http', timeout=BACKEND_START_TIMEOUT)

            # graceful stop through the stdin command channel (Electron path)
            proc.process.stdin.write('command:stop\n')
            proc.process.stdin.flush()

            code = proc.wait_for_exit(timeout=SHUTDOWN_TIMEOUT)
            assert code == 0, proc.get_output()
            # the supervisor finished its loop cleanly
            assert proc.has_output('Supervisor loop ended'), proc.get_output()

    def test_stop_right_after_ready(self):
        """
        A stop written right after the backend is ready must be processed
        immediately.

        Regression for the slow webapp close: the stdin listener only
        starts after recv_loop's startup confirmation. Before the backend
        announced command:started (right after binding its listeners) the
        confirmation waited out the whole startup_timeout (5s), so a stop
        written right after ready sat unread for ~4s -- longer than the
        Electron close flow (2s+2s) waits before force-killing the tree.
        """
        port = _free_port()
        with ManagedProcess(GUI_PATH, '--host', '127.0.0.1', '--port', str(port)) as proc:
            # the real backend must come up: hypercorn announces the bind
            proc.wait_for_output('Running on http', timeout=BACKEND_START_TIMEOUT)

            # stop immediately after ready: if the stdin listener were
            # still down, the stop would sit in the pipe until the
            # startup window ended (~4s later)
            proc.process.stdin.write('command:stop\n')
            proc.process.stdin.flush()

            code = proc.wait_for_exit(timeout=3)
            assert code == 0, proc.get_output()
            # the supervisor finished its loop cleanly
            assert proc.has_output('Supervisor loop ended'), proc.get_output()
