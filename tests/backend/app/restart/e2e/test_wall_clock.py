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

    def test_a_sleep_really_sleeps(self):
        """time.sleep is not patched: it waits, the way a worker's stop waits"""
        # the two clocks have different origins (monotonic is the uptime on
        # Windows, perf_counter a high resolution counter): each is measured
        # against its own reading
        mono = time.monotonic()
        perf = time.perf_counter()
        time.sleep(0.02)
        assert time.monotonic() - mono >= 0.015
        assert time.perf_counter() - perf >= 0.015

    def test_the_wall_clock_holds_still_while_the_real_clock_runs(self):
        """The passage of time is not what the run reads: only the instant is"""
        before = time.time()
        time.sleep(0.02)
        assert time.time() == before
