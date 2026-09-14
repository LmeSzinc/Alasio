"""
Hold a worker spawn inside its spawn window until the test releases it

The spawn window is the interval between the "starting" mark (the pipe is
already registered by then, see WorkerManager._mark_starting_locked) and the
moment process.start() returned. A test that places a stop / kill request in
that window needs the spawn to stay there while the request is sent: the gate
parks the spawn until the test releases it, so the request is provably sent
inside the window and nothing waits on a fixed delay (a sleep long enough for a
slow machine only slows down the normal case and still leaves a race).
"""
import threading

from alasio.backend.worker.manager import WorkerManager


class SpawnGate:
    """
    Park the next worker spawn inside its spawn window until release()

    The patch is undone by the monkeypatch fixture at the end of the test (the
    gate never unpatches itself, a test failing early must not leave
    WorkerManager patched).

    Args:
        monkeypatch: pytest monkeypatch fixture
        timeout (float): Seconds a parked spawn waits for release() before it
            raises (in the spawn thread, the test fails right after)
    """

    def __init__(self, monkeypatch, timeout=10.0):
        self._timeout = timeout
        self._entered = threading.Event()
        self._release = threading.Event()
        # the patched method receives the manager as its first argument, so the
        # gate state must be captured by the closure (not read from "self")
        entered = self._entered
        release = self._release
        original = WorkerManager._worker_start_process

        def gated(manager, *args, **kwargs):
            entered.set()
            if not release.wait(timeout):
                raise RuntimeError('The spawn gate was never released')
            return original(manager, *args, **kwargs)

        monkeypatch.setattr(WorkerManager, '_worker_start_process', gated)

    def wait_entered(self, timeout=None):
        """
        Wait until a spawn is parked inside its spawn window

        Args:
            timeout (float): Seconds to wait at most. Defaults to the gate
                timeout
        """
        if not self._entered.wait(self._timeout if timeout is None else timeout):
            raise AssertionError('No worker spawn reached its spawn window')

    def release(self):
        """Let the parked spawn create its process"""
        self._release.set()
