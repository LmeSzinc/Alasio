"""
The worker peer of the runs: who owns its end of the pipe

The peer of the restart runs is an in-process thread, so its pipe is a real
``multiprocessing`` connection of this process, shared by the thread that
terminates the peer and by the peer itself. Only the peer may close it, see
``MockWorkerProcess``.
"""
import threading

from .conftest import MockProcessContext


class RecordingConn:
    """
    The connection of a peer, recording the thread that closes it

    Delegates everything but ``close()``, so the peer speaks the real wire
    protocol while the test sees who closed the pipe.
    """

    def __init__(self, conn):
        self.conn = conn
        self.closers = []

    def __getattr__(self, name):
        return getattr(self.conn, name)

    def close(self):
        self.closers.append(threading.current_thread().name)
        self.conn.close()


def test_terminate_leaves_the_pipe_to_the_peer():
    """
    A terminated peer closes its own connection and nobody else does

    A Windows pipe handle closed by two threads is undefined: the terminating
    thread and the peer can both pass the closed check of
    ``Connection.close()`` and close the same handle twice, or close it under
    a pending overlapped read -- the process dies with an access violation
    instead of failing the test.
    """
    context = MockProcessContext()
    parent, child = context.Pipe()
    process = context.Process(args=('WorkerTestScheduler', 'cfg_a', child), name='cfg_a')
    recording = RecordingConn(process.conn)
    process.conn = recording
    process.start()
    try:
        process.terminate()
        assert process.exitcode == 0
        assert not process.is_alive()
        assert recording.closers == ['cfg_a'], recording.closers
    finally:
        parent.close()


def test_terminate_of_a_peer_that_never_started_closes_its_pipe():
    """
    A peer without a thread has no owner of its pipe: terminate closes it
    """
    context = MockProcessContext()
    parent, child = context.Pipe()
    process = context.Process(args=('WorkerTestScheduler', 'cfg_a', child), name='cfg_a')
    recording = RecordingConn(process.conn)
    process.conn = recording
    try:
        process.terminate()
        assert process.exitcode == -15
        assert recording.closers == ['MainThread'], recording.closers
    finally:
        parent.close()
