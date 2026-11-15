"""
The dependency closure of a distribution: its METADATA, and the names its
``Requires-Dist`` lines declare.

The closure of a mod is walked over the distributions of its requirements file:
a registered name pulls the names its pinned version declares, and so on. The
metadata of a version is read from its index rather than from the environment
of the developer, so the closure does not drift with what happens to be
installed:

- the PEP 658 metadata file of a wheel when the index serves one
  (``data-dist-info-metadata``, `{wheel_url}.metadata`), a few KB for the
  headers of the whole wheel;
- the METADATA of the wheel itself otherwise, the fallback for an index that
  predates PEP 658.

One metadata per version is enough, which is the assumption uv makes too: every
wheel of a version carries the same ``Requires-Dist``, the platform differences
are written as PEP 508 markers (see the uv resolver internals, "Metadata
consistency"). The markers are evaluated for every platform a mod is installed
on, and for every extra the caller registered: a name is kept when one
combination asks for it, the union over the clients.

A dependency the requirements file does not pin is not part of the closure: the
pack channel installs the pinned version, so a name outside the lock is not
going to be installed by this mod at all.

Usage:
    from alasio.deploy_dev.pack_deps.closure import read_metadata, requires_dist

    content, origin = await read_metadata(fetcher, candidates)
    names = requires_dist(content)          # ['h11>=0.16', 'sniffio', ...]
"""

import io
import zipfile

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from alasio.deploy.pip_download._wheel import is_pure_wheel
from alasio.deploy.simple_pip.pip_list import iter_metadata_headers
from alasio.logger import logger

__all__ = [
    'PLATFORMS',
    'marker_environments',
    'read_metadata',
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
# three platforms (see the module note of
# alasio.deploy_dev.pack.pack_wheel).
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


async def read_metadata(fetcher, candidates):
    """
    Read the METADATA of one version of a distribution from its index.

    The PEP 658 metadata file is preferred: it is a separate file next to the
    wheel, so the headers of a distribution cost a few KB instead of the whole
    wheel. A pure wheel is tried first when the version ships several, it is
    the file a pack is built from.

    An index that serves no metadata file is served by the fallback: the wheel
    is downloaded into memory (checked against the hash of its link) and read
    there. A backport index such as py38deps is one of those: its page carries
    no PEP 658 attribute, and a wheel of it is a few hundred KB.

    Args:
        fetcher (PypiFile): The fetcher of the index of the distribution
        candidates (Iterable[WheelCandidate]): The wheels of the version, see
            alasio.deploy_dev.pack_deps.resolve.wheels_of

    Returns:
        tuple[str, str]: (content of METADATA, what it was read from: the url
            of the metadata file or the url of the wheel)

    Raises:
        ValueError: If no wheel of the version can be read
        httpx2.HTTPError: If a request fails
    """
    ordered = sorted(
        candidates,
        key=lambda candidate: (not is_pure_wheel(candidate.wheel.file_tags), candidate.filename))

    for candidate in ordered:
        url = candidate.link.metadata_url
        if not url:
            continue
        expected = candidate.link.metadata_hashes.get('sha256', '')
        content = await fetcher.get_metadata(url, expected)
        try:
            return content.decode('utf-8'), url
        except UnicodeDecodeError as e:
            logger.warning(f'The metadata file is not UTF-8, reading the wheel instead: "{url}", {e}')

    for candidate in ordered:
        content = await fetcher.get_bytes(candidate.link.url)
        try:
            return wheel_metadata(content), candidate.link.url_without_fragment
        except (ValueError, zipfile.BadZipFile) as e:
            # a wheel of a version that holds no METADATA, or a file the index
            # replaced under the same name: the next wheel of the version
            logger.warning(f'Failed to read the metadata of "{candidate.filename}": {e}')

    raise ValueError('No wheel of the version to read the metadata from')


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
