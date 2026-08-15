import multiprocessing
import os
import sys
import threading
import time

import pytest

from alasio.backend.supervisor import Supervisor


@pytest.fixture
def replace_stdin():
    """Replace sys.stdin for the duration of a test."""
    original = sys.stdin

    def _replace(stream):
        sys.stdin = stream

    yield _replace
    sys.stdin = original


class FakeProcess:
    """Minimal stand-in for multiprocessing.Process used in shutdown tests."""

    def __init__(self, alive=True, exit_on_join=True):
        self._alive = alive
        self._exit_on_join = exit_on_join
        self.exitcode = None

    def is_alive(self):
        return self._alive

    def join(self, timeout=None):
        if self._exit_on_join:
            self._alive = False

    def terminate(self):
        self._alive = False

    def kill(self):
        self._alive = False


def fake_stdin_from_pipe(data=b''):
    """
    Create a fake stdin backed by a real OS pipe.

    BytesIO has no fileno(), while the stdin listener needs a real handle for
    msvcrt.get_osfhandle() / multiprocessing.connection.wait().

    Note: the caller must close the returned write fd after writing all data,
    so the listener sees EOF and never blocks on a readline.

    Args:
        data (bytes): Initial content written to the pipe

    Returns:
        tuple[io.TextIOWrapper, int]: Fake stdin stream and the write fd
    """
    read_fd, write_fd = os.pipe()
    if data:
        os.write(write_fd, data)
    return os.fdopen(read_fd, 'r', encoding='utf-8'), write_fd


def make_supervisor_with_pipe():
    """
    Create a Supervisor with a real pipe attached as parent_conn.

    Returns:
        tuple[Supervisor, PipeConnection]: supervisor and the child end of the pipe
    """
    parent_conn, child_conn = multiprocessing.Pipe()
    supervisor = Supervisor()
    supervisor.parent_conn = parent_conn
    return supervisor, child_conn


def recv_with_timeout(conn, timeout=2.0):
    """
    Receive a message from a pipe with a timeout.

    Args:
        conn (PipeConnection): Pipe to receive from
        timeout (float): Timeout in seconds. Defaults to 2.0.

    Returns:
        bytes: The received message
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if conn.poll(timeout=0.05):
            return conn.recv_bytes()
    pytest.fail('no message received on pipe')


class TestStartStdinListener:
    """Tests for Supervisor.start_stdin_listener."""

    def test_forwards_command_stop(self, replace_stdin):
        stream, write_fd = fake_stdin_from_pipe(b'command:stop\n')
        replace_stdin(stream)
        supervisor, child_conn = make_supervisor_with_pipe()
        os.close(write_fd)

        thread = supervisor.start_stdin_listener()
        thread.join(timeout=2)

        assert not thread.is_alive()
        assert recv_with_timeout(child_conn) == b'command:stop'
        assert supervisor.stop_requested is True

    def test_forwards_crlf_line_ending(self, replace_stdin):
        stream, write_fd = fake_stdin_from_pipe(b'command:stop\r\n')
        replace_stdin(stream)
        supervisor, child_conn = make_supervisor_with_pipe()
        os.close(write_fd)

        thread = supervisor.start_stdin_listener()
        thread.join(timeout=2)

        assert not thread.is_alive()
        assert recv_with_timeout(child_conn) == b'command:stop'

    def test_discards_unknown_input(self, replace_stdin):
        stream, write_fd = fake_stdin_from_pipe(b'hello world\nrandom text\n')
        replace_stdin(stream)
        supervisor, child_conn = make_supervisor_with_pipe()
        os.close(write_fd)

        thread = supervisor.start_stdin_listener()
        thread.join(timeout=2)

        assert not thread.is_alive()
        assert not child_conn.poll(timeout=0.2)
        assert supervisor.stop_requested is False

    def test_mixed_input_forwards_only_known(self, replace_stdin):
        stream, write_fd = fake_stdin_from_pipe(b'unknown\ncommand:stop\nignored\n')
        replace_stdin(stream)
        supervisor, child_conn = make_supervisor_with_pipe()
        os.close(write_fd)

        thread = supervisor.start_stdin_listener()
        thread.join(timeout=2)

        assert not thread.is_alive()
        assert recv_with_timeout(child_conn) == b'command:stop'
        assert not child_conn.poll(timeout=0.2)

    def test_stdin_eof_stops_listener(self, replace_stdin):
        stream, write_fd = fake_stdin_from_pipe()
        replace_stdin(stream)
        supervisor, child_conn = make_supervisor_with_pipe()
        os.close(write_fd)

        thread = supervisor.start_stdin_listener()
        thread.join(timeout=2)

        assert not thread.is_alive()
        assert not child_conn.poll(timeout=0.2)

    def test_no_stdin(self, replace_stdin):
        # sys.stdin may be None (e.g. pythonw), listener should exit silently
        replace_stdin(None)
        supervisor, _ = make_supervisor_with_pipe()

        thread = supervisor.start_stdin_listener()
        thread.join(timeout=2)

        assert not thread.is_alive()

    def test_no_parent_conn(self, replace_stdin):
        # Backend not started yet, stop command sets the flag without forwarding
        stream, write_fd = fake_stdin_from_pipe(b'command:stop\n')
        replace_stdin(stream)
        supervisor = Supervisor()
        os.close(write_fd)

        thread = supervisor.start_stdin_listener()
        thread.join(timeout=2)

        assert not thread.is_alive()
        assert supervisor.stop_requested is True

    def test_start_returns_none_when_running(self, replace_stdin):
        stream, write_fd = fake_stdin_from_pipe()
        replace_stdin(stream)
        supervisor, _ = make_supervisor_with_pipe()

        thread = supervisor.start_stdin_listener()
        assert supervisor.start_stdin_listener() is None
        assert thread.is_alive()

        # EOF lets the listener exit, then stop cleans up the state
        os.close(write_fd)
        thread.join(timeout=2)
        supervisor.stop_stdin_listener()
        assert not thread.is_alive()
        assert supervisor._stdin_thread is None

    def test_stop_stdin_listener(self, replace_stdin):
        stream, write_fd = fake_stdin_from_pipe()
        replace_stdin(stream)
        supervisor, _ = make_supervisor_with_pipe()

        thread = supervisor.start_stdin_listener()
        assert thread.is_alive()

        # EOF lets the listener exit, stop joins it and clears the state
        os.close(write_fd)
        supervisor.stop_stdin_listener()

        assert not thread.is_alive()
        assert supervisor._stdin_thread is None


class TestRecvLoopStartsListener:
    """Tests for recv_loop starting the stdin listener after startup."""

    def test_starts_listener_after_startup(self, replace_stdin):
        stream, write_fd = fake_stdin_from_pipe()
        replace_stdin(stream)
        supervisor, child_conn = make_supervisor_with_pipe()
        supervisor.startup_timeout = 0.3

        result = {}

        def run_recv_loop():
            result['ok'] = supervisor.recv_loop()

        thread = threading.Thread(target=run_recv_loop, daemon=True)
        thread.start()

        # after startup timeout, the stdin listener should be running
        time.sleep(0.8)
        assert supervisor._stdin_thread is not None
        assert supervisor._stdin_thread.is_alive()

        # close the child end of the pipe, recv_loop hits EOF and returns
        child_conn.close()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert result['ok'] is True

        os.close(write_fd)
        supervisor.stop_stdin_listener()
        assert supervisor._stdin_thread is None


class TestBackendProcessPickle:
    """The Supervisor instance must never be pickled into the backend child."""

    def test_process_args_carry_no_supervisor_instance(self):
        from alasio.backend.supervisor import _backend_process_entry

        ctx = multiprocessing.get_context('spawn')
        parent_conn, child_conn = ctx.Pipe()
        process = ctx.Process(
            target=_backend_process_entry,
            args=(child_conn, ['--port', '22267'], Supervisor.backend_entry),
        )

        # The backend entry must be a plain function, not a bound method: a
        # bound method would carry the Supervisor instance (with its
        # threading.Event) into the pickled child payload.
        entry = process._args[2]
        assert callable(entry)
        assert getattr(entry, '__self__', None) is None

        child_conn.close()
        parent_conn.close()

    def test_supervisor_instance_not_picklable(self):
        # threading.Event contains a lock, so the Supervisor instance itself
        # cannot be pickled. This documents why the process target must stay a
        # module-level function carrying no instance reference.
        import pickle

        with pytest.raises(Exception):
            pickle.dumps(Supervisor())


class TestStartBackendStopsListener:
    """The stdin listener must be fully stopped while spawning a backend."""

    def test_listener_stopped_during_spawn(self, replace_stdin, monkeypatch):
        import multiprocessing.process as mp_process

        stream, write_fd = fake_stdin_from_pipe()
        replace_stdin(stream)
        supervisor, _ = make_supervisor_with_pipe()

        # listener running, as if a previous backend were still alive
        thread = supervisor.start_stdin_listener()
        assert thread.is_alive()

        observed = {}

        def recording_start(self):
            observed['listener_alive'] = (
                supervisor._stdin_thread is not None and supervisor._stdin_thread.is_alive()
            )
            observed['stop_set'] = supervisor._stdin_stop.is_set()
            # do not actually spawn a child process

        monkeypatch.setattr(mp_process.BaseProcess, 'start', recording_start)
        supervisor.start_backend([])

        # spawn happens with the listener fully stopped...
        assert observed['listener_alive'] is False
        assert observed['stop_set'] is True
        # ...and it stays stopped until recv_loop restarts it after startup
        assert supervisor._stdin_thread is None

        os.close(write_fd)


class TestHandleBackendMessage:
    """Tests for Supervisor.handle_backend_message."""

    def test_command_stop_raises_keyboard_interrupt(self):
        supervisor = Supervisor()
        with pytest.raises(KeyboardInterrupt):
            supervisor.handle_backend_message(b'command:stop')
        assert supervisor.sigint_count == 1

    def test_restart_sets_flag(self):
        supervisor = Supervisor()
        supervisor.handle_backend_message(b'command:restart')
        assert supervisor.restart_requested is True

    def test_unknown_message_logs_warning(self, monkeypatch):
        from alasio.logger.writer import CaptureStream

        capture = CaptureStream()
        monkeypatch.setattr(sys, 'stdout', capture)
        supervisor = Supervisor()

        supervisor.handle_backend_message(b'unknown')

        assert capture.any_contains("Unknown command from backend: b'unknown'")
        assert supervisor.sigint_count == 0
        assert supervisor.restart_requested is False


class TestGracefulShutdown:
    """Tests for Supervisor.graceful_shutdown."""

    def test_sends_stop_and_cleans_up(self):
        supervisor, child_conn = make_supervisor_with_pipe()
        supervisor.process = FakeProcess()

        supervisor.graceful_shutdown()

        assert child_conn.poll(timeout=1)
        assert child_conn.recv_bytes() == b'command:stop'
        assert supervisor.process is None
        assert supervisor.parent_conn is None

    def test_no_process_returns_true(self):
        supervisor = Supervisor()
        assert supervisor.graceful_shutdown() is True

    def test_dead_process_returns_true_without_send(self):
        supervisor, child_conn = make_supervisor_with_pipe()
        supervisor.process = FakeProcess(alive=False)

        assert supervisor.graceful_shutdown() is True
        assert not child_conn.poll(timeout=0.5)

    def test_timeout_returns_false(self):
        supervisor = Supervisor(graceful_shutdown_timeout=0.5)
        supervisor.process = FakeProcess(alive=True, exit_on_join=False)

        assert supervisor.graceful_shutdown() is False
        assert supervisor.process is not None

    def test_no_parent_conn_still_waits(self):
        supervisor = Supervisor()
        supervisor.process = FakeProcess()

        supervisor.graceful_shutdown()

        assert supervisor.process is None
