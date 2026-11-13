"""Cross-platform import of the emulator search entry point.

`search.py` must resolve to the implementation of the current platform, and it
must not load the modules of another platform: `windows.py` imports `winreg` at
module level, which does not exist on posix.

Both platforms are measured by an isolated import subprocess, so the posix
behaviour is checked on every host. The callbacks live in `import_entry.py`, an
entry module without pytest and without the imports of this file, because the
subprocess imports the module that defines the callback to unpickle it.
"""
import sys

import pytest

from alasio.ext.env import WINDOWS
from tests.backend.import_testing import run_import_test_isolated
from tests.device.search.import_entry import import_as_posix, import_as_windows

pytestmark = [
    pytest.mark.parallel,
]

# The modules that only work on Windows, a posix import must never load them
WINDOWS_MODULES = {
    'alasio.device.search.windows',
    'alasio.device.search.windows_reg',
}


class TestEmulatorSearchEntryPoint:
    """The entry point resolves to the implementation of this platform."""

    def test_the_entry_point_is_the_implementation_of_this_platform(self):
        from alasio.device.search.base import EmulatorSearchBase
        from alasio.device.search.search import EmulatorSearch

        if WINDOWS:
            from alasio.device.search.windows import EmulatorSearchWindows

            assert EmulatorSearch is EmulatorSearchWindows
        else:
            assert EmulatorSearch is EmulatorSearchBase

    def test_the_entry_point_is_a_search_base(self):
        from alasio.device.search.base import EmulatorSearchBase
        from alasio.device.search.search import EmulatorSearch

        assert issubclass(EmulatorSearch, EmulatorSearchBase)

    def test_the_public_name_is_the_entry_point_only(self):
        from alasio.device.search import search

        assert search.__all__ == ['EmulatorSearch']

    def test_the_entry_point_is_exported_by_name(self):
        """`from ...search import EmulatorSearch` is the only supported import."""
        from alasio.device.search import search

        for name in search.__all__:
            assert getattr(search, name) is search.EmulatorSearch


class TestPlatformModuleIsolation:
    """Only the module of the current platform is loaded."""

    def test_posix_import_does_not_load_the_windows_modules(self):
        """A posix import stays on base, the windows modules need winreg."""
        violations = run_import_test_isolated(
            import_as_posix,
            WINDOWS_MODULES,
            'EmulatorSearch posix import',
        )
        assert violations is None

    @pytest.mark.skipif(
        not WINDOWS,
        reason='The Windows entry point imports winreg, which exists on Windows only',
    )
    def test_windows_import_does_not_load_a_forbidden_module(self):
        """The windows entry point is selected, with nothing else pulled in.

        This runs on a Windows host only. On posix the requirement is the
        opposite one, and `test_posix_import_does_not_load_the_windows_modules`
        holds it: the entry point must never reach a module that needs winreg.
        """
        violations = run_import_test_isolated(
            import_as_windows,
            set(),
            'EmulatorSearch windows import',
        )
        assert violations is None

    def test_the_windows_modules_are_loaded_here_only_on_windows(self):
        """The same rule holds for this process, which imported the entry point."""
        from alasio.device.search import search  # noqa: F401

        for name in WINDOWS_MODULES:
            assert (name in sys.modules) is WINDOWS
