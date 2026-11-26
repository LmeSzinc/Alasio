"""
The clock of the runs: the instant is the test's, a duration is the real clock

The workers of the runs are real processes and the manager waits for them by
``time.monotonic`` (``WorkerState.process_wait_exit`` polls a deadline), so the
two properties pinned here are the ones the rest of the suite stands on: an
instant a test picks (the daily maintenance is due on it), and waits that
really wait. Freezing the clock of durations would let every timeout of the
manager expire at once, and the entry would take the kill path instead of the
one the run takes in production.
"""
import datetime
import time

from .conftest import NOW, at

# The clocks FrozenWallClock documents as untouched, bound at import time so a
# patch of the module attribute cannot move the reference under the test
REAL_SLEEP = time.sleep
REAL_MONOTONIC = time.monotonic
REAL_PERF_COUNTER = time.perf_counter


class TestFrozenWallClock:
    def test_the_wall_clock_is_the_instant_of_the_run(self):
        """The run starts at 2026-10-13 02:00 local, before the 03:50 target"""
        assert time.time() == at(*NOW)
        assert datetime.datetime.now().timestamp() == at(*NOW)
        assert datetime.datetime.now().date() == datetime.date(2026, 10, 13)

    def test_the_test_moves_the_wall_clock_over_the_daily_target(self, clock):
        """A crossing is what the daily flow is due at, and set() is the crossing"""
        clock.set(at(4, 0))
        assert time.time() == at(4, 0)
        assert datetime.datetime.now().hour == 4

    def test_the_clocks_of_durations_are_the_real_ones(self):
        """
        The instant is patched, the clocks of durations are not

        Asserted on identity rather than on a measurement: the objects are the
        ones the interpreter had before the fixture ran, and that is exactly the
        property the runs depend on. A frozen sleep would not be a slow sleep,
        it would be another function.
        """
        assert time.sleep is REAL_SLEEP
        assert time.monotonic is REAL_MONOTONIC
        assert time.perf_counter is REAL_PERF_COUNTER

    def test_a_sleep_really_sleeps(self):
        """
        time.sleep is not patched: it waits, the way a worker's stop waits

        The wait is measured with perf_counter, the high resolution counter of
        the platform. time.monotonic is the millisecond tick of GetTickCount64
        on Windows, which is far too coarse to time a short wait: a read on it
        can report a wait of 20ms as one tick of 15.6ms.
        """
        start = time.perf_counter()
        time.sleep(0.2)
        # a frozen sleep returns at once, and a real one cannot return in half
        # the time it was asked for
        assert time.perf_counter() - start >= 0.1

    def test_the_wall_clock_holds_still_while_the_real_clock_runs(self):
        """The passage of time is not what the run reads: only the instant is"""
        before = time.time()
        time.sleep(0.02)
        assert time.time() == before
