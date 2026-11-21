import sys
import threading
import time

import pytest

from alasio.base.sleep import get_resolution, sleep

pytestmark = [
    pytest.mark.parallel,
]


def system_period():
    """
    Query the current timer period of the system, the granularity of a waitable
    timer.

    Returns:
        float | None: Period in seconds, None when it is not available
    """
    if sys.platform != 'win32':
        return None
    import ctypes
    from ctypes import wintypes

    try:
        query = ctypes.WinDLL('ntdll').NtQueryTimerResolution
    except (AttributeError, OSError):
        return None
    query.restype = ctypes.c_long
    query.argtypes = (
        ctypes.POINTER(wintypes.ULONG), ctypes.POINTER(wintypes.ULONG), ctypes.POINTER(wintypes.ULONG),
    )
    coarse, fine, current = wintypes.ULONG(), wintypes.ULONG(), wintypes.ULONG()
    if query(ctypes.byref(coarse), ctypes.byref(fine), ctypes.byref(current)) != 0:
        return None
    return current.value * 1e-7 or None


class TestSleepDuration:
    """sleep() never returns before the requested time"""

    @pytest.mark.parametrize('seconds', [0.001, 0.002, 0.005, 0.010])
    def test_never_returns_early(self, seconds):
        """Every sample lasts at least the requested time.

        time.sleep() rounds the request up to a whole millisecond on Windows
        and still returns a little early on half of the samples, a sleep that
        returned early would let a polling loop spin for nothing. The samples
        are repeated because the late wake-up of a loaded machine is fine, only
        the early one is a defect.
        """
        for _ in range(10):
            start = time.perf_counter()
            sleep(seconds)
            assert time.perf_counter() - start >= seconds

    def test_zero_returns_immediately(self):
        """Zero returns without sleeping"""
        start = time.perf_counter()
        sleep(0)
        assert time.perf_counter() - start < 0.05

    @pytest.mark.parametrize('value', [-1, -0.5, -1e9])
    def test_negative_raises_value_error(self, value):
        """A negative time is rejected, like time.sleep()"""
        with pytest.raises(ValueError):
            sleep(value)

    @pytest.mark.parametrize('value', [float('inf'), float('-inf')])
    def test_infinite_raises_overflow_error(self, value):
        """An infinite time is rejected instead of hanging forever

        A slice loop would keep a sleep of infinity running without ever
        returning, so the value has to be rejected up front.
        """
        with pytest.raises(OverflowError):
            sleep(value)

    def test_nan_raises_value_error(self):
        """NaN is rejected, like time.sleep()"""
        with pytest.raises(ValueError):
            sleep(float('nan'))

    @pytest.mark.parametrize(
        'value',
        [-1, -0.5, float('inf'), float('-inf'), float('nan')],
    )
    def test_invalid_time_raises_like_time_sleep(self, value):
        """The exception type of an invalid time matches time.sleep()

        Callers move between the two functions, an exception they already
        handle must not turn into a different one.
        """
        with pytest.raises(Exception) as expected:
            time.sleep(value)
        with pytest.raises(type(expected.value)):
            sleep(value)

    def test_zero_never_reaches_the_platform(self, monkeypatch):
        """A zero time is a no-op before the platform sleep is called

        Nothing has to be waited for, so the call does not even resolve the
        platform implementation.
        """
        from alasio.base import sleep as sleep_module

        calls = []
        monkeypatch.setattr(sleep_module, '_impl', calls.append)
        sleep(0)
        assert calls == []

    @pytest.mark.parametrize(
        'value, error',
        [
            (-1, ValueError),
            (-0.5, ValueError),
            (float('nan'), ValueError),
            (float('inf'), OverflowError),
            (float('-inf'), OverflowError),
        ],
    )
    def test_platform_implementation_validates_on_its_own(self, value, error):
        """The platform sleep rejects an invalid time without the wrapper

        The validation lives in the implementation of the platform, so an
        implementation that is not time.sleep() has to enforce the same
        contract itself instead of relying on the wrapper.
        """
        from alasio.base import sleep as sleep_module

        sleep_module._resolve()
        with pytest.raises(error):
            sleep_module._impl(value)


@pytest.mark.skipif(sys.platform != 'win32', reason='only Windows has an implementation of its own')
class TestSleepDelegation:
    """A Python whose time.sleep() is already precise is used directly"""

    def test_precise_time_sleep_is_reused(self, monkeypatch):
        """Python 3.11 and later sleep on the timer this module would build

        CPython 3.11 replaced the millisecond Sleep() with a high resolution
        waitable timer, the same mechanism and the same 100ns due time, and it
        validates its argument as well. Delegating keeps the Win32 binding of
        this module out of the call entirely.
        """
        from alasio.base import sleep as sleep_module

        monkeypatch.setattr(sleep_module, '_TIMEMODULE_HIGH_RESOLUTION', True)
        monkeypatch.setattr(sleep_module, '_impl', None)
        sleep_module._resolve()

        assert sleep_module._impl is time.sleep

    def test_own_implementation_is_used_before_python_311(self, monkeypatch):
        """An older Python calls Sleep() and needs the implementation here"""
        from alasio.base import sleep as sleep_module

        monkeypatch.setattr(sleep_module, '_TIMEMODULE_HIGH_RESOLUTION', False)
        monkeypatch.setattr(sleep_module, '_impl', None)
        sleep_module._resolve()

        assert sleep_module._impl is not time.sleep
        assert callable(sleep_module._impl)


class TestSleepPrecision:
    """The wake-up is not quantized to a coarse system tick"""

    def test_overshoot_below_1ms(self):
        """A short sleep wakes up within 1ms of the requested time.

        The smallest overshoot of repeated sleeps is the sample least disturbed
        by the scheduler and it exposes the timer granularity: a sleep that is
        quantized to the 15.6ms system tick cannot return a 3ms sleep within
        1ms at all.

        A platform without a high resolution timer, Windows 7 and Windows 10
        before 1803, cannot do better than its system timer period without
        raising that period, which sleep() does not do. The test skips there
        and the period stays the documented floor.
        """
        resolution = get_resolution()
        if resolution >= 0.001:
            pytest.skip(f'the platform timer resolution is {resolution * 1e3:.3f}ms')

        overshoots = []
        for _ in range(20):
            start = time.perf_counter()
            sleep(0.003)
            overshoots.append(time.perf_counter() - start - 0.003)

        assert min(overshoots) < 0.001

    def test_long_sleep_keeps_its_deadline(self):
        """A long sleep is not extended by the overshoot of every slice.

        A sleep longer than the internal slice is split into several waits, a
        drift of one coarse tick per slice would add up to tens of milliseconds.
        """
        start = time.perf_counter()
        sleep(0.15)
        elapsed = time.perf_counter() - start

        assert 0.15 <= elapsed < 0.17


class TestSleepResourceUse:
    """The sleep must not busy wait and must not hold the interpreter"""

    def test_does_not_burn_cpu(self):
        """Sleeping costs no CPU time, busy waiting would burn all of it"""
        start = time.process_time()
        sleep(0.06)
        assert time.process_time() - start < 0.010

    def test_releases_the_interpreter(self):
        """Other threads keep running while the caller sleeps"""
        counter = [0]
        stop = [False]

        def spin():
            while not stop[0]:
                counter[0] += 1

        thread = threading.Thread(target=spin, daemon=True)
        thread.start()
        try:
            # Wait until the worker has been scheduled at least once
            deadline = time.perf_counter() + 1.0
            while counter[0] == 0 and time.perf_counter() < deadline:
                time.sleep(0.001)
            counter[0] = 0
            sleep(0.05)
            progressed = counter[0]
        finally:
            stop[0] = True
            thread.join()

        assert progressed > 1000


class TestSleepThreads:
    """Sleeps of different threads do not interfere with each other"""

    def test_concurrent_sleeps_are_independent(self):
        """Every thread sleeps its full time while the others do the same

        A shared timer handle would cut the sleep of a thread short or extend
        it to the due time of another thread.
        """
        elapsed = []

        def run():
            start = time.perf_counter()
            sleep(0.02)
            elapsed.append(time.perf_counter() - start)

        threads = [threading.Thread(target=run) for _ in range(4)]
        start = time.perf_counter()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        wall = time.perf_counter() - start

        assert len(elapsed) == 4
        assert min(elapsed) >= 0.02
        # The four sleeps overlap, serial sleeps would take at least 80ms
        assert wall < 0.07


@pytest.mark.skipif(sys.platform != 'win32', reason='the timer period is a Windows setting')
class TestSleepSystemTimerPeriod:
    """sleep() never changes the timer period of the system"""

    def test_sleep_does_not_raise_the_system_period(self):
        """A sleep is not allowed to make the whole machine tick faster.

        The timer period is a system wide setting before Windows 10 version
        2004, so raising it in a library call would slow every other process
        down and fight with whatever else adjusts it.
        """
        before = system_period()
        if before is None:
            pytest.skip('the timer period cannot be queried')

        sleep(0.005)
        sleep(0.001)

        after = system_period()
        assert after is not None
        # A finer period means this process raised it, another process may only
        # make it coarser while the test runs
        assert after >= before
