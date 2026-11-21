import inspect
import sys
import threading
import time

import pytest

from alasio.base import sleep as sleep_module
from alasio.base.sleep import get_resolution, sleep

pytestmark = [
    pytest.mark.parallel,
]


def _delegates_to_time_sleep():
    """
    Check whether the precise sleep delegates to time.sleep() here

    On POSIX, and on Windows from Python 3.11, the platform implementation is
    time.sleep() itself, so a property of the implementation of this project
    cannot be exercised.

    Returns:
        bool: True when there is no implementation of this project to test
    """
    sleep_module._resolve()
    return sleep_module._impl is time.sleep


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

        Only the implementation of this project slices a sleep. On a platform
        where sleep() is time.sleep() itself nothing is sliced and there is no
        deadline to keep, so the test is skipped there. The least disturbed of
        several samples decides: the scheduler of a loaded CI machine delays a
        sample by an arbitrary amount, and only the quiet one shows what the
        sleep itself costs.
        """
        if _delegates_to_time_sleep():
            pytest.skip('sleep() is time.sleep() on this platform, nothing is sliced')

        elapsed = []
        for _ in range(5):
            start = time.perf_counter()
            sleep(0.15)
            elapsed.append(time.perf_counter() - start)

        # 3 slices of 50ms: a drift of one 15.6ms tick per slice would be +47ms
        assert min(elapsed) < 0.17


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
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(elapsed) == 4
        # No thread wakes before its own time, a shared timer would cut one
        # short to the due time of another thread
        assert min(elapsed) >= 0.02
        # No thread is left waiting for a signal that woke another one. The
        # bound is generous on purpose: a CI machine can run four threads on
        # fewer cores, which serializes the sleeps without being a defect, so
        # only a wait that never ends is a failure.
        assert max(elapsed) < 1.0


class TestSleepSystemTimerPeriod:
    """sleep() cannot change the timer period of the system"""

    @pytest.mark.parametrize('api', ['NtSetTimerResolution', 'timeBeginPeriod', 'timeEndPeriod'])
    def test_the_module_never_names_the_api(self, api):
        """No code path of the module can ask the system for a timer period

        The period is a system wide setting before Windows 10 version 2004:
        raising it makes the whole machine tick faster and fights with whatever
        else adjusts it, so a library call must not keep the capability at all.

        The source is read instead of the timer period of the machine on
        purpose. The period is shared, any other process may change it while
        this test runs, so comparing the period before and after a sleep says
        nothing about what this process did.
        """
        assert api not in inspect.getsource(sleep_module)

    def test_the_public_surface_is_the_sleep_and_its_resolution(self):
        """Nothing else is exported, in particular no way to ask for a period"""
        assert list(sleep_module.__all__) == ['sleep', 'get_resolution']
