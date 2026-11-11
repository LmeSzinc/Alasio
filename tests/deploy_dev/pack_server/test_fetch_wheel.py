"""
Tests for WheelFetcher: the wheels of one distribution version, fetched from a
PyPI simple index into the cache folder of the version.

The mirror is simulated with MockMirror, an httpx2.MockTransport handler like
the other tests of the deploy domain: the index pages and the wheel files are
registered in memory and answered by the transport of the client, no socket is
bound and no real mirror is requested. The wheel is written to the fake
filesystem (alasio.testing.filesystem).

The fetch itself (the parse of the page, the hash of the wheel, the retry of a
transfer) is the shared implementation of alasio.deploy.pip_download, its tests
are tests/deploy/pip_download: here the cache of the release side (the snapshot
of the page, the folder of the version, the reload of a page that does not
match its wheels) and the modes are tested.
"""
import hashlib
import json
from urllib.parse import urlsplit

import httpx2
import pytest
import trio

from alasio.deploy.pip_download.pypi_file import DOWNLOAD_MODE_ALL, DOWNLOAD_MODE_PY3_ANY
from alasio.deploy_dev.pack_server.fetch_wheel import (
    FETCH_CONCURRENT, INDEX_FILE, JSON_PAGE, WheelFetcher, WheelHashError, WheelNotFoundError, create_client
)
from alasio.deploy_dev.pack_server.parse_dep import normalize_name
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401

pytestmark = [
    pytest.mark.parallel,
]

# folder of the downloaded wheels in the workspace of the tests, see
# RepoBase.wheel_cache_folder
CACHE_FOLDER = '.wheel_cache'

# proxy url of the tests: nothing listens on the port, the client of the tests
# only reads the route it was built with
PROXY = 'http://127.0.0.1:7890'


def wheel_file(root, name, version, filename):
    """
    Path of a wheel in the cache folder of the tests.

    Returns:
        PathStr: {root}/.wheel_cache/{name}/{version}/{filename}
    """
    return root.joinpath(CACHE_FOLDER).joinpath(name).joinpath(version).joinpath(filename)


def index_file(root, name, version):
    """
    Path of the snapshot of the index page of a version in the tests.

    Returns:
        PathStr: {root}/.wheel_cache/{name}/{version}/index.html
    """
    return root.joinpath(CACHE_FOLDER).joinpath(name).joinpath(version).joinpath(INDEX_FILE)


def assert_log_unordered(capture, expected):
    """
    Assert the lines of a captured log, the order of the lines ignored.

    The wheels of a version are fetched at once (WheelFetcher.fetch_async), so
    the lines of two transfers of the same version interleave in the order the
    files answer: the multiset of the lines is the contract of a run, the order
    of two concurrent transfers is not. Every line is still matched exactly
    once, so a missing, an added or an edited line fails the assertion like it
    does against concat().

    Args:
        capture (CaptureWriter): The capture of logger.mock_capture_writer()
        expected (str): The expected log, one line each
    """
    assert sorted(capture.concat().splitlines()) == sorted(expected.splitlines())


def fetch_all(fetcher, mode):
    """
    Run the async fetch of a fetcher, the blocking entry of the tests.

    Args:
        fetcher (WheelFetcher): Fetcher to run
        mode (str): Mode of the fetch, see WheelFetcher.fetch_async()

    Returns:
        list[WheelInfo]: The wheels, each with the digests of its bytes
    """
    return trio.run(fetcher.fetch_async, mode)


def fetch(fetcher):
    """
    Run the fetch of the pure python wheel of a version, the pack build mode.

    Args:
        fetcher (WheelFetcher): Fetcher to run

    Returns:
        PathStr: Path of the fetched wheel
    """
    files = fetch_all(fetcher, DOWNLOAD_MODE_PY3_ANY)
    assert len(files) == 1
    return files[0].file


def page_link(filename, digest):
    """
    One link of a PEP 503 index page of the mock mirror.

    The link is relative, like the links of a real mirror page; it carries
    the sha256 fragment of the file when the test registered one.

    Args:
        filename (str): Filename of the file
        digest (str): sha256 hex of the fragment, '' for a link without one

    Returns:
        str: The link
    """
    fragment = f'#sha256={digest}' if digest else ''
    return f'<a href="../../packages/{filename}{fragment}">{filename}</a><br/>\n'


def base_path(base):
    """
    The path prefix of the index of a base url.

    Args:
        base (str): Base url of a mirror, e.g. 'http://mirror' or
            'http://mirror/simple'

    Returns:
        str: Path prefix of the index, e.g. '' or '/simple'
    """
    path = base.partition('://')[2].partition('/')[2].strip('/')
    return f'/{path}' if path else ''


class MockMirror:
    """
    In-memory PyPI simple mirror, answered by an httpx2.MockTransport handler.

    The handler of the instance answers the requests of the client of the
    mirror: the index page of `{base}/{dist-key}/` and the files of
    `{base}/packages/{filename}`, under every base the mirror was given, see
    alias(). The page of a distribution is built from the registered files:
    every link is relative and carries the sha256 fragment like the page of a
    real mirror, so the client resolves the url against the index url; a file
    that was registered without a digest is linked without a fragment. A
    distribution or a file that is not registered is a 404; a page a test
    writes itself is served as it is, with the content type it was given.

    Attributes:
        base (str): Base url of the mirror. Defaults to 'http://mirror'
        bases (dict[str, str]): {base url: path prefix of its index}, the
            registry is served at every base, see alias()
        files (dict[str, dict[str, tuple[bytes, str]]]): {dist key:
            {filename: (content, sha256)}}
        pages (dict[str, tuple[bytes, str]]): {dist key: (body, content
            type)} of the pages the test writes itself, see serve_page()
        requests (list[str]): Urls of the requests made by the clients, in
            order, for the assertions
        client (httpx2.AsyncClient): The client of the mirror, the code under
            test sends its requests with it
    """

    def __init__(self, base='http://mirror'):
        """
        Args:
            base (str): Base url of the mirror. Defaults to 'http://mirror'
        """
        self.base = base
        # {base url: path prefix of its index}, one entry per base the
        # registry is served at
        self.bases = {base: base_path(base)}
        # {dist key: {filename: (content, sha256)}}
        self.files = {}
        # {dist key: (body, content type)} of the pages the test writes, the
        # other pages are built from the registry
        self.pages = {}
        # urls of the requests made by the clients, for the assertions
        self.requests = []
        self.client = httpx2.AsyncClient(transport=httpx2.MockTransport(self.handler))

    def alias(self, base):
        """
        Serve the index of the mirror at a second base url.

        The registry is the same, only the path of the index page changes: a
        test that declares a provided index of a mod serves the files it
        registered here at that base too, and the urls of the requests tell
        which index the code under test fetched from.

        Args:
            base (str): Base url to serve the index at, e.g.
                'http://provided.example/simple'

        Returns:
            str: The base url
        """
        self.bases[base] = base_path(base)
        return base

    def register(self, name, version, tags='py3-none-any', filename='', content=b'', digest=None):
        """
        Register a file under the index of a distribution.

        Args:
            name (str): Distribution name as the publisher writes it
            version (str): Version of the file
            tags (str): Trailing tags of the filename. Defaults to
                'py3-none-any'
            filename (str): Filename of the file. Defaults to '', a wheel
                filename built from the arguments
            content (bytes): Content of the file. Defaults to b'', a content
                generated from the filename
            digest (str | None): sha256 hex of the link fragment. Defaults to
                None, the sha256 of the content; pass '' to link the file
                without a sha256 fragment, like a mirror that carries no hash

        Returns:
            bytes: Content of the file
        """
        if not filename:
            filename = f'{name.replace("-", "_")}-{version}-{tags}.whl'
        if not content:
            content = f'{filename} content'.encode('utf-8')
        if digest is None:
            digest = hashlib.sha256(content).hexdigest()
        self.files.setdefault(normalize_name(name), {})[filename] = (content, digest)
        return content

    def handler(self, request):
        """
        Answer one request of the client of the mirror.

        Args:
            request (httpx2.Request): The request

        Returns:
            httpx2.Response: The page or the file, a 404 for an unknown path
        """
        url = str(request.url)
        self.requests.append(url)
        rest = self._rest_of(urlsplit(url).path)
        dist_key, sep, name = rest.partition('/')
        if not sep:
            return httpx2.Response(404)
        if not name:
            page = self._page_of(dist_key)
            if page is None:
                return httpx2.Response(404)
            body, content_type = page
            headers = {'content-type': content_type} if content_type else {}
            return httpx2.Response(200, content=body, headers=headers)
        content = self._file_of(name)
        if content is None:
            return httpx2.Response(404)
        if request.method == 'HEAD':
            # the headers of the file and no body, the answer of a probe
            return httpx2.Response(200, headers={'content-length': str(len(content))})
        return httpx2.Response(200, content=content)

    def page(self, name):
        """
        The bytes of the index page of a distribution, as the mirror serves it.

        Args:
            name (str): Distribution name as the publisher writes it

        Returns:
            bytes: The page
        """
        return self._page_of(normalize_name(name))[0]

    def serve_page(self, name, body, content_type=''):
        """
        Serve the page of a distribution with a body the test writes itself.

        The files of the distribution stay registered: a page that lists a file
        the registry serves, or lists none, are the two sides of a page the
        index changed between two runs.

        Args:
            name (str): Distribution name as the publisher writes it
            body (bytes): Body of the page
            content_type (str): Content type of the page. Defaults to '', an
                HTML page
        """
        self.pages[normalize_name(name)] = (body, content_type)

    def serve_json(self, name):
        """
        Serve the page of a distribution as a PEP 691 JSON page.

        The body is the `files` list of the registry, with the relative urls
        the page of a mirror carries, and it is served with the content type a
        client tells the JSON page by.

        Args:
            name (str): Distribution name as the publisher writes it
        """
        key = normalize_name(name)
        self.serve_page(name, self._json_page(self.files[key]), JSON_PAGE)

    def _rest_of(self, path):
        """
        The path below the index of a base, e.g. 'httpx/' or 'packages/x.whl'.

        The longest base path wins: a mirror may serve its index at the root
        and below a path at the same time, see alias().

        Args:
            path (str): Path of the request, e.g. '/simple/httpx/'

        Returns:
            str: The path below the base, '' for the root of the mirror
        """
        prefixes = sorted(dict.fromkeys(self.bases.values()), key=len, reverse=True)
        for prefix in prefixes:
            if prefix and path.startswith(f'{prefix}/'):
                return path[len(prefix) + 1:]
        return path.lstrip('/')

    def _page_of(self, dist_key):
        """
        The body and the content type of the page of a distribution.

        Args:
            dist_key (str): Normalized name of the distribution

        Returns:
            tuple[bytes, str] | None: The page and its content type, '' for
                none, None when the mirror has no such distribution
        """
        page = self.pages.get(dist_key)
        if page is not None:
            return page
        dist = self.files.get(dist_key)
        if dist is None:
            return None
        return self._html_page(dist), ''

    def _file_of(self, filename):
        """
        The content of a file of the packages folder of the mirror.

        Args:
            filename (str): Filename of the file

        Returns:
            bytes | None: The content, None when no distribution registers it
        """
        for dist in self.files.values():
            row = dist.get(filename)
            if row is not None:
                return row[0]
        return None

    @staticmethod
    def _html_page(dist):
        """
        The index of a distribution as a PEP 503 page: a relative link per file.

        Args:
            dist (dict[str, tuple[bytes, str]]): {filename: (content, sha256)}

        Returns:
            bytes: The page
        """
        links = ''.join(
            page_link(filename, digest)
            for filename, (_, digest) in sorted(dist.items())
        )
        return f'<!DOCTYPE html><html>{links}</html>'.encode('utf-8')

    @staticmethod
    def _json_page(dist):
        """
        The index of a distribution as a PEP 691 JSON page.

        Args:
            dist (dict[str, tuple[bytes, str]]): {filename: (content, sha256)}

        Returns:
            bytes: The page
        """
        files = [
            {
                'filename': filename,
                'url': f'../../packages/{filename}',
                'hashes': {'sha256': digest} if digest else {},
            }
            for filename, (_, digest) in sorted(dist.items())
        ]
        body = {'meta': {'api-version': '1.0'}, 'files': files}
        return json.dumps(body).encode('utf-8')


@pytest.fixture
def root(fs, monkeypatch):
    """
    Folder of a repo of the tests, in the fake filesystem.

    The root the fetcher writes its cache into: the folder of the
    repo, {run directory}/pack/Author_Repo_master.

    Returns:
        PathStr: Absolute path of the folder
    """
    run = PathStr.new(fs.root_dir.path).joinpath('pack_server')
    root = run.joinpath('pack').joinpath('Author_Repo_master')
    fs.create_dir(root)
    monkeypatch.setattr(env, 'PROJECT_ROOT', run)
    return root


@pytest.fixture
def mirror():
    """
    The mock mirror of the tests.

    Returns:
        MockMirror:
    """
    return MockMirror()


@pytest.fixture
def make_fetcher(root, mirror):
    """
    Factory of a WheelFetcher of the mock mirror, the client of the mock is
    injected.

    Returns:
        Callable[[str, str], WheelFetcher]: (name, version) -> fetcher
    """
    def create(name, version):
        folder = root.joinpath(CACHE_FOLDER).joinpath(name).joinpath(version)
        return WheelFetcher(folder, mirror.base, name, version, client=mirror.client)
    return create


class TestWheelFetcher:
    """Fetch the wheel of a version, from the index and the files of the mock mirror."""

    def test_fetch(self, make_fetcher, mirror, root):
        """The wheel of the version is downloaded to the wheel folder."""
        # the index also lists another version: only the asked one is fetched
        mirror.register('httpx', '0.28.0')
        mirror.register('httpx', '0.28.1')
        file = fetch(make_fetcher('httpx', '0.28.1'))
        assert file == wheel_file(root, 'httpx', '0.28.1', 'httpx-0.28.1-py3-none-any.whl')
        assert file.atomic_read_bytes() == b'httpx-0.28.1-py3-none-any.whl content'
        # the relative links of the index are resolved against the index url,
        # the fragment of the link carries the sha256 the bytes are checked
        # against while streaming
        assert mirror.requests == [
            'http://mirror/httpx/',
            'http://mirror/packages/httpx-0.28.1-py3-none-any.whl',
        ]

    def test_name_normalized(self, make_fetcher, mirror, root):
        """The index url is built from the normalized name, the folder is the caller's."""
        mirror.register('PyYAML', '6.0.1')
        file = fetch(make_fetcher('PyYAML', '6.0.1'))
        # the folder is the one the caller gave, the name the sample
        # normalized before the fetch, see RepoWheel
        assert file == wheel_file(root, 'PyYAML', '6.0.1', 'PyYAML-6.0.1-py3-none-any.whl')
        assert mirror.requests == [
            'http://mirror/pyyaml/',
            'http://mirror/packages/PyYAML-6.0.1-py3-none-any.whl',
        ]

    def test_name_with_separators(self, make_fetcher, mirror, root):
        """A name with separators matches the escaped name part of the filename."""
        mirror.register('opencv-python-headless', '4.10.0.84')
        file = fetch(make_fetcher('opencv-python-headless', '4.10.0.84'))
        assert file == wheel_file(
            root, 'opencv-python-headless', '4.10.0.84',
            'opencv_python_headless-4.10.0.84-py3-none-any.whl')

    def test_skips_existing(self, fs, make_fetcher, mirror, root):
        """A wheel with the sha256 of the index is kept, the file is not downloaded."""
        filename = 'httpx-0.28.1-py3-none-any.whl'
        content = b'existing wheel content'
        # the index links the content of the local file, its sha256 matches
        mirror.register('httpx', '0.28.1', content=content)
        fs.create_file(wheel_file(root, 'httpx', '0.28.1', filename), contents=content)
        file = fetch(make_fetcher('httpx', '0.28.1'))
        assert file == wheel_file(root, 'httpx', '0.28.1', filename)
        assert file.atomic_read_bytes() == content
        # the index page is requested, the wheel itself is kept
        assert mirror.requests == ['http://mirror/httpx/']

    def test_existing_wheel_hash_mismatch(self, fs, make_fetcher, mirror, root):
        """A local wheel that does not match the sha256 of the index is downloaded again."""
        filename = 'httpx-0.28.1-py3-none-any.whl'
        content = mirror.register('httpx', '0.28.1')
        # the local file is not the file the index describes
        fs.create_file(wheel_file(root, 'httpx', '0.28.1', filename), contents=b'broken wheel')
        file = fetch(make_fetcher('httpx', '0.28.1'))
        assert file == wheel_file(root, 'httpx', '0.28.1', filename)
        assert file.atomic_read_bytes() == content
        assert mirror.requests == [
            'http://mirror/httpx/',
            'http://mirror/packages/httpx-0.28.1-py3-none-any.whl',
        ]

    def test_pure_wheel_selected(self, make_fetcher, mirror):
        """The pure wheel is selected when the version also has platform wheels."""
        mirror.register('psutil', '5.9.8', filename='psutil-5.9.8-cp38-cp38-win_amd64.whl')
        mirror.register('psutil', '5.9.8')
        file = fetch(make_fetcher('psutil', '5.9.8'))
        assert file.name == 'psutil-5.9.8-py3-none-any.whl'
        assert mirror.requests[-1] == 'http://mirror/packages/psutil-5.9.8-py3-none-any.whl'

    def test_every_wheel_of_the_version(self, make_fetcher, mirror):
        """The mode of the pip index fetches every wheel, each with its digest."""
        pure = mirror.register('msgspec', '0.22.0')
        compiled = mirror.register('msgspec', '0.22.0', tags='cp38-cp38-win_amd64')
        wheels = fetch_all(make_fetcher('msgspec', '0.22.0'), DOWNLOAD_MODE_ALL)
        # the answer is sorted by filename, the order of a page of a name
        assert [wheel.file.name for wheel in wheels] == [
            'msgspec-0.22.0-cp38-cp38-win_amd64.whl',
            'msgspec-0.22.0-py3-none-any.whl',
        ]
        assert [wheel.hashes['sha256'] for wheel in wheels] == [
            hashlib.sha256(compiled).hexdigest(),
            hashlib.sha256(pure).hexdigest(),
        ]

    def test_compiled_only_wheel_not_fetched(self, make_fetcher, mirror):
        """A version with no pure python wheel is not a target of the pack build."""
        mirror.register('psutil', '5.9.8', filename='psutil-5.9.8-cp38-cp38-win_amd64.whl')
        mirror.register('psutil', '5.9.8', filename='psutil-5.9.8.tar.gz')
        with pytest.raises(WheelNotFoundError, match='No wheel of "psutil==5.9.8" matches "py3-any"'):
            fetch(make_fetcher('psutil', '5.9.8'))

    def test_py2_only_wheel(self, make_fetcher, mirror):
        """A py2 only pure wheel is not fetchable: the pack build runs on python3."""
        mirror.register('foo', '1.0', filename='foo-1.0-py2-none-any.whl')
        with pytest.raises(WheelNotFoundError, match='No wheel of "foo==1.0" matches "py3-any"'):
            fetch(make_fetcher('foo', '1.0'))

    def test_no_index(self, make_fetcher, root):
        """A distribution the mirror has no index of is refused, no snapshot is left."""
        with pytest.raises(WheelNotFoundError) as e:
            fetch(make_fetcher('httpx', '0.28.1'))
        assert str(e.value) == 'No index of "httpx" in "http://mirror"'
        assert not index_file(root, 'httpx', '0.28.1').exists()

    def test_no_wheel(self, make_fetcher, mirror):
        """A version the index has no wheel of is refused."""
        mirror.register('httpx', '0.28.0')
        with pytest.raises(WheelNotFoundError) as e:
            fetch(make_fetcher('httpx', '0.28.1'))
        assert str(e.value) == 'No wheel of "httpx==0.28.1" in the index'

    def test_hash_mismatch(self, make_fetcher, mirror, root):
        """A download that does not match the sha256 of the index is refused."""
        mirror.register('httpx', '0.28.1', digest='0' * 64)
        file = wheel_file(root, 'httpx', '0.28.1', 'httpx-0.28.1-py3-none-any.whl')
        url = 'http://mirror/packages/httpx-0.28.1-py3-none-any.whl'
        digest = hashlib.sha256(b'httpx-0.28.1-py3-none-any.whl content').hexdigest()
        mismatch = (
            f'Request to "{url}" failed (attempt 1/3): WheelHashError: Hash of "{url}" '
            f'does not match the index: {digest} != {"0" * 64}')
        with logger.mock_capture_writer() as capture:
            with pytest.raises(WheelHashError, match='does not match the index'):
                fetch(make_fetcher('httpx', '0.28.1'))
        assert capture.concat() == f"""\
INFO | Download index page: "{mirror.base}/httpx/"
INFO | Download wheel: "{url}" -> "{file}"
WARNING | {mismatch}
WARNING | The wheels do not match the index snapshot, downloaded again: "{index_file(root, 'httpx', '0.28.1')}"
INFO | Download index page: "{mirror.base}/httpx/"
INFO | Download wheel: "{url}" -> "{file}"
WARNING | {mismatch}
"""
        assert not file.exists()
        # the page and the wheel, and the page downloaded again with the second
        # round that fails on the same wrong hash: the reload is spent
        assert mirror.requests == [
            'http://mirror/httpx/',
            'http://mirror/packages/httpx-0.28.1-py3-none-any.whl',
            'http://mirror/httpx/',
            'http://mirror/packages/httpx-0.28.1-py3-none-any.whl',
        ]

    def test_sha256_from_the_link(self, make_fetcher, mirror):
        """The digest of a fetched wheel is the one its index link carries."""
        content = mirror.register('httpx', '0.28.1')
        wheel = fetch_all(make_fetcher('httpx', '0.28.1'), DOWNLOAD_MODE_PY3_ANY)[0]
        assert wheel.hashes['sha256'] == hashlib.sha256(content).hexdigest()

    def test_sha256_of_a_link_without_hash(self, make_fetcher, mirror):
        """A link without a hash: the digest is computed from the local bytes."""
        content = mirror.register('httpx', '0.28.1', digest='')
        wheel = fetch_all(make_fetcher('httpx', '0.28.1'), DOWNLOAD_MODE_PY3_ANY)[0]
        assert wheel.hashes['sha256'] == hashlib.sha256(content).hexdigest()

    def test_invalid_mode(self, make_fetcher, mirror):
        """A mode the fetcher does not know fails before any wheel is downloaded."""
        mirror.register('httpx', '0.28.1')
        with pytest.raises(ValueError) as e:
            fetch_all(make_fetcher('httpx', '0.28.1'), 'everything')
        assert str(e.value) == (
            "Invalid download mode: 'everything', expected one of ('current', 'py3-any', 'all')")
        # the page is what the selection reads: it is requested, the wheel is not
        assert mirror.requests == ['http://mirror/httpx/']

    def test_aclose_of_an_unused_fetcher(self, root):
        """A fetcher that made no request closes the client it never created."""
        folder = root.joinpath(CACHE_FOLDER).joinpath('httpx').joinpath('0.28.1')
        fetcher = WheelFetcher(folder, 'http://mirror', 'httpx', '0.28.1')
        assert trio.run(fetcher.aclose) is None

    def test_injected_client_is_not_closed(self, make_fetcher, mirror):
        """An injected client belongs to the caller: aclose() does not close it."""
        mirror.register('httpx', '0.28.1')
        mirror.register('httpx', '0.28.2')
        fetcher = make_fetcher('httpx', '0.28.1')
        trio.run(fetcher.fetch_async, DOWNLOAD_MODE_PY3_ANY)
        trio.run(fetcher.aclose)
        # the client of the caller is still usable by a later fetch
        assert fetch(make_fetcher('httpx', '0.28.2')).exists()

    def test_modes_share_one_page(self, make_fetcher, mirror):
        """Both modes of one fetcher read the page once and download each wheel once."""
        mirror.register('httpx', '0.28.1')
        mirror.register('httpx', '0.28.1', tags='cp38-cp38-win_amd64')
        fetcher = make_fetcher('httpx', '0.28.1')
        pure = fetch_all(fetcher, DOWNLOAD_MODE_PY3_ANY)
        every = fetch_all(fetcher, DOWNLOAD_MODE_ALL)
        assert [wheel.file.name for wheel in pure] == ['httpx-0.28.1-py3-none-any.whl']
        assert [wheel.file.name for wheel in every] == [
            'httpx-0.28.1-cp38-cp38-win_amd64.whl',
            'httpx-0.28.1-py3-none-any.whl',
        ]
        # the page once, the pure wheel of the first mode once, the compiled
        # wheel of the second one: the pure wheel is not downloaded again
        assert mirror.requests == [
            'http://mirror/httpx/',
            'http://mirror/packages/httpx-0.28.1-py3-none-any.whl',
            'http://mirror/packages/httpx-0.28.1-cp38-cp38-win_amd64.whl',
        ]


class TestProperties:
    """The folders, the url and the index text of a fetcher."""

    def test_index_file(self, root):
        """The snapshot is index.html in the folder the caller gave."""
        folder = root.joinpath(CACHE_FOLDER).joinpath('httpx').joinpath('0.28.1')
        fetcher = WheelFetcher(folder, 'http://mirror', 'httpx', '0.28.1')
        assert fetcher.index_file == folder.joinpath('index.html')

    def test_page_url(self, make_fetcher):
        """The index url is built from the PEP 503 normalized name."""
        fetcher = make_fetcher('OpenCV-Python-Headless', '4.10.0.84')
        assert fetcher.page_url == 'http://mirror/opencv-python-headless/'

    def test_page_url_of_a_group(self, root):
        """The snapshot is parsed against the first index of the mirror set.

        The release side gives one index per pin (the source of the pin), so a
        set of mirrors is a list of the same index: the first one is the base
        of the relative links of a snapshot of an earlier run.
        """
        folder = root.joinpath(CACHE_FOLDER).joinpath('httpx').joinpath('0.28.1')
        mirrors = {'cn': {'a': 'http://a.example/simple', 'b': 'http://b.example/simple'}}
        fetcher = WheelFetcher(folder, mirrors, 'httpx', '0.28.1')
        assert fetcher.page_url == 'http://a.example/simple/httpx/'

    def test_mirrors_text(self, make_fetcher):
        """A set of one index prints that index alone."""
        assert make_fetcher('httpx', '0.28.1').mirrors_text == 'http://mirror'

    def test_mirrors_text_of_a_group(self, root):
        """The index set prints its urls in the declared order, each one once."""
        folder = root.joinpath(CACHE_FOLDER).joinpath('httpx').joinpath('0.28.1')
        mirrors = {
            'cn': {'a': 'http://a.example/simple', 'b': 'http://b.example/simple'},
            'global': {'mirror': 'http://a.example/simple'},
        }
        fetcher = WheelFetcher(folder, mirrors, 'httpx', '0.28.1')
        assert fetcher.mirrors_text == 'http://a.example/simple, http://b.example/simple'


class TestPageUrlShape:
    """
    The shape of the page url: the directory of the distribution, never a file.

    PEP 503 names the page of a distribution `{root}/{name}/`, and pip builds
    every index request that way (`mkurl_pypi_url()` in pip joins the name and
    appends a trailing slash). A server that only answers an explicit
    `index.html` is not a pip index at all: pypi.org answers 404 for
    `/simple/{name}/index.html`, and a mirror that does answer it may answer
    with something else entirely (a global index of every distribution).

    So the url is a directory, and no request of the flow appends `index.html`
    to it: `INDEX_FILE` is the name of the local snapshot only.
    """

    @pytest.mark.parametrize('name', [
        'httpx',
        'PyYAML',
        'opencv-python-headless',
        'ruamel.yaml',
        'zope__interface',
    ])
    def test_url_is_a_directory(self, root, name):
        folder = root.joinpath(CACHE_FOLDER).joinpath('a').joinpath('1')
        url = WheelFetcher(folder, 'http://mirror/simple', name, '1').page_url
        assert url.startswith('http://mirror/simple/')
        assert url.endswith('/')
        assert 'index.html' not in url
        # the name of the page is one path segment: a name that escapes it
        # would ask another distribution of the mirror
        tail = url[len('http://mirror/simple/'):].rstrip('/')
        assert '/' not in tail
        assert tail == tail.lower()

    def test_snapshot_name_is_not_the_url(self, make_fetcher):
        """The snapshot file is index.html, the url it was served at is not."""
        fetcher = make_fetcher('httpx', '0.28.1')
        assert fetcher.index_file.name == 'index.html'
        assert not fetcher.page_url.endswith('index.html')

    def test_one_request_to_the_directory(self, make_fetcher, mirror):
        """The page of a version costs one request, at the directory form."""
        mirror.register('httpx', '0.28.1')
        fetch(make_fetcher('httpx', '0.28.1'))
        page_requests = [url for url in mirror.requests if not url.endswith('.whl')]
        assert page_requests == ['http://mirror/httpx/']

    def test_no_request_to_an_index_html(self, make_fetcher, mirror):
        """Nothing of the flow asks a mirror for an index.html."""
        mirror.register('httpx', '0.28.1')
        mirror.register('httpx', '0.28.1', tags='cp38-cp38-win_amd64')
        fetcher = make_fetcher('httpx', '0.28.1')
        # both modes read the page and fetch the wheels, the page once
        fetch_all(fetcher, DOWNLOAD_MODE_PY3_ANY)
        fetch_all(fetcher, DOWNLOAD_MODE_ALL)
        assert not [url for url in mirror.requests if 'index.html' in url]

    def test_page_url_of_a_mirror_group(self, root):
        """Every mirror of a set is asked at the directory form, the first is the base."""
        folder = root.joinpath(CACHE_FOLDER).joinpath('httpx').joinpath('0.28.1')
        mirrors = {'cn': {'a': 'http://a.example/simple', 'b': 'http://b.example/simple'}}
        fetcher = WheelFetcher(folder, mirrors, 'httpx', '0.28.1')
        for url in fetcher.mirrors.urls.values():
            assert f'{url}/httpx/'.endswith('/')
            assert 'index.html' not in url


class TestIndexPage:
    """The snapshot of the index page of a version, the slowest request of the flow."""

    def test_snapshot_written(self, make_fetcher, mirror, root):
        """The page is kept as the index served it, byte for byte."""
        mirror.register('httpx', '0.28.1')
        fetch(make_fetcher('httpx', '0.28.1'))
        assert index_file(root, 'httpx', '0.28.1').atomic_read_bytes() == mirror.page('httpx')

    def test_snapshot_used(self, fs, make_fetcher, mirror, root):
        """A run over the snapshot makes no request at all."""
        mirror.register('httpx', '0.28.1')
        fetch(make_fetcher('httpx', '0.28.1'))
        requests = list(mirror.requests)

        file = fetch(make_fetcher('httpx', '0.28.1'))
        assert file == wheel_file(root, 'httpx', '0.28.1', 'httpx-0.28.1-py3-none-any.whl')
        # neither the page nor the wheel: the snapshot and the local file are
        # the cache of the earlier run
        assert mirror.requests == requests

    def test_snapshot_answers_a_missing_wheel_alone(self, fs, make_fetcher, mirror, root):
        """A wheel deleted from the cache is downloaded, the page is not requested."""
        mirror.register('httpx', '0.28.1')
        fetch(make_fetcher('httpx', '0.28.1'))
        wheel_file(root, 'httpx', '0.28.1', 'httpx-0.28.1-py3-none-any.whl').atomic_remove()
        requests = list(mirror.requests)

        file = fetch(make_fetcher('httpx', '0.28.1'))

        assert file.atomic_read_bytes() == b'httpx-0.28.1-py3-none-any.whl content'
        # the page of the distribution is the slowest request of the flow: the
        # snapshot answers it, only the wheel is transferred
        assert mirror.requests[len(requests):] == [
            'http://mirror/packages/httpx-0.28.1-py3-none-any.whl']

    def test_each_version_keeps_its_own_snapshot(self, make_fetcher, mirror, root):
        """The snapshot lives in the folder of the version, not of the distribution."""
        mirror.register('httpx', '0.27.8')
        mirror.register('httpx', '0.28.1')
        fetch(make_fetcher('httpx', '0.28.1'))
        assert index_file(root, 'httpx', '0.28.1').isfile()
        assert not index_file(root, 'httpx', '0.27.8').exists()

    def test_snapshot_is_the_source(self, fs, make_fetcher, mirror, root):
        """An edited snapshot is read as it is, the page is not requested again."""
        mirror.register('httpx', '0.28.1')
        fetch(make_fetcher('httpx', '0.28.1'))
        requests = list(mirror.requests)
        # the snapshot of the tests lists no file at all
        index_file(root, 'httpx', '0.28.1').atomic_write(b'<!DOCTYPE html><html></html>')

        with pytest.raises(WheelNotFoundError, match='No wheel of "httpx==0.28.1" in the index'):
            fetch(make_fetcher('httpx', '0.28.1'))
        assert mirror.requests == requests

    def test_broken_snapshot_downloaded_again(self, fs, make_fetcher, mirror, root):
        """A snapshot that does not parse is a broken cache: the page is requested."""
        mirror.register('httpx', '0.28.1')
        fs.create_file(index_file(root, 'httpx', '0.28.1'), contents=b'{not json')
        with logger.mock_capture_writer() as capture:
            file = fetch(make_fetcher('httpx', '0.28.1'))
        assert capture.concat() == f"""\
WARNING | The index snapshot does not parse, downloaded again: "{index_file(root, 'httpx', '0.28.1')}", Expecting property name enclosed in double quotes: line 1 column 2 (char 1)
INFO | Download index page: "{mirror.base}/httpx/"
INFO | Download wheel: "http://mirror/packages/httpx-0.28.1-py3-none-any.whl" -> "{file}"
"""

        assert file.atomic_read_bytes() == b'httpx-0.28.1-py3-none-any.whl content'
        assert mirror.requests == [
            'http://mirror/httpx/',
            'http://mirror/packages/httpx-0.28.1-py3-none-any.whl',
        ]
        # the broken snapshot is replaced by the page the index serves
        assert index_file(root, 'httpx', '0.28.1').atomic_read_bytes() == mirror.page('httpx')

    def test_json_snapshot(self, fs, make_fetcher, mirror, root):
        """A JSON snapshot is told from an HTML one by the first character of its body."""
        content = b'json wheel content'
        mirror.register('httpx', '0.28.1', content=content)
        body = json.dumps({
            'meta': {'api-version': '1.0'},
            'name': 'httpx',
            'files': [{
                'filename': 'httpx-0.28.1-py3-none-any.whl',
                'url': '../../packages/httpx-0.28.1-py3-none-any.whl',
                'hashes': {'sha256': hashlib.sha256(content).hexdigest()},
            }],
        }).encode('utf-8')
        # the body of a JSON page is kept as it was served, the content type is
        # not: the snapshot of the mock starts with a blank line, the body of
        # the page follows
        fs.create_file(index_file(root, 'httpx', '0.28.1'), contents=b'\n' + body)

        file = fetch(make_fetcher('httpx', '0.28.1'))

        assert file.atomic_read_bytes() == content
        # the snapshot is the page, only its wheel is transferred
        assert mirror.requests == [
            'http://mirror/packages/httpx-0.28.1-py3-none-any.whl']

    def test_json_page(self, make_fetcher, mirror, root):
        """A page the index serves as JSON is read by its files list, and snapshotted."""
        content = mirror.register('httpx', '0.28.1')
        mirror.serve_json('httpx')
        # the page of the mirror is a JSON page, the fetcher is told so by the
        # content type of the response
        assert mirror.page('httpx').startswith(b'{')

        file = fetch(make_fetcher('httpx', '0.28.1'))

        assert file.atomic_read_bytes() == content
        assert index_file(root, 'httpx', '0.28.1').atomic_read_bytes() == mirror.page('httpx')
        # a second run is answered by the JSON snapshot and the wheel alone
        requests = list(mirror.requests)
        fetch(make_fetcher('httpx', '0.28.1'))
        assert mirror.requests == requests

    def test_page_that_does_not_parse(self, make_fetcher, mirror, root):
        """A page the index serves that does not parse fails the fetch, nothing is cached."""
        mirror.register('httpx', '0.28.1')
        mirror.serve_page('httpx', b'{"files": [', JSON_PAGE)
        with pytest.raises(WheelNotFoundError) as e:
            fetch(make_fetcher('httpx', '0.28.1'))
        assert str(e.value).startswith('The index page of "http://mirror/httpx/" does not parse: ')
        assert not index_file(root, 'httpx', '0.28.1').exists()

    def test_error_status(self, root):
        """A status that is not a 404 is the error of the fetch, not a missing index."""
        folder = root.joinpath(CACHE_FOLDER).joinpath('httpx').joinpath('0.28.1')
        # every request is answered with a 500: a mirror that fails as a whole
        transport = httpx2.MockTransport(lambda request: httpx2.Response(500))
        client = httpx2.AsyncClient(transport=transport)
        fetcher = WheelFetcher(folder, 'http://mirror', 'httpx', '0.28.1', client=client)
        with pytest.raises(httpx2.HTTPStatusError) as e:
            fetch(fetcher)
        assert e.value.response.status_code == 500

    def test_page_downloaded_again_when_a_wheel_mismatches(self, fs, make_fetcher, mirror, root):
        """A file replaced under the same name is fetched from the page of the index."""
        filename = 'httpx-0.28.1-py3-none-any.whl'
        mirror.register('httpx', '0.28.1')
        fetch(make_fetcher('httpx', '0.28.1'))
        # the index replaces the file under the same name, the local copy is
        # neither the old file nor the new one (a half written leftover)
        mirror.register('httpx', '0.28.1', content=b'replaced wheel')
        wheel_file(root, 'httpx', '0.28.1', filename).atomic_write(b'broken wheel')

        with logger.mock_capture_writer() as capture:
            file = fetch(make_fetcher('httpx', '0.28.1'))
        url = 'http://mirror/packages/httpx-0.28.1-py3-none-any.whl'
        assert capture.concat() == f"""\
INFO | Download wheel: "{url}" -> "{file}"
WARNING | Request to "{url}" failed (attempt 1/3): WheelHashError: Hash of "{url}" does not match the index: {hashlib.sha256(b'replaced wheel').hexdigest()} != {hashlib.sha256(b'httpx-0.28.1-py3-none-any.whl content').hexdigest()}
WARNING | The wheels do not match the index snapshot, downloaded again: "{index_file(root, 'httpx', '0.28.1')}"
INFO | Download index page: "{mirror.base}/httpx/"
INFO | Download wheel: "{url}" -> "{file}"
"""

        assert file.atomic_read_bytes() == b'replaced wheel'
        # the page is downloaded again, and the wheel with it: the url of the
        # old page carries the old hash, the new file does not match it
        assert mirror.requests.count('http://mirror/httpx/') == 2
        assert mirror.requests[-1] == f'http://mirror/packages/{filename}'
        assert index_file(root, 'httpx', '0.28.1').atomic_read_bytes() == mirror.page('httpx')

    def test_page_downloaded_once(self, make_fetcher, mirror, root):
        """The page of a fetch is downloaded again once, however many wheels fail."""
        mirror.register('httpx', '0.28.1', digest='0' * 64)
        mirror.register('httpx', '0.28.1', tags='cp38-cp38-win_amd64', digest='0' * 64)
        pure_url = 'http://mirror/packages/httpx-0.28.1-py3-none-any.whl'
        pure_file = wheel_file(root, 'httpx', '0.28.1', 'httpx-0.28.1-py3-none-any.whl')
        compiled_url = 'http://mirror/packages/httpx-0.28.1-cp38-cp38-win_amd64.whl'
        compiled_file = wheel_file(root, 'httpx', '0.28.1', 'httpx-0.28.1-cp38-cp38-win_amd64.whl')

        def mismatch(url, content):
            return (
                f'WARNING | Request to "{url}" failed (attempt 1/3): WheelHashError: '
                f'Hash of "{url}" does not match the index: '
                f'{hashlib.sha256(content).hexdigest()} != {"0" * 64}')

        pure = mismatch(pure_url, b'httpx-0.28.1-py3-none-any.whl content')
        compiled = mismatch(compiled_url, b'httpx-0.28.1-cp38-cp38-win_amd64.whl content')
        page = f'INFO | Download index page: "{mirror.base}/httpx/"'
        reloaded = (f'WARNING | The wheels do not match the index snapshot, downloaded again: '
                    f'"{index_file(root, "httpx", "0.28.1")}"')
        with logger.mock_capture_writer() as capture:
            with pytest.raises(WheelHashError):
                fetch_all(make_fetcher('httpx', '0.28.1'), DOWNLOAD_MODE_ALL)
        # both wheels of the page are fetched, and the page is downloaded again
        # once, the second round fails on the same wrong hash: the reload is spent
        assert_log_unordered(capture, f"""\
{page}
INFO | Download wheel: "{pure_url}" -> "{pure_file}"
INFO | Download wheel: "{compiled_url}" -> "{compiled_file}"
{pure}
{compiled}
{reloaded}
{page}
INFO | Download wheel: "{pure_url}" -> "{pure_file}"
INFO | Download wheel: "{compiled_url}" -> "{compiled_file}"
{pure}
{compiled}
""")
        # the first page and the page downloaded again, not one per wheel
        assert mirror.requests.count('http://mirror/httpx/') == 2

    def test_reloaded_page_without_the_wheel(self, fs, make_fetcher, mirror, root):
        """A page downloaded again that does not list the version is the answer."""
        mirror.register('httpx', '0.28.1')
        # the snapshot of an earlier run lists the version with a hash the
        # index does not serve anymore, and the page of the index dropped the
        # version: the reload does not find it either
        stale = f'<!DOCTYPE html><html>{page_link("httpx-0.28.1-py3-none-any.whl", "0" * 64)}</html>'
        fs.create_file(index_file(root, 'httpx', '0.28.1'), contents=stale.encode('utf-8'))
        mirror.serve_page('httpx', b'<!DOCTYPE html><html></html>')

        with logger.mock_capture_writer() as capture:
            with pytest.raises(WheelNotFoundError) as e:
                fetch(make_fetcher('httpx', '0.28.1'))
        url = 'http://mirror/packages/httpx-0.28.1-py3-none-any.whl'
        assert capture.concat() == f"""\
INFO | Download wheel: "{url}" -> "{wheel_file(root, 'httpx', '0.28.1', 'httpx-0.28.1-py3-none-any.whl')}"
WARNING | Request to "{url}" failed (attempt 1/3): WheelHashError: Hash of "{url}" does not match the index: {hashlib.sha256(b'httpx-0.28.1-py3-none-any.whl content').hexdigest()} != {'0' * 64}
WARNING | The wheels do not match the index snapshot, downloaded again: "{index_file(root, 'httpx', '0.28.1')}"
INFO | Download index page: "{mirror.base}/httpx/"
"""

        assert str(e.value) == 'No wheel of "httpx==0.28.1" in the index'
        # the first round is answered by the snapshot, the second one by the
        # page of the index: no third request, the reload is spent
        assert mirror.requests == [
            'http://mirror/packages/httpx-0.28.1-py3-none-any.whl',
            'http://mirror/httpx/',
        ]
        # the snapshot of the next run is the page the index serves now, not
        # the stale one that failed: the reload is not repeated
        assert index_file(root, 'httpx', '0.28.1').atomic_read_bytes() == mirror.page('httpx')

    def test_wheels_checked_again_after_the_page(self, fs, make_fetcher, mirror, root):
        """The wheels checked against the old page are checked against the new one."""
        pure = mirror.register('httpx', '0.28.1', content=b'pure v1')
        mirror.register('httpx', '0.28.1', tags='py38-none-any', content=b'py38 v1')
        fetch_all(make_fetcher('httpx', '0.28.1'), DOWNLOAD_MODE_ALL)
        # the index replaces both files, and the local copy of the second one
        # is broken: it forces the page to be downloaded again after the first
        # one was checked against the old page
        mirror.register('httpx', '0.28.1', content=b'pure v2')
        mirror.register('httpx', '0.28.1', tags='py38-none-any', content=b'py38 v2')
        wheel_file(root, 'httpx', '0.28.1', 'httpx-0.28.1-py38-none-any.whl').atomic_write(b'broken')
        pure_file = wheel_file(root, 'httpx', '0.28.1', 'httpx-0.28.1-py3-none-any.whl')
        assert pure_file.atomic_read_bytes() == pure

        with logger.mock_capture_writer() as capture:
            files = fetch_all(make_fetcher('httpx', '0.28.1'), DOWNLOAD_MODE_ALL)
        pure_url = 'http://mirror/packages/httpx-0.28.1-py3-none-any.whl'
        py38_url = 'http://mirror/packages/httpx-0.28.1-py38-none-any.whl'
        py38_file = wheel_file(root, 'httpx', '0.28.1', 'httpx-0.28.1-py38-none-any.whl')
        # the two wheels of a round are fetched at once: the lines are the same
        # set every run, their order is not
        assert_log_unordered(capture, f"""\
INFO | Wheel exists, skipped: "{pure_file}"
INFO | Download wheel: "{py38_url}" -> "{py38_file}"
WARNING | Request to "{py38_url}" failed (attempt 1/3): WheelHashError: Hash of "{py38_url}" does not match the index: {hashlib.sha256(b'py38 v2').hexdigest()} != {hashlib.sha256(b'py38 v1').hexdigest()}
WARNING | The wheels do not match the index snapshot, downloaded again: "{index_file(root, 'httpx', '0.28.1')}"
INFO | Download index page: "{mirror.base}/httpx/"
INFO | Download wheel: "{pure_url}" -> "{pure_file}"
INFO | Download wheel: "{py38_url}" -> "{py38_file}"
""")

        # the first wheel was verified against the page of the earlier run, it
        # is verified against the page downloaded again: the file of the index
        # is the new one
        assert [wheel.file.name for wheel in files] == [
            'httpx-0.28.1-py3-none-any.whl',
            'httpx-0.28.1-py38-none-any.whl',
        ]
        assert pure_file.atomic_read_bytes() == b'pure v2'
        assert files[1].file.atomic_read_bytes() == b'py38 v2'

    def test_the_reloaded_page_is_cached(self, fs, make_fetcher, mirror, root):
        """The page of a reload answers the next fetch of the same instance."""
        filename = 'httpx-0.28.1-py3-none-any.whl'
        mirror.register('httpx', '0.28.1')
        fetch(make_fetcher('httpx', '0.28.1'))
        # the index replaces the file under the same name, the local copy is a
        # half written leftover: the fetch downloads the page again
        mirror.register('httpx', '0.28.1', content=b'replaced wheel')
        wheel_file(root, 'httpx', '0.28.1', filename).atomic_write(b'broken wheel')
        fetcher = make_fetcher('httpx', '0.28.1')
        url = 'http://mirror/packages/httpx-0.28.1-py3-none-any.whl'
        file = wheel_file(root, 'httpx', '0.28.1', filename)
        with logger.mock_capture_writer() as capture:
            fetch_all(fetcher, DOWNLOAD_MODE_PY3_ANY)
        assert capture.concat() == f"""\
INFO | Download wheel: "{url}" -> "{file}"
WARNING | Request to "{url}" failed (attempt 1/3): WheelHashError: Hash of "{url}" does not match the index: {hashlib.sha256(b'replaced wheel').hexdigest()} != {hashlib.sha256(b'httpx-0.28.1-py3-none-any.whl content').hexdigest()}
WARNING | The wheels do not match the index snapshot, downloaded again: "{index_file(root, 'httpx', '0.28.1')}"
INFO | Download index page: "{mirror.base}/httpx/"
INFO | Download wheel: "{url}" -> "{file}"
"""
        requests = list(mirror.requests)

        with logger.mock_capture_writer() as capture:
            wheels = fetch_all(fetcher, DOWNLOAD_MODE_PY3_ANY)
        assert capture.concat() == f'INFO | Wheel exists, skipped: "{file}"\n'

        # the links of the reload are the parsed page of the instance, and the
        # wheel of the reload is on the disk: the second fetch asks nothing
        assert mirror.requests == requests
        assert wheels[0].file == wheel_file(root, 'httpx', '0.28.1', filename)
        assert wheels[0].file.atomic_read_bytes() == b'replaced wheel'

    def test_the_second_round_rechecks_without_downloading(self, fs, make_fetcher, mirror, root):
        """The second round restarts the wheels, and a file of the new page is kept."""
        pure_name = 'httpx-0.28.1-py3-none-any.whl'
        compiled_name = 'httpx-0.28.1-py38-none-any.whl'
        pure = mirror.register('httpx', '0.28.1', content=b'pure v1')
        mirror.register('httpx', '0.28.1', tags='py38-none-any', content=b'py38 v1')
        # the page and the wheels of an earlier run
        fetch_all(make_fetcher('httpx', '0.28.1'), DOWNLOAD_MODE_ALL)
        # the index replaces the compiled wheel only, the local copy of it is
        # broken after the snapshot was written: the page of the first round is
        # stale for it, and the pure wheel has to be downloaded again
        mirror.register('httpx', '0.28.1', tags='py38-none-any', content=b'py38 v2')
        wheel_file(root, 'httpx', '0.28.1', pure_name).atomic_remove()
        wheel_file(root, 'httpx', '0.28.1', compiled_name).atomic_write(b'broken')
        start = len(mirror.requests)

        with logger.mock_capture_writer() as capture:
            files = fetch_all(make_fetcher('httpx', '0.28.1'), DOWNLOAD_MODE_ALL)
        pure_url = 'http://mirror/packages/httpx-0.28.1-py3-none-any.whl'
        compiled_url = 'http://mirror/packages/httpx-0.28.1-py38-none-any.whl'
        pure_file = wheel_file(root, 'httpx', '0.28.1', pure_name)
        compiled_file = wheel_file(root, 'httpx', '0.28.1', compiled_name)
        assert_log_unordered(capture, f"""\
INFO | Download wheel: "{pure_url}" -> "{pure_file}"
INFO | Download wheel: "{compiled_url}" -> "{compiled_file}"
WARNING | Request to "{compiled_url}" failed (attempt 1/3): WheelHashError: Hash of "{compiled_url}" does not match the index: {hashlib.sha256(b'py38 v2').hexdigest()} != {hashlib.sha256(b'py38 v1').hexdigest()}
WARNING | The wheels do not match the index snapshot, downloaded again: "{index_file(root, 'httpx', '0.28.1')}"
INFO | Download index page: "{mirror.base}/httpx/"
INFO | Wheel exists, skipped: "{pure_file}"
INFO | Download wheel: "{compiled_url}" -> "{compiled_file}"
""")

        assert [wheel.file.name for wheel in files] == [pure_name, compiled_name]
        assert files[0].file.atomic_read_bytes() == pure
        assert files[1].file.atomic_read_bytes() == b'py38 v2'
        # the first round reads the snapshot, the second one downloads the page
        # again. The pure wheel of the new page is the file the first round
        # downloaded, only its hash is calculated again
        requests = mirror.requests[start:]
        assert requests.count('http://mirror/httpx/') == 1
        assert requests.count(f'http://mirror/packages/{pure_name}') == 1
        assert requests.count(f'http://mirror/packages/{compiled_name}') == 2


class TestConcurrentFetch:
    """
    The transfers of one version overlap, see WheelFetcher._fetch_wheels.

    The transfer itself is replaced by a stub: the subject is the scheduling of
    the burst, not the http client. The stub counts the transfers in flight and
    decides the order they complete in, which the assertions read.
    """

    @staticmethod
    def register_platforms(mirror, count):
        """
        Register `count` platform wheels of one version in the mock mirror.

        Args:
            mirror (MockMirror): Mirror to register the files in
            count (int): Number of wheels

        Returns:
            list[str]: The filenames, in the order select_wheels() answers them
        """
        names = [
            f'httpx-0.28.1-cp38-cp38-plat{index}.whl'
            for index in range(count)
        ]
        for name in names:
            mirror.register('httpx', '0.28.1', filename=name)
        return sorted(names)

    @staticmethod
    def filename_of(url):
        """
        The filename of a wheel url, the hash fragment of its link dropped.

        Args:
            url (str): Url of a wheel of an index page

        Returns:
            str: Filename of the wheel
        """
        return url.partition('#')[0].rsplit('/', 1)[-1]

    def test_transfers_overlap_up_to_the_limit(self, make_fetcher, mirror, monkeypatch):
        """The files of a version are fetched at once, FETCH_CONCURRENT at most."""
        names = self.register_platforms(mirror, FETCH_CONCURRENT * 2)
        fetcher = make_fetcher('httpx', '0.28.1')
        in_flight = []
        peak = []

        async def get_wheel(url):
            in_flight.append(url)
            peak.append(len(in_flight))
            await trio.sleep(0.01)
            in_flight.remove(url)
            return self.filename_of(url)

        monkeypatch.setattr(fetcher.pypi, 'get_wheel', get_wheel)
        files = fetch_all(fetcher, DOWNLOAD_MODE_ALL)

        assert files == names
        # every transfer of the burst runs, and the limiter is what bounds them:
        # one at a time would peak at 1, unbounded ones at 16
        assert max(peak) == FETCH_CONCURRENT

    def test_answer_keeps_the_page_order(self, make_fetcher, mirror, monkeypatch):
        """The answer is the order of the page, not the order the transfers complete in."""
        names = self.register_platforms(mirror, 6)
        fetcher = make_fetcher('httpx', '0.28.1')

        async def get_wheel(url):
            # the first wheel of the page answers last, the last one first
            index = names.index(self.filename_of(url))
            await trio.sleep(0.01 * (len(names) - index))
            return self.filename_of(url)

        monkeypatch.setattr(fetcher.pypi, 'get_wheel', get_wheel)
        files = fetch_all(fetcher, DOWNLOAD_MODE_ALL)

        assert files == names

    def test_no_hole_in_the_answer(self, make_fetcher, mirror, monkeypatch):
        """A wheel that does not come back fails the run instead of leaving a hole."""
        names = self.register_platforms(mirror, 4)
        fetcher = make_fetcher('httpx', '0.28.1')

        async def get_wheel(url):
            filename = self.filename_of(url)
            # a transfer that answers nothing: the caller must not publish a
            # version with a missing wheel, and the answer must not carry a None
            return None if filename == names[2] else filename

        monkeypatch.setattr(fetcher.pypi, 'get_wheel', get_wheel)
        with pytest.raises(RuntimeError) as e:
            fetch_all(fetcher, DOWNLOAD_MODE_ALL)

        assert str(e.value) == 'Empty answer of a wheel transfer of "httpx==0.28.1"'

    def test_a_failed_wheel_does_not_stop_the_others(self, make_fetcher, mirror, monkeypatch):
        """A failure of one wheel leaves the burst running, and the first wheel answers."""
        names = self.register_platforms(mirror, 4)
        fetcher = make_fetcher('httpx', '0.28.1')
        called = []
        first = httpx2.ReadError('the first wheel of the page')
        second = httpx2.ReadError('the second wheel of the page')

        async def get_wheel(url):
            filename = self.filename_of(url)
            called.append(filename)
            if filename == names[0]:
                # the first wheel of the page fails last: the answer of the run
                # is its failure either way
                await trio.sleep(0.02)
                raise first
            if filename == names[1]:
                raise second
            await trio.sleep(0.01)
            return filename

        monkeypatch.setattr(fetcher.pypi, 'get_wheel', get_wheel)
        with pytest.raises(httpx2.ReadError) as e:
            fetch_all(fetcher, DOWNLOAD_MODE_ALL)

        assert e.value is first
        # the transfers of the other wheels of the page are not cancelled: a
        # page reloaded by the caller finds the files of this round on the disk
        assert sorted(called) == names


class TestCreateClient:
    """The client a step downloads the wheels with, see create_client."""

    def test_proxy(self):
        """The client ignores the environment of the user and routes every scheme to the proxy."""
        client = create_client(PROXY)
        try:
            assert isinstance(client, httpx2.AsyncClient)
            # the proxy is the one the operator configured, never the one a proxy
            # tool left in the environment of the process
            assert client.trust_env is False
            # httpx keeps the proxy transports in the mounts of the client: one
            # pattern of every scheme is the route of the requests through the
            # proxy (a private attribute, the only way to read the route without
            # a proxy server)
            assert [pattern.pattern for pattern in client._mounts] == ['all://']
        finally:
            trio.run(client.aclose)

    def test_direct(self):
        """Without a proxy the client is the direct one of the domain, no mount of its own."""
        client = create_client()
        try:
            assert isinstance(client, httpx2.AsyncClient)
            assert client.trust_env is False
            assert client._mounts == {}
        finally:
            trio.run(client.aclose)
