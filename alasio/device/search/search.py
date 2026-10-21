from alasio.ext.env import WINDOWS

# The platform modules import the libraries of their platform at module level,
# windows_reg.py imports winreg which does not exist on posix. Import only the
# module of the current platform, so the others are never loaded.
if WINDOWS:
    from alasio.device.search.windows import EmulatorSearchWindows as EmulatorSearch
else:
    from alasio.device.search.base import EmulatorSearchBase as EmulatorSearch

__all__ = ['EmulatorSearch']
