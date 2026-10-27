"""
Tests for WheelFetcher: download the wheel of a distribution version from a
PyPI simple mirror.

The mirror is simulated with MockStaticFile, the in-memory static file server
of the tests: the index pages and the wheel files are registered into it and
served through an httpx2.AsyncClient over the ASGI transport, no socket is
bound and no real mirror is requested. The wheel is written to the fake
filesystem (alasio.testing.filesystem).

The fetch itself (the parse of the page, the hash of the wheel, the retry of a
transfer) is the shared implementation of alasio.deploy.pip_download, its tests
are tests/deploy/pip_download: here the mode of the release side and the layout
of the pack server are tested.
"""
import hashlib

import httpx2
import pytest
import trio

from alasio.deploy_dev.pack_server.fetch_wheel import WHEEL_FOLDER, WheelFetcher, WheelHashError, WheelNotFoundError
from alasio.deploy_dev.pack_server.parse_dep import normalize_name
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.testing.filesystem import fs  # noqa: F401
from alasio.testing.static_file import MockStaticFile


def wheel_file(root, name, version, filename):
    """
    Path of a wheel in the wheel folder of the tests.

    Returns:
        PathStr: {root}/wheel/{name}/{version}/{filename}
    """
    return root.joinpath(WHEEL_FOLDER).joinpath(name).joinpath(version).joinpath(filename)


def fetch(fetcher):
    """
    Run the async fetch of a fetcher, the blocking entry of the tests.

    Args:
        fetcher (WheelFetcher): Fetcher to run

    Returns:
        PathStr: Path of the fetched wheel
    """
    return trio.run(fetcher.fetch_async)


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


def recording_app(static, requests):
    """
    The app of the mirror: the static file server, the requests recorded.

    Args:
        static (MockStaticFile): The static file server of the mirror
        requests (list): Appended with the urls of the requests, in order

    Returns:
        Callable: The ASGI app to serve
    """
    async def app(scope, receive, send):
        if scope['type'] == 'http':
            headers = dict(scope['headers'])
            requests.append(
                f"{scope['scheme']}://{headers.get(b'host', b'').decode()}{scope['path']}")
        await static(scope, receive, send)

    return app


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
    In-memory PyPI simple mirror, served by the static file server of the tests.

    The index page of a distribution is built from the registered files and
    registered into the server like any other file: every link is relative and
    carries the sha256 fragment like the page of a real mirror, so the client
    resolves the url against the index url; a file that was registered without
    a digest is linked without a fragment. A file that is not registered is a
    404, no request is parsed by hand here.

    Attributes:
        base (str): Base url of the mirror. Defaults to 'http://mirror'
        bases (dict[str, str]): {base url: path prefix of its index}, the
            registry is served at every base, see alias()
        files (dict[str, dict[str, tuple[bytes, str]]]): {dist key:
            {filename: (content, sha256)}}
        requests (list[str]): Urls of the requests made by the clients, in
            order, for the assertions
        static (MockStaticFile): The static file server of the mirror
        app (Callable): The ASGI app of the mirror, the requests recorded
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
        # urls of the requests made by the clients, for the assertions
        self.requests = []
        # the files of the mirror and the app serving them: the requests are
        # recorded for the assertions, the client is the one of the code under
        # test
        self.static = MockStaticFile()
        self.app = recording_app(self.static, self.requests)
        self.client = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=self.app))

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
        self._rebuild()
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
        self._rebuild()
        return content

    def _rebuild(self):
        """
        Register the index pages and the files into the static file server.

        The pages are files like the wheels: a mirror serves the same bytes
        every time, the registry of the tests is what changes.
        """
        files = {
            f'packages/{filename}': content
            for dist in self.files.values()
            for filename, (content, _) in dist.items()
        }
        # the index pages of every base of the mirror: a base with a path
        # serves the same index under that path, e.g. the index a mod
        # provides, see alias()
        for prefix in dict.fromkeys(self.bases.values()):
            files.update({
                f'{prefix}/{dist_key}/': self._page(dist)
                for dist_key, dist in self.files.items()
            })
        self.static.register_files(files)

    @staticmethod
    def _page(dist):
        """
        The index page of a distribution: a relative link per file.

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


@pytest.fixture
def root(fs, monkeypatch):
    """
    Folder of a repo of the tests, in the fake filesystem.

    The root the fetcher writes its wheel folder into: the folder of the
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
        return WheelFetcher(root, mirror.base, name, version, client=mirror.client)
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

    def test_target_folder(self, make_fetcher, root):
        """The target folder is {root}/wheel/{name}/{version}, cached."""
        fetcher = make_fetcher('PyYAML', '6.0.1')
        folder = root.joinpath(WHEEL_FOLDER).joinpath('pyyaml').joinpath('6.0.1')
        assert fetcher.target_folder == folder
        assert fetcher.target_folder is fetcher.target_folder

    def test_name_normalized(self, make_fetcher, mirror, root):
        """The name is normalized with PEP 503 for the index url and the folder."""
        mirror.register('PyYAML', '6.0.1')
        file = fetch(make_fetcher('PyYAML', '6.0.1'))
        assert file == wheel_file(root, 'pyyaml', '6.0.1', 'PyYAML-6.0.1-py3-none-any.whl')
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

    def test_no_index(self, make_fetcher):
        """A distribution the mirror has no index of is refused."""
        with pytest.raises(WheelNotFoundError, match='No index of "httpx"'):
            fetch(make_fetcher('httpx', '0.28.1'))

    def test_no_wheel(self, make_fetcher, mirror):
        """A version the index has no wheel of is refused."""
        mirror.register('httpx', '0.28.0')
        with pytest.raises(WheelNotFoundError, match='No wheel of "httpx==0.28.1"'):
            fetch(make_fetcher('httpx', '0.28.1'))

    def test_hash_mismatch(self, make_fetcher, mirror, root):
        """A download that does not match the sha256 of the index is refused."""
        mirror.register('httpx', '0.28.1', digest='0' * 64)
        with pytest.raises(WheelHashError, match='does not match the index'):
            fetch(make_fetcher('httpx', '0.28.1'))
        file = wheel_file(root, 'httpx', '0.28.1', 'httpx-0.28.1-py3-none-any.whl')
        assert not file.exists()

    def test_injected_client_is_not_closed(self, make_fetcher, mirror):
        """An injected client belongs to the caller: the fetch does not close it."""
        mirror.register('httpx', '0.28.1')
        mirror.register('httpx', '0.28.2')
        fetch(make_fetcher('httpx', '0.28.1'))
        # the client of the caller is still usable by a later fetch
        assert fetch(make_fetcher('httpx', '0.28.2')).exists()
