"""
The supported tag set of a target interpreter.

Ported from `pip/_internal/utils/compatibility_tags.py` (pip, MIT):
`get_supported`, the custom platform expansion and `version_info_to_nodot`
are the source implementation. The iOS and Android platform families of the
source are not ported (this project runs on the desktop), see the sibling
`_tags` module.

Usage:
    from alasio.deploy.pip_download._compatibility_tags import get_supported

    tags = get_supported()               # the running interpreter
    tags = get_supported('38')           # python 3.8
"""

import re

from alasio.deploy.pip_download._tags import (
    compatible_tags, cpython_tags, generic_tags, interpreter_name, interpreter_version, mac_platforms
)

_apple_arch_pat = re.compile(r'(.+)_(\d+)_(\d+)_(.+)')


def version_info_to_nodot(version_info):
    """
    Join a version info into the short form, e.g. (3, 8, 10) -> '38'.

    Ported from pip/_internal/utils/compatibility_tags.py: only the first two
    numbers are used.

    Args:
        version_info (Sequence[int]): Python version info

    Returns:
        str: The version without the dots
    """
    return ''.join(map(str, version_info[:2]))


def _mac_platforms(arch):
    """
    Expand a `macosx_<major>_<minor>_<arch>` platform into its family.

    Ported from pip/_internal/utils/compatibility_tags.py.

    Args:
        arch (str): Platform string, e.g. 'macosx_10_9_x86_64'

    Returns:
        list[str]: The platform tags of the family, most preferred first
    """
    match = _apple_arch_pat.match(arch)
    if match:
        name, major, minor, actual_arch = match.groups()
        mac_version = (int(major), int(minor))
        arches = [
            # the prefix the caller gave is kept as it is
            '{}_{}'.format(name, tag[len('macosx_'):])
            for tag in mac_platforms(mac_version, actual_arch)
        ]
    else:
        # the pattern did not match, the platform is used as it is
        arches = [arch]
    return arches


def _custom_manylinux_platforms(arch):
    """
    Expand a manylinux2010/2014 platform into the older names it covers.

    Ported from pip/_internal/utils/compatibility_tags.py: a manylinux2014
    system runs the manylinux2010 and manylinux1 wheels, PEP 599 / PEP 571.

    Args:
        arch (str): Platform string, e.g. 'manylinux2014_x86_64'

    Returns:
        list[str]: The platform tags, most preferred first
    """
    arches = [arch]
    arch_prefix, arch_sep, arch_suffix = arch.partition('_')
    if arch_prefix == 'manylinux2014':
        # manylinux1/manylinux2010 wheels run on most manylinux2014 systems,
        # the exception being wheels that depend on ncurses
        if arch_suffix in {'i686', 'x86_64'}:
            arches.append('manylinux2010' + arch_sep + arch_suffix)
            arches.append('manylinux1' + arch_sep + arch_suffix)
    elif arch_prefix == 'manylinux2010':
        # manylinux1 wheels run on most manylinux2010 systems
        arches.append('manylinux1' + arch_sep + arch_suffix)
    return arches


def _get_custom_platforms(arch):
    """
    Expand one custom platform of a target into its family.

    Ported from pip/_internal/utils/compatibility_tags.py.

    Args:
        arch (str): Platform string

    Returns:
        list[str]: The platform tags of the family
    """
    arch_prefix, arch_sep, arch_suffix = arch.partition('_')
    if arch.startswith('macosx'):
        arches = _mac_platforms(arch)
    elif arch_prefix in ['manylinux2014', 'manylinux2010']:
        arches = _custom_manylinux_platforms(arch)
    else:
        arches = [arch]
    return arches


def _expand_allowed_platforms(platforms):
    """
    Expand the platform list of a target, duplicates removed.

    Ported from pip/_internal/utils/compatibility_tags.py.

    Args:
        platforms (list[str] | None): Platforms the target allows

    Returns:
        list[str] | None: The expanded platforms, None when the caller gave
            none
    """
    if not platforms:
        return None

    seen = set()
    result = []

    for platform in platforms:
        if platform in seen:
            continue
        additions = [p for p in _get_custom_platforms(platform) if p not in seen]
        seen.update(additions)
        result.extend(additions)

    return result


def _get_python_version(version):
    """
    Parse the short version of a target, e.g. '38' -> (3, 8).

    Ported from pip/_internal/utils/compatibility_tags.py.

    Args:
        version (str): Short version, e.g. '38'

    Returns:
        tuple[int, ...]: The version
    """
    if len(version) > 1:
        return int(version[0]), int(version[1:])
    return (int(version[0]),)


def _get_custom_interpreter(implementation=None, version=None):
    """
    The interpreter tag of a target, e.g. 'cp38'.

    Ported from pip/_internal/utils/compatibility_tags.py.

    Args:
        implementation (str | None): Implementation, e.g. 'cp'. Defaults to
            None, the running interpreter
        version (str | None): Short version, e.g. '38'. Defaults to None, the
            running interpreter

    Returns:
        str: The interpreter tag
    """
    if implementation is None:
        implementation = interpreter_name()
    if version is None:
        version = interpreter_version()
    return f'{implementation}{version}'


def get_supported(version=None, platforms=None, impl=None, abis=None):
    """
    The supported tags of a target interpreter.

    Ported from pip/_internal/utils/compatibility_tags.py.

    Args:
        version (str | None): Short version, e.g. '38'. Defaults to None, the
            running interpreter
        platforms (list[str] | None): Platforms of the target. Defaults to
            None, the platforms of the running system
        impl (str | None): Implementation of the target, e.g. 'cp'. Defaults
            to None, the running interpreter
        abis (list[str] | None): ABIs of the target. Defaults to None, the
            ABIs of the running interpreter

    Returns:
        list[Tag]: The supported tags, most preferred first
    """
    supported = []

    python_version = None
    if version is not None:
        python_version = _get_python_version(version)

    interpreter = _get_custom_interpreter(impl, version)

    platforms = _expand_allowed_platforms(platforms)

    is_cpython = (impl or interpreter_name()) == 'cp'
    if is_cpython:
        supported.extend(
            cpython_tags(
                python_version=python_version,
                abis=abis,
                platforms=platforms,
            )
        )
    else:
        supported.extend(
            generic_tags(
                interpreter=interpreter,
                abis=abis,
                platforms=platforms,
            )
        )
    supported.extend(
        compatible_tags(
            python_version=python_version,
            interpreter=interpreter,
            platforms=platforms,
        )
    )

    return supported
