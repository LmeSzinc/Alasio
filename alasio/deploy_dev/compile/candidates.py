"""
The versions of a distribution a compile may pick, read from its index pages.

An index page lists every file of a distribution, not its versions: the
versions are the grouping of the files by the version in their filename. A
version is a candidate of a compile when it publishes a wheel the target
interpreter can install, which is the whole of the filter (see
alasio.deploy_dev.compile.resolver for the constraints and the markers):

- a wheel of the distribution, of the version, its filename parsed by
  ``Wheel``: the enumerator never downloads a file, and the ``Requires-Python``
  and yanked attributes come from the link of the page;
- a wheel the target can install, by tag: a version that only publishes
  wheels of another interpreter or platform is not a candidate, because the
  lock has to install;
- a version whose files are all yanked is not a candidate (PEP 592 lets a
  client install one only when the version is pinned exactly, a resolution
  has no reason to pick one);
- the sources are tried in order, and a version is served by the first source
  that publishes an installable wheel of it. The preference is what makes the
  ``# from`` annotation of a lock stable: the official index serves a file
  when it can, a backport index only supplies the interpreters the official
  one dropped.

Usage:
    from alasio.deploy_dev.compile.candidates import versions_of

    candidates = versions_of('httpx2', [Index(...), Index(...)], target)
    candidates[0].version, candidates[0].source, candidates[0].wheels
"""

from typing import Tuple

from msgspec import Struct
from packaging.utils import canonicalize_name

from alasio.deploy.pip_download._specifiers import InvalidSpecifier
from alasio.deploy.pip_download._utils import check_requires_python
from alasio.deploy.pip_download._version import InvalidVersion, Version
from alasio.deploy.pip_download._wheel import InvalidWheelFilename, Wheel, is_pure_wheel
from alasio.deploy_dev.metadata.sdist import is_sdist, sdist_name_of
from alasio.logger import logger

__all__ = ['Candidate', 'Index', 'is_prerelease', 'versions_of']


class Index(Struct, frozen=True):
    """
    One index a compile reads, its identity and the text a lock writes.

    Attributes:
        url (str): Base url, no trailing slash, the identity of the index
        text (str): The url as the dependency file declares it, which is the
            form the lock file writes (``--extra-index-url``, ``# from``)
        primary (bool): True for the index a lock takes its files from first
    """
    url: str
    text: str
    primary: bool = False

    @classmethod
    def of(cls, url, primary=False):
        """
        Build an index from the url a dependency file declares.

        Args:
            url (str): Base url of the index, e.g.
                'https://py38deps.github.io/py38deps/simple/'
            primary (bool): True for the primary index. Defaults to False

        Returns:
            Index: The index, its url without the trailing slash
        """
        return cls(url=url.rstrip('/'), text=url, primary=primary)


class Candidate(Struct, frozen=True):
    """
    One version of a distribution a compile may pick.

    Attributes:
        version (str): Version text, as the filenames spell it
        source (str): Base url of the index that serves the version
        wheels (Tuple[str, ...]): Filenames of the wheels the target can
            install, the preferred one first. Empty when the version only
            publishes a source distribution
        pure (bool): True when one of them is a pure python wheel, which is
            what the pack channel builds a pack from
        sdist (bool): True when the version publishes no wheel the target can
            install: pip builds its source distribution, and the metadata is
            read from the archive
    """
    version: str
    source: str
    wheels: Tuple[str, ...]
    pure: bool = False
    sdist: bool = False

    @property
    def key(self):
        """
        Returns:
            Version: The version, for the ordering of a resolution
        """
        return Version(self.version)

    @property
    def prerelease(self):
        """
        Returns:
            bool: True when the version is a prerelease or a development release
        """
        return is_prerelease(self.key)


def is_prerelease(version):
    """
    Whether a version is one a resolution only picks when it is asked for.

    A prerelease (``1.0rc1``) and a development release (``1.0.dev1``) are both
    excluded from a resolution that does not name one, which is the rule of
    PEP 440 and of pip.

    Args:
        version (Version): The version to check

    Returns:
        bool: True for a prerelease or a development release
    """
    return version.is_prerelease


def _requires_python_ok(link, target):
    """
    Whether the target interpreter satisfies the Requires-Python of a link.

    A link without the attribute is fine, and a value that does not parse is
    taken as fine too: a broken specifier of an index must not hide a file from
    every client, the answer pip gives (see pack_deps.resolve).

    Args:
        link (Link): Link of the index page
        target (TargetPython): The interpreter the lock is compiled for

    Returns:
        bool: True when the file allows the interpreter
    """
    if not link.requires_python:
        return True
    try:
        return check_requires_python(link.requires_python, target.py_version_info)
    except InvalidSpecifier as e:
        logger.debug(
            f'Ignoring invalid Requires-Python of "{link.show_url}": '
            f'{link.requires_python!r}, {e}')
        return True


def versions_of(name, pages, target):
    """
    The versions of a distribution a compile may pick, highest first.

    Args:
        name (str): Distribution name, any case and separator style
        pages (Iterable[Tuple[Index, PackageIndex]]): The pages of the
            indexes, in the order they are preferred
        target (TargetPython): The interpreter the lock is compiled for

    Returns:
        list[Candidate]: The candidates, highest version first. A version is
            in the list once, served by the first page that publishes an
            installable wheel of it
    """
    dist = canonicalize_name(name)
    tags = target.get_unsorted_tags()
    # {(version, source): [(wheel, yanked)]}, the files the target can install
    files = {}
    # {(version, source): filename} of the source distributions
    sdists = {}
    # the Requires-Python of a page is one value repeated by every file of it:
    # the check is parsed once per value, not once per file
    compatible = {}

    def allows(link):
        value = link.requires_python
        if not value:
            return True
        known = compatible.get(value)
        if known is None:
            known = compatible[value] = _requires_python_ok(link, target)
        return known

    for index, page in pages:
        for link in page.links:
            # the filename is a property of the url, parsed once per link here
            # while the loop reads it three times
            filename = link.filename
            if is_sdist(filename):
                parsed = sdist_name_of(filename)
                if not parsed or canonicalize_name(parsed[0]) != dist:
                    continue
                # an sdist installs on the interpreters its Requires-Python
                # allows, exactly like a wheel: pip builds it only there
                if not allows(link):
                    continue
                sdists[(parsed[1], index.url)] = filename
                continue
            if not link.is_wheel:
                continue
            try:
                wheel = Wheel(filename)
            except InvalidWheelFilename:
                continue
            if wheel.name != dist:
                continue
            if not allows(link):
                continue
            if not wheel.supported(tags):
                continue
            files.setdefault((wheel.version, index.url), []).append(
                (wheel, bool(link.is_yanked)))

    # a version is served by the first index that publishes a non yanked wheel
    # of it the target can install: the preference is the order of the pages
    rank = {index.url: position for position, (index, _) in enumerate(pages)}
    chosen = {}
    for (version, source), entries in files.items():
        if all(is_yanked for _, is_yanked in entries):
            continue
        current = chosen.get(version)
        if current is not None and rank[source] >= rank[current[0]]:
            continue
        wheels = sorted(
            (wheel for wheel, _ in entries),
            key=lambda wheel: (not is_pure_wheel(wheel.file_tags), wheel.filename))
        chosen[version] = (source, wheels)

    # a version that publishes no wheel the target can install is still
    # installable from its source distribution: pip builds it, and the reader
    # takes the dependencies from the archive (see metadata.sdist)
    for (version, source), _ in sdists.items():
        current = chosen.get(version)
        if current is not None and rank[source] >= rank[current[0]]:
            continue
        if current is not None and current[1]:
            # an index that publishes a wheel of the version is preferred over
            # one that only publishes the sdist
            continue
        chosen[version] = (source, [])

    out = []
    for version, (source, wheels) in chosen.items():
        try:
            key = Version(version)
        except InvalidVersion:
            logger.debug(f'Ignoring an invalid version of "{name}": {version!r}')
            continue
        out.append(Candidate(
            version=str(key),
            source=source,
            wheels=tuple(wheel.filename for wheel in wheels),
            pure=any(is_pure_wheel(wheel.file_tags) for wheel in wheels),
            sdist=not wheels,
        ))
    out.sort(key=lambda candidate: candidate.key, reverse=True)
    return out


def sdist_versions_of(name, pages):
    """
    The versions a distribution publishes as a source distribution only.

    A version of this set is a candidate, its dependencies are read from the
    archive (see metadata.sdist), and the pack channel never serves it. The
    set is reported when such a version cannot be read at all.

    Args:
        name (str): Distribution name
        pages (Iterable[Tuple[Index, PackageIndex]]): The pages of the compile

    Returns:
        set[str]: The version texts that only have an sdist
    """
    dist = canonicalize_name(name)
    sdist = set()
    wheel = set()
    for _, page in pages:
        for link in page.links:
            filename = link.filename
            if link.is_wheel:
                try:
                    parsed = Wheel(filename)
                except InvalidWheelFilename:
                    continue
                if parsed.name == dist:
                    wheel.add(parsed.version)
                continue
            parsed = sdist_name_of(filename)
            if parsed is None:
                continue
            if canonicalize_name(parsed[0]) != dist:
                continue
            sdist.add(parsed[1])
    return sdist - wheel
