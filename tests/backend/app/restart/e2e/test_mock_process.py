"""
The worker peer of the runs: who closes its end of the pipe

The peer of the restart runs is an in-process thread, so its pipe is a real
``multiprocessing`` connection of this process, shared by the thread that
terminates the peer and by the peer itself. Only one thread may close it, see
``MockWorkerProcess``.

The waits of these tests are real waits: the runs freeze ``time.sleep`` and the
clock, so a wait on them would return before the peer ever ran.
"""
import socket
import threading

import msgspec

from alasio.backend.worker.event import CommandEvent

from .conftest import MockProcessContext


class RecordingConn:
    """
    The connection of a peer, recording who closes it and whether it was open

    Delegates everything but ``close()``, so the peer speaks the real wire
    protocol while the test sees who closed the pipe and in what state it found
    it.
    """

    def __init__(self, conn):
        self.conn = conn
        self.closers = []

    def __getattr__(self, name):
        return getattr(self.conn, name)

    def close(self):
        self.closers.append((threading.current_thread().name, self.conn.closed))
        self.conn.close()


def make_peer():
    """
    Returns:
        tuple: The peer, its recording connection and the other end of the pipe
    """
    context = MockProcessContext()
    parent, child = context.Pipe()
    process = context.Process(args=('WorkerTestScheduler', 'cfg_a', child), name='cfg_a')
    recording = RecordingConn(process.conn)
    process.conn = recording
    return process, recording, parent


def close_counts(recording):
    """
    Returns:
        tuple: (closes that found the pipe open, closes that found it closed)
    """
    opened = [name for name, closed in recording.closers if not closed]
    closed = [name for name, closed in recording.closers if closed]
    return opened, closed


def test_terminate_ends_a_parked_peer_and_closes_the_pipe_once():
    """
    A terminated peer leaves its blocked read and closes its pipe exactly once

    A real worker process is ended from the outside, and its blocked read never
    returns: terminate() does the same through the pipe. Two closers of one
    handle is what the runs must not do -- a Windows pipe handle closed twice
    closes whatever the number was handed to next, which killed the test worker
    with an access violation instead of failing a test.
    """
    process, recording, parent = make_peer()
    process.start()
    try:
        # some time in the read of the pipe, not in the spawn of the thread
        threading.Event().wait(0.05)
        process.terminate()
        opened, closed = close_counts(recording)
        assert process.exitcode == 0
        assert not process.is_alive()
        assert len(opened) == 1, recording.closers
        assert opened[0] == 'MainThread', recording.closers
        assert sorted(closed) == ['cfg_a'], recording.closers
    finally:
        parent.close()


def test_a_peer_stopped_by_a_command_closes_its_own_pipe_once():
    """
    A peer that leaves by itself closes its pipe, and terminate() leaves it alone
    """
    process, recording, parent = make_peer()
    process.start()
    try:
        parent.send_bytes(msgspec.msgpack.encode(CommandEvent(c='force-kill')))
        process.join(5.0)
        assert not process.is_alive()
        opened, closed = close_counts(recording)
        assert opened == ['cfg_a'], recording.closers
        assert closed == [], recording.closers
        # the peer is gone: terminate() has no read to wake, and must not close
        # the pipe a second time
        process.terminate()
        assert close_counts(recording) == (['cfg_a'], []), recording.closers
        assert process.exitcode == 0
    finally:
        parent.close()


def test_terminate_of_a_peer_that_never_started_closes_its_pipe():
    """
    A peer without a thread has no owner of its pipe: terminate closes it
    """
    process, recording, parent = make_peer()
    try:
        process.terminate()
        assert process.exitcode == -15
        assert close_counts(recording) == (['MainThread'], []), recording.closers
    finally:
        parent.close()


class SocketConn:
    """
    The peer end of a socket pair, shaped like the pipe of a POSIX spawn

    On POSIX the pipe of a duplex ``Pipe()`` is a socketpair and the peer reads
    it with a blocking ``os.read()``, which no ``close()`` wakes. The Windows
    named pipe is not a socket, so this stands in for the POSIX side and runs on
    every platform.
    """

    def __init__(self, sock):
        self._sock = sock
        self.closed = False

    def fileno(self):
        return self._sock.fileno()

    def recv_bytes(self):
        data = self._sock.recv(4096)
        if not data:
            raise EOFError
        return data

    def close(self):
        self.closed = True
        self._sock.close()


def test_closing_the_peer_end_wakes_a_blocked_read():
    """
    The close ends a read parked in the pipe, like the death of a process does

    A POSIX close alone does not wake a read blocked in another thread, only
    data, EOF or ``shutdown()`` do: without the shutdown a terminated peer stays
    parked until its pipe breaks for another reason, which is what kept the
    Windows crash fix from working on the POSIX jobs.
    """
    process, _, parent = make_peer()
    left, right = socket.socketpair()
    conn = SocketConn(right)
    process.conn = conn
    outcome = []
    reading = threading.Event()

    def read():
        reading.set()
        try:
            conn.recv_bytes()
            outcome.append('data')
        except (EOFError, OSError) as exc:
            outcome.append(type(exc).__name__)

    thread = threading.Thread(target=read, name='cfg_a', daemon=True)
    thread.start()
    try:
        reading.wait()
        threading.Event().wait(0.05)
        process._close_conn()
        thread.join(5.0)
        assert not thread.is_alive(), 'the read of the peer was not woken'
        assert outcome and outcome[0] in ('EOFError', 'OSError'), outcome
        assert conn.closed
    finally:
        left.close()
        parent.close()
