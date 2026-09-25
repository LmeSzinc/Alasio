"""
Guard of the run directory of the pack server.

The pack server clones the repos to pack into its run directory and writes
the packs and the configs there, so it must run in a directory that belongs
to it:

- not the alasio library itself, the workspace, the packs and the configs
  would end up in the source tree of the library
- not a mod (a game script like AzurLaneAutoScript), the folder layout of
  the pack server would mix with the files of the script, and the config
  folder of the script would be reused by the pack configs

The pack server operates on several repos, so it needs a run directory of
its own instead of the project root of a mod.

Usage:
    from alasio.deploy_dev.pack_server.gate import check_run_dir

    check_run_dir()              # the default, env.PROJECT_ROOT
    check_run_dir('D:/AlasPack') # an explicit run directory
"""

import os
import stat

from alasio.ext import env
from alasio.ext.path.calc import joinnormpath

# Markers of a mod (a game script) in its root, see ModEntryInfo:
# - the generated index of the config definitions, ModEntryInfo.exist()
# - the scheduler of the mod, ModEntryInfo.path_main
# - the folder of the mod code, the config definitions are inside it
# - the assets of the mod, ModEntryInfo.path_assets
MOD_MARKERS = (
    'module/config/_index/config.index.json',
    'module/main.py',
    'module',
    'assets',
)


class RunDirError(ValueError):
    """
    Raised when the run directory of the pack server is not usable
    """


def is_library_dir(root):
    """
    Check whether a folder is the alasio library itself, or inside the library

    Args:
        root (str): Absolute path of the folder

    Returns:
        bool:
    """
    root = os.path.normcase(os.path.realpath(root))
    library = os.path.normcase(os.path.realpath(env.ALASIO_ROOT))
    if root == library:
        return True
    # a folder inside the library
    return root.startswith(library + os.sep)


def is_mod_dir(root):
    """
    Check whether a folder is a mod, a game script

    Args:
        root (str): Absolute path of the folder

    Returns:
        str: The marker found, empty string if the folder is not a mod
    """
    for marker in MOD_MARKERS:
        try:
            os.stat(joinnormpath(root, marker))
        except (FileNotFoundError, NotADirectoryError):
            continue
        return marker
    return ''


def check_run_dir(root=''):
    """
    Check that the pack server runs in a run directory of its own

    Args:
        root (str): Run directory, default to env.PROJECT_ROOT

    Raises:
        RunDirError: If the run directory is not set, does not exist, is the
            alasio library itself or inside it, or is a mod
    """
    if not root:
        root = env.PROJECT_ROOT
    if not root:
        raise RunDirError('Run directory is not set, call env.set_project_root() first')

    try:
        st = os.stat(root)
    except (FileNotFoundError, NotADirectoryError):
        raise RunDirError(f'Run directory does not exist: "{root}"')
    if not stat.S_ISDIR(st.st_mode):
        raise RunDirError(f'Run directory is not a folder: "{root}"')

    if is_library_dir(root):
        raise RunDirError(
            f'Cannot run in the alasio library: "{root}", '
            f'the pack server needs a run directory of its own'
        )

    marker = is_mod_dir(root)
    if marker:
        raise RunDirError(
            f'Cannot run in a mod: "{root}" is a game script, "{marker}" is found, '
            f'the pack server needs a run directory of its own'
        )
