"""
The worker peer of the runs: who closes its end of the pipe

The peer of the restart runs is an in-process thread, so its pipe is a real
``multiprocessing`` connection of this process, shared by the thread that
terminates the peer and by the peer itself. Only the peer closes it, see
``MockWorkerProcess``.

The waits of these tests are real waits: the runs freeze ``time.sleep`` and the
clock, so a wait on them would return before the peer ever ran.
"""
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
    A terminated peer leaves its wait on the pipe and closes the pipe itself

    A real worker process is ended from the outside, and its blocked read never
    returns. A peer has no process to end, so terminate() tells it to go down
    and the peer leaves its own wait, at its next look at the pipe.

    The pipe of a peer is a real connection of this process. A Windows pipe
    handle closed under a read that another thread still has in flight, or
    closed twice, closes whatever the number was handed to next, which killed
    the test worker with an access violation instead of failing a test: the
    terminating thread must not touch the handle at all.
    """
    process, recording, parent = make_peer()
    process.start()
    try:
        # some time in the wait of the pipe, not in the spawn of the thread
        threading.Event().wait(0.05)
        process.terminate()
        opened, closed = close_counts(recording)
        assert process.exitcode == 0
        assert not process.is_alive()
        assert opened == ['cfg_a'], recording.closers
        assert closed == [], recording.closers
    finally:
        parent.close()


def test_terminate_of_a_peer_that_never_started_closes_its_pipe():
    """
    A peer without a thread has no reader of its pipe: terminate closes it
    """
    process, recording, parent = make_peer()
    try:
        process.terminate()
        assert process.exitcode == -15
        assert close_counts(recording) == (['MainThread'], []), recording.closers
    finally:
        parent.close()


def test_a_terminated_peer_is_reported_as_a_kill_without_closing():
    """
    A peer that is still parked keeps its pipe: terminate reports the kill

    A peer parked in its own wait cannot be moved out of it by the terminating
    thread, and closing the pipe underneath it is exactly the undefined
    operation the runs must not do. The exit status reports the kill and the
    peer keeps ownership of the pipe; it closes the pipe itself when its wait
    is over.
    """
    process, recording, parent = make_peer()
    # the peer parks for much longer than terminate() is willing to wait
    process.POLL_INTERVAL = 0.5
    process.TERMINATE_TIMEOUT = 0.05
    process.start()
    try:
        assert process.waiting.wait(1.0), 'the peer did not reach its wait'
        process.terminate()
        assert close_counts(recording) == ([], []), recording.closers
        assert process.exitcode == -15
        assert process.conn is not None
    finally:
        process.join(5.0)
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
        # the peer is gone: terminate() has no read to end, and must not close
        # the pipe a second time
        process.terminate()
        assert close_counts(recording) == (['cfg_a'], []), recording.closers
        assert process.exitcode == 0
    finally:
        parent.close()
