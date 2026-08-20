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


class TestStopStdinListenerWhenBlocked:
    """The listener must exit even when stuck on a blocking readline."""

    def test_unblocks_blocked_readline(self, replace_stdin):
        # Pipe holds data without a newline, so readline() blocks forever
        stream, write_fd = fake_stdin_from_pipe(b'partial data without newline')
        replace_stdin(stream)
        supervisor, _ = make_supervisor_with_pipe()

        thread = supervisor.start_stdin_listener()
        # Give the listener time to enter the blocking readline
        time.sleep(0.2)
        assert thread.is_alive()

        supervisor.stop_stdin_listener()

        assert not thread.is_alive()
        assert supervisor._stdin_thread is None
        os.close(write_fd)

    def test_stop_while_pipe_partially_written(self, replace_stdin):
        # Partial data without a newline used to block readline() forever;
        # the listener must still exit promptly on stop
        stream, write_fd = fake_stdin_from_pipe(b'command:st')
        replace_stdin(stream)
        supervisor, _ = make_supervisor_with_pipe()

        thread = supervisor.start_stdin_listener()
        time.sleep(0.2)
        assert thread.is_alive()

        supervisor.stop_stdin_listener()

        assert not thread.is_alive()
        assert supervisor._stdin_thread is None
        os.close(write_fd)


class TestRunStopsListener:
    """Supervisor.run() must stop the stdin listener before returning."""

    def test_stops_listener_on_exit(self, replace_stdin, monkeypatch):
        import signal

        stream, write_fd = fake_stdin_from_pipe()
        replace_stdin(stream)
        supervisor = Supervisor()
        supervisor.backend_entry = staticmethod(lambda args: None)

        # Listener running, as if a previous backend had started
        thread = supervisor.start_stdin_listener()
        assert thread.is_alive()

        def fake_start_backend(args):
            raise KeyboardInterrupt

        monkeypatch.setattr(supervisor, 'start_backend', fake_start_backend)

        try:
            supervisor.run([])
        finally:
            # run() installs custom signal handlers, restore the defaults
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            if sys.platform == 'win32':
                signal.signal(signal.SIGBREAK, signal.SIG_DFL)

        assert not thread.is_alive()
        assert supervisor._stdin_thread is None
        os.close(write_fd)


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


class TestHandleStdinLine:
    """Tests for Supervisor._handle_stdin_line."""

    def test_command_stop_forwards_and_stops(self):
        supervisor, child_conn = make_supervisor_with_pipe()

        assert supervisor._handle_stdin_line(b'command:stop\n') is True
        assert supervisor.stop_requested is True
        assert recv_with_timeout(child_conn) == b'command:stop'

    def test_command_stop_crlf(self):
        supervisor, child_conn = make_supervisor_with_pipe()

        assert supervisor._handle_stdin_line(b'command:stop\r\n') is True
        assert recv_with_timeout(child_conn) == b'command:stop'

    def test_command_stop_no_parent_conn(self):
        supervisor = Supervisor()

        assert supervisor._handle_stdin_line(b'command:stop') is True
        assert supervisor.stop_requested is True

    def test_command_stop_broken_pipe_still_stops(self):
        supervisor, child_conn = make_supervisor_with_pipe()
        child_conn.close()

        assert supervisor._handle_stdin_line(b'command:stop\n') is True
        assert supervisor.stop_requested is True

    def test_unknown_line_ignored(self):
        supervisor, child_conn = make_supervisor_with_pipe()

        assert supervisor._handle_stdin_line(b'garbage\n') is False
        assert supervisor.stop_requested is False
        assert not child_conn.poll(timeout=0.2)

    def test_line_with_surrounding_spaces(self):
        supervisor, _ = make_supervisor_with_pipe()

        assert supervisor._handle_stdin_line(b'  command:stop  \n') is True
        assert supervisor.stop_requested is True

    def test_command_set_lang_forwards_without_stopping(self):
        supervisor, child_conn = make_supervisor_with_pipe()

        assert supervisor._handle_stdin_line(b'command:set_lang:zh-CN\n') is False
        assert supervisor.stop_requested is False
        assert recv_with_timeout(child_conn) == b'command:set_lang:zh-CN'

    def test_command_set_lang_system_forwards(self):
        supervisor, child_conn = make_supervisor_with_pipe()

        assert supervisor._handle_stdin_line(b'command:set_lang:system\n') is False
        assert supervisor.stop_requested is False
        assert recv_with_timeout(child_conn) == b'command:set_lang:system'

    def test_command_set_theme_forwards_without_stopping(self):
        supervisor, child_conn = make_supervisor_with_pipe()

        assert supervisor._handle_stdin_line(b'command:set_theme:dark\n') is False
        assert supervisor.stop_requested is False
        assert recv_with_timeout(child_conn) == b'command:set_theme:dark'

    def test_command_set_lang_crlf(self):
        supervisor, child_conn = make_supervisor_with_pipe()

        assert supervisor._handle_stdin_line(b'command:set_lang:zh-TW\r\n') is False
        assert recv_with_timeout(child_conn) == b'command:set_lang:zh-TW'

    def test_command_set_lang_no_parent_conn(self):
        supervisor = Supervisor()

        # Without a backend the command is dropped, the listener keeps running
        assert supervisor._handle_stdin_line(b'command:set_lang:zh-CN') is False
        assert supervisor.stop_requested is False

    def test_command_set_lang_broken_pipe_still_continues(self):
        supervisor, child_conn = make_supervisor_with_pipe()
        child_conn.close()

        # Broken pipe must not raise; the listener keeps running
        assert supervisor._handle_stdin_line(b'command:set_lang:zh-CN\n') is False
        assert supervisor.stop_requested is False

    def test_command_set_lang_unknown_value_still_forwards(self):
        # The supervisor forwards verbatim without validating the value:
        # validation belongs to the backend (which can be updated without
        # touching the supervisor).
        supervisor, child_conn = make_supervisor_with_pipe()

        assert supervisor._handle_stdin_line(b'command:set_lang:fr-FR\n') is False
        assert recv_with_timeout(child_conn) == b'command:set_lang:fr-FR'

    def test_any_command_prefix_forwards(self):
        # Any command:* line is forwarded verbatim, the backend owns the
        # semantics; the listener keeps running
        supervisor, child_conn = make_supervisor_with_pipe()

        assert supervisor._handle_stdin_line(b'command:set_font:big\n') is False
        assert supervisor.stop_requested is False
        assert recv_with_timeout(child_conn) == b'command:set_font:big'

    def test_command_prefix_broken_pipe_still_continues(self):
        supervisor, child_conn = make_supervisor_with_pipe()
        child_conn.close()

        # Broken pipe must not raise; the listener keeps running
        assert supervisor._handle_stdin_line(b'command:set_font:big\n') is False
        assert supervisor.stop_requested is False


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
