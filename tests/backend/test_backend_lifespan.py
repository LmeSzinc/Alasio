import builtins
import multiprocessing
import threading

import trio

from alasio.backend import lifespan
from alasio.backend.lifespan import mpipe_recv_loop


class TestAnnounceStarted:
    """
    Tests for announce_started: the pipe readiness announcement that ends
    the supervisor's startup window early (see BackendConfig.create_sockets
    in app.py).
    """

    def test_sends_command_started_on_pipe(self, monkeypatch):
        """
        announce_started must send b'command:started' over the supervisor
        pipe
        """
        parent_conn, child_conn = multiprocessing.Pipe()
        monkeypatch.setattr(builtins, '__mpipe_conn__', child_conn, raising=False)
        try:
            lifespan.announce_started()

            assert parent_conn.poll(timeout=1)
            assert parent_conn.recv_bytes() == b'command:started'
        finally:
            parent_conn.close()
            child_conn.close()

    def test_no_pipe_is_noop(self, monkeypatch):
        """
        announce_started without a supervisor pipe (e.g. standalone runs
        or tests that call config.create_sockets directly) must not raise
        """
        monkeypatch.delattr(builtins, '__mpipe_conn__', raising=False)
        lifespan.announce_started()


class TestMpipeRecvLoop:
    """
    Tests for mpipe_recv_loop: the shutdown event must always be set when
    the supervisor disconnects or requests a stop, even if logging fails
    (e.g. the stdout pipe is broken because the host electron process died).
    """

    def _make(self, monkeypatch):
        """
        Create a fresh shutdown event and a pipe pair

        Args:
            monkeypatch: pytest monkeypatch fixture

        Returns:
            tuple: (parent_conn, event, child_conn)
        """
        event = trio.Event()
        monkeypatch.setattr(lifespan, 'SHUTDOWN_EVENT', event)
        parent_conn, child_conn = multiprocessing.Pipe()
        return parent_conn, event, child_conn

    @staticmethod
    def _run(loop_body):
        """
        Run an async body inside a real trio run

        Args:
            loop_body (callable): async function to run
        """
        trio.run(loop_body)

    def test_conn_eof_sets_shutdown_event(self, monkeypatch):
        """
        When the supervisor's end of the pipe is closed (supervisor died),
        the shutdown event must be set and the listener thread must exit
        """
        parent_conn, event, child_conn = self._make(monkeypatch)
        parent_conn.close()

        async def main():
            token = trio.lowlevel.current_trio_token()
            thread = threading.Thread(
                target=mpipe_recv_loop, args=(child_conn, token), daemon=True)
            thread.start()
            with trio.fail_after(5):
                await event.wait()
            thread.join(timeout=2)
            assert not thread.is_alive()

        self._run(main)
        child_conn.close()

    def test_command_stop_sets_shutdown_event(self, monkeypatch):
        """
        A command:stop message must set the shutdown event and stop the
        listener thread
        """
        parent_conn, event, child_conn = self._make(monkeypatch)

        async def main():
            token = trio.lowlevel.current_trio_token()
            thread = threading.Thread(
                target=mpipe_recv_loop, args=(child_conn, token), daemon=True)
            thread.start()
            await trio.sleep(0.1)
            parent_conn.send_bytes(b'command:stop')
            with trio.fail_after(5):
                await event.wait()
            thread.join(timeout=2)
            assert not thread.is_alive()

        self._run(main)
        parent_conn.close()
        child_conn.close()

    def test_shutdown_event_set_even_when_stdout_broken(self, monkeypatch):
        """
        The zombie bug: when the host process died, the stdout pipe is broken
        and logger.info raises. The shutdown event must still be set, so the
        backend never stays running as an orphan
        """
        from alasio.ext.cache import cached_property_threadsafe
        from alasio.logger.writer import LogWriter

        class RaiseStream:
            """
            A stream whose write/flush always raise OSError, simulating a
            broken stdout pipe
            """

            def write(self, text):
                raise OSError(22, 'Invalid argument')

            def flush(self):
                raise OSError(22, 'Invalid argument')

        parent_conn, event, child_conn = self._make(monkeypatch)
        parent_conn.close()

        writer = LogWriter()
        cached_property_threadsafe.set(writer, 'stdout', RaiseStream())
        try:
            async def main():
                token = trio.lowlevel.current_trio_token()
                thread = threading.Thread(
                    target=mpipe_recv_loop, args=(child_conn, token), daemon=True)
                thread.start()
                with trio.fail_after(5):
                    await event.wait()
                thread.join(timeout=2)
                assert not thread.is_alive()

            self._run(main)
        finally:
            cached_property_threadsafe.pop(writer, 'stdout')
        child_conn.close()

    def test_command_stop_sets_shutdown_event_even_when_stdout_broken(self, monkeypatch):
        """
        Same as above but through the command:stop path: the stop request is
        received, the logger fails, and the shutdown event must still be set
        """
        from alasio.ext.cache import cached_property_threadsafe
        from alasio.logger.writer import LogWriter

        class RaiseStream:
            """
            A stream whose write/flush always raise OSError, simulating a
            broken stdout pipe
            """

            def write(self, text):
                raise OSError(22, 'Invalid argument')

            def flush(self):
                raise OSError(22, 'Invalid argument')

        parent_conn, event, child_conn = self._make(monkeypatch)

        writer = LogWriter()
        cached_property_threadsafe.set(writer, 'stdout', RaiseStream())
        try:
            async def main():
                token = trio.lowlevel.current_trio_token()
                thread = threading.Thread(
                    target=mpipe_recv_loop, args=(child_conn, token), daemon=True)
                thread.start()
                await trio.sleep(0.1)
                parent_conn.send_bytes(b'command:stop')
                with trio.fail_after(5):
                    await event.wait()
                thread.join(timeout=2)
                assert not thread.is_alive()

            self._run(main)
        finally:
            cached_property_threadsafe.pop(writer, 'stdout')
        parent_conn.close()
        child_conn.close()

    def test_set_lang_forwards_without_stopping(self, monkeypatch):
        """
        command:set_lang must be forwarded to the prefs handler and the
        listener must keep running; command:stop afterwards still works
        """
        from alasio.backend import lifespan as lifespan_mod

        parent_conn, event, child_conn = self._make(monkeypatch)

        received = []
        monkeypatch.setattr(lifespan_mod, 'handle_stdin_set_lang',
                            lambda lang: received.append(lang))

        async def main():
            token = trio.lowlevel.current_trio_token()
            thread = threading.Thread(
                target=mpipe_recv_loop, args=(child_conn, token), daemon=True)
            thread.start()
            await trio.sleep(0.1)
            parent_conn.send_bytes(b'command:set_lang:zh-CN')
            await trio.sleep(0.1)
            assert not event.is_set()
            assert thread.is_alive()
            parent_conn.send_bytes(b'command:stop')
            with trio.fail_after(5):
                await event.wait()
            thread.join(timeout=2)
            assert not thread.is_alive()

        self._run(main)
        assert received == ['zh-CN']
        parent_conn.close()
        child_conn.close()
