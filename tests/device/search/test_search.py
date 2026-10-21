from alasio.device.search.base import EmulatorSearchBase
from alasio.device.search.search import EmulatorSearch
from alasio.ext.env import WINDOWS

if WINDOWS:
    from alasio.device.search.windows import EmulatorSearchWindows


class TestEmulatorSearch:
    def test_platform_implementation(self):
        """
        The entry point resolves to the implementation of the current platform
        """
        if WINDOWS:
            assert EmulatorSearch is EmulatorSearchWindows
        else:
            assert EmulatorSearch is EmulatorSearchBase
