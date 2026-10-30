"""
Download the wheels of a distribution version from a PyPI simple index.

The fetcher is the client side counterpart of
`alasio/deploy_dev/pack_server/fetch_wheel.py`: it takes `(name, version)` and
downloads the wheels that match `mode` into the folder the caller gave, with
the index parsed by the ported pip helpers (`_link`, `_wheel`, `_utils`):

- `DOWNLOAD_MODE_CURRENT` (`current`): every wheel compatible with the target
  interpreter (the running one by default), ordered by the tag preference of
  PEP 425;
- `DOWNLOAD_MODE_PY3_ANY` (`py3-any`): every pure python wheel of python 3
  (`none-any` ABI and platform), the semantics of the release side wheel fetch;
- `DOWNLOAD_MODE_ALL` (`all`): every wheel of the version, whatever its tags.

The index page is probe-driven, like the pack server: `get_package_index()`
and `download()` serve the page path through `AsyncProbe.fetch()` and pass the
work of the path as a function of their own, so the mirror is resolved by the
record of the machine or by a probe of the page, and a failure the domain
declares (`WheelNotFoundError`) makes the engine probe the path again without
the failed mirror and retry it once:

- `get_package_index(name)` requests `{mirror}/{dist-key}/` and returns the
  page: the links parsed from it, the body as the mirror served it and the url
  it was served at. The probe of the path is the default HEAD of the probe
  layer: the page of a big distribution is large (the page of numpy is a few
  MB), a probe must not transfer it. The page is fetched once by the work of
  the flow, on the selected mirror. The body answers the caller that keeps a
  snapshot of the page instead of only its links (the pack server caches the
  page of a version, see fetch_wheel); the optional `select` step answers the
  flow that asks a question about the page, and it runs inside the probe, so a
  page without the answer is a failure of the mirror like a page that does not
  parse (see download());
- `get_wheel(url)` downloads one wheel of a page by the absolute url of its
  link, and returns the wheel with the digests of its bytes. A page may point
  at a files host of its own (files.pythonhosted.org), so the url is used as it
  is and the transfer goes through the http client alone: the file is not
  served by the mirror, and there is no path of the mirror to probe. The mirror
  is the one `get_package_index()` selected.

`download(name, version, mode)` is the two steps: the page of the distribution
is fetched and filtered by `select_wheels()`, then every selected wheel is
downloaded by `get_wheel()`. The selection runs inside the work of the page, so
a page that has no wheel of the version counts as a failure of that mirror:
the probe layer reselects a mirror without it and retries the page once (the
same rule the pack client uses for a pack that does not validate). A candidate
whose file is already in the folder and matches the hash of its link is
skipped, a downloaded file is checked against the hash of its link before it is
put at the final path (a mismatch raises WheelHashError and no file is written).

The files of a distribution are never extracted or installed here: the caller
decides what happens to them (install, keep, delete), the fetcher only writes
them and returns their paths.

Usage:
    from alasio.deploy.pip_download.pypi_file import DOWNLOAD_MODE_CURRENT, PypiFile

    pypi = PypiFile(mirrors, dest)
    files = await pypi.download('numpy', '2.1.0', DOWNLOAD_MODE_CURRENT)
    await pypi.aclose()
"""

import hashlib
from typing import Dict, List, Literal

import msgspec
import trio

from alasio.deploy.httpclient.probe_async import AsyncHttpProbe
from alasio.deploy.pip_download._link import Link, parse_links
from alasio.deploy.pip_download._specifiers import InvalidSpecifier
from alasio.deploy.pip_download._target_python import TargetPython
from alasio.deploy.pip_download._utils import InvalidWheelFilename, canonicalize_name, check_requires_python
from alasio.deploy.pip_download._version import InvalidVersion, Version
from alasio.deploy.pip_download._wheel import Wheel
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_bytes_stream
from alasio.logger import logger

__all__ = [
    'DOWNLOAD_MODE_ALL',
    'DOWNLOAD_MODE_CURRENT',
    'DOWNLOAD_MODE_PY3_ANY',
    'DOWNLOAD_MODES',
    'DownloadMode',
    'PackageIndex',
    'PypiFile',
    'WheelCandidate',
    'WheelHashError',
    'WheelInfo',
    'WheelNotFoundError',
    'select_wheels',
]

# The wheels a download asks for, see select_wheels()
DownloadMode = Literal['current', 'py3-any', 'all']

# every wheel compatible with the target interpreter
DOWNLOAD_MODE_CURRENT = 'current'
# every pure python wheel of python 3 (`none-any` abi and platform)
DOWNLOAD_MODE_PY3_ANY = 'py3-any'
# every wheel of the version, whatever its tags
DOWNLOAD_MODE_ALL = 'all'

DOWNLOAD_MODES = (DOWNLOAD_MODE_CURRENT, DOWNLOAD_MODE_PY3_ANY, DOWNLOAD_MODE_ALL)

# The hashes a link may carry, the strongest one the fetcher knows comes first
_HASH_PREFERENCE = ('sha256', 'sha512', 'sha384', 'sha224', 'sha1', 'md5')


class WheelNotFoundError(ValueError):
    """
    Raised when the index of a mirror has no wheel the download asks for.

    The cases: the mirror has no index of the distribution (a 404), the index
    has no wheel of the version, or the version has no wheel that matches the
    mode of the download (`current` on a platform without a compatible wheel,
    `py3-any` on a distribution that ships compiled wheels only).
    """


class WheelHashError(ValueError):
    """
    Raised when a downloaded wheel does not match the hash of its index link.

    The bytes are not the file the index describes: nothing is written at the
    final path, and no local file is trusted for the next run.
    """


class WheelCandidate:
    """
    One wheel of an index page, the file and the link it came from.

    Attributes:
        link (Link): The link of the index page
        wheel (Wheel): The parsed filename of the link
    """

    __slots__ = ('link', 'wheel')

    def __init__(self, link, wheel):
        """
        Args:
            link (Link): The link of the index page
            wheel (Wheel): The parsed filename of the link
        """
        self.link = link
        self.wheel = wheel

    @property
    def filename(self):
        """
        Returns:
            str: Filename of the wheel
        """
        return self.wheel.filename

    def __repr__(self):
        return f'<WheelCandidate {self.filename}>'


class PackageIndex(msgspec.Struct):
    """
    One index page of a distribution: the body, the url and the parsed links.

    The body is kept as the mirror served it, byte for byte, for the caller that
    snapshots the page instead of only reading its links (the pack server caches
    the page of a version, see fetch_wheel).

    Attributes:
        url (str): Url the page was served at, the base of its relative links
        body (bytes): Body of the page, as the index served it
        links (List[Link]): The links of the page, in the order of the page
    """

    url: str
    body: bytes
    links: List[Link]


class WheelInfo(msgspec.Struct):
    """
    One wheel on the disk and the digests of its bytes.

    The digests are computed while the bytes are read or downloaded (see
    PypiFile.get_wheel()), so a caller that publishes the file or records it
    never reads it again.

    Attributes:
        file (PathStr): Path of the wheel in the destination folder
        hashes (Dict[str, str]): Lowercase hex digests of the file, hash name
            to value. 'sha256' is always there
    """

    file: PathStr
    hashes: Dict[str, str]


def _is_pure_python3(wheel):
    """
    Whether the wheel is a pure python wheel of python 3.

    The equivalent of `fetch_wheel._is_pure_python3`: the ABI and the platform
    tags are `none-any`, so the file has no environment constraint, and the
    python tag covers python 3 ('py3', 'py2.py3', 'py38', ...).

    Args:
        wheel (Wheel): The wheel to check

    Returns:
        bool: True for a pure python wheel of python 3
    """
    return any(
        tag.abi == 'none' and tag.platform == 'any' and tag.interpreter.startswith('py3')
        for tag in wheel.file_tags
    )


def _requires_python_ok(link, target):
    """
    Whether the target interpreter satisfies the `Requires-Python` of a link.

    A link that carries no value is fine, and a value that does not parse is
    taken as fine too, which is the answer of pip: a broken specifier of an
    index must not hide the file from every client.

    Args:
        link (Link): The link to check
        target (TargetPython): The interpreter the wheels are selected for

    Returns:
        bool: True when the file is usable by the target
    """
    try:
        return check_requires_python(link.requires_python, target.py_version_info)
    except InvalidSpecifier as e:
        logger.debug(
            f'Ignoring invalid Requires-Python of "{link.show_url}": {link.requires_python!r}, {e}')
        return True


def select_wheels(links, name, version, mode: DownloadMode, target=None):
    """
    Select the wheels of one version from the links of an index page.

    The filter of the links: a wheel of the distribution, of the version, that
    matches the mode, and that is not yanked (PEP 592) unless every candidate
    of the mode is yanked. The order of the answer: the tag preference of the
    target for `current` (`Wheel.support_index_min`), the filename otherwise,
    so the answer is deterministic.

    Args:
        links (Iterable[Link]): Links of the index page
        name (str): Distribution name, any case and separator style
        version (str): Exact version of the distribution
        mode (DownloadMode): One of DOWNLOAD_MODES
        target (TargetPython | None): The interpreter the wheels are selected
            for. Defaults to None, the running interpreter

    Returns:
        list[WheelCandidate]: The wheels to download, most preferred first

    Raises:
        ValueError: If the mode is not one of DOWNLOAD_MODES
        WheelNotFoundError: If no wheel of the version matches the mode
    """
    if mode not in DOWNLOAD_MODES:
        raise ValueError(f'Invalid download mode: {mode!r}, expected one of {DOWNLOAD_MODES}')
    target = target if target is not None else TargetPython()

    dist = canonicalize_name(name)
    try:
        wanted = Version(version)
    except InvalidVersion as e:
        raise WheelNotFoundError(
            f'Invalid version of "{name}": {version!r}, {e}') from None

    # 1. the wheels of the distribution and the version
    candidates = []
    filenames = []
    for link in links:
        if not link.is_wheel:
            continue
        try:
            wheel = Wheel(link.filename)
        except InvalidWheelFilename as e:
            logger.debug(f'Ignoring a link that is not a wheel filename: {e}')
            continue
        if wheel.name != dist:
            continue
        if Version(wheel.version) != wanted:
            continue
        filenames.append(wheel.filename)
        candidates.append(WheelCandidate(link, wheel))

    # 2. the wheels the mode asks for
    if mode == DOWNLOAD_MODE_CURRENT:
        supported = target.get_unsorted_tags()
        eligible = [
            candidate for candidate in candidates
            if candidate.wheel.supported(supported) and _requires_python_ok(candidate.link, target)
        ]
    elif mode == DOWNLOAD_MODE_PY3_ANY:
        eligible = [
            candidate for candidate in candidates
            if _is_pure_python3(candidate.wheel)
        ]
    else:
        # DOWNLOAD_MODE_ALL: every wheel of the version
        eligible = list(candidates)

    # 3. the yanked files, PEP 592: a pinned version may only have yanked
    # wheels left, and pip accepts them then too
    if mode == DOWNLOAD_MODE_ALL:
        yanked = [candidate.filename for candidate in eligible if candidate.link.is_yanked]
        if yanked:
            logger.info(f'Yanked wheels of "{name}=={version}" are kept: {", ".join(yanked)}')
    else:
        not_yanked = [candidate for candidate in eligible if not candidate.link.is_yanked]
        if not_yanked:
            eligible = not_yanked
        elif eligible:
            yanked = [candidate.filename for candidate in eligible if candidate.link.is_yanked]
            logger.warning(
                f'Every wheel of "{name}=={version}" that matches "{mode}" is yanked: '
                f'{", ".join(yanked)}')

    if not eligible:
        files = ', '.join(sorted(filenames))
        if filenames:
            raise WheelNotFoundError(
                f'No wheel of "{name}=={version}" matches "{mode}", the version has: {files}')
        raise WheelNotFoundError(f'No wheel of "{name}=={version}" in the index')

    # 4. the order of the answer
    if mode == DOWNLOAD_MODE_CURRENT:
        tags = target.get_sorted_tags()
        eligible.sort(key=lambda candidate: (candidate.wheel.support_index_min(tags), candidate.filename))
    else:
        eligible.sort(key=lambda candidate: candidate.filename)
    return eligible


def _file_digests(file, names):
    """
    The digests of a local file, every name in one read of it.

    Args:
        file (PathStr): Path of the file
        names (tuple[str, ...]): Names of the hashes, e.g. ('sha256', 'md5')

    Returns:
        dict[str, str] | None: Hash name to lowercase hex digest, None when the
            file does not exist
    """
    digests = {name: hashlib.new(name) for name in names}
    try:
        for chunk in atomic_read_bytes_stream(file):
            for digest in digests.values():
                digest.update(chunk)
    except FileNotFoundError:
        return None
    return {name: digest.hexdigest() for name, digest in digests.items()}


def _expected_hash(link):
    """
    The strongest hash a link carries.

    Args:
        link (Link): The link of the index page

    Returns:
        tuple[str, str]: (hash name, digest), ('', '') when the link carries
            none of the hashes the fetcher knows
    """
    for name in _HASH_PREFERENCE:
        value = link.hashes.get(name)
        if value:
            return name, value
    return '', ''


class PypiFile(AsyncHttpProbe):
    """
    Download the wheels of a distribution version from a PyPI simple index.

    The layout, the selection of the wheels and the checks are in the module
    docstring. The fetcher is lazy: nothing is requested until the first call
    of get_package_index(), get_wheel() or download().

    The index page is driven by the probe layer: get_package_index() and
    download() serve the page path through fetch(), so the mirror is resolved by
    the record of the machine or by a probe of the page, and a failure of the
    page the domain declares (it does not parse, or it has no wheel of the
    version) makes the engine probe the path again without that mirror and
    retry it once. The probe of a path is the default HEAD: the page of a big
    distribution is large (the page of numpy is a few MB), so a probe of it
    must not transfer the page. The page is fetched by the work of the flow,
    once, on the selected mirror.

    A wheel is not a path of a mirror: it is served by the host of its url (see
    get_wheel()), so its transfer goes through the http client alone.

    Attributes:
        SCENE (str): The usage domain of the pypi mirrors ('pypi')
        FAILURES (tuple): Failures that mean the mirror cannot serve the
            request: its page does not parse, or its page has no wheel of the
            version and the mode of the download
    """

    SCENE = 'pypi'
    FAILURES = (WheelNotFoundError,)

    def __init__(self, mirrors, dest, name='', client=None, target=None):
        """
        Args:
            mirrors (Mirrors | str | dict): The pypi mirrors to select from,
                see Mirrors.from_input() for the accepted forms
            dest (str): Folder the wheels are downloaded into; it is created by
                the write of a wheel (the atomic write creates the folders of
                its path), no call creates it up front
            name (str): Identity of this mirror set in the record of the
                machine, `ModEntryInfo.pypi_record_name`. Defaults to ''
            client (httpx2.AsyncClient | AsyncHttpClient, optional): Client to
                send the requests with, or an AsyncHttpClient whose client is
                taken. Its lifetime belongs to the caller. Defaults to None, a
                client of this instance is created on the first request,
                aclose() closes it
            target (TargetPython | None): The interpreter the wheels are
                selected for. Defaults to None, the running interpreter
        """
        super().__init__(mirrors, name=name, client=client)
        self.dest = PathStr.new(dest)
        self.target = target if target is not None else TargetPython()

    async def warmup(self):
        """
        Resolve the record of the machine before the first request.

        The record read is blocking sqlite IO (the mirror selected by an
        earlier flow), and the first request would otherwise pay it on the
        event loop. The probe is left to the first request on purpose: it is a
        request of the path the flow is about to use, warming it up here would
        only add a HEAD nobody asked for. Idempotent, an instance whose mirror
        was already resolved does nothing.
        """
        if self._mirror or self.mirrors.single:
            return
        name = await trio.to_thread.run_sync(self.load_record)
        if name:
            self._mirror = name

    async def download(self, name, version, mode: DownloadMode):
        """
        Download the wheels of one version to the destination folder.

        The index page of the distribution is fetched on a mirror, the wheels of
        the mode are selected from its links (see select_wheels()), and every
        wheel is downloaded through get_wheel() to `{dest}/{filename}`. A file
        that exists and matches the hash of its link is skipped, the other ones
        are downloaded, checked against the hash of their link and written
        atomically.

        The selection runs inside the work of the probed path, so a page that
        has no wheel of the version is a failure of the mirror and not of the
        flow: the probe layer reselects a mirror without that one and retries
        the page once (WheelNotFoundError is a failure type of the domain, see
        FAILURES and AsyncProbe.fetch). The page of the mirror that lacks the
        version is a copy the index has not caught up with yet; the wheel of
        another mirror is the same file.

        Args:
            name (str): Distribution name, e.g. 'numpy', any case and
                separator style
            version (str): Exact version, e.g. '2.1.0'
            mode (DownloadMode): DOWNLOAD_MODE_CURRENT, DOWNLOAD_MODE_PY3_ANY
                or DOWNLOAD_MODE_ALL, see select_wheels()

        Returns:
            list[PathStr]: Paths of the wheels, the order of select_wheels()

        Raises:
            ValueError: If the mode is not one of DOWNLOAD_MODES
            WheelNotFoundError: If no mirror has a wheel of the version that
                matches the mode
            WheelHashError: If a downloaded wheel does not match the hash of
                its link, no file is put at the final path
            AllMirrorsFailedError: If no mirror serves the index page
            httpx2.HTTPError: If a request fails
        """
        if mode not in DOWNLOAD_MODES:
            raise ValueError(f'Invalid download mode: {mode!r}, expected one of {DOWNLOAD_MODES}')
        candidates = await self.get_package_index(
            name, lambda index: select_wheels(index.links, name, version, mode, self.target))
        return [(await self.get_wheel(candidate.link.url)).file for candidate in candidates]

    # ------------------------------------------------------------------
    # The index page of a distribution
    # ------------------------------------------------------------------

    async def get_package_index(self, name, select=None):
        """
        Get the index page of a distribution from {mirror}/{dist-key}/.

        The page is the probed path, and the probe is the default HEAD of the
        probe layer: the page of a big distribution is large (the page of
        numpy is a few MB), so the probe must not transfer it. The page itself
        is fetched once by the work below, on the selected mirror, and the
        redirects of the mirror are followed: a mirror may send `/{dist}` to
        `/{dist}/`, or serve its index below another host. The links carry the
        filenames, the hashes and the `data-*` attributes of the page, whatever
        the version (see select_wheels() for the filter).

        Args:
            name (str): Distribution name, any case and separator style
            select (Callable, optional): The step of a flow that asks a
                question about the page, `select(index)`, run inside the probe:
                its answer is the answer of this call, and a failure of it is a
                failure of the mirror, so the flow moves to another one and
                retries once (download() selects the wheels of a version this
                way). Defaults to None, the page itself

        Returns:
            PackageIndex | Any: The page, or the answer of select()

        Raises:
            WheelNotFoundError: If the served page does not parse (the mirror
                is reselected and the page is fetched again once)
            httpx2.HTTPError: If the request fails (a 404 is the answer of the
                mirror for a distribution it has no index of)
            AllMirrorsFailedError: If no mirror is usable
        """
        path = f'/{canonicalize_name(name)}/'

        async def work(mirror_name, url):
            page_url = f'{url}{path}'
            logger.info(f'Download index page: "{page_url}"')
            response = await self._http.get(page_url, follow_redirects=True)
            body = response.content
            try:
                links = parse_links(body, page_url, response.headers.get('content-type', ''))
            except ValueError as e:
                # a page that does not parse (a broken JSON page, a response
                # that is not a page at all) is a page this mirror cannot serve
                raise WheelNotFoundError(
                    f'The index page of "{page_url}" does not parse: {e}') from e
            index = PackageIndex(page_url, body, links)
            return index if select is None else select(index)

        return await self.fetch(path, work)

    # ------------------------------------------------------------------
    # One wheel of a page
    # ------------------------------------------------------------------

    async def get_wheel(self, url):
        """
        Download one wheel to the destination folder, with the digests of its bytes.

        The url is the one of the link of an index page, its fragment included
        (`#sha256=...`): the file is requested at the url without the fragment,
        and the hash of the fragment is the check of the bytes. The url is used
        as it is, not resolved against the mirror: a page may point at a files
        host of its own (files.pythonhosted.org).

        The transfer does not go through the probe layer: a wheel is served by
        the host of its url, not by the mirror that listed it, so there is no
        path of the mirror to probe. The mirror was selected by
        get_package_index() (its page listed the file), and a transport error
        of the transfer is retried by the http client.

        A file that is already in the destination folder and matches the hash
        of its link is kept as it is, no request is made. A link without a
        hash has nothing to check against, the local file is trusted: the
        first download is the authority (TOFU).

        The destination folder is not created here: the atomic write of the
        file creates the folders of its path when they do not exist.

        Args:
            url (str): Absolute url of the wheel, the fragment of the link
                included

        Returns:
            WheelInfo: The wheel at {dest}/{filename} and the digests of its
                bytes. The sha256 is always computed, the pip index the pack
                server publishes links every wheel with its `#sha256=`; the
                digest of the hash of the link is computed in the same read or
                download, so no caller reads the file again

        Raises:
            WheelHashError: If the downloaded bytes do not match the hash of
                their link, no file is put at the final path
            httpx2.HTTPError: If the request fails
        """
        link = Link(url)
        file = self.dest.joinpath(link.filename)
        sha_name, sha_value = _expected_hash(link)
        names = ('sha256',) if sha_name in ('', 'sha256') else ('sha256', sha_name)
        hashes = await trio.to_thread.run_sync(_file_digests, file, names)
        if hashes is not None and (not sha_name or hashes[sha_name] == sha_value):
            logger.info(f'Wheel exists, skipped: "{file}"')
            return WheelInfo(file, hashes)

        source = link.url_without_fragment
        logger.info(f'Download wheel: "{source}" -> "{file}"')
        # the bytes are collected in memory and written in a worker thread: a
        # wheel of the dependencies of this project is a few MB, and the write
        # of a file must not block the event loop
        collected = []
        computed = {}

        async def consume(response):
            # every digest is fed in the same pass of the bytes: a second read
            # of the file for another hash would cost another transfer
            digests = {name: hashlib.new(name) for name in names}
            chunks = []
            async for chunk in response.aiter_bytes():
                chunks.append(chunk)
                for digest in digests.values():
                    digest.update(chunk)
            values = {name: digest.hexdigest() for name, digest in digests.items()}
            if sha_name and values[sha_name] != sha_value:
                raise WheelHashError(
                    f'Hash of "{source}" does not match the index: '
                    f'{values[sha_name]} != {sha_value}')
            # the buffer is local until the transfer completes: a failed
            # attempt leaves nothing behind for the retry
            collected.clear()
            collected.extend(chunks)
            computed.update(values)

        # a transport error of the transfer is retried as a whole by the http
        # client, the chunks of a failed attempt are dropped with it; the
        # redirects of the files host (a CDN, another host) are followed
        await self._http.stream(source, consume, follow_redirects=True)
        await trio.to_thread.run_sync(file.atomic_write, b''.join(collected))
        return WheelInfo(file, computed)
