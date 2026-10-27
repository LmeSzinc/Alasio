"""
Download the wheels of a distribution version from a PyPI simple index.

The fetcher is the client side counterpart of
`alasio/deploy_dev/pack_server/fetch_wheel.py`: it takes `(name, version)` and
downloads the wheels that match `mode` into the folder the caller gave, with
the index parsed by the ported pip helpers (`_link`, `_wheel`, `_utils`):

- `current`: every wheel compatible with the target interpreter (the running
  one by default), ordered by the tag preference of PEP 425;
- `py3-any`: every pure python wheel of python 3 (`none-any` ABI and
  platform), the semantics of the release side wheel fetch;
- `all`: every wheel of the version, whatever its tags.

The index page is probe-driven, like the pack server: `get_package_index()`
and `download()` serve the page path through `AsyncProbe.fetch()` and pass the
work of the path as a function of their own, so the mirror is resolved by the
record of the machine or by a probe of the page, and a failure the domain
declares (`WheelNotFoundError`) makes the engine probe the path again without
the failed mirror and retry it once:

- `get_package_index(name)` requests `{mirror}/{dist-key}/` and returns the
  links of the page. The probe of the path is the default HEAD of the probe
  layer: the page of a big distribution is large (the page of numpy is a few
  MB), a probe must not transfer it. The page is fetched once by the work of
  the flow, on the selected mirror;
- `get_wheel(url)` downloads one wheel of a page by the absolute url of its
  link. A page may point at a files host of its own
  (files.pythonhosted.org), so the url is used as it is and the transfer goes
  through the http client alone: the file is not served by the mirror, and
  there is no path of the mirror to probe. The mirror is the one
  `get_package_index()` selected.

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
    from alasio.deploy.pip_download.pypi_file import PypiFile

    pypi = PypiFile(mirrors, dest)
    files = await pypi.download('numpy', '2.1.0', 'current')
    await pypi.aclose()
"""

import hashlib
from typing import Literal

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
    'DOWNLOAD_MODES',
    'DownloadMode',
    'PypiFile',
    'WheelCandidate',
    'WheelHashError',
    'WheelNotFoundError',
    'select_wheels',
]

# The wheels a download asks for, see select_wheels()
DownloadMode = Literal['current', 'py3-any', 'all']
DOWNLOAD_MODES = ('current', 'py3-any', 'all')

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
            f'Ignoring invalid Requires-Python of "{link.show_url}": '
            f'{link.requires_python!r}, {e}')
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
    if mode == 'current':
        supported = target.get_unsorted_tags()
        eligible = [
            candidate for candidate in candidates
            if candidate.wheel.supported(supported) and _requires_python_ok(candidate.link, target)
        ]
    elif mode == 'py3-any':
        eligible = [
            candidate for candidate in candidates
            if _is_pure_python3(candidate.wheel)
        ]
    else:
        # 'all': every wheel of the version
        eligible = list(candidates)

    # 3. the yanked files, PEP 592: a pinned version may only have yanked
    # wheels left, and pip accepts them then too
    if mode == 'all':
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
    if mode == 'current':
        tags = target.get_sorted_tags()
        eligible.sort(key=lambda candidate: (candidate.wheel.support_index_min(tags),
                                             candidate.filename))
    else:
        eligible.sort(key=lambda candidate: candidate.filename)
    return eligible


def _hash_file(file, name):
    """
    The digest of a file, streamed in chunks.

    Args:
        file (str): Path of the file
        name (str): Name of the hash, e.g. 'sha256'

    Returns:
        str: Lowercase hex digest

    Raises:
        FileNotFoundError: If the file does not exist
    """
    digest = hashlib.new(name)
    for chunk in atomic_read_bytes_stream(file):
        digest.update(chunk)
    return digest.hexdigest()


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
            mode (DownloadMode): 'current', 'py3-any' or 'all', see
                select_wheels()

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
        path = f'/{canonicalize_name(name)}/'

        async def work(mirror_name, url):
            links = await self._get_index(url, path)
            return select_wheels(links, name, version, mode, self.target)

        candidates = await self.fetch(path, work)
        return [await self.get_wheel(candidate.link.url) for candidate in candidates]

    # ------------------------------------------------------------------
    # The index page of a distribution
    # ------------------------------------------------------------------

    async def get_package_index(self, name):
        """
        Get the links of the index page of a distribution from
        {mirror}/{dist-key}/.

        The page is the probed path, and the probe is the default HEAD of the
        probe layer: the page of a big distribution is large (the page of
        numpy is a few MB), so the probe must not transfer it. The page itself
        is fetched once by the work below, on the selected mirror. The links
        carry the filenames, the hashes and the `data-*` attributes of the
        page, whatever the version (see select_wheels() for the filter).

        Args:
            name (str): Distribution name, any case and separator style

        Returns:
            list[Link]: The links of the page, in the order of the page

        Raises:
            WheelNotFoundError: If the served page does not parse (the mirror
                is reselected and the page is fetched again once)
            httpx2.HTTPError: If the request fails (a 404 is the answer of the
                mirror for a distribution it has no index of)
            AllMirrorsFailedError: If no mirror is usable
        """
        path = f'/{canonicalize_name(name)}/'

        async def work(mirror_name, url):
            return await self._get_index(url, path)

        return await self.fetch(path, work)

    async def _get_index(self, url, path):
        """
        Request and parse the index page of one mirror.

        The page is requested with the default timeout of the client, and the
        redirects of the mirror are followed: a mirror may send `/{dist}` to
        `/{dist}/`, or serve its index below another host.

        Args:
            url (str): Base url of the mirror
            path (str): Path of the index page

        Returns:
            list[Link]: The links of the page, in the order of the page

        Raises:
            WheelNotFoundError: If the page does not parse: a page this mirror
                cannot serve is a failure of the mirror, so the probe layer
                retries the path on another one once
            httpx2.HTTPError: If the request fails
        """
        page_url = f'{url}{path}'
        response = await self._http.get(page_url, follow_redirects=True)
        content_type = response.headers.get('content-type', '')
        try:
            return parse_links(response.content, page_url, content_type)
        except ValueError as e:
            # a page that does not parse (a broken JSON page, a response that
            # is not a page at all) is a page this mirror cannot serve
            raise WheelNotFoundError(
                f'The index page of "{page_url}" does not parse: {e}') from e

    # ------------------------------------------------------------------
    # One wheel of a page
    # ------------------------------------------------------------------

    async def get_wheel(self, url):
        """
        Download one wheel to the destination folder and return its path.

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
            PathStr: Path of the wheel, {dest}/{filename}

        Raises:
            WheelHashError: If the downloaded bytes do not match the hash of
                their link, no file is put at the final path
            httpx2.HTTPError: If the request fails
        """
        link = Link(url)
        file = self.dest.joinpath(link.filename)
        sha_name, sha_value = _expected_hash(link)
        if await trio.to_thread.run_sync(_wheel_is_valid, file, sha_name, sha_value):
            logger.info(f'Wheel exists, skipped: "{file}"')
            return file

        source = link.url_without_fragment
        logger.info(f'Download wheel: "{source}" -> "{file}"')
        # the bytes are collected in memory and written in a worker thread: a
        # wheel of the dependencies of this project is a few MB, and the write
        # of a file must not block the event loop
        collected = []

        async def consume(response):
            digest = hashlib.new(sha_name) if sha_name else None
            chunks = []
            async for chunk in response.aiter_bytes():
                chunks.append(chunk)
                if digest is not None:
                    digest.update(chunk)
            if digest is not None and digest.hexdigest() != sha_value:
                raise WheelHashError(
                    f'Hash of "{source}" does not match the index: '
                    f'{digest.hexdigest()} != {sha_value}')
            # the buffer is local until the transfer completes: a failed
            # attempt leaves nothing behind for the retry
            collected.clear()
            collected.extend(chunks)

        # a transport error of the transfer is retried as a whole by the http
        # client, the chunks of a failed attempt are dropped with it; the
        # redirects of the files host (a CDN, another host) are followed
        await self._http.stream(source, consume, follow_redirects=True)
        await trio.to_thread.run_sync(file.atomic_write, b''.join(collected))
        return file


def _wheel_is_valid(file, sha_name, sha_value):
    """
    Whether the local file is the wheel its index link describes.

    A file that does not exist is not valid, it is downloaded. A link without
    a hash has nothing to check against, the local file is trusted: the first
    download is the authority (TOFU).

    Args:
        file (PathStr): Path of the wheel in the destination folder
        sha_name (str): Name of the expected hash, '' for no check
        sha_value (str): Expected digest, '' for no check

    Returns:
        bool: True when the file is usable as it is, no download needed
    """
    if not sha_name:
        return file.exists()
    try:
        return _hash_file(file, sha_name) == sha_value
    except FileNotFoundError:
        return False
