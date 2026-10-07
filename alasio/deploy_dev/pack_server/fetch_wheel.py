"""
Fetch the wheel of a distribution version from a PyPI simple mirror.

A pack config with PythonDeps builds the packs of the dependencies of a repo,
the wheels are the input of the build: this module downloads the wheel of one
(name, version) from the PyPI simple index (PEP 503) of a mirror to the wheel
folder of a repo of the pack server:

    {run directory}/pack/{Author}_{Repo}_{Branch}/wheel/{distribution}/{version}/{filename}.whl

One WheelFetcher instance fetches one (name, version) of one mirror,
target_folder is the folder the wheel and its index data are put in. The
fetch runs in two steps:

1. index_data: the index page of the distribution is requested at
   {mirror}/{name}/ and parsed into one WheelInfo per wheel file of the page,
   the parse is written to {target_folder}/index_data.json with an indent, so
   an operator can read it (jsonfile.write_json). A fetch of the same
   (name, version) loads the file instead of requesting the page again: the
   cache is trusted as it is, delete the file to read the page again, e.g.
   after the mirror of the config was changed. A cache file that holds no
   index data is reported with a warning and the page is read again, the file
   is written again;
2. wheel: the pure python wheel of the version is selected from index_data
   and downloaded to {target_folder}/{filename}. The file is downloaded when
   it is not in the folder yet, and when the sha256 of the local file does
   not match the sha256 of its index link: a file that matches is kept as it
   is, no request is made. A link without sha256 has nothing to check
   against, the local file is trusted, the first download is the authority
   (TOFU).

Only the pure python wheels of python3 are fetched: the abi and the platform
tag of the file must be none-any, the python tag must cover py3. A version
that has no such wheel is not a target of the pack build (a distribution with
compiled extensions is updated with pip, not with a pack), the fetch raises
WheelNotFoundError. The distribution name is normalized with PEP 503 for the
index url and the folder, the same dist-key the ledger and the pack folders
use, see parse_dep.normalize_name.

A download is written atomically, and the bytes are checked against the
sha256 of the index link while streaming when the link carries one: a
mismatch raises WheelHashError and no file is put at the final path.

Usage:
    from alasio.deploy_dev.pack_server.fetch_wheel import WheelFetcher

    # root = {run directory}/pack/{Author}_{Repo}_{Branch}
    root = env.PROJECT_ROOT.joinpath('pack').joinpath('LmeSzinc_AzurLaneAutoScript_master')
    fetcher = WheelFetcher(root, mirror, 'httpx', '0.28.1')
    file = fetcher.fetch()
    fetcher.close()
"""

import hashlib
import re
from typing import List
from urllib.parse import unquote, urljoin, urlparse

import httpx2
import msgspec
from msgspec.structs import asdict

from alasio.deploy_dev.pack_server.parse_dep import normalize_name
from alasio.ext.cache import cached_property
from alasio.ext.file.jsonfile import write_json
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_bytes, atomic_read_bytes_stream
from alasio.logger import logger

# folder of the fetched wheels in the repo folder
WHEEL_FOLDER = 'wheel'

# cache of the parsed index page, in the folder of the version
INDEX_DATA_FILE = 'index_data.json'

# the href of a link of a PEP 503 index page: href="..." or href='...'
REGEX_LINK = re.compile(r'href=["\']([^"\']+)["\']')


class WheelInfo(msgspec.Struct):
    """
    One wheel file parsed from a link of a PEP 503 index page.

    Attributes:
        name (str): PEP 503 normalized distribution name of the filename
        version (str): Version of the filename
        filename (str): Filename of the wheel, e.g. 'httpx-0.28.1-py3-none-any.whl'
        url (str): Absolute url of the file, resolved against the index url,
            without the fragment: the fragment of a link carries the sha256
        sha256 (str): Lowercase sha256 hex of the link fragment, '' if the
            link carries none
        tags (List[str]): Trailing python, abi and platform tags of the filename
    """

    name: str
    version: str
    filename: str
    url: str
    sha256: str
    tags: List[str]


class WheelNotFoundError(ValueError):
    """
    Raised when the mirror holds no wheel to fetch for the name and version.

    The cases: the mirror has no index of the distribution (a 404), the index
    has no wheel of the version, or the version has no pure python wheel of
    python3 (a distribution with compiled extensions is not a target of the
    pack build, it is updated with pip).
    """


class WheelHashError(ValueError):
    """
    Raised when the downloaded wheel does not match the sha256 of the index
    link: the bytes are not the file the index describes and are not kept.
    """


def _parse_wheel_file(filename):
    """
    Parse a wheel filename into the name, the version and the tags.

    The wheel filename format (PEP 427):
    {name}-{version}(-{build tag})?-{python tag}-{abi tag}-{platform tag}.whl
    The name and the version of the file carry no '-', the name part is
    normalized with PEP 503 for the compare with a dependency name.

    Args:
        filename (str): Filename of a wheel, e.g. 'httpx-0.28.1-py3-none-any.whl'

    Returns:
        tuple[str, str, list[str]] | None: (normalized name, version, tags) of
            the file, the tags are the trailing python, abi and platform tags,
            None if the filename is not a wheel filename
    """
    if not filename.endswith('.whl'):
        return None
    parts = filename[:-len('.whl')].split('-')
    # {name}-{version}-{python}-{abi}-{platform}(-{build})...: the name and
    # the version carry no '-', the last three parts are always the tags
    if len(parts) < 5:
        return None
    return normalize_name(parts[0]), parts[1], parts[-3:]


def _is_pure_python3(tags):
    """
    Whether the tags are a pure python wheel of python3.

    A pure python wheel has no environment constraint: the abi and the
    platform tag are none-any, only the python tag narrows the interpreter.
    The python tag must cover py3 ('py3', 'py2.py3', 'py38', ...): the pack
    build runs on python3, a py2 only wheel is not installable.

    Args:
        tags (list[str]): Trailing tags of a wheel filename: python, abi, platform

    Returns:
        bool: True if the wheel is a pure python wheel of python3
    """
    python, abi, platform = tags
    return abi == 'none' and platform == 'any' and 'py3' in python


def _fragment_sha256(fragment):
    """
    The sha256 of a link fragment of a PEP 503 index page.

    Args:
        fragment (str): Fragment of the url, e.g. 'sha256=ab12...'

    Returns:
        str: Lowercase hex digest, '' if the fragment carries no sha256
    """
    key, sep, value = fragment.partition('=')
    if sep and key.lower() == 'sha256':
        return value.lower()
    return ''


def _parse_index_page(text, index_url):
    """
    Parse a PEP 503 index page into the wheel files it links.

    The links of the page are read as they are: the relative urls are
    resolved against the index url, the sha256 fragment of a link is read and
    the filename is parsed. A link that is not a wheel file is skipped, the
    wheel files of every version are parsed.

    Args:
        text (str): Content of the index page
        index_url (str): Url of the page, the relative urls of the links are
            resolved against it

    Returns:
        list[WheelInfo]: One WheelInfo per wheel file the page links, in the
            order of the page
    """
    data = []
    for href in REGEX_LINK.findall(text):
        link_url = urljoin(index_url, href)
        split = urlparse(link_url)
        filename = unquote(split.path.rpartition('/')[2])
        parsed = _parse_wheel_file(filename)
        if parsed is None:
            continue
        name, version, tags = parsed
        # the request url carries no fragment, the sha256 fragment of the
        # link is read separately
        url = split._replace(fragment='').geturl()
        data.append(WheelInfo(
            name=name, version=version, filename=filename, url=url,
            sha256=_fragment_sha256(split.fragment), tags=tags))
    return data


def _sha256_file(file):
    """
    The sha256 hex digest of a file, streamed in chunks.

    Args:
        file (str): Source file path

    Returns:
        str: Lowercase sha256 hex digest

    Raises:
        FileNotFoundError: If the file does not exist
    """
    digest = hashlib.sha256()
    for chunk in atomic_read_bytes_stream(file):
        digest.update(chunk)
    return digest.hexdigest()


def _read_index_data(file):
    """
    Read the cached index data of a version.

    A cache file that holds no index data of the version (an interrupted
    write, a file of another tool) is reported with a warning and None is
    answered: the caller reads the index page and writes the file again,
    like a missing file.

    Args:
        file (PathStr): Path of the cache file, see WheelFetcher.index_data_file

    Returns:
        list[WheelInfo] | None: The cached wheel information, None if the
            file does not exist or holds no index data
    """
    try:
        content = atomic_read_bytes(file)
    except FileNotFoundError:
        return None
    try:
        return msgspec.json.decode(content, type=List[WheelInfo])
    except msgspec.DecodeError as e:
        logger.warning(f'Invalid index data, read the index page again: "{file}", {e}')
        return None


def _iter_checked(chunks, sha256, url):
    """
    Iter the chunks of a download, check the sha256 when the stream ends.

    The check lives in the generator on purpose: the generator is consumed by
    atomic_write_stream, a raise here (after every chunk is yielded) fails the
    write before the tmp file is put at the final path.

    Args:
        chunks (Iterable[bytes]): Chunks of the download
        sha256 (str): Expected sha256 hex, '' to skip the check
        url (str): Url of the download, for the error message

    Yields:
        bytes: The chunks, unchanged

    Raises:
        WheelHashError: If the digest of the chunks does not match sha256
    """
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(chunk)
        yield chunk
    if sha256 and digest.hexdigest() != sha256:
        raise WheelHashError(
            f'Sha256 of "{url}" does not match the index: {digest.hexdigest()} != {sha256}')


class WheelFetcher:
    """
    Fetch the wheel of one distribution version from a PyPI simple mirror.

    The layout, the selection of the wheel and the checks are in the module
    docstring. The fetch is lazy: nothing is requested until fetch() or
    index_data is read.

    Usage:
        # the folder of the repo, {run directory}/pack/{Author}_{Repo}_{Branch}
        fetcher = WheelFetcher(repo_folder, mirror, 'httpx', '0.28.1')
        file = fetcher.fetch()
    """

    def __init__(self, root, mirror, name, version, client=None):
        """
        Args:
            root (str): Folder of the repo in the pack folder of the run
                directory, e.g. {run directory}/pack/{Author}_{Repo}_{Branch};
                the index data and the wheel are put in
                {root}/wheel/{name}/{version}/
            mirror (str): Base url of the PyPI simple index (PEP 503), e.g.
                'https://mirrors.aliyun.com/pypi/simple', with or without the
                trailing '/'
            name (str): Distribution name, e.g. 'httpx', the name a dependency
                file writes. Normalized with PEP 503 for the index url and the
                folder.
            version (str): Exact version of the distribution, e.g. '0.28.1',
                the version string a dependency file pins
            client (httpx2.Client, optional): Client to use, its lifetime
                belongs to the caller (this class never closes an injected
                client). Defaults to None, a client of this instance is
                created on the first request and reused by the later ones,
                close() closes it
        """
        self.root = PathStr.new(root)
        self.mirror = mirror.rstrip('/')
        self.name = name
        self.version = version
        self._client = client
        self._own_client = client is None

    @cached_property
    def target_folder(self):
        """
        Folder the wheel is fetched into: {root}/wheel/{name}/{version}

        The name is normalized with PEP 503, like the dist-key of the pack
        files.

        Returns:
            PathStr: Absolute path of the folder
        """
        return self.root.joinpath(WHEEL_FOLDER).joinpath(normalize_name(self.name)).joinpath(self.version)

    @cached_property
    def index_data_file(self):
        """
        Cache file of the index data: {target_folder}/index_data.json

        The file holds the parse of the index page of an earlier fetch, see
        index_data.

        Returns:
            PathStr: Absolute path of the file
        """
        return self.target_folder.joinpath(INDEX_DATA_FILE)

    @cached_property
    def index_data(self):
        """
        The wheel information of the distribution, from the cache or the page.

        An existing index_data_file is loaded as it is and the page is not
        requested again: the cache is trusted like the wheel folder, delete
        the file to read the page again, e.g. after the mirror of the config
        was changed. A cache file that holds no index data is reported with a
        warning and the page is read again, the file is written again (an
        interrupted write, a file of another tool). Otherwise the index of
        the distribution is requested at {mirror}/{name}/, the links of the
        page are read as they are: the relative urls are resolved against the
        index url, the sha256 fragment of a link is read and the filename is
        parsed. A link that is not a wheel file is skipped, the wheel files
        of every version are parsed. The parse is written to the cache file
        for the later fetches with jsonfile.write_json, with an indent, so an
        operator can read it. The result is cached on the instance: the cache
        file or the page is read once per instance.

        Returns:
            list[WheelInfo]: One WheelInfo per wheel file the page links, in
                the order of the page

        Raises:
            WheelNotFoundError: If the mirror has no index of the
                distribution (a 404)
            httpx2.HTTPStatusError: If the index request fails with another
                status
        """
        file = self.index_data_file
        data = _read_index_data(file)
        if data is not None:
            logger.info(f'Index data exists, loaded: "{file}"')
            return data

        index_url = f'{self.mirror}/{normalize_name(self.name)}/'
        response = self._get_index(index_url)
        data = _parse_index_page(response.text, index_url)
        write_json(file, [asdict(wheel) for wheel in data])
        logger.info(f'Index data cached: "{index_url}" -> "{file}"')
        return data

    def fetch(self):
        """
        Fetch the wheel of the distribution version to the target folder.

        The fetch runs in two steps, see the module docstring: index_data is
        read from the cache file or the index page, then the wheel of the
        version is selected from it. The selected file is downloaded when it
        is not in the folder yet, and when the sha256 of the local file does
        not match the sha256 of its index link: a file that matches is kept
        as it is and no request is made. The bytes of a download are checked
        against the sha256 of the index link when the link carries one.

        Returns:
            PathStr: Path of the wheel file, target_folder/{filename}

        Raises:
            WheelNotFoundError: If the mirror has no index of the
                distribution, the index has no wheel of the version, or the
                version has no pure python wheel of python3
            WheelHashError: If the downloaded wheel does not match the sha256
                of the index link, no file is put at the final path
            httpx2.HTTPError: If a request fails
        """
        wheel = self._select_wheel()
        file = self.target_folder.joinpath(wheel.filename)
        if self._wheel_is_valid(file, wheel):
            logger.info(f'Wheel exists, skipped: "{file}"')
            return file
        logger.info(f'Download wheel: "{wheel.url}" -> "{file}"')
        self._download(wheel.url, file, wheel.sha256)
        return file

    def close(self):
        """
        Close the http client of this instance when it created one.

        An injected client is never closed: its lifetime belongs to the
        caller, see __init__. Closing is idempotent, `with WheelFetcher(...)
        as fetcher:` closes it on exit.
        """
        if self._own_client and self._client is not None:
            self._client.close()

    def __enter__(self):
        """
        Returns:
            WheelFetcher: The instance itself, for the with statement
        """
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """
        Close the client of this instance on exit, see close().
        """
        self.close()

    def _wheel_is_valid(self, file, wheel):
        """
        Whether the local file is the wheel the index data describes.

        The file is the one the index data names to fetch, the filename of
        the selected wheel: a file of another name is not this fetch, see
        fetch. A file that does not exist is not valid, it is downloaded.
        The sha256 of the file is checked against the sha256 of the index
        link when the link carries one: other bytes mean the local file is
        not the file the index describes, the file is downloaded again. A
        link without sha256 has nothing to check against, the local file is
        trusted: the mirror carries no hash, the first download is the
        authority (TOFU).

        Args:
            file (PathStr): Path of the wheel in the target folder
            wheel (WheelInfo): Wheel selected from the index data

        Returns:
            bool: True if the file is usable as it is, no download needed
        """
        if not wheel.sha256:
            return file.exists()
        try:
            return _sha256_file(file) == wheel.sha256
        except FileNotFoundError:
            return False

    def _select_wheel(self):
        """
        Select the pure python wheel of the version from the index data.

        A wheel of another version and a wheel with an environment constraint
        are skipped; when the version has several pure wheels (e.g. different
        python tags), the first by filename is selected.

        Returns:
            WheelInfo: The selected wheel

        Raises:
            WheelNotFoundError: If the index has no wheel of the version, or
                the version has no pure python wheel of python3
        """
        dist = normalize_name(self.name)
        index_url = f'{self.mirror}/{dist}/'
        wheels = [
            wheel for wheel in self.index_data
            if wheel.name == dist and wheel.version == self.version
        ]
        pure = [wheel for wheel in wheels if _is_pure_python3(wheel.tags)]
        if not pure:
            if wheels:
                files = ', '.join(sorted(wheel.filename for wheel in wheels))
                raise WheelNotFoundError(
                    f'No pure python wheel of "{self.name}=={self.version}" in "{index_url}", '
                    f'the version has: {files}')
            raise WheelNotFoundError(
                f'No wheel of "{self.name}=={self.version}" in "{index_url}"')
        return sorted(pure, key=lambda wheel: wheel.filename)[0]

    def _get_index(self, index_url):
        """
        Get the index page of the distribution from the mirror.

        Args:
            index_url (str): Url of the index page

        Returns:
            httpx2.Response: The index page

        Raises:
            WheelNotFoundError: If the mirror has no index of the
                distribution (a 404)
            httpx2.HTTPStatusError: If the request fails with another status
        """
        response = self._get_client().get(index_url, follow_redirects=True)
        try:
            response.raise_for_status()
        except httpx2.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise WheelNotFoundError(
                    f'No index of "{self.name}" in "{self.mirror}"') from None
            raise
        return response

    def _download(self, url, file, sha256):
        """
        Download a file, check the sha256 while streaming, write it atomically.

        Args:
            url (str): Url of the file
            file (PathStr): Path of the file to write
            sha256 (str): Expected sha256 hex, '' to skip the check

        Raises:
            WheelHashError: If the bytes do not match sha256, no file is put
                at the final path
            httpx2.HTTPError: If the request fails
        """
        with self._get_client().stream('GET', url, follow_redirects=True) as response:
            response.raise_for_status()
            file.atomic_write_stream(_iter_checked(response.iter_bytes(), sha256, url))

    def _get_client(self):
        """
        The http client of this instance: the injected one, or the client of
        the instance created on the first request.

        The client is reused by every request of the instance, so the
        keep-alive connections of a fetch are reused. Only a client created
        here is closed by close(); an injected one belongs to the caller.

        Returns:
            httpx2.Client: The client to send the request with
        """
        if self._client is None:
            self._client = httpx2.Client()
        return self._client
