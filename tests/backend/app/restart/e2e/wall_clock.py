"""
The wall clock of the restart runs: frozen at an instant the test picks

The runs decide when the daily maintenance is due (``datetime.now()`` against
the 03:50 target) and what the resume file records (``time.time()``), so the
wall clock has to be the test's, not the clock of the machine the suite runs
on.

The clock that measures *durations* is left alone: ``time.monotonic``,
``time.perf_counter`` and ``time.sleep`` stay real. The workers of the runs are
real processes (``alasio.backend.worker.entry``), and the manager waits for
them by the real clock -- ``WorkerState.process_wait_exit`` polls a deadline it
computes from ``time.monotonic``. This is why the shared ``PatchTime`` of the
test suite, which freezes that clock as well and turns ``time.sleep`` into an
instant advance of the mocked timestamp, is not the tool here: under it every
timeout of the manager expires in microseconds, the wait for a worker that is
exiting on its own gives up at once, and the entry takes the kill path
(``Graceful killing process``) instead of the path the run takes in production.

What the tests need from the wall clock is the instant, not the passage of
time: a test moves the clock over the daily target with ``set()``, while the
processes it drives keep waiting on the real clock.
"""
import datetime
import time

import pytest


class FrozenWallClock:
    """
    ``datetime.now()`` / ``time.time()`` pinned to an instant, durations real

    Attributes:
        timestamp (float): The epoch second the wall clock reads
    """

    def __init__(self, timestamp):
        """
        Args:
            timestamp (float): The epoch second the wall clock reads
        """
        self.timestamp = float(timestamp)
        self._monkeypatch = pytest.MonkeyPatch()

    def set(self, timestamp):
        """
        Move the wall clock (the daily target of a test is crossed this way)

        Args:
            timestamp (float): The epoch second the wall clock reads from now on
        """
        self.timestamp = float(timestamp)

    def __enter__(self):
        clock = self

        def read():
            """The epoch second of the wall clock, read at call time"""
            return clock.timestamp

        class MockDatetime(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    # a naive datetime is the local wall clock, like the real now()
                    return datetime.datetime.fromtimestamp(read())
                return datetime.datetime.fromtimestamp(read(), datetime.timezone.utc).astimezone(tz)

            @classmethod
            def utcnow(cls):
                return datetime.datetime.fromtimestamp(read(), datetime.timezone.utc).replace(tzinfo=None)

            @classmethod
            def today(cls):
                return cls.now()

        # The wall clock: the instant the runs decide by. Only the classes that
        # read an instant are replaced -- datetime.date stays the real one: the
        # readers of today() bind it at import time (``from datetime import
        # date``, the logger names its file by it) and a module attribute that
        # no longer is the real class breaks every isinstance() of a date
        self._monkeypatch.setattr(datetime, 'datetime', MockDatetime)
        self._monkeypatch.setattr(time, 'time', read)
        self._monkeypatch.setattr(time, 'time_ns', lambda: int(read() * 1_000_000_000))
        # time.monotonic / perf_counter / sleep are NOT patched, see the module
        # docstring: the timeouts of the manager and the workers are real waits
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._monkeypatch.undo()
