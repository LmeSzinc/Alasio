import sys
import time

__all__ = ['sleep', 'get_resolution']

# A long sleep is split into slices of at most this many seconds, so a signal
# is handled without waiting for the whole sleep. Every slice is measured from
# an absolute deadline, the accuracy of a sleep does not depend on the count.
_MAX_SLICE = 0.05

# CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, Windows 10 version 1803 and later. A
# high resolution waitable timer is precise on its own, it needs no system wide
# timer period and affects nothing else.
_HIGH_RESOLUTION_TIMER = 0x00000002

# Resolution of a sleep on a high resolution timer, 100ns for a waitable timer
# and finer than that for clock_nanosleep()
_FINE = 0.0001
# Coarse system timer period of Windows before the high resolution timer
_COARSE = 0.015625

_INFINITY = float('inf')

# time.sleep() sleeps on a high resolution waitable timer since Python 3.11 on
# Windows, see pysleep() in Modules/timemodule.c. Reusing it keeps the Win32
# binding of this module off the hot path; an older version calls Sleep() with
# a whole millisecond and needs the implementation below.
_TIMEMODULE_HIGH_RESOLUTION = sys.version_info >= (3, 11)

# Resolved on the first call, so importing this module costs nothing
_impl = None
_resolution = None
# True when the platform sleeps on a per-timer high resolution timer
_high_resolution = False
# Win32 bindings, built on the first call, see _win32()
_win = None


def get_resolution():
    """
    Get the resolution of sleep() on this platform.

    The resolution is the granularity of the timer the sleep waits on. A
    platform without a high resolution timer answers with the timer period of
    the system right now, so the value follows a period raised by another
    process.

    The granularity bounds the overshoot only as far as the timer itself goes.
    It is not a promise about the wake-up: a system may coalesce timers to save
    power, which macOS does, and a busy machine delays a woken thread. Neither
    is visible here, so a caller that needs a bound on the overshoot has to
    measure it.

    Returns:
        float: Resolution in seconds
    """
    _resolve()
    if _high_resolution:
        return _resolution
    return _system_period() or _resolution


def sleep(seconds):
    """
    Sleep for at least `seconds`, without busy waiting.

    The time is not rounded up to a whole millisecond and, where the platform
    offers a high resolution timer, the wake-up is not quantized to the 15.6ms
    system tick:

    - Windows 10 1803 and later: `time.sleep()` already sleeps on a high
      resolution waitable timer from Python 3.11, and earlier versions get one
      from this module. Either way the timer is precise on its own and the
      timer period of the system is left alone, other processes are not
      affected.
    - Windows 7 and Windows 10 before 1803: a waitable timer, its overshoot is
      bounded by the timer period of the system, see `get_resolution()`. The
      period is a system wide setting there, so this sleep never raises it.
    - POSIX: time.sleep(), which sleeps on clock_nanosleep() or nanosleep(),
      a high resolution timer with a resolution of nanoseconds.

    A sleeping thread can always be delayed by the scheduler of a busy machine.
    No implementation that avoids busy waiting can bound that delay, so the
    guarantee is about the timer granularity, not about the load of the host.

    The platform implementation is resolved on the first call, so a process
    that never sleeps precisely does not pay for the Win32 bindings.

    The argument is validated like time.sleep(): a negative time raises
    ValueError, an infinite time raises OverflowError and a NaN raises
    ValueError. An infinite time is almost always a bug of the caller and must
    not become a silent hang. The validation belongs to the implementation of
    each platform, so POSIX reuses the one of time.sleep() instead of repeating
    it, and only the waitable timer has to spell it out.

    Args:
        seconds (int | float): Time to sleep in seconds. Zero returns
            immediately, it does not yield the CPU.

    Returns:
        None

    Raises:
        ValueError: The time is negative or NaN
        OverflowError: The time is infinite
    """
    if seconds == 0:
        # A zero sleep is a no-op, do not resolve the platform for it
        return
    _resolve()
    _impl(seconds)


def _resolve():
    """
    Resolve the implementation and the resolution of this platform once.

    Returns:
        None
    """
    global _impl, _resolution, _high_resolution
    if _impl is not None:
        return
    try:
        impl, resolution, high_resolution = _init()
    except (AttributeError, OSError):
        # No Win32 binding on this system, degrade to the builtin sleep. It is
        # quantized to the system tick but it still blocks correctly.
        impl = time.sleep
        resolution = _system_period() or _COARSE
        high_resolution = False
    _resolution = resolution
    _high_resolution = high_resolution
    _impl = impl


if sys.platform == 'win32':

    def _win32():
        """
        Resolve the Win32 bindings, once per process.

        Returns:
            dict: ctypes module, wintypes module and the kernel32 functions
        """
        global _win
        if _win is not None:
            return _win
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        create_timer = kernel32.CreateWaitableTimerExW
        create_timer.restype = wintypes.HANDLE
        create_timer.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD)
        set_timer = kernel32.SetWaitableTimer
        set_timer.restype = wintypes.BOOL
        set_timer.argtypes = (
            wintypes.HANDLE, ctypes.POINTER(ctypes.c_longlong), wintypes.LONG,
            wintypes.LPVOID, wintypes.LPVOID, wintypes.BOOL,
        )
        wait_for = kernel32.WaitForSingleObject
        wait_for.restype = wintypes.DWORD
        wait_for.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        close_handle = kernel32.CloseHandle
        close_handle.restype = wintypes.BOOL
        close_handle.argtypes = (wintypes.HANDLE,)

        _win = {
            'ctypes': ctypes,
            'wintypes': wintypes,
            'create_timer': create_timer,
            'set_timer': set_timer,
            'wait_for': wait_for,
            'close_handle': close_handle,
        }
        return _win

    def _validate(seconds):
        """
        Reject a time that time.sleep() rejects as well.

        The due time of a waitable timer is a signed count of 100ns units, a
        negative or non-finite value has no encoding there, so it is rejected
        before any timer work. Python 3.11 and POSIX need no copy of this, they
        reuse time.sleep(), which validates on its own.

        Args:
            seconds (int | float): Time to sleep in seconds

        Returns:
            None

        Raises:
            ValueError: The time is negative or NaN
            OverflowError: The time is infinite
        """
        if seconds != seconds:
            # NaN is the only value that is not equal to itself
            raise ValueError('Invalid value NaN (not a number)')
        if seconds == _INFINITY or seconds == -_INFINITY:
            raise OverflowError('timestamp too large to convert to C _PyTime_t')
        if seconds < 0:
            raise ValueError('sleep length must be non-negative')

    def _init():
        """
        Probe the high resolution timer and build the sleep on top of it.

        Returns:
            tuple: (sleep function, resolution in seconds, high resolution)
        """
        win = _win32()
        ctypes = win['ctypes']
        create_timer = win['create_timer']
        set_timer = win['set_timer']
        wait_for = win['wait_for']
        close_handle = win['close_handle']

        # TIMER_ALL_ACCESS
        timer_access = 0x1F0003
        flags = _HIGH_RESOLUTION_TIMER
        probe = create_timer(None, None, flags, timer_access)
        if probe:
            close_handle(probe)
            resolution = _FINE
            high_resolution = True
        else:
            # Windows before 10 version 1803 does not know the flag
            flags = 0
            resolution = _system_period() or _COARSE
            high_resolution = False

        if _TIMEMODULE_HIGH_RESOLUTION:
            # time.sleep() arms the very same timer, and it validates its
            # argument itself, so there is nothing left to implement here
            return time.sleep, resolution, high_resolution

        # SetWaitableTimer() takes the due time in 100ns units, a negative
        # value is relative to now
        due = ctypes.c_longlong()
        infinite = 0xFFFFFFFF

        def sleep_impl(seconds):
            _validate(seconds)
            if seconds == 0:
                return
            # A timer per call, a shared timer cannot serve two threads: the
            # second SetWaitableTimer() would overwrite the due time of the
            # first one and one signal wakes a single waiter only
            handle = create_timer(None, None, flags, timer_access)
            if not handle:
                time.sleep(seconds)
                return
            try:
                deadline = time.perf_counter() + seconds
                while True:
                    remaining = deadline - time.perf_counter()
                    if remaining > _MAX_SLICE:
                        remaining = _MAX_SLICE
                    if remaining <= 0:
                        break
                    # Round the due time up, a truncated one could fire up to
                    # 100ns before the deadline of the caller
                    due.value = -(int(remaining * 10000000) + 1)
                    armed = set_timer(handle, ctypes.byref(due), 0, None, None, False)
                    if not armed or wait_for(handle, infinite) != 0:
                        # The timer is not usable, the deadline still decides
                        # how long the caller waits
                        time.sleep(remaining)
            finally:
                close_handle(handle)

        return sleep_impl, resolution, high_resolution

    def _system_period():
        """
        Query the current timer period of the system, the granularity of a
        waitable timer.

        The period is shared, another process may have raised it, so the value
        is queried instead of cached.

        Returns:
            float | None: Period in seconds, None when it cannot be queried
        """
        import ctypes
        from ctypes import wintypes

        try:
            query = ctypes.WinDLL('ntdll').NtQueryTimerResolution
        except (AttributeError, OSError):
            return None
        query.restype = ctypes.c_long
        query.argtypes = (
            ctypes.POINTER(wintypes.ULONG), ctypes.POINTER(wintypes.ULONG),
            ctypes.POINTER(wintypes.ULONG),
        )
        coarse, fine, current = wintypes.ULONG(), wintypes.ULONG(), wintypes.ULONG()
        if query(ctypes.byref(coarse), ctypes.byref(fine), ctypes.byref(current)) != 0:
            return None
        # The value is in 100ns units
        return current.value * 1e-7 or None

else:

    def _system_period():
        """
        Query the current timer period of the system.

        POSIX has no system wide timer period, a sleep always waits on a high
        resolution timer, so there is nothing to report.

        Returns:
            float | None: Always None
        """
        return None

    def _init():
        """
        Resolve the sleep of the platform.

        Returns:
            tuple: (sleep function, resolution in seconds, high resolution)
        """
        # time.sleep() calls clock_nanosleep() or nanosleep(), both sleep on a
        # high resolution timer and are not limited by a system tick
        return time.sleep, _FINE, True
