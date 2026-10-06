"""
Tests for WheelFetcher: download the wheel of a distribution version from a
PyPI simple mirror.

The mirror is simulated with an httpx2.MockTransport client: the index pages
and the wheel files are served from the memory of the mock, no socket is
bound and no real mirror is requested, the same pattern as the tests of
ServerFile (tests/deploy_dev/pack/test_server_file.py).
"""
import hashlib

import httpx2
import pytest

from alasio.deploy_dev.pack_server.fetch_wheel import WHEEL_FOLDER, WheelFetcher, WheelHashError, WheelNotFoundError
from alasio.deploy_dev.pack_server.parse_dep import normalize_name
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.testing.filesystem import fs  # noqa: F401


def wheel_file(root, name, version, filename):
    """
    Path of a wheel in the wheel folder of the tests.

    Returns:
        PathStr: {root}/wheel/{name}/{version}/{filename}
    """
    return root.joinpath(WHEEL_FOLDER).joinpath(name).joinpath(version).joinpath(filename)


class MockMirror:
    """
    In-memory PyPI simple mirror, served through an httpx2 MockTransport.

    The index page of a distribution is built from the registered files: every
    link is relative and carries the sha256 fragment like the page of a real
    mirror, so the client resolves the url against the index url. The files
    are served from the memory, a file that is not registered is a 404.
    """

    def __init__(self, base='http://mirror'):
        """
        Args:
            base (str): Base url of the mirror. Defaults to 'http://mirror'
        """
        self.base = base
        # {dist key: {filename: (content, sha256)}}
        self.files = {}
        # urls of the requests made by the clients, for the assertions
        self.requests = []
        self.client = httpx2.Client(transport=httpx2.MockTransport(self._handle))

    def register(self, name, version, tags='py3-none-any', filename='', content=b'', digest=''):
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
            digest (str): sha256 hex of the link fragment. Defaults to '',
                the sha256 of the content

        Returns:
            bytes: Content of the file
        """
        if not filename:
            filename = f'{name.replace("-", "_")}-{version}-{tags}.whl'
        if not content:
            content = f'{filename} content'.encode('utf-8')
        if not digest:
            digest = hashlib.sha256(content).hexdigest()
        self.files.setdefault(normalize_name(name), {})[filename] = (content, digest)
        return content

    def _handle(self, request):
        """
        MockTransport handler, serves the index pages and the files.

        Args:
            request (httpx2.Request): The request

        Returns:
            httpx2.Response: The response
        """
        self.requests.append(str(request.url))
        path = request.url.path
        if path.startswith('/packages/'):
            filename = path.rpartition('/')[2]
            for files in self.files.values():
                if filename in files:
                    return httpx2.Response(200, content=files[filename][0])
            return httpx2.Response(404, content=b'')
        files = self.files.get(path.strip('/'))
        if files is None:
            return httpx2.Response(404, content=b'')
        links = ''.join(
            f'<a href="../../packages/{filename}#sha256={digest}">{filename}</a><br/>\n'
            for filename, (_, digest) in sorted(files.items())
        )
        return httpx2.Response(200, content=f'<!DOCTYPE html><html>{links}</html>'.encode('utf-8'))


@pytest.fixture
def root(fs, monkeypatch):
    """
    Run directory of the tests, in the fake filesystem.

    Returns:
        PathStr: Absolute path of the run directory
    """
    root = PathStr.new(fs.root_dir.path).joinpath('pack_server')
    fs.create_dir(root)
    monkeypatch.setattr(env, 'PROJECT_ROOT', root)
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
        file = make_fetcher('httpx', '0.28.1').fetch()
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

    def test_index_data(self, make_fetcher, mirror):
        """The wheel files of the index page are parsed once and cached."""
        mirror.register('httpx', '0.28.0')
        mirror.register('httpx', '0.28.1')
        mirror.register('httpx', '0.28.1', filename='httpx-0.28.1-cp38-cp38-win_amd64.whl')
        mirror.register('httpx', '0.28.1', filename='httpx-0.28.1.tar.gz')
        fetcher = make_fetcher('httpx', '0.28.1')
        data = fetcher.index_data
        # the wheel files of every version, the sdist link is skipped
        assert [wheel.filename for wheel in data] == [
            'httpx-0.28.0-py3-none-any.whl',
            'httpx-0.28.1-cp38-cp38-win_amd64.whl',
            'httpx-0.28.1-py3-none-any.whl',
        ]
        # the links of the page are resolved against the index url and parsed
        wheel = data[2]
        assert wheel.name == 'httpx'
        assert wheel.version == '0.28.1'
        assert wheel.url == 'http://mirror/packages/httpx-0.28.1-py3-none-any.whl'
        assert wheel.tags == ['py3', 'none', 'any']
        assert wheel.sha256 == hashlib.sha256(b'httpx-0.28.1-py3-none-any.whl content').hexdigest()
        # the page is read once: the data is cached
        assert fetcher.index_data is data
        assert mirror.requests == ['http://mirror/httpx/']

    def test_name_normalized(self, make_fetcher, mirror, root):
        """The name is normalized with PEP 503 for the index url and the folder."""
        mirror.register('PyYAML', '6.0.1')
        file = make_fetcher('PyYAML', '6.0.1').fetch()
        assert file == wheel_file(root, 'pyyaml', '6.0.1', 'PyYAML-6.0.1-py3-none-any.whl')
        assert mirror.requests == [
            'http://mirror/pyyaml/',
            'http://mirror/packages/PyYAML-6.0.1-py3-none-any.whl',
        ]

    def test_name_with_separators(self, make_fetcher, mirror, root):
        """A name with separators matches the escaped name part of the filename."""
        mirror.register('opencv-python-headless', '4.10.0.84')
        file = make_fetcher('opencv-python-headless', '4.10.0.84').fetch()
        assert file == wheel_file(
            root, 'opencv-python-headless', '4.10.0.84',
            'opencv_python_headless-4.10.0.84-py3-none-any.whl')

    def test_skips_existing(self, fs, make_fetcher, mirror, root):
        """A wheel already fetched is kept as it is, no request is made."""
        filename = 'httpx-0.28.1-py3-none-any.whl'
        content = b'existing wheel content'
        fs.create_file(wheel_file(root, 'httpx', '0.28.1', filename), contents=content)
        file = make_fetcher('httpx', '0.28.1').fetch()
        assert file == wheel_file(root, 'httpx', '0.28.1', filename)
        assert file.atomic_read_bytes() == content
        assert mirror.requests == []

    def test_pure_wheel_selected(self, make_fetcher, mirror):
        """The pure wheel is selected when the version also has platform wheels."""
        mirror.register('psutil', '5.9.8', filename='psutil-5.9.8-cp38-cp38-win_amd64.whl')
        mirror.register('psutil', '5.9.8')
        file = make_fetcher('psutil', '5.9.8').fetch()
        assert file.name == 'psutil-5.9.8-py3-none-any.whl'
        assert mirror.requests[-1] == 'http://mirror/packages/psutil-5.9.8-py3-none-any.whl'

    def test_no_index(self, make_fetcher):
        """A distribution the mirror has no index of is refused."""
        with pytest.raises(WheelNotFoundError, match='No index of "httpx"'):
            make_fetcher('httpx', '0.28.1').fetch()

    def test_no_wheel(self, make_fetcher, mirror):
        """A version the index has no wheel of is refused."""
        mirror.register('httpx', '0.28.0')
        with pytest.raises(WheelNotFoundError, match='No wheel of "httpx==0.28.1"'):
            make_fetcher('httpx', '0.28.1').fetch()

    def test_no_pure_wheel(self, make_fetcher, mirror):
        """A version with no pure python wheel is refused."""
        mirror.register('psutil', '5.9.8', filename='psutil-5.9.8-cp38-cp38-win_amd64.whl')
        mirror.register('psutil', '5.9.8', filename='psutil-5.9.8.tar.gz')
        with pytest.raises(WheelNotFoundError, match='No pure python wheel of "psutil==5.9.8"'):
            make_fetcher('psutil', '5.9.8').fetch()

    def test_py2_only_wheel(self, make_fetcher, mirror):
        """A py2 only pure wheel is not fetchable: the pack build runs on python3."""
        mirror.register('foo', '1.0', filename='foo-1.0-py2-none-any.whl')
        with pytest.raises(WheelNotFoundError, match='No pure python wheel of "foo==1.0"'):
            make_fetcher('foo', '1.0').fetch()

    def test_foreign_wheel_not_trusted(self, fs, make_fetcher, mirror, root):
        """A wheel of another version in the folder is not the fetch of the version."""
        fs.create_file(
            wheel_file(root, 'httpx', '0.28.1', 'httpx-0.28.0-py3-none-any.whl'),
            contents=b'other version')
        mirror.register('httpx', '0.28.1')
        file = make_fetcher('httpx', '0.28.1').fetch()
        assert file.name == 'httpx-0.28.1-py3-none-any.whl'

    def test_hash_mismatch(self, make_fetcher, mirror, root):
        """A download that does not match the sha256 of the index is refused."""
        mirror.register('httpx', '0.28.1', digest='0' * 64)
        with pytest.raises(WheelHashError, match='does not match the index'):
            make_fetcher('httpx', '0.28.1').fetch()
        file = wheel_file(root, 'httpx', '0.28.1', 'httpx-0.28.1-py3-none-any.whl')
        assert not file.exists()

    def test_close_keeps_injected_client(self, make_fetcher, mirror):
        """An injected client belongs to the caller: close() does not close it."""
        mirror.register('httpx', '0.28.1')
        mirror.register('httpx', '0.28.2')
        fetcher = make_fetcher('httpx', '0.28.1')
        fetcher.fetch()
        fetcher.close()
        # the client of the caller is still usable by a later fetch
        assert make_fetcher('httpx', '0.28.2').fetch().exists()
