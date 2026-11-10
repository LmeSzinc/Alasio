"""
Standalone helper that replaces app.asar and relaunches electron.

The module is a single file on purpose: the backend spawns it as
``python <path>/updater.py`` (see the app.asar relaunch design), so it must
not rely on package ``__init__`` side effects and must not import anything
outside the standard library. The import allowlist is enforced by
``tests/deploy/updater/test_start_time.py``.

The process identity helpers are a stdlib translation of psutil 7.2.2:

- Windows: ``psutil_proc_times()`` in ``arch/windows/proc.c``
- Linux: ``Process.create_time()`` in ``_pslinux.py``
- macOS: ``psutil_proc_kinfo_oneshot()`` in ``arch/osx/proc.c``

Unlike psutil, the value returned by ``start_time()`` never mixes in the
boot time or the current wall clock: it is the raw kernel counter, so a
clock adjustment during the update cannot make a live process look
restarted.
"""
import ctypes
import errno
import socket
import sys

# The probe has to answer "can the backend bind this port again", which is
# also the question the updater asks before it relaunches electron.
_TCP = (socket.AF_INET, socket.SOCK_STREAM)


def _parse_starttime(data):
    """
    Extract the starttime field of a /proc/<pid>/stat content.

    Translated from ``psutil._pslinux.Process._parse_stat_file()``: the
    process name sits between parentheses and may itself contain spaces and
    parentheses, so fields are counted from the last ')'. "man proc" numbers
    starttime as field 22, which is index 19 after dropping pid, comm and
    state.

    Defined on every platform so the parser can be exercised by the whole CI
    matrix, it is only reachable from ``start_time()`` on Linux.

    Args:
        data (bytes): Raw content of /proc/<pid>/stat

    Returns:
        int: Start time in clock ticks since boot

    Raises:
        OSError: The content is truncated or malformed, the value is unknown
            and the caller must not treat it as "gone"
    """
    rparen = data.rfind(b')')
    if rparen < 0:
        raise OSError(errno.EINVAL, 'malformed /proc/<pid>/stat', None)
    fields = data[rparen + 2:].split()
    try:
        return int(fields[19])
    except (IndexError, ValueError):
        raise OSError(errno.EINVAL, 'truncated /proc/<pid>/stat', None) from None


def port_available(host, port):
    """
    Check whether a TCP port can be bound right now.

    The probe mirrors how the backend itself binds the port, so the answer is
    exactly "would the backend be able to listen here again":

    - Windows: ``SO_EXCLUSIVEADDRUSE``, because ``SO_REUSEADDR`` there lets a
      second socket steal a bound port and the probe would always succeed.
    - POSIX: ``SO_REUSEADDR``, which ignores TIME_WAIT leftovers but still
      refuses a second listener on the same address.

    Args:
        host (str): Address to bind, '' for every interface
        port (int): TCP port

    Returns:
        bool: True if the port is free, False if anything holds it
    """
    sock = socket.socket(*_TCP)
    try:
        if sys.platform == 'win32':
            # -5 is SO_EXCLUSIVEADDRUSE, absent from the socket module
            sock.setsockopt(socket.SOL_SOCKET, getattr(socket, 'SO_EXCLUSIVEADDRUSE', -5), 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
    except OSError:
        # EADDRINUSE for a listener, EACCES for a privileged port: both mean
        # "not available to us", which is the only question being asked
        return False
    finally:
        sock.close()
    return True


if sys.platform == 'win32':

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _ERROR_INVALID_PARAMETER = 87
    # GetExitCodeProcess returns this value for a running process
    _STILL_ACTIVE = 259

    class _FILETIME(ctypes.Structure):
        _fields_ = [
            ('dwLowDateTime', ctypes.c_uint32),
            ('dwHighDateTime', ctypes.c_uint32),
        ]

    _kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    _kernel32.OpenProcess.restype = ctypes.c_void_p
    _kernel32.GetProcessTimes.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
    )
    _kernel32.GetProcessTimes.restype = ctypes.c_int
    _kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    _kernel32.CloseHandle.restype = ctypes.c_int
    _kernel32.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32))
    _kernel32.GetExitCodeProcess.restype = ctypes.c_int

    def start_time(pid):
        """
        Read the creation time of a process.

        Translated from ``psutil_proc_times()``: open the process, ask for
        its creation FILETIME and fold it into a single integer. FILETIME is
        a snapshot taken at creation, so the value survives later clock
        adjustments.

        Args:
            pid (int): Target pid

        Returns:
            int: Creation time in 100ns ticks since 1601-01-01 UTC

        Raises:
            ProcessLookupError: No such process
            PermissionError: The process exists but cannot be queried
        """
        if pid == 0:
            # psutil refuses pid 0 explicitly, OpenProcess would turn it into
            # a misleading "no such process"
            raise PermissionError(0, 'cannot query the idle process', pid)
        handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == _ERROR_INVALID_PARAMETER:
                # psutil_check_phandle() maps this to NoSuchProcess
                raise ProcessLookupError(error, 'OpenProcess', pid) from None
            raise PermissionError(error, 'OpenProcess', pid) from None
        try:
            # psutil_check_phandle(check_exit_code=1): OpenProcess also
            # succeeds for a terminated process whose kernel object is kept
            # alive by another handle, so the exit code has to be checked
            # separately. ERROR_ACCESS_DENIED is ignored by psutil and the
            # handle is kept, which is what happens here.
            exit_code = ctypes.c_uint32()
            if _kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                if exit_code.value != _STILL_ACTIVE:
                    raise ProcessLookupError(0, 'process no longer exists', pid) from None
            created = _FILETIME()
            exited = _FILETIME()
            kernel = _FILETIME()
            user = _FILETIME()
            if not _kernel32.GetProcessTimes(
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                # psutil reports NoSuchProcess for ERROR_ACCESS_DENIED here
                # ("usually means the process has died"); an unreadable live
                # process must keep the caller waiting, so it is PermissionError
                raise PermissionError(ctypes.get_last_error(), 'GetProcessTimes', pid) from None
            return (created.dwHighDateTime << 32) | created.dwLowDateTime
        finally:
            _kernel32.CloseHandle(handle)


elif sys.platform.startswith('linux'):

    def start_time(pid):
        """
        Read the creation time of a process.

        Translated from ``psutil._pslinux.Process.create_time()`` with
        ``monotonic=True``: the raw ``starttime`` counter from procfs, which
        is expressed in clock ticks since boot and is unaffected by wall
        clock updates. The boot time is deliberately not added.

        Args:
            pid (int): Target pid

        Returns:
            int: Start time in clock ticks since boot

        Raises:
            ProcessLookupError: No such process
            PermissionError: The process exists but cannot be queried
            OSError: procfs returned unexpected content
        """
        try:
            with open('/proc/%s/stat' % pid, 'rb') as f:
                data = f.read()
        except FileNotFoundError:
            raise ProcessLookupError(errno.ENOENT, 'no such process', pid) from None
        except NotADirectoryError:
            # /proc itself is missing (non standard kernel build)
            raise OSError(errno.ENOSYS, 'procfs is not mounted', pid) from None
        return _parse_starttime(data)


elif sys.platform == 'darwin':

    _CTL_KERN = 1
    _KERN_PROC = 14
    _KERN_PROC_PID = 1
    # struct kinfo_proc is 648 bytes on every 64 bit macOS; the buffer is
    # oversized on purpose, sysctl writes sizeof(struct kinfo_proc) at most
    _KINFO_PROC_SIZE = 1024

    class _Timeval(ctypes.Structure):
        _fields_ = [
            ('tv_sec', ctypes.c_long),
            ('tv_usec', ctypes.c_int),
        ]

    _libc = ctypes.CDLL(None, use_errno=True)
    _libc.sysctl.argtypes = (
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.c_void_p,
        ctypes.c_size_t,
    )
    _libc.sysctl.restype = ctypes.c_int

    def start_time(pid):
        """
        Read the creation time of a process.

        Translated from ``psutil_proc_kinfo_oneshot()``: ``sysctl`` fills a
        ``struct kinfo_proc`` and ``kp_proc.p_starttime`` is read from it.
        ``struct extern_proc`` starts with a union whose other member is a
        pair of pointers, so the timeval is the first 16 bytes of the struct.

        Args:
            pid (int): Target pid

        Returns:
            int: Creation time in microseconds since the epoch

        Raises:
            ProcessLookupError: No such process
            PermissionError: The process exists but cannot be queried
            OSError: sysctl failed for another reason
        """
        mib = (ctypes.c_int * 4)(_CTL_KERN, _KERN_PROC, _KERN_PROC_PID, pid)
        buffer = ctypes.create_string_buffer(_KINFO_PROC_SIZE)
        size = ctypes.c_size_t(_KINFO_PROC_SIZE)
        if _libc.sysctl(mib, 4, buffer, ctypes.byref(size), None, 0) != 0:
            error = ctypes.get_errno()
            if error in (errno.ESRCH, errno.ENOENT):
                raise ProcessLookupError(error, 'sysctl', pid) from None
            if error in (errno.EPERM, errno.EACCES):
                raise PermissionError(error, 'sysctl', pid) from None
            raise OSError(error, 'sysctl', pid) from None
        # sysctl succeeds but returns nothing when the process has gone away
        if size.value == 0:
            raise ProcessLookupError(errno.ESRCH, 'sysctl returned no data', pid)
        start = ctypes.cast(buffer, ctypes.POINTER(_Timeval)).contents
        return start.tv_sec * 1000000 + start.tv_usec


else:

    def start_time(pid):
        """
        Reject unsupported platforms.

        Args:
            pid (int): Target pid

        Raises:
            NotImplementedError: Always, the platform is not supported
        """
        raise NotImplementedError('platform %s is not supported' % sys.platform)
