"""
Standalone helper that replaces app.asar and relaunches electron.

The file is a single script on purpose: the backend starts it as
``python <ALASIO_ROOT>/deploy/updater.py``, by path and never through ``-m``,
so the ``alasio`` package never enters the import chain and the standard
library only rule is enforced by the interpreter instead of by agreement.
Nothing in this module may read the environment at import time: the backend
imports it too (for ``start_time``), and the two processes do not share one.

``ElectronUpdater`` is the whole relaunch: it reads its input in the
constructor, and every step of the flow (wait, replace, relaunch) is a method
of it, so the state a step works on is the object it is called on.

This process copies nothing. The archive it installs was written next to the
installed one by the backend, before the chain came down, together with the
digest handed over in the environment: the bytes are pinned at the moment the
relaunch is decided, while a failure is still recoverable. Here the archive is
read once to prove it is still the agreed one, and then renamed over the
installed file -- os.replace is atomic and the two paths share a directory, so
there is no intermediate copy to make, retry or clean up.

The process identity helpers are a stdlib translation of psutil 7.2.2:

- Windows: ``psutil_proc_times()`` in ``arch/windows/proc.c``
- Linux: ``Process.create_time()`` in ``_pslinux.py``
- macOS: ``psutil_proc_kinfo_oneshot()`` in ``arch/osx/proc.c``

Unlike psutil, the value returned by ``start_time()`` never mixes in the
boot time or the current wall clock: it is the raw kernel counter, so a
clock adjustment during the update cannot make a live process look
restarted.

The marker alone does not identify a process: linux counts it in jiffies
(10ms by default), so two processes of the same tick report the same
value. Identity is the ``(pid, marker)`` pair, the pids always differ.
"""
import ctypes
import errno
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import traceback
from datetime import datetime

# Exit codes, see the design document of the app.asar relaunch
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_PREFLIGHT = 2
EXIT_TIMEOUT = 3
EXIT_REPLACE = 4
EXIT_RELAUNCH = 5
EXIT_NO_GO = 6

# States of one waited target
GONE = 'GONE'
ALIVE = 'ALIVE'
REUSED = 'REUSED'
UNKNOWN = 'UNKNOWN'

# The three roles of ALASIO_RELAUNCH_TARGETS, all of them are required
TARGET_NAMES = ('backend', 'supervisor', 'electron')

# Interval and budget of the wait, and of the poll that decides whether the new
# process started: the window is long enough for a crash on startup to show,
# short enough that a swallowed instance is noticed at once
POLL_INTERVAL = 0.1
START_INTERVAL = 0.5
START_WINDOW = 10.0
CHUNK_SIZE = 1 << 20

# The TCP socket type of the port probe
_TCP = (socket.AF_INET, socket.SOCK_STREAM)


def _is_abspath(path):
    """
    Check whether a path is absolute, with the same answer on every python
    version.

    os.path.isabs() is not asked: on Windows a rooted path without a drive
    (``\\resources\\app.asar``) counted as absolute up to python 3.12 and
    became relative in python 3.13, so one and the same environment would pass
    or fail the preflight depending on the interpreter that runs it. The rule
    is the one the rest of the project uses (alasio.ext.path.is_abspath),
    restated here because this module imports the standard library only:

    - POSIX: the path starts at the root, '/'
    - Windows: a rooted path (the root of the current drive, a UNC share) or a
      drive with a root after it ('C:/', 'C:/a'); a drive relative path
      ('C:a') is not absolute

    Args:
        path (str): Path to check, normalized or not

    Returns:
        bool: True when the path is absolute
    """
    if os.sep != '\\':
        return path.startswith('/')
    if path[1:2] == ':' and path[:1].isalpha():
        return path[2:3] in ('/', '\\', '')
    return path.startswith(('/', '\\'))


class PreflightError(Exception):
    """The input of the updater is missing or malformed, nothing was touched."""


class Target:
    """One process the updater waits for."""

    __slots__ = ('name', 'pid', 'start')

    def __init__(self, name, pid, start=None):
        """
        Args:
            name (str): Role of the process, one of TARGET_NAMES
            pid (int): Pid recorded at spawn time
            start (int): Start marker read at spawn time, None when it could
                not be read and only the existence of the pid can be asked
                about. Defaults to None.
        """
        self.name = name
        self.pid = pid
        self.start = start

    def __repr__(self):
        return f'<Target {self.name} {self.pid} {self.start}>'

    def probe(self):
        """
        Classify the process against the marker recorded at spawn time.

        The caller treats GONE and REUSED as "this process is done" and keeps
        waiting on ALIVE and UNKNOWN, so an unreadable process never makes the
        updater act early.

        Returns:
            str: GONE, ALIVE, REUSED or UNKNOWN
        """
        try:
            current = start_time(self.pid)
        except ProcessLookupError:
            return GONE
        except (PermissionError, OSError, NotImplementedError, ValueError):
            # permission denied, an unreadable procfs entry, an unsupported
            # platform: the value is unknown, which is not a reason to act
            return UNKNOWN
        if self.start is None:
            return ALIVE
        return ALIVE if current == self.start else REUSED


def port_available(host, port):
    """
    Check whether a TCP port can be bound right now.

    The probe mirrors how the backend itself binds the port, so the answer is
    exactly "would the backend be able to listen here again":

    - Windows: ``SO_EXCLUSIVEADDRUSE``, because ``SO_REUSEADDR`` there lets a
      second socket steal a bound port and the probe would always succeed.
    - POSIX: ``SO_REUSEADDR``, which ignores TIME_WAIT leftovers but still
      refuses a second listener on the same address.

    The updater does not use it: a free port does not prove electron is gone,
    and the start markers already answer that question. It is kept for the
    diagnostics of a chain that does not come down.

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


def _parse_stat(data):
    """
    Read the state and the starttime of a /proc/<pid>/stat content.

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
        "tuple[bytes, int]": Process state letter and start time in clock
            ticks since boot

    Raises:
        OSError: The content is truncated or malformed, the value is unknown
            and the caller must not treat it as "gone"
    """
    rparen = data.rfind(b')')
    if rparen < 0:
        raise OSError(errno.EINVAL, 'malformed /proc/<pid>/stat', None)
    fields = data[rparen + 2:].split()
    try:
        return fields[0], int(fields[19])
    except (IndexError, ValueError):
        raise OSError(errno.EINVAL, 'truncated /proc/<pid>/stat', None) from None


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

        The resolution is one clock tick (10ms by default), so the value is
        only unique together with the pid it was read for.

        Args:
            pid (int): Target pid

        Returns:
            int: Start time in clock ticks since boot

        Raises:
            ProcessLookupError: No such process, or a zombie (the process is
                gone and every resource it held is released)
            PermissionError: The process exists but cannot be queried
            OSError: procfs returned unexpected content
        """
        try:
            with open(f'/proc/{pid}/stat', 'rb') as f:
                data = f.read()
        except FileNotFoundError:
            raise ProcessLookupError(errno.ENOENT, 'no such process', pid) from None
        except NotADirectoryError:
            # /proc itself is missing (non standard kernel build)
            raise OSError(errno.ENOSYS, 'procfs is not mounted', pid) from None
        state, marker = _parse_stat(data)
        if state == b'Z':
            # a zombie has exited and holds nothing, which is what the caller
            # asks about; psutil calls it running, that difference is intended
            raise ProcessLookupError(errno.ESRCH, 'process is a zombie', pid)
        return marker


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

        A zombie is not detected here (it would need the state field of the
        same struct); the macOS shell target is unsupported anyway, so the
        value is only used by the tests and by the diagnostics.

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
        raise NotImplementedError(f'platform {sys.platform} is not supported')


# The script lives in <root>/alasio/deploy but the log belongs to the
# installation, not to the package: the backend starts the updater in the
# project root, so the working directory is that root on every launch path
# (electron -> supervisor -> backend -> here). The directory is resolved from
# it and never from __file__, which points into the library.
LOG_DIRECTORY_NAME = 'log'


class Log:
    """Append only log file, silent when no path is configured.

    The lines are laid out like the lines of the application logger, so one
    reader (and one habit) covers both files::

        2026-11-11 08:01:20.123 | INFO | preflight: /app/new-app.asar -> ...

    The level tells the consequence, not the mood of the writer:

    - ``INFO``: the flow went on, or it stopped without touching anything
      because it was told to (the relaunch was withdrawn).
    - ``WARNING``: the application was started, but not from the new build:
      the archive could not be read or swapped, or the goal could not be
      read. The user runs the previous version and can try again.
    - ``ERROR``: the flow stopped on a broken input, a chain that never came
      down, or a new build that would not start. Nothing is running.
    """

    __slots__ = ('path', 'file')

    def __init__(self, path=''):
        """
        Args:
            path (str): Log file, '' to write nothing. Defaults to ''.
        """
        self.path = path
        self.file = None

    def open(self):
        """Open the log file, creating its directory."""
        if not self.path:
            return
        folder = os.path.dirname(self.path)
        if folder:
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError:
                # read only installation: run without a log, never fail here
                return
        try:
            self.file = open(self.path, 'a', encoding='utf-8')
        except OSError:
            self.file = None

    def info(self, message):
        """
        Append one line at INFO level.

        Args:
            message (str): Text to write, must not contain a credential
        """
        self._write('INFO', message)

    def warning(self, message):
        """
        Append one line at WARNING level.

        Args:
            message (str): Text to write, must not contain a credential
        """
        self._write('WARNING', message)

    def error(self, message, exc_info=False):
        """
        Append one line at ERROR level, optionally with the stack that raised.

        The stack is appended the way the application logger appends it: after
        the line, verbatim, without a level of its own.

        Args:
            message (str): Text to write, must not contain a credential
            exc_info (bool): Append the traceback of the exception being
                handled. Defaults to False.
        """
        detail = ''
        if exc_info:
            detail = traceback.format_exc()
            if not detail.endswith('\n'):
                detail = f'{detail}\n'
        self._write('ERROR', message, detail)

    def _write(self, level, message, detail=''):
        """
        Append one stamped, flushed line.

        Args:
            level (str): INFO, WARNING or ERROR
            message (str): Text to write
            detail (str): Extra block printed after the line, already ending
                with a newline. Defaults to ''.
        """
        if self.file is None:
            return
        text = f'{self.stamp()} | {level} | {message}\n'
        if detail:
            text = f'{text}{detail}'
        try:
            self.file.write(text)
            self.file.flush()
        except OSError:
            # a full or removed log file must not stop the update
            self.file = None

    @staticmethod
    def stamp():
        """
        Timestamp prefix of one line.

        Returns:
            str: Local time with milliseconds, ``yyyy-mm-dd HH:MM:SS.mmm``
        """
        return datetime.fromtimestamp(time.time()).isoformat(sep=' ', timespec='milliseconds')

    def close(self):
        """Close the file, ignoring every error."""
        if self.file is not None:
            try:
                self.file.close()
            except OSError:
                pass
            self.file = None


class ElectronUpdater:
    """The app.asar relaunch: read the input, wait, install, start again.

    The variables of the environment are the whole input of the process: the
    command line is empty, so there is no second source to disagree with.
    Nothing is read at import time, because the backend imports this module
    for ``start_time`` too.

    Usage::

        sys.exit(ElectronUpdater.main())
    """

    __slots__ = (
        'dst', 'src', 'exe', 'cwd', 'argv', 'targets', 'sha256', 'resume_file',
        'token', 'timeout', 'env', 'log',
    )

    def __init__(self, environ=None, log=None):
        """
        Args:
            environ (Mapping[str, str]): Environment to read, os.environ when
                omitted. Defaults to None.
            log (Log): Opened sink of the messages, today's log file when
                omitted. Defaults to None.

        Raises:
            PreflightError: A variable is missing or malformed
        """
        environ = os.environ if environ is None else environ
        self.dst = self._as_path(environ.get('ALASIO_APP_ASAR', ''), 'ALASIO_APP_ASAR')
        if os.path.basename(self.dst) != 'app.asar':
            raise PreflightError(f'ALASIO_APP_ASAR must point at app.asar: {self.dst!r}')
        # the staged archive lives next to the installed one, so os.replace
        # stays on one volume and needs no copy of its own
        self.src = os.path.join(os.path.dirname(self.dst), 'new-app.asar')
        self.exe = self._as_path(environ.get('ALASIO_APP_EXE', ''), 'ALASIO_APP_EXE')
        self.cwd = environ.get('ALASIO_APP_CWD', '')
        if not self.cwd:
            raise PreflightError('ALASIO_APP_CWD is not set')
        self.argv = self._read_argv(environ.get('ALASIO_APP_ARGV', ''))
        targets_text = environ.get('ALASIO_RELAUNCH_TARGETS', '')
        if not targets_text:
            raise PreflightError('ALASIO_RELAUNCH_TARGETS is not set')
        self.targets = self.parse_targets(targets_text)
        self.sha256 = self._read_digest(environ.get('ALASIO_RELAUNCH_SHA256', ''))
        self.resume_file = self._as_path(environ.get('ALASIO_RESUME_FILE', ''), 'ALASIO_RESUME_FILE')
        self.token = environ.get('ALASIO_RESUME_TOKEN', '')
        if not self.token:
            # without it the new chain starts but never resumes anything: the
            # failure would only show up minutes later as "the tasks stopped"
            raise PreflightError('ALASIO_RESUME_TOKEN is not set')
        self.timeout = self._read_timeout(environ.get('ALASIO_RELAUNCH_TIMEOUT', ''))
        # The environment of the new chain is inherited, minus every key that
        # describes the old one. It is a subtraction, not a whitelist: this
        # environment came from electron itself (updater is spawned by the
        # backend, which came from the supervisor, which came from electron),
        # so passing it back cannot disclose anything the application did not
        # already have, while a whitelist would have to name every variable
        # electron needs per platform (SYSTEMROOT / APPDATA on Windows, DISPLAY
        # / DBUS_SESSION_BUS_ADDRESS on Linux, TMPDIR on macOS) and would break
        # silently when one of them is missing.
        #
        # Dropped:
        # - ALASIO_RELAUNCH_* / ALASIO_RESUME_FILE: this run's own input.
        # - ALASIO_APP_*: the chain writes them (electron the install context
        #   and its own pid) and rewrites them at the next start, so forwarding
        #   them adds nothing and is how a stale path would survive into the next
        #   generation (an older electron, e.g. after a downgrade, does not
        #   rewrite them at all). The prefix also drops the host pid, which is
        #   the electron mode switch of the next generation.
        # Kept: ALASIO_RESUME_TOKEN (must reach the new backend) and the
        # general environment above.
        self.env = {
            name: value
            for name, value in environ.items()
            if not name.startswith(('ALASIO_RELAUNCH_', 'ALASIO_APP_'))
            and name != 'ALASIO_RESUME_FILE'
        }
        self.env['ALASIO_RESUME_TOKEN'] = self.token
        self.log = Log(self.log_path()) if log is None else log

    @staticmethod
    def log_path():
        """
        Name of the log file of today, inside the installation root.

        Returns:
            str: ``<cwd>/log/{yyyy-mm-dd}_ElectronUpdater.txt``, named like
                every other log the application writes
        """
        name = f'{time.strftime("%Y-%m-%d")}_ElectronUpdater.txt'
        return os.path.join(os.getcwd(), LOG_DIRECTORY_NAME, name)

    @staticmethod
    def _as_path(value, name):
        """
        Validate one required absolute path variable.

        Args:
            value (str): Raw value, may be empty
            name (str): Variable name, for the error message

        Returns:
            str: The path

        Raises:
            PreflightError: The value is empty or relative
        """
        if not value:
            raise PreflightError(f'{name} is not set')
        if not _is_abspath(value):
            raise PreflightError(f'{name} must be absolute: {value!r}')
        return value

    @staticmethod
    def _read_argv(value):
        """
        Read the argument list handed to the new process.

        Args:
            value (str): Raw value of ALASIO_APP_ARGV

        Returns:
            "list[str]": The arguments

        Raises:
            PreflightError: The value is not a json list of strings
        """
        if not value:
            raise PreflightError('ALASIO_APP_ARGV is not set')
        try:
            argv = json.loads(value)
        except ValueError as e:
            raise PreflightError(f'ALASIO_APP_ARGV is not json: {e}') from None
        if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            raise PreflightError('ALASIO_APP_ARGV must be a list of strings')
        return argv

    @staticmethod
    def _read_digest(value):
        """
        Read the expected digest of the staged archive.

        Args:
            value (str): Raw value of ALASIO_RELAUNCH_SHA256

        Returns:
            str: Lowercase hex sha256

        Raises:
            PreflightError: The value is not a sha256 digest
        """
        digest = value.strip().lower()
        if not digest:
            raise PreflightError('ALASIO_RELAUNCH_SHA256 is not set')
        if len(digest) != 64 or any(char not in '0123456789abcdef' for char in digest):
            raise PreflightError(f'ALASIO_RELAUNCH_SHA256 is not a sha256 digest: {digest!r}')
        return digest

    @staticmethod
    def _read_timeout(value):
        """
        Read the budget of the wait.

        Args:
            value (str): Raw value of ALASIO_RELAUNCH_TIMEOUT, '' for the default

        Returns:
            float: Seconds to wait at most

        Raises:
            PreflightError: The value is not a positive number
        """
        if not value:
            return 120.0
        try:
            timeout = float(value)
        except ValueError:
            raise PreflightError(f'ALASIO_RELAUNCH_TIMEOUT is not a number: {value!r}') from None
        if not timeout > 0:
            raise PreflightError(f'ALASIO_RELAUNCH_TIMEOUT must be positive: {value!r}')
        return timeout

    @staticmethod
    def parse_targets(value):
        """
        Read ``ALASIO_RELAUNCH_TARGETS`` into the processes to wait for.

        The value is a JSON object keyed by role::

            {"backend": {"pid": 1234, "start": 987654},
             "supervisor": {"pid": 4321, "start": 987700},
             "electron": {"pid": 8765}}

        ``start`` is the process start marker read when the list was built; it
        may be omitted or null, and then only the existence of the pid is
        asked about. A process that was already gone needs no sentinel:
        probing its pid returns GONE at once.

        Args:
            value (str): Raw value of the variable

        Returns:
            "list[Target]": The three targets, in the order of TARGET_NAMES

        Raises:
            PreflightError: The text is not json, or a target is missing or
                malformed
        """
        try:
            parsed = json.loads(value)
        except ValueError as e:
            raise PreflightError(f'ALASIO_RELAUNCH_TARGETS is not json: {e}') from None
        if not isinstance(parsed, dict):
            raise PreflightError('ALASIO_RELAUNCH_TARGETS must be a json object')
        missing = [name for name in TARGET_NAMES if name not in parsed]
        if missing:
            raise PreflightError(f'ALASIO_RELAUNCH_TARGETS lacks: {", ".join(missing)}')
        extra = sorted(set(parsed) - set(TARGET_NAMES))
        if extra:
            raise PreflightError(
                f'ALASIO_RELAUNCH_TARGETS has unexpected entries: {", ".join(extra)}')
        return [ElectronUpdater._read_target(name, parsed[name]) for name in TARGET_NAMES]

    @staticmethod
    def _read_target(name, raw):
        """
        Read one entry of ``ALASIO_RELAUNCH_TARGETS``.

        Args:
            name (str): Role of the process
            raw (dict): The entry, with a positive integer pid and an optional
                integer start marker

        Returns:
            Target: The target

        Raises:
            PreflightError: The entry is malformed
        """
        if not isinstance(raw, dict):
            raise PreflightError(f'target {name} must be a json object')
        pid = raw.get('pid')
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise PreflightError(f'target {name} has no positive integer pid')
        start = raw.get('start')
        if start is not None and (not isinstance(start, int) or isinstance(start, bool) or start < 0):
            raise PreflightError(f'target {name} has a malformed start marker')
        return Target(name, pid, start)

    @staticmethod
    def sha256_file(path):
        """
        Digest the content of a file.

        Args:
            path (str): File to read

        Returns:
            str: Lowercase hex sha256

        Raises:
            OSError: The file cannot be read
        """
        digest = hashlib.sha256()
        with open(path, 'rb') as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def verify(self):
        """
        Check the staged archive against the digest this run was started with.

        The bytes were pinned before the chain came down: the backend wrote the
        package archive next to the installed one and handed over its digest
        with this process, so there is nothing to prepare here. This is a read,
        never a copy: the install is a rename of the very file that was
        checked, and checking it twice (before waiting, and again before the
        rename) costs one read of a few hundred kilobytes each time.

        Returns:
            bool: True when the staged archive matches, False when it is gone
                (another updater installed the very same content)

        Raises:
            OSError: The staged archive cannot be read
            PreflightError: The bytes are not the ones that were agreed on
        """
        try:
            digest = self.sha256_file(self.src)
        except FileNotFoundError:
            return False
        if digest != self.sha256:
            raise PreflightError(f'{self.src} is {digest}, expected {self.sha256}')
        return True

    def wait_targets(self, interval=POLL_INTERVAL):
        """
        Wait until every target is done or the budget is spent.

        Polling is deliberate: it needs nothing armed before the processes
        die, so no handle has to be opened in time and no signal has to be
        delivered. The first sleep comes before the first probe: the old chain
        never comes down that fast after the relaunch request, so probing at
        once only burns a system call.

        Args:
            interval (float): Seconds between two probe rounds. Defaults to 0.1.

        Returns:
            "list[str]": Names of the targets still running, empty when all
                are done
        """
        deadline = time.monotonic() + self.timeout
        while True:
            time.sleep(interval)
            pending = []
            for target in self.targets:
                if target.probe() in (ALIVE, UNKNOWN):
                    pending.append(target.name)
            if not pending:
                return []
            if time.monotonic() >= deadline:
                return pending

    def replace(self, attempts=5):
        """
        Move the staged archive over the installed one.

        The staged file sits in the directory of the destination, so the swap
        is one rename on one volume and os.replace is atomic on every
        platform: the installed archive is either the old file or the new one,
        never a mix, and a failure leaves it untouched. The rename consumes the
        staged file, which is the wanted end state -- a copy left behind would
        be compared against by the next run. Nothing is copied here, so a retry
        has nothing else to redo.

        Args:
            attempts (int): Attempts before the failure is reported. Defaults
                to 5.

        Raises:
            FileNotFoundError: The staged archive is gone (another updater
                installed it first)
            OSError: The swap failed after every attempt
        """
        delay = 0.2
        error = None
        for attempt in range(attempts):
            try:
                os.replace(self.src, self.dst)
            except FileNotFoundError:
                # nothing left to install: retrying cannot create the file
                raise
            except OSError as e:
                error = e
                if attempt + 1 < attempts:
                    time.sleep(delay)
                    delay *= 2
            else:
                return
        raise error

    @staticmethod
    def _wait_started(process, window, interval):
        """
        Watch a freshly started process for a while and report what it did.

        A process that is alive at the end of the window has started: the
        window is what catches a crash on startup, which happens within
        seconds. The process is polled, never slept through, so a fast exit is
        noticed at the first interval instead of after the whole window.

        Args:
            process (subprocess.Popen): The started process
            window (float): Seconds to watch at most
            interval (float): Seconds between two polls

        Returns:
            int: Exit code when the process exited during the window, None
                when it was still running at the end of it
        """
        deadline = time.monotonic() + window
        while True:
            code = process.poll()
            if code is not None:
                return code
            if time.monotonic() >= deadline:
                return None
            time.sleep(interval)

    def relaunch(self, attempts=3, window=START_WINDOW, interval=START_INTERVAL):
        """
        Start the application again, detached from this process.

        The arguments are a list, never a shell string, and the credential
        stays in the environment (built in __init__): it is not visible in
        the process list that way.

        The child is polled for ``window`` seconds: still running at the end
        of it means it started; exiting with a non zero code is a crash of the
        new build, which retrying would only repeat; exiting with 0 is the
        single instance lock of a running electron or an application that quit
        on its own, and both are worth another attempt.

        Args:
            attempts (int): Attempts before the failure is reported. Defaults
                to 3.
            window (float): Seconds each attempt is watched. Defaults to 10.
            interval (float): Seconds between two polls. Defaults to 0.5.

        Raises:
            OSError: The program cannot be started
            RuntimeError: The program exited with an error, or exited on every
                attempt without starting
        """
        kwargs = {
            'cwd': self.cwd,
            'env': self.env,
            'stdin': subprocess.DEVNULL,
            'stdout': subprocess.DEVNULL,
            'stderr': subprocess.DEVNULL,
            'close_fds': True,
        }
        if sys.platform == 'win32':
            kwargs['creationflags'] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs['start_new_session'] = True
        error = None
        for attempt in range(attempts):
            try:
                process = subprocess.Popen([self.exe, *self.argv], **kwargs)
            except OSError as e:
                error = e
            else:
                code = self._wait_started(process, window, interval)
                if code is None:
                    return
                if code != 0:
                    # a crash on startup: retrying would only repeat it
                    raise RuntimeError(f'the application exited with code {code}')
                error = RuntimeError(
                    'the application exited at once, another instance is running')
            if attempt + 1 < attempts:
                time.sleep(interval)
        raise error

    def run(self):
        """
        Carry the relaunch through, from the preflight check to the new process.

        Returns:
            int: Exit code, see the constants of this module
        """
        log = self.log
        log.info(f'preflight: {self.src} -> {self.dst}')
        try:
            staged = self.verify()
        except PreflightError as e:
            log.error(f'preflight: {e}, not installing')
            return EXIT_PREFLIGHT
        except OSError as e:
            log.error(f'preflight: cannot read {self.src}: {e}')
            return EXIT_PREFLIGHT
        if not staged:
            log.error(f'preflight: {self.src} is gone, nothing to install')
            return EXIT_PREFLIGHT

        pending = self.wait_targets()
        if pending:
            log.error(f'wait: still running after {self.timeout:.1f}s: {", ".join(pending)}')
            return EXIT_TIMEOUT
        log.info('wait: the old chain is gone')

        try:
            os.stat(self.resume_file)
        except FileNotFoundError:
            log.info(f'go: {self.resume_file} is gone, the relaunch was withdrawn')
            return EXIT_NO_GO
        except OSError as e:
            # the answer is unknown, and acting on an unknown answer would
            # install an archive the user may have cancelled
            log.warning(f'go: cannot read {self.resume_file}: {e}')
            return EXIT_NO_GO

        installed = False
        try:
            staged = self.verify()
        except PreflightError as e:
            log.warning(f'replace: {e}, keeping the installed one')
        except OSError as e:
            log.warning(f'replace: cannot read {self.src}: {e}')
        else:
            if not staged:
                # another updater consumed the staged archive: it installed the
                # very same content, so there is nothing left to do here
                log.info(f'replace: {self.src} already consumed, leaving {self.dst} alone')
                installed = True
            else:
                try:
                    self.replace()
                except FileNotFoundError:
                    log.info(f'replace: {self.src} was consumed by another updater')
                    installed = True
                except OSError as e:
                    log.warning(f'replace: {self.src} -> {self.dst} failed: {e}')
                else:
                    installed = True
                    log.info(f'replace: installed {self.dst}')

        try:
            self.relaunch()
        except (OSError, RuntimeError) as e:
            log.error(f'relaunch: {self.exe} failed: {e}')
            return EXIT_RELAUNCH
        log.info(f'relaunch: started {self.exe}')
        return EXIT_OK if installed else EXIT_REPLACE

    @classmethod
    def main(cls):
        """
        Entry point of ``python updater.py``.

        The command line carries nothing, the environment is the whole input
        and today's log file is the whole output. stdout and stderr belong to
        the chain that started this process and has exited by the time the
        work starts: those pipes have no reader left, so a write would either
        be lost or block once the pipe buffer is full.

        Returns:
            int: Exit code, see the constants of this module
        """
        log = Log(cls.log_path())
        log.open()
        try:
            try:
                updater = cls(log=log)
            except PreflightError as e:
                # dst and src are unknown without the environment, but the
                # place to say so is not
                log.error(f'preflight: {e}')
                return EXIT_PREFLIGHT
            try:
                return updater.run()
            except Exception as e:
                # the stack is worth keeping: this is where a bug of the
                # updater itself ends up, and the process is gone afterwards
                log.error(f'error: {type(e).__name__}: {e}', exc_info=True)
                return EXIT_ERROR
        finally:
            log.close()


if __name__ == '__main__':
    sys.exit(ElectronUpdater.main())
