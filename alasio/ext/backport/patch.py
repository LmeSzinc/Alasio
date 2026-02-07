# This file is a collection of magic matches to provide consistent behaviour on all deployment
import sys
import threading

from alasio.ext.backport import process_cpu_count
from alasio.ext.backport.once import patch_once


@patch_once
def patch_mimetype():
    """
    Patch mimetype db to use the builtin table instead of reading from environment.

    By default, mimetype reads user configured mimetype table from environment. It's good for server but bad on our
    side, because we deploy on user's machine which may have polluted environment. To have a consistent behaviour on
    all deployment, we use the builtin mimetype table only.
    """
    import mimetypes
    if mimetypes.inited:
        # ohno mimetypes already inited
        db = mimetypes.MimeTypes()
        mimetypes._db = db
        # override global variable
        mimetypes.encodings_map = db.encodings_map
        mimetypes.suffix_map = db.suffix_map
        mimetypes.types_map = db.types_map[True]
        mimetypes.common_types = db.types_map[False]
    else:
        # init db with the default table
        db = mimetypes.MimeTypes()
        mimetypes._db = db
        mimetypes.inited = True


@patch_once
def patch_std():
    """
    Force use utf-8 in stdin, stdout, stderr, ignoring any user env.

    Returns:
        bool: if success
    """
    # note that reconfigure() requires python>=3.7
    import sys
    try:
        sys.stdin.reconfigure(encoding='utf-8', errors='replace')
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        return True
    except (AttributeError, TypeError):
        # std may get replaced by user's TextIO
        # which does not have reconfigure()
        return False


@patch_once
def patch_environ():
    """
    Remove all python related environs, updated to python 3.15
    Note that some environs effects python interpreter startup, removing them at runtime won't work.

    You can AI to extract the list from https://docs.python.org/3/using/cmdline.html
    """
    python_environment_variables = [
        "FORCE_COLOR",
        "NO_COLOR",
        "PYTHONASYNCIODEBUG",
        "PYTHONBREAKPOINT",
        "PYTHONCASEOK",
        "PYTHONCOERCECLOCALE",
        "PYTHONDEBUG",
        "PYTHONDEVMODE",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONDUMPREFS",
        "PYTHONDUMPREFSFILE",
        "PYTHONEXECUTABLE",
        "PYTHONFAULTHANDLER",
        "PYTHONHASHSEED",
        "PYTHONHOME",
        "PYTHONINSPECT",
        "PYTHONINTMAXSTRDIGITS",
        "PYTHONIOENCODING",
        "PYTHONLEGACYWINDOWSFSENCODING",
        "PYTHONLEGACYWINDOWSSTDIO",
        "PYTHONMALLOC",
        "PYTHONMALLOCSTATS",
        "PYTHONNODEBUGRANGES",
        # "PYTHONNOUSERSITE",
        "PYTHONOPTIMIZE",
        "PYTHONPATH",
        "PYTHONPERFSUPPORT",
        "PYTHONPLATLIBDIR",
        "PYTHONPROFILEIMPORTTIME",
        "PYTHONPYCACHEPREFIX",
        "PYTHONSAFEPATH",
        "PYTHONSTARTUP",
        "PYTHONTRACEMALLOC",
        "PYTHONUNBUFFERED",
        "PYTHONUSERBASE",
        # "PYTHONUTF8",
        "PYTHONVERBOSE",
        "PYTHONWARNDEFAULTENCODING",
        "PYTHONWARNINGS",
        "PYTHON_BASIC_REPL",
        "PYTHON_COLORS",
        "PYTHON_CONTEXT_AWARE_WARNINGS",
        "PYTHON_CPU_COUNT",
        "PYTHON_DISABLE_REMOTE_DEBUG",
        "PYTHON_FROZEN_MODULES",
        "PYTHON_GIL",
        "PYTHON_HISTORY",
        "PYTHON_JIT",
        "PYTHON_PERF_JIT_SUPPORT",
        "PYTHON_PRESITE",
        "PYTHON_THREAD_INHERIT_CONTEXT",
        "PYTHON_TLBC",
    ]
    proxy_environment_variables = [
        'HTTP_PROXY',
        'HTTPS_PROXY',
        'ALL_PROXY',
        'NO_PROXY',
        'HTTPPROXY',
        'HTTPSPROXY',
        'ALLPROXY',
        'NOPROXY',
    ]
    removes = set(python_environment_variables + proxy_environment_variables)

    import os
    # listify to safely iterate
    environs = [key for key in os.environ]
    for key in environs:
        upper = key.upper()
        if upper in removes:
            os.environ.pop(key, None)

    # set PYTHONUTF8 so child process will use UTF8
    os.environ["PYTHONUTF8"] = "1"
    # set PYTHONNOUSERSITE so child process will ignore user site-packages
    os.environ["PYTHONNOUSERSITE"] = "1"


@patch_once
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


@patch_once
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


def patch_startup():
    """
    A collection of patches on process startup
    This function is supposed to be called in entry file (outside `if __name__ == "__main__":`)
        so subprocesses can be patched too

    It's also recommended to launch with
        python -s -OO gui.py
    "-s" to ignore user site-packages
    "-OO" to drop assert and docstring at runtime
    """
    patch_environ()
    patch_std()
