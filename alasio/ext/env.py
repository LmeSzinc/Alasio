import os
import sys

from alasio.ext.path import PathStr
from alasio.ext.path.calc import abspath

# Operating System
POSIX = os.name == "posix"
WINDOWS = os.name == "nt"
LINUX = sys.platform.startswith("linux")
MACOS = sys.platform.startswith("darwin")
OSX = MACOS  # deprecated alias
FREEBSD = sys.platform.startswith(("freebsd", "midnightbsd"))
OPENBSD = sys.platform.startswith("openbsd")
NETBSD = sys.platform.startswith("netbsd")
BSD = FREEBSD or OPENBSD or NETBSD
SUNOS = sys.platform.startswith(("sunos", "solaris"))
AIX = sys.platform.startswith("aix")

# Global variable
PROJECT_ROOT = PathStr.new('')
ALASIO_ROOT = PathStr.new(__file__).uppath(3)
# Disposable folder of the repository, tests put their temporary files here
ALASIO_TEMP = ALASIO_ROOT.joinpath('temp')

# Continuous integration: the runners of github actions and of most other
# services set CI, the value differs (true, 1) so only the emptiness is trusted
CI = os.environ.get('CI', '') not in ('', '0')


def set_project_root(root, up=0):
    """
    Set the project root, a relative root is resolved against the current
    working directory

    Args:
        root (str): Project root path, absolute or relative. A relative path
            is what __file__ holds when the interpreter is started with a
            relative script path, e.g. `python module/main.py`.
        up (int): Directory upward levels to apply after the root is
            resolved. Defaults to 0.
    """
    root = PathStr.new(abspath(root))
    if up:
        root = root.uppath(up)
    global PROJECT_ROOT
    PROJECT_ROOT = root


# Environ
# Role of the current process: 'worker' runs the game script of one mod,
# 'backend' serves the GUI and validates the config of every mod.
# Default to 'worker' so a plain `python -m module.main`, a script or a test
# behaves like a real worker without setting anything; only the backend
# process declares itself.
PROCESS_ROLE = 'worker'
# Set to "1" by the supervisor when started with --electron; silences structlog stdout
ELECTRON = os.environ.get("ELECTRON", "")
CHINAC_CLOUDPHONE = os.environ.get("cloudphone", "") == "cloudphone"
