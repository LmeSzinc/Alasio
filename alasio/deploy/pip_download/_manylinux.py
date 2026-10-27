"""
PEP 513 / PEP 571 / PEP 599 / PEP 600 manylinux platform tags.

Ported from `packaging/_manylinux.py` (packaging, Apache-2.0 OR BSD-2-Clause):
the glibc version detection, the compatibility check and the tag generation
are the source implementation, with one documented simplification:

- the source falls back to reading the ELF of the interpreter and to the
  `_manylinux` C extension to find the libc version and the ABI of the
  machine; here the glibc version is read with os.confstr() only. When the
  version cannot be read, no manylinux tag is produced, which is the answer
  the source gives too when it cannot detect the libc.

Usage:
    from alasio.deploy.pip_download._manylinux import platform_tags

    list(platform_tags(['x86_64']))    # e.g. ['manylinux_2_17_x86_64', ...]
"""

import collections
import functools
import os
import re
from typing import NamedTuple

from alasio.logger import logger

# The highest minor version of a glibc major version the source knows about,
# a major version that is not listed is treated as 50 (glibc has never had a
# minor version above a few tens)
_LAST_GLIBC_MINOR = collections.defaultdict(lambda: 50)


class _GLibCVersion(NamedTuple):
    """
    The major and minor version of a glibc.

    Ported from packaging/_manylinux.py.
    """

    major: int
    minor: int


def _glibc_version_string_confstr():
    """
    The glibc version string, read with os.confstr.

    Ported from packaging/_manylinux.py: os.confstr is faster than the
    ctypes/DLL route and is the strategy of the platform module of the
    standard library.

    Returns:
        str | None: The version, e.g. '2.17', None when the system does not
            answer (not a glibc system, or os.confstr is not available)
    """
    try:
        # should be a string like 'glibc 2.17'
        version_string = os.confstr('CS_GNU_LIBC_VERSION')
    except (AttributeError, OSError, ValueError):
        # os.confstr() or CS_GNU_LIBC_VERSION not available (or a bad value)
        return None
    if not version_string:
        return None
    _, sep, version = version_string.rpartition(' ')
    if not sep:
        return None
    return version


def _glibc_version_string():
    """
    The glibc version string of the running system.

    Ported from packaging/_manylinux.py, with the ELF fallback of the source
    removed (see the module docstring).

    Returns:
        str | None: The version, e.g. '2.17', None when the libc is not a
            glibc or its version cannot be read
    """
    return _glibc_version_string_confstr()


def _parse_glibc_version(version_str):
    """
    Parse a glibc version string into its major and minor version.

    Ported from packaging/_manylinux.py: the junk a patched glibc appends
    after the minor version (e.g. '2.20-2014.11') is ignored.

    Args:
        version_str (str): Version string, e.g. '2.17'

    Returns:
        _GLibCVersion: The version, (-1, -1) when the string does not parse
    """
    m = re.match(r'(?P<major>[0-9]+)\.(?P<minor>[0-9]+)', version_str)
    if not m:
        logger.warning(
            f'Expected glibc version with 2 components major.minor, got: {version_str}')
        return _GLibCVersion(-1, -1)
    return _GLibCVersion(int(m.group('major')), int(m.group('minor')))


@functools.lru_cache(maxsize=None)
def _get_glibc_version():
    """
    The glibc version of the running system, read once per process.

    Ported from packaging/_manylinux.py.

    Returns:
        _GLibCVersion: The version, (-1, -1) when the libc is not a glibc or
            its version cannot be read
    """
    version_str = _glibc_version_string()
    if version_str is None:
        return _GLibCVersion(-1, -1)
    return _parse_glibc_version(version_str)


@functools.lru_cache(maxsize=None)
def _get_manylinux_module():
    """
    The `_manylinux` C extension module, None when it is not installed.

    Ported from packaging/_manylinux.py: the module lets a distribution
    override the compatibility decision of the glibc version.

    Returns:
        types.ModuleType | None: The module, or None
    """
    try:
        return __import__('_manylinux')
    except ImportError:
        return None


def _is_compatible(arch, version):
    """
    Whether a manylinux tag of one arch and glibc version runs here.

    Ported from packaging/_manylinux.py: the glibc of the system must be at
    least the version of the tag, and the `_manylinux` module may veto it.

    Args:
        arch (str): Architecture, e.g. 'x86_64'
        version (_GLibCVersion): glibc version of the tag

    Returns:
        bool: True when the tag is compatible
    """
    sys_glibc = _get_glibc_version()
    if sys_glibc < version:
        return False

    # check for the presence of the _manylinux module
    manylinux_module = _get_manylinux_module()
    if manylinux_module is None:
        return True

    if hasattr(manylinux_module, 'manylinux_compatible'):
        result = manylinux_module.manylinux_compatible(version.major, version.minor, arch)
        if result is not None:
            return bool(result)
        return True

    if version == _GLibCVersion(2, 5) and hasattr(manylinux_module, 'manylinux1_compatible'):
        return bool(manylinux_module.manylinux1_compatible)
    if version == _GLibCVersion(2, 12) and hasattr(manylinux_module, 'manylinux2010_compatible'):
        return bool(manylinux_module.manylinux2010_compatible)
    if version == _GLibCVersion(2, 17) and hasattr(manylinux_module, 'manylinux2014_compatible'):
        return bool(manylinux_module.manylinux2014_compatible)
    return True


# The legacy names of the glibc versions, PEP 513 / 571 / 599
_LEGACY_MANYLINUX_MAP = {
    # CentOS 7 w/ glibc 2.17 (PEP 599)
    _GLibCVersion(2, 17): 'manylinux2014',
    # CentOS 6 w/ glibc 2.12 (PEP 571)
    _GLibCVersion(2, 12): 'manylinux2010',
    # CentOS 5 w/ glibc 2.5 (PEP 513)
    _GLibCVersion(2, 5): 'manylinux1',
}


def platform_tags(archs):
    """
    Generate the manylinux tags compatible with the running system.

    Ported from packaging/_manylinux.py: every glibc from the version of the
    system down to the oldest version the PEPs support is a tag, the legacy
    names (manylinux2014 and friends) follow their version.

    Args:
        archs (Sequence[str]): Compatible architectures, the first one is the
            closest to the machine and is the part after 'linux_'

    Yields:
        str: A platform tag, e.g. 'manylinux_2_17_x86_64'
    """
    # oldest glibc to be supported regardless of architecture is (2, 17)
    too_old_glibc2 = _GLibCVersion(2, 16)
    if set(archs) & {'x86_64', 'i686'}:
        # on x86/i686 the oldest glibc to be supported is (2, 5)
        too_old_glibc2 = _GLibCVersion(2, 4)

    current_glibc = _GLibCVersion(*_get_glibc_version())
    glibc_max_list = [current_glibc]
    # glibc majors are compatible with each other:
    # https://sourceware.org/bugzilla/show_bug.cgi?id=24636
    #
    # the list of the maximum glibc versions is built, so the tags go from the
    # version of the system down to too_old_glibc2, the intermediate versions
    # included
    for glibc_major in range(current_glibc.major - 1, 1, -1):
        glibc_minor = _LAST_GLIBC_MINOR[glibc_major]
        glibc_max_list.append(_GLibCVersion(glibc_major, glibc_minor))

    for arch in archs:
        for glibc_max in glibc_max_list:
            if glibc_max.major == too_old_glibc2.major:
                min_minor = too_old_glibc2.minor
            else:
                # for the other glibc majors the oldest supported is (x, 0)
                min_minor = -1
            for glibc_minor in range(glibc_max.minor, min_minor, -1):
                glibc_version = _GLibCVersion(glibc_max.major, glibc_minor)
                if _is_compatible(arch, glibc_version):
                    yield 'manylinux_{}_{}_{}'.format(
                        glibc_version.major, glibc_version.minor, arch)

                    # the legacy manylinux1, manylinux2010 and manylinux2014
                    # names of the same version
                    legacy_tag = _LEGACY_MANYLINUX_MAP.get(glibc_version)
                    if legacy_tag:
                        yield f'{legacy_tag}_{arch}'
