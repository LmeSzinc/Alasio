"""Import entry points of the emulator search entry module.

These functions are the callbacks of `run_import_test_isolated()`. They live in
their own module, not in `test_search.py`, because the import subprocess
unpickles the callback by importing the module that defines it: the test module
carries pytest, the request fixtures and everything the suite imported, and
none of it belongs in a measurement of what the entry point loads.

Keep this module free of module level imports of the code under test. The
import subprocess records its module baseline when the target starts, so the
modules of this file are already imported at that point; an import of the entry
point here would be loaded before the measurement and would hide a violation.

`import_as_posix()` has to run on a Windows host as well, so it does not rely on
`winreg` being absent: it blocks the import itself, which is what a real posix
platform does, and a regression that reaches `windows.py` then fails loudly
instead of quietly importing the Windows registry on the Windows host.
"""
import sys


class _NoWinreg:
    """Import hook that behaves like a platform without the winreg module."""

    def find_spec(self, name, path=None, target=None):
        if name == 'winreg':
            raise ModuleNotFoundError("No module named 'winreg'", name=name)
        return None


def block_winreg():
    """
    Make `import winreg` fail, like it does on posix.

    `winreg` is a builtin module on Windows and is loaded before user code runs,
    so it has to be dropped from `sys.modules` before the hook can take effect.
    """
    sys.modules.pop('winreg', None)
    sys.meta_path.insert(0, _NoWinreg())


def import_as_posix():
    """
    Import the entry point on a simulated posix platform.

    Raises:
        AssertionError: If the entry point is not the base implementation
    """
    block_winreg()

    from alasio.ext import env

    env.WINDOWS = False

    from alasio.device.search.base import EmulatorSearchBase
    from alasio.device.search.search import EmulatorSearch

    assert EmulatorSearch is EmulatorSearchBase, EmulatorSearch


def import_as_windows():
    """
    Import the entry point on a simulated Windows platform.

    Raises:
        AssertionError: If the entry point is not the Windows implementation
    """
    from alasio.ext import env

    env.WINDOWS = True

    from alasio.device.search.search import EmulatorSearch
    from alasio.device.search.windows import EmulatorSearchWindows

    assert EmulatorSearch is EmulatorSearchWindows, EmulatorSearch
