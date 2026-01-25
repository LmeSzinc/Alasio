import sys

if sys.version_info >= (3, 9):
    def removeprefix(s, prefix):
        return s.removeprefix(prefix)


    def removesuffix(s, prefix):
        return s.removesuffix(prefix)

else:
    # Backport `string.removeprefix(prefix)`, which is on Python>=3.9
    def removeprefix(s, prefix):
        """
        Args:
            s (T):
            prefix (T):

        Returns:
            T:
        """
        if s.startswith(prefix):
            return s[len(prefix):]
        else:
            return s


    # Backport `string.removesuffix(suffix)`, which is on Python>=3.9
    def removesuffix(s, suffix):
        """
        Args:
            s (T):
            suffix (T):

        Returns:
            T:
        """
        if s.endswith(suffix):
            return s[:-len(suffix)]
        else:
            return s


# Quote LiteralType because typing_extensions.LiteralType is not available on 3.8
def to_literal(items):
    """
    Dynamically create literal object from a list, `Literal[*items]`, which is on Python>=3.11

    Useful when you don't want to write the same things twice like:
        lang = ['zh', 'en', 'ja', 'kr']
        langT = Literal['zh', 'en', 'ja', 'kr']
    With backport you can do:
        lang = ['zh', 'en', 'ja', 'kr']
        langT = to_literal(lang)

    Args:
        items (iterable[T]):

    Returns:
        LiteralType[T]:
    """
    from typing import Literal
    return Literal.__getitem__(tuple(items))


def fix_py37_subprocess_communicate():
    """
    Monkey patch for subprocess.Popen._communicate on Windows Python 3.7
    Fixes: IndexError: list index out of range

    This bug is fixed on python>=3.8, so we backport the fix

    Ref:
        https://github.com/LmeSzinc/AzurLaneAutoScript/issues/5226
        https://bugs.python.org/issue43423
        https://github.com/python/cpython/pull/24777
    """
    import subprocess
    import sys
    import threading

    if sys.platform != 'win32' or sys.version_info[:2] != (3, 7):
        return

    def _communicate_fixed(self, input, endtime, orig_timeout):
        # Start reader threads feeding into a list hanging off of this
        # object, unless they've already been started.
        if self.stdout and not hasattr(self, "_stdout_buff"):
            self._stdout_buff = []
            self.stdout_thread = \
                threading.Thread(target=self._readerthread,
                                 args=(self.stdout, self._stdout_buff))
            self.stdout_thread.daemon = True
            self.stdout_thread.start()
        if self.stderr and not hasattr(self, "_stderr_buff"):
            self._stderr_buff = []
            self.stderr_thread = \
                threading.Thread(target=self._readerthread,
                                 args=(self.stderr, self._stderr_buff))
            self.stderr_thread.daemon = True
            self.stderr_thread.start()

        if self.stdin:
            self._stdin_write(input)

        # Wait for the reader threads, or time out.  If we time out, the
        # threads remain reading and the fds left open in case the user
        # calls communicate again.
        if self.stdout is not None:
            self.stdout_thread.join(self._remaining_time(endtime))
            if self.stdout_thread.is_alive():
                raise subprocess.TimeoutExpired(self.args, orig_timeout)
        if self.stderr is not None:
            self.stderr_thread.join(self._remaining_time(endtime))
            if self.stderr_thread.is_alive():
                raise subprocess.TimeoutExpired(self.args, orig_timeout)

        # Collect the output from and close both pipes, now that we know
        # both have been read successfully.
        stdout = None
        stderr = None
        if self.stdout:
            stdout = self._stdout_buff
            self.stdout.close()
        if self.stderr:
            stderr = self._stderr_buff
            self.stderr.close()

        # All data exchanged.  Translate lists into strings.

        # --- FIX START ---
        stdout = stdout[0] if stdout else None
        stderr = stderr[0] if stderr else None
        # --- FIX END ---

        return (stdout, stderr)

    subprocess.Popen._communicate = _communicate_fixed


def process_cpu_count():
    """
    Backport os.process_cpu_count() on python >= 3.13

    If the current environment lacks this function (3.7-3.12), fall back to os.cpu_count.
    os.cpu_count returns the number of physical/logical cores without considering process affinity,
    but this is the closest behavior achievable in older Python versions.

    Returns:
        int | None:
    """
    import os
    get_cpu_count = getattr(os, "process_cpu_count", os.cpu_count)
    try:
        # cpu_count may return None (e.g., on systems where it cannot be detected)
        return get_cpu_count()
    except Exception:
        return None


def patch_threadpool_executor_maxworker():
    """
    Backport Python 3.13's default max_workers logic to older Python versions.
    Applicable for: Python 3.7 ~ 3.12

    py3.7:
        max_workers = (os.cpu_count() or 1) * 5
    py3.8~py3.12:
        max_workers = min(32, (os.cpu_count() or 1) + 4)
    py3.13:
        max_workers = min(32, (os.process_cpu_count() or 1) + 4)

    Ref:
        https://bugs.python.org/issue35279
        https://github.com/python/cpython/pull/110165
    """
    current_version = sys.version_info[:2]
    if not ((3, 7) <= current_version < (3, 13)):
        return

    from concurrent.futures import ThreadPoolExecutor
    original_init = ThreadPoolExecutor.__init__

    # Prevent duplicate patching
    if getattr(ThreadPoolExecutor, "_is_defaults_patched", False):
        return

    def init_backport(self, max_workers=None, *args, **kwargs):
        """
        Wrapper around ThreadPoolExecutor.__init__ to inject new default max_workers.
        """
        if max_workers is None:
            # --- BACKPORT START ---
            # Python 3.13 logic:
            # max_workers = min(32, (os.process_cpu_count() or 1) + 4)
            max_workers = min(32, (process_cpu_count() or 1) + 4)
            # --- BACKPORT END ---

        # Call the original __init__, passing the calculated max_workers or the user-specified value
        # Note: Python 3.7's __init__ does not accept **kwargs (ctxkwargs),
        # but subsequent versions do. For generality, we pass them through directly.
        # If invalid arguments are passed on 3.7, original_init will raise a TypeError as expected.
        original_init(self, max_workers, *args, **kwargs)

    # Apply Monkey Patch
    ThreadPoolExecutor.__init__ = init_backport
    ThreadPoolExecutor._is_defaults_patched = True


def patch_rich_traceback_extract():
    """
    Patch rich.traceback Traceback.extract() to remove python version check on exceptiongroup

    Returns:
        bool: If Traceback.extract() can handle exceptiongroup
    """
    if sys.version_info >= (3, 11):
        # python>=3.11 have builtin exceptiongroup, no need to patch
        return True
    try:
        from rich.traceback import Traceback
    except ImportError:
        # no rich, no need to patch
        return True

    from importlib.metadata import version, PackageNotFoundError
    try:
        ver = version('rich')
    except PackageNotFoundError:
        # this shouldn't happen
        return True

    if ver in ['14.1.0', '14.2.0']:
        from alasio.ext.backport.rich_14_1 import RichTracebackBackport
    elif ver in ['14.3.0', '14.3.1']:
        from alasio.ext.backport.rich_14_3 import RichTracebackBackport
    else:
        return False

    # Apply Monkey Patch
    Traceback.extract = RichTracebackBackport.extract
