"""
The data of a pip index page: one wheel, its name and its link.

A page lists wheels, and a wheel is three facts about a file: the distribution
it belongs to, the version of it, and the file name. The link of the page is
built from those facts, never from a path the caller assembled, so the page and
the folder the static host serves cannot drift apart:

    WheelLink('httpx2', '2.13.1', 'httpx2-2.13.1-py3-none-any.whl', sha256).href()
    # '2.13.1/httpx2-2.13.1-py3-none-any.whl#sha256=...'

    WheelLink(...).href(with_name=True)
    # 'httpx2/2.13.1/httpx2-2.13.1-py3-none-any.whl#sha256=...'

The two forms are the two pages pip reads: the PEP 503 page of a project sits in
the folder of the project, so its links start at the version; the find-links
page of the whole folder sits above the projects, so its links start at the name.

The values come from an index page of another host, so they are checked when the
link is made: a name that is not a distribution name, a version or a file name
that is not one path segment, and a digest that is not a lowercase sha256 are
refused instead of escaping the folder of the page.

Usage:
    from alasio.codegen.pypisite import WheelLink

    link = WheelLink('httpx2', '2.13.1', 'httpx2-2.13.1-py3-none-any.whl', sha256)
    link.href()   # '2.13.1/httpx2-2.13.1-py3-none-any.whl#sha256=...'
"""

import re

from msgspec import Struct

from alasio.deploy.pip_download._version import InvalidVersion, Version

__all__ = [
    'WheelLink',
    'is_name',
    'normalize_name',
    'prepare_links',
    'split_by_name',
    'split_by_version',
    'unique_links',
    'version_key',
]

# PEP 503: runs of '-', '_' and '.' are one '-'
NAME_SEPARATORS = re.compile(r'[-_.]+')

# A normalized distribution name: lowercase parts of letters and digits,
# separated by single '-'
NORMALIZED_NAME = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')

# The characters that would end the path segment a version or a file name is:
# a link containing one of them does not point at a file of the folder
URL_SEPARATORS = '/\\#?'

# The digest pip verifies, the lowercase hex sha256 an index link carries
SHA256 = re.compile(r'[0-9a-f]{64}')


def normalize_name(name):
    """
    Normalize a distribution name with PEP 503.

    Args:
        name (str): Distribution name, any case and separator style

    Returns:
        str: Lowercase name with every run of '-', '_' and '.' as one '-'
    """
    return NAME_SEPARATORS.sub('-', str(name)).lower()


def is_name(name):
    """
    Whether a normalized name is a distribution name.

    Args:
        name (str): Normalized name, see normalize_name

    Returns:
        bool: True when the name is lowercase parts of letters and digits
            separated by single '-'
    """
    return bool(NORMALIZED_NAME.fullmatch(name))


def version_key(version):
    """
    Sort key of a version: the PEP 440 order, the one pip picks a candidate by.

    The implementation is the one the deploy domain vendored from packaging
    (alasio.deploy.pip_download._version), so the pages order versions with the
    same rules pip does instead of a rule of their own.

    The obvious key (split the digits and compare them as numbers) gets the
    common cases right and the interesting ones wrong: it puts '2.0.0rc1' after
    '2.0.0' and knows nothing about epochs, post releases or local versions.
    The pages are read next to the versions pip resolves, so they use the same
    rules.

    A version that is not PEP 440 is not part of any pip candidate set. It sorts
    after every version that is, by its text, so a page always renders and two
    runs of the same links still agree.

    Args:
        version (str): Exact version of a distribution

    Returns:
        tuple: Comparable key, PEP 440 order first and the unparsable text
            after it
    """
    try:
        parsed = Version(version)
    except InvalidVersion:
        return 1, str(version)
    return 0, parsed._cmpkey


class WheelLink(Struct, frozen=True):
    """
    One wheel of a pip index page.

    Attributes:
        name (str): Distribution name, any case and separator style, it is
            normalized with PEP 503 for the link
        version (str): Exact version of the distribution
        filename (str): File name of the wheel, e.g.
            'httpx2-2.13.1-py3-none-any.whl'
        sha256 (str): Lowercase hex digest of the file, '' to link it without a
            digest

    Raises:
        ValueError: If the name is not a distribution name, if the version or
            the file name is not a single url path segment, or if the digest is
            not a lowercase sha256
    """

    name: str
    version: str
    filename: str
    sha256: str = ''

    def __post_init__(self):
        if not is_name(normalize_name(self.name)):
            raise ValueError(
                f'The name of a wheel link is not a distribution name: {self.name!r}')
        for field, value in (('version', self.version), ('filename', self.filename)):
            if not value:
                raise ValueError(f'The {field} of a wheel link "{self.name}" is empty')
            for char in URL_SEPARATORS:
                if char in value:
                    raise ValueError(
                        f'The {field} of a wheel link is not a single url path segment: {value!r}')
        if self.sha256 and not SHA256.fullmatch(self.sha256):
            raise ValueError(
                f'The sha256 of the wheel "{self.filename}" is not a lowercase hex digest: '
                f'{self.sha256!r}')

    @property
    def key(self):
        """
        The name of the link as PEP 503 normalizes it.

        Returns:
            str: Normalized distribution name
        """
        return normalize_name(self.name)

    def href(self, with_name=False):
        """
        The link of the wheel on a page.

        Args:
            with_name (bool): True for a page above the folders of the
                distributions (the find-links page of the whole folder), the
                path then starts at the name. False for the page inside the
                folder of the distribution, the path starts at the version

        Returns:
            str: Relative url, with '#sha256=' appended when the link has one
        """
        parts = [self.version, self.filename]
        if with_name:
            parts.insert(0, self.key)
        url = '/'.join(parts)
        if self.sha256:
            url = f'{url}#sha256={self.sha256}'
        return url

    def __repr__(self):
        return f'<WheelLink {self.key}=={self.version} {self.filename}>'


def _group(links, field):
    """
    Group the wheels by a field of the link, in the order of the input.

    The groups keep the links as they were given, duplicates included: this is a
    grouping of the data, not a page, and the step that deduplicates and sorts
    a page is prepare_links.

    Args:
        links (Iterable[WheelLink]): The wheels to group
        field (str): Name of the attribute the key is taken from, 'key' or
            'version'

    Returns:
        dict[str, list[WheelLink]]: The wheels of each key, the keys in the
            order they first appear

    Raises:
        TypeError: If a link is not a WheelLink
    """
    groups = {}
    for link in links:
        if not isinstance(link, WheelLink):
            raise TypeError(
                f'A wheel of a pip page is not a WheelLink: {type(link).__name__}')
        groups.setdefault(getattr(link, field), []).append(link)
    return groups


def split_by_name(links):
    """
    Group the wheels by the distribution they belong to.

    The key is the name as PEP 503 normalizes it, which is the folder the page
    of the distribution sits in, so two spellings of one name are one group.

    Args:
        links (Iterable[WheelLink]): The wheels to group, of any distributions

    Returns:
        dict[str, list[WheelLink]]: The wheels of each normalized name, the
            names in the order they first appear

    Raises:
        TypeError: If a link is not a WheelLink
    """
    return _group(links, 'key')


def split_by_version(links):
    """
    Group the wheels by the version they carry.

    The key is the exact version as the page spells it, which is the folder the
    file sits in: the wheels of one version (a release built for several
    platforms) are one group, while two versions that only PEP 440 reads as
    equal ('1.0' and '1.0.0') stay apart because they are two folders.

    A flat page groups the versions of one distribution, so the input is the
    wheels of one project. Links of several projects still group by the text of
    the version, they are just not a page that is written.

    Args:
        links (Iterable[WheelLink]): The wheels of one distribution

    Returns:
        dict[str, list[WheelLink]]: The wheels of each version, the versions in
            the order they first appear

    Raises:
        TypeError: If a link is not a WheelLink
    """
    return _group(links, 'version')


def unique_links(links):
    """
    Check and deduplicate the wheels, keeping the order of the input.

    A page is written from the places a run read, and the same wheel can be
    read twice: the answer holds it once. The order is the one the caller gave:
    sorting is a step of a page (prepare_links), not of the data.

    Args:
        links (Iterable[WheelLink]): The wheels

    Returns:
        list[WheelLink]: The wheels, unique, in the order of the input

    Raises:
        TypeError: If a link is not a WheelLink
        ValueError: If two links carry one file name with different digests
    """
    unique = {}
    for link in links:
        if not isinstance(link, WheelLink):
            raise TypeError(
                f'A wheel of a pip page is not a WheelLink: {type(link).__name__}')
        key = (link.key, link.version, link.filename)
        current = unique.get(key)
        if current is None:
            unique[key] = link
        elif current.sha256 != link.sha256:
            raise ValueError(
                f'The wheel "{link.filename}" of "{link.name}=={link.version}" is linked with '
                f'two digests: {current.sha256!r} and {link.sha256!r}')
    return list(unique.values())


def prepare_links(links, with_name=False, project=''):
    """
    Check, deduplicate and sort the wheels of a page.

    A page is written from the places a run read, and the same wheel can be read
    twice; the answer holds it once. The order is fixed here and not by the
    caller, so two runs of the same links render the same bytes, and it is the
    order pip resolves a version in (PEP 440, see version_key):

    - the page of a distribution (with_name=False) sorts by version, then by
      file name;
    - the find-links page of the whole folder (with_name=True) sorts by name,
      then version, then file name.

    Args:
        links (Iterable[WheelLink]): The wheels of the page
        with_name (bool): True for the find-links page of a whole folder, False
            for the page of one distribution. Defaults to False
        project (str): Normalized name the page belongs to, '' to check only
            that the links agree with each other. Defaults to ''

    Returns:
        list[WheelLink]: The wheels of the page, unique and sorted

    Raises:
        TypeError: If a link is not a WheelLink
        ValueError: If two links carry one file name with different digests, or
            a page of one distribution gets the wheel of another
    """
    out = unique_links(links)

    if not with_name:
        # the page of a distribution sits in the folder of it: a wheel of another
        # name would be a link out of the folder, and pip would read it here
        page = project or (out[0].key if out else '')
        for link in out:
            if link.key != page:
                raise ValueError(
                    f'The page of "{page}" cannot link the wheel of another distribution: '
                    f'"{link.filename}" of "{link.name}"')
        out.sort(key=lambda link: (version_key(link.version), link.filename))
    else:
        out.sort(key=lambda link: (link.key, version_key(link.version), link.filename))
    return out
