import sys
from typing import Literal

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
