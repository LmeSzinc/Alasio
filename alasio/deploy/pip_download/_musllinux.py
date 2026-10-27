"""
PEP 656 musllinux platform tags.

Ported from `packaging/_musllinux.py` (packaging, Apache-2.0 OR BSD-2-Clause):
the parse of the loader output and the tag generation are the source
implementation, with one documented simplification:

- the source finds the dynamic loader of the interpreter by reading the ELF
  header of the executable; here the loader of the system is looked up by its
  name (`ld-musl-*.so.1` in the lib folders). A system whose loader cannot be
  found produces no musllinux tag, like a system whose libc the source cannot
  detect.

Usage:
    from alasio.deploy.pip_download._musllinux import platform_tags

    list(platform_tags(['x86_64']))    # e.g. ['musllinux_1_2_x86_64', ...]
"""

import functools
import glob
import os
import re
import subprocess
import sys
from typing import NamedTuple

from alasio.deploy.pip_download._manylinux import _get_glibc_version
from alasio.logger import logger

# The folders a musl loader is installed in
_LOADER_FOLDERS = ('/lib', '/usr/lib', '/lib64', '/usr/local/lib')

# The timeout of the loader run, a hung loader is not worth a wait
_LOADER_TIMEOUT = 5


class _MuslVersion(NamedTuple):
    """
    The major and minor version of a musl libc.

    Ported from packaging/_musllinux.py.
    """

    major: int
    minor: int


def _parse_musl_version(output):
    """
    Parse the version out of the output of a musl loader.

    Ported from packaging/_musllinux.py: the loader prints the name of the
    libc on the first line and the version on the second one:

        musl libc (x86_64)
        Version 1.2.2
        Dynamic Program Loader

    Args:
        output (str): Output of the loader

    Returns:
        _MuslVersion | None: The version, None when the output is not the
            output of a musl loader
    """
    lines = [line for line in (line.strip() for line in output.splitlines()) if line]
    if len(lines) < 2 or lines[0][:4] != 'musl':
        return None
    m = re.match(r'Version (\d+)\.(\d+)', lines[1])
    if not m:
        return None
    return _MuslVersion(major=int(m.group(1)), minor=int(m.group(2)))


def _musl_loader_paths():
    """
    The musl loaders installed on the system.

    This is the simplification of the source (see the module docstring): the
    loader of the interpreter is not read from its ELF header, the loaders of
    the standard lib folders are used.

    Returns:
        list[str]: Paths of the loaders, in the order of the folders
    """
    paths = []
    for folder in _LOADER_FOLDERS:
        paths.extend(sorted(glob.glob(os.path.join(folder, 'ld-musl-*.so.1'))))
    return paths


def _run_musl_loader(loader):
    """
    Run a musl loader and parse the version it prints.

    Ported from packaging/_musllinux.py, which runs the loader of the
    interpreter with its stderr captured.

    Args:
        loader (str): Path of the loader

    Returns:
        _MuslVersion | None: The version, None when the loader does not run
            or does not print a musl version
    """
    try:
        proc = subprocess.run(
            [loader], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=_LOADER_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug(f'Failed to run the musl loader "{loader}": {e}')
        return None
    output = proc.stderr or b''
    output += proc.stdout or b''
    return _parse_musl_version(output.decode('utf-8', 'replace'))


@functools.lru_cache(maxsize=None)
def _get_musl_version(executable):
    """
    The musl version of the system, None when the system runs a glibc.

    Ported from packaging/_musllinux.py, the loader lookup is simplified (see
    the module docstring). The answer is cached per executable.

    Args:
        executable (str): Path of the interpreter, the key of the cache (the
            loader of the system is what is actually read)

    Returns:
        _MuslVersion | None: The musl version, None when the libc is not a
            musl or its version cannot be read
    """
    if not sys.platform.startswith('linux'):
        # musl is a linux libc
        return None
    if _get_glibc_version().major != -1:
        # the system answers the glibc version: it is not a musl system
        return None
    for loader in _musl_loader_paths():
        version = _run_musl_loader(loader)
        if version is not None:
            return version
    return None


def platform_tags(archs):
    """
    Generate the musllinux tags compatible with the running system.

    Ported from packaging/_musllinux.py: the musl version of the system and
    the older minor versions of the same major version are the tags.

    Args:
        archs (Sequence[str]): Compatible architectures, the first one is the
            closest to the machine and is the part after 'linux_'

    Yields:
        str: A platform tag, e.g. 'musllinux_1_2_x86_64'
    """
    sys_musl = _get_musl_version(sys.executable)
    if sys_musl is None:
        # python is not linked against a musl libc
        return
    for arch in archs:
        for minor in range(sys_musl.minor, -1, -1):
            yield f'musllinux_{sys_musl.major}_{minor}_{arch}'
