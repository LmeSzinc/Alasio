"""
The dependency names a METADATA file asks for, over the platforms of a mod.

The headers of a distribution are read from its index rather than from the
environment of the developer, so the closure of a mod does not drift with what
happens to be installed. The names it declares are the ``Requires-Dist`` lines,
which is the whole of the dependency information a wheel carries: the markers
of a line are evaluated for every platform a mod is installed on and for every
extra the caller registered, and a name is kept when one combination asks for
it, the union over the clients.

One metadata per version is enough, which is the assumption uv makes too: every
wheel of a version carries the same ``Requires-Dist``, the platform differences
are written as PEP 508 markers (see the uv resolver internals, "Metadata
consistency").

Usage:
    from alasio.deploy_dev.metadata.requirements import requires_dist, requirement_names

    names = requires_dist(content)          # ['h11>=0.16', 'sniffio', ...]
    wanted = requirement_names(names, [''], target)   # {'h11': frozenset(), ...}
"""

import io
import zipfile

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from alasio.deploy.simple_pip.pip_list import iter_metadata_headers
from alasio.logger import logger

__all__ = [
    'PLATFORMS',
    'marker_environments',
    'requirement_names',
    'requires_dist',
    'wheel_metadata',
]

# The platforms a mod is installed on: (sys_platform, platform_system,
# platform_machine) of one client. The closure takes the union of them, so a
# dependency of any of the three is in the closure (see requirement_names).
#
# The machine is the one the sys_platform ships most of: the architectures a
# marker test names are rare, and a marker that only matches another
# architecture (e.g. platform_machine == "armv7l") is not a dependency of a
# desktop or a server. The platform families of the pack format follow the same
# three platforms (see the module note of alasio.deploy_dev.pack.pack_wheel).
PLATFORMS = (
    ('win32', 'Windows', 'AMD64'),
    ('linux', 'Linux', 'x86_64'),
    ('darwin', 'Darwin', 'arm64'),
)


def requires_dist(content):
    """
    The ``Requires-Dist`` values of a METADATA file.

    The block is read by the parser of the client installer
    (alasio.deploy.simple_pip.pip_list.iter_metadata_headers), which keeps
    every line: ``Requires-Dist`` repeats once per dependency, so a reader that
    collects the headers in a dict would see the last dependency only.

    Args:
        content (str): Content of the METADATA file

    Returns:
        list[str]: The requirement texts, e.g. ['h11>=0.16', 'sniffio']
    """
    return [
        value for name, value in iter_metadata_headers(content)
        if name == 'requires-dist'
    ]


def wheel_metadata(content):
    """
    Read the METADATA of a wheel held in memory.

    The fallback of the range reader: a wheel the reader cannot locate a member
    in is read whole, and the archive library answers here rather than the
    central directory parser of alasio.deploy_dev.metadata.zip_reader.

    Args:
        content (bytes): Content of the .whl file

    Returns:
        str: Content of ``{dist-info}/METADATA``

    Raises:
        ValueError: If the wheel holds no METADATA, or it is not UTF-8
        zipfile.BadZipFile: If the bytes are not a zip archive
    """
    with zipfile.ZipFile(io.BytesIO(content), 'r') as zf:
        for name in zf.namelist():
            folder, sep, path = name.partition('/')
            if sep and path == 'METADATA' and folder.endswith('.dist-info'):
                data = zf.read(name)
                break
        else:
            raise ValueError('Invalid wheel, missing .dist-info/METADATA')
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError as e:
        raise ValueError(f'Invalid wheel, the METADATA is not UTF-8: {e}') from e


def marker_environments(target):
    """
    The PEP 508 environments a mod is installed in.

    Args:
        target (TargetPython): The interpreter the closure is resolved for

    Yields:
        dict[str, str]: The environment of one client platform
    """
    major, minor, micro = target.py_version_info[:3]
    version = f'{major}.{minor}'
    full = f'{major}.{minor}.{micro}'
    for sys_platform, system, machine in PLATFORMS:
        yield {
            'implementation_name': 'cpython',
            'implementation_version': full,
            'os_name': 'nt' if sys_platform == 'win32' else 'posix',
            'platform_machine': machine,
            'platform_python_implementation': 'CPython',
            # the three fields a marker may read but no environment of a wheel
            # records: they are empty, the same value pip evaluates a marker of
            # a lock with
            'platform_release': '',
            'platform_version': '',
            'platform_system': system,
            'python_full_version': full,
            'python_version': version,
            'sys_platform': sys_platform,
        }


def requirement_names(requires, extras, target):
    """
    The distribution names a ``Requires-Dist`` list asks for.

    A requirement is kept when one combination of (platform, extra) matches its
    marker: the union over the clients, see the module docstring. The extras of
    the requirement itself are collected too, so the closure of a name reached
    with ``httpx[http2]`` includes the dependencies of that extra.

    Args:
        requires (Iterable[str]): The ``Requires-Dist`` values
        extras (Iterable[str]): Extras of the registered root, the base
            dependencies are the empty extra
        target (TargetPython): The interpreter the closure is resolved for

    Returns:
        dict[str, frozenset[str]]: PEP 503 name -> extras the requirement asked
            for, in the order the metadata lists the names
    """
    environments = list(marker_environments(target))
    extras = list(dict.fromkeys(extras))
    names = {}
    for text in requires:
        try:
            requirement = Requirement(text)
        except InvalidRequirement as e:
            logger.warning(f'Skipped a requirement of a distribution: "{text}", {e}')
            continue
        if requirement.marker is not None and not any(
            requirement.marker.evaluate({**environment, 'extra': extra})
            for environment in environments
            for extra in extras
        ):
            continue
        wanted = names.setdefault(canonicalize_name(requirement.name), set())
        wanted.update(requirement.extras)
    return {name: frozenset(value) for name, value in names.items()}
