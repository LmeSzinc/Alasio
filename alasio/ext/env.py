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
# Pid of the electron main process that hosts this chain, written by electron
# itself. It is the electron mode switch of every process of the chain: the
# chain is spawned with an inherited environment, so the backend and its workers
# read the same value the supervisor does. Nothing verifies the pid at startup --
# it is a switch there, and a target only where the electron archive actually
# has to be replaced (see backend/app/restart/shell_update.py).
ELECTRON_PID = os.environ.get('ALASIO_APP_ELECTRON_PID', '')
CHINAC_CLOUDPHONE = os.environ.get("cloudphone", "") == "cloudphone"
