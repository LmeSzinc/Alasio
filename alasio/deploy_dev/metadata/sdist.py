"""
The dependency metadata of a source distribution, read without building it.

A version that publishes no wheel exists only as an sdist, and a resolver that
refuses every sdist cannot lock a project whose dependency set contains one
(``apkutils2``, a dependency of ``adbutils``, is such a version). The
dependencies of an sdist are written in the archive, though, and a compiler
that reads them statically can pin the version without building it:

- ``PKG-INFO`` of core metadata 1.2 and later carries ``Requires-Dist``, the
  same header a wheel carries, and it is read by the same parser;
- a setuptools sdist carries ``<name>.egg-info/requires.txt``, the record of
  ``install_requires`` and of the extras the project declares. The sections of
  the file are translated back to the markers of ``Requires-Dist``: an entry of
  the ``[extra]`` section is required by that extra.

Neither file is a build: a sdist that holds neither (a ``setup.py`` that
computes its requirements at build time, core metadata 1.1) is refused, so a
lock never silently misses the dependencies of a distribution. Building such a
project is what pip does at install time, and the pack channel never serves it
(the pack of a version is built from its pure wheel, see
alasio.deploy_dev.pack_deps.resolve).

Usage:
    from alasio.deploy_dev.metadata.sdist import is_sdist, read_sdist_metadata

    if is_sdist(filename):
        requires = read_sdist_metadata(content, filename)
"""

import io
import re
import tarfile
import zipfile

__all__ = [
    'MAX_METADATA_SIZE',
    'SDIST_SUFFIXES',
    'is_sdist',
    'read_sdist_metadata',
    'sdist_name_of',
]

# The suffixes of a source distribution, the ones a PyPI index publishes
SDIST_SUFFIXES = ('.tar.gz', '.tar.bz2', '.tar.xz', '.tgz', '.zip')

# The largest metadata file the reader accepts: a PKG-INFO or a requires.txt of
# a distribution is a few KB, and an archive of a hostile publisher must not be
# able to make the reader allocate its whole content
MAX_METADATA_SIZE = 1024 * 1024

# The filename of a source distribution: <name>-<version><suffix>
_SDIST_NAME = re.compile(
    r'^(?P<name>.+?)-(?P<version>[^-]+?)(?:' + '|'.join(
        re.escape(suffix) for suffix in SDIST_SUFFIXES) + r')$')

# The section header of an egg-info requires.txt, e.g. '[extra]' or
# '[:python_version < "3.9"]'
_REQUIRES_SECTION = re.compile(r'^\[(?P<section>.+)\]\s*$')


def is_sdist(filename):
    """
    Whether a filename is a source distribution.

    Args:
        filename (str): Filename of an index link, e.g. 'demo-1.0.tar.gz'

    Returns:
        bool: True when the name is one of SDIST_SUFFIXES
    """
    return filename.lower().endswith(SDIST_SUFFIXES)


def sdist_name_of(filename):
    """
    The distribution name and the version of an sdist filename.

    Args:
        filename (str): Filename of an index link, e.g. 'demo-1.0.tar.gz'

    Returns:
        tuple[str, str] | None: (name as written, version), None when the
            filename is not an sdist of a known suffix
    """
    match = _SDIST_NAME.match(filename)
    if not match:
        return None
    return match.group('name'), match.group('version')


def read_sdist_metadata(content, filename):
    """
    The dependencies a source distribution declares, read without building it.

    Args:
        content (bytes): Content of the sdist
        filename (str): Filename of the sdist, used in the error messages and
            to pick the archive format

    Returns:
        list[str]: The ``Requires-Dist`` values, e.g. ['pyelftools', 'cigam']
            or ['pytest; extra == "test"']

    Raises:
        ValueError: If the archive cannot be read, or it declares no static
            dependency metadata (no Requires-Dist, no egg-info requires.txt)
    """
    try:
        members = _read_members(content, filename)
    except (tarfile.TarError, zipfile.BadZipFile, OSError) as e:
        raise ValueError(f'The sdist "{filename}" cannot be read: {e}') from e
    pkg_info = _find(members, 'PKG-INFO')
    if pkg_info is not None:
        requires = _requires_of_pkg_info(pkg_info)
        if requires:
            return requires
    requires_txt = _find(members, 'requires.txt')
    if requires_txt is not None:
        return _requires_of_requires_txt(requires_txt, filename)
    raise ValueError(
        f'The sdist "{filename}" declares no static dependency metadata: it has no '
        f'Requires-Dist in its PKG-INFO and no egg-info requires.txt, so its '
        f'dependencies can only be read by building it')


def _read_members(content, filename):
    """
    The text files of an archive, keyed by their path in it.

    Args:
        content (bytes): Content of the sdist
        filename (str): Filename of the sdist

    Returns:
        dict[str, str]: {path: content} of the files the reader may need

    Raises:
        tarfile.TarError: If the content is not a tar archive
        zipfile.BadZipFile: If the content is not a zip archive
    """
    wanted = ('PKG-INFO', 'requires.txt', 'setup.cfg')
    members = {}
    if filename.lower().endswith('.zip'):
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            for name in zf.namelist():
                if not name.endswith(wanted):
                    continue
                info = zf.getinfo(name)
                if info.file_size > MAX_METADATA_SIZE:
                    continue
                members[name] = zf.read(name).decode('utf-8', 'replace')
        return members
    with tarfile.open(fileobj=io.BytesIO(content), mode='r:*') as tf:
        for member in tf.getmembers():
            if not member.isfile() or not member.name.endswith(wanted):
                continue
            if member.size > MAX_METADATA_SIZE:
                continue
            file = tf.extractfile(member)
            if file is None:
                continue
            members[member.name] = file.read().decode('utf-8', 'replace')
    return members


def _find(members, suffix):
    """
    The content of the shortest file whose path ends with a suffix.

    The PKG-INFO of an archive is at its root, and the same file may also be
    copied into the egg-info folder: the shortest path is the root one.

    Args:
        members (dict[str, str]): {path: content}
        suffix (str): Suffix of the path, e.g. 'requires.txt'

    Returns:
        str | None: The content, None when no file matches
    """
    matches = [
        (len(path), content)
        for path, content in members.items()
        if path == suffix or path.endswith('/' + suffix)
    ]
    if not matches:
        return None
    return min(matches, key=lambda item: item[0])[1]


def _requires_of_pkg_info(content):
    """
    The Requires-Dist values of a PKG-INFO.

    A PKG-INFO of core metadata 1.1 has no Requires-Dist at all: the reader
    returns nothing and the caller falls back to the egg-info record.

    Args:
        content (str): Content of the PKG-INFO

    Returns:
        list[str]: The Requires-Dist values, empty when there is none
    """
    from alasio.deploy_dev.metadata.requirements import requires_dist

    return requires_dist(content)


def _requires_of_requires_txt(content, filename):
    """
    The Requires-Dist values of an egg-info requires.txt.

    The file lists one requirement per line, grouped in sections: the lines
    before the first section are the base dependencies, and the lines of the
    ``[extra]`` section are the dependencies of that extra, which the marker of
    the entry writes back.

    Args:
        content (str): Content of the requires.txt
        filename (str): Filename of the sdist, for the error message

    Returns:
        list[str]: The requirements, e.g. ['cigam', 'pytest; extra == "test"']

    Raises:
        ValueError: If a line is not a requirement, or it is an include of
            another file (which the reader does not follow)
    """
    from packaging.requirements import InvalidRequirement, Requirement

    requires = []
    sections = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = _REQUIRES_SECTION.match(line)
        if match:
            section = match.group('section').strip()
            sections = _section_extras(section, filename)
            continue
        if line.startswith(('-r ', '--requirement')):
            raise ValueError(
                f'The requires.txt of "{filename}" includes another file ("{line}"), '
                f'which the reader does not follow')
        try:
            requirement = Requirement(line)
        except InvalidRequirement as e:
            raise ValueError(
                f'The requires.txt of "{filename}" holds a line that is not a '
                f'requirement: "{line}", {e}') from None
        if not sections:
            requires.append(str(requirement))
            continue
        for extra in sections:
            requires.append(f'{requirement}; extra == "{extra}"')
    return requires


def _section_extras(section, filename):
    """
    The extras a requires.txt section stands for.

    A section header is an extra name (``[test]``), and setuptools also writes
    the marker of a conditional dependency group (``[:python_version < "3.9"]``).
    A conditional section is not an extra: its entries are required by the base
    distribution, and the condition is read as the marker of the requirement.

    Args:
        section (str): The header text without its brackets
        filename (str): Filename of the sdist, for the error message

    Returns:
        list[str]: The extra names, the empty list for a section that is not
            an extra

    Raises:
        ValueError: If the header is a conditional section, which would need
            the marker merged into every entry
    """
    text = section.strip()
    if not text:
        return []
    if text.startswith(':'):
        raise ValueError(
            f'The requires.txt of "{filename}" has the conditional section '
            f'"[{text}]", which the reader does not evaluate')
    return [text]
