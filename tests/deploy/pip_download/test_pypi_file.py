"""
Tests for PypiFile: download the wheels of a version from a simple index.

The mirrors are in-memory indexes served by an httpx2.MockTransport client, no
socket is bound and no real mirror is requested: the pages and the files are
registered per host, and every request of the fetcher is recorded for the
assertions. The destination folder is the in-memory filesystem of the tests
(alasio.testing.filesystem).
"""
import hashlib
import urllib.parse

import httpx2
import pytest

from alasio.deploy.httpclient.probe_mirrors import Mirrors
from alasio.deploy.pip_download._target_python import TargetPython
from alasio.deploy.pip_download.pypi_file import PypiFile, WheelHashError, WheelNotFoundError
from alasio.ext.path import PathStr
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack.conftest import FakeMirrorTable

# the target of the tests, fixed so the selected wheels do not depend on the
# interpreter that runs the tests
PY38_WIN = TargetPython(py_version_info=(3, 8, 10), platforms=['win_amd64'])


class MockIndex:
    """
    An in-memory PyPI simple index, served through a MockTransport handler.

    Every distribution has an index page at `{base}/{dist}/`, and every wheel
    of it is served at `{base}/{dist}/{filename}`; the links of the page are
    relative and carry the sha256 of the content, unless the registration says
    otherwise.

    Attributes:
        base (str): Base url of the index, e.g. 'https://a.example/simple'
        dists (dict[str, list[dict]]): {dist key: [file info]}
        requests (list[tuple[str, str]]): (method, url) of every request, in
            order
    """

    def __init__(self, base, files_base=None):
        """
        Args:
            base (str): Base url of the index, without the trailing '/'
            files_base (str | None): Base url the page links its files with,
                None for the base of the index (a real mirror may point at a
                files host of its own)
        """
        self.base = base.rstrip('/')
        self.files_base = (files_base or base).rstrip('/')
        # {dist key: [file info]}, the file info: filename, content, digest,
        # requires_python, yanked
        self.dists = {}
        # dist keys whose page is a JSON page that does not parse
        self.broken_pages = set()
        # (method, url) of every request, for the assertions
        self.requests = []

    def register(self, name, version, tags='py3-none-any', content=None, digest=None,
                 requires_python=None, yanked=None):
        """
        Register one wheel under the index of its distribution.

        Args:
            name (str): Distribution name as the publisher writes it
            version (str): Version of the wheel
            tags (str): Trailing tags of the filename. Defaults to 'py3-none-any'
            content (bytes | None): Content of the file. Defaults to a content
                generated from the filename
            digest (str | None): sha256 the link declares. Defaults to None,
                the sha256 of the content; pass '' to link the file without a
                fragment
            requires_python (str | None): Value of `data-requires-python`
            yanked (str | None): Reason of `data-yanked`

        Returns:
            bytes: Content of the registered wheel
        """
        filename = f'{name.replace("-", "_")}-{version}-{tags}.whl'
        if content is None:
            content = f'{filename} content'.encode('utf-8')
        if digest is None:
            digest = hashlib.sha256(content).hexdigest()
        self.dists.setdefault(name.lower().replace('_', '-'), []).append({
            'filename': filename,
            'content': content,
            'digest': digest,
            'requires_python': requires_python,
            'yanked': yanked,
        })
        return content

    def break_page(self, name):
        """
        Serve the page of a distribution as a JSON page that does not parse.

        Args:
            name (str): Distribution name, the key of the page
        """
        self.broken_pages.add(name)

    def page_url(self, name):
        """
        Returns:
            str: Url of the index page of a distribution
        """
        return f'{self.base}/{name}/'

    def file_url(self, name, filename):
        """
        Returns:
            str: Url of one wheel of a distribution
        """
        return f'{self.files_base}/{name}/{filename}'

    def _page(self, name):
        """
        Returns:
            bytes: The index page of a distribution
        """
        anchors = []
        for info in self.dists.get(name, []):
            fragment = f'#sha256={info["digest"]}' if info['digest'] else ''
            # the link is absolute, like the links of a mirror that serves the
            # files from a host of its own
            attrs = f' href="{self.file_url(name, info["filename"])}{fragment}"'
            if info['requires_python'] is not None:
                attrs += f' data-requires-python="{info["requires_python"]}"'
            if info['yanked'] is not None:
                attrs += f' data-yanked="{info["yanked"]}"'
            anchors.append(f'<a{attrs}>{info["filename"]}</a>')
        return f'<html><body>{"".join(anchors)}</body></html>'.encode('utf-8')

    def handler(self, request):
        """
        Serve one request of the fetcher.

        Args:
            request (httpx2.Request): The request

        Returns:
            httpx2.Response: The page or the file, a 404 for an unknown path
        """
        url = str(request.url)
        self.requests.append((request.method, url))
        path = urllib.parse.urlsplit(url).path
        for prefix in (self.base, self.files_base):
            prefix = urllib.parse.urlsplit(prefix).path.rstrip('/') + '/'
            if path.startswith(prefix):
                break
        else:
            return httpx2.Response(404)
        rest = path[len(prefix):]
        name, sep, filename = rest.partition('/')
        if not sep:
            return httpx2.Response(404)
        if not filename:
            # the index page of the distribution, 404 when the mirror has no
            # index of it
            if name not in self.dists:
                return httpx2.Response(404)
            if name in self.broken_pages:
                return httpx2.Response(
                    200, content=b'{"files": [',
                    headers={'content-type': 'application/vnd.pypi.simple.v1+json'})
            return httpx2.Response(200, content=self._page(name))
        for info in self.dists.get(name, []):
            if info['filename'] == filename:
                return httpx2.Response(200, content=info['content'])
        return httpx2.Response(404)

    def file_requests(self):
        """
        Returns:
            list[str]: Urls of the wheel requests, in order
        """
        return [url for method, url in self.requests if url.endswith('.whl')]

    def page_requests(self):
        """
        Returns:
            list[str]: Urls of the index page requests, in order
        """
        return [url for method, url in self.requests if url.endswith('/')]


def make_client(*indexes):
    """
    A client serving several mock indexes, selected by host.

    Args:
        *indexes (MockIndex): The indexes to serve

    Returns:
        httpx2.AsyncClient: The client to inject into PypiFile
    """
    hosts = {urllib.parse.urlsplit(index.base).netloc: index for index in indexes}

    def handler(request):
        host = urllib.parse.urlsplit(str(request.url)).netloc
        return hosts[host].handler(request)

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


@pytest.fixture
def record_table():
    """
    The in-memory record table of the probe, restored after the test.
    """
    original = PypiFile.RECORD_TABLE
    PypiFile.RECORD_TABLE = FakeMirrorTable()
    yield PypiFile.RECORD_TABLE
    PypiFile.RECORD_TABLE = original


@pytest.fixture
def dest(fs):
    """
    Folder of the downloads of the tests, in the fake filesystem.

    Returns:
        PathStr: The folder, it does not exist yet
    """
    return PathStr.new(fs.root_dir.path).joinpath('wheels')


@pytest.fixture
def index():
    """
    The single mirror of the tests.

    Returns:
        MockIndex:
    """
    return MockIndex('https://a.example/simple')


def make_pypi(index, dest, mode_target=PY38_WIN):
    """
    A fetcher of one mock index.

    Returns:
        PypiFile: The fetcher, its client is the mock transport
    """
    return PypiFile(index.base, dest, client=make_client(index), target=mode_target)


class TestDownload:
    """The files of a mode are downloaded to the destination folder."""

    @pytest.mark.trio
    async def test_current(self, index, dest):
        """The wheels compatible with the target are downloaded, in the tag
        preference order."""
        index.register('numpy', '2.1.0', tags='cp312-cp312-win_amd64')
        index.register('numpy', '2.1.0', tags='cp38-cp38-win_amd64')
        index.register('numpy', '2.1.0', tags='py3-none-any')
        pypi = make_pypi(index, dest)
        with logger.mock_capture_writer():
            files = await pypi.download('NumPy', '2.1.0', 'current')
        assert [str(file) for file in files] == [
            str(dest.joinpath('numpy-2.1.0-cp38-cp38-win_amd64.whl')),
            str(dest.joinpath('numpy-2.1.0-py3-none-any.whl')),
        ]
        assert all(isinstance(file, str) for file in files)
        assert dest.joinpath('numpy-2.1.0-cp38-cp38-win_amd64.whl').atomic_read_bytes() == \
            b'numpy-2.1.0-cp38-cp38-win_amd64.whl content'
        assert index.requests == [
            ('GET', index.page_url('numpy')),
            ('GET', index.file_url('numpy', 'numpy-2.1.0-cp38-cp38-win_amd64.whl')),
            ('GET', index.file_url('numpy', 'numpy-2.1.0-py3-none-any.whl')),
        ]

    @pytest.mark.trio
    async def test_py3_any(self, index, dest):
        """Only the pure python wheels of python 3 are downloaded."""
        index.register('httpx', '0.28.1', tags='py3-none-any')
        index.register('httpx', '0.28.1', tags='cp38-cp38-win_amd64')
        pypi = make_pypi(index, dest)
        with logger.mock_capture_writer():
            files = await pypi.download('httpx', '0.28.1', 'py3-any')
        assert [file.name for file in files] == ['httpx-0.28.1-py3-none-any.whl']
        assert index.file_requests() == [
            index.file_url('httpx', 'httpx-0.28.1-py3-none-any.whl')]

    @pytest.mark.trio
    async def test_all(self, index, dest):
        """Every wheel of the version is downloaded."""
        index.register('numpy', '2.1.0', tags='cp312-cp312-win_amd64')
        index.register('numpy', '2.1.0', tags='cp38-cp38-win_amd64')
        index.register('numpy', '2.1.0', tags='py3-none-any')
        pypi = make_pypi(index, dest)
        with logger.mock_capture_writer():
            files = await pypi.download('numpy', '2.1.0', 'all')
        assert [file.name for file in files] == [
            'numpy-2.1.0-cp312-cp312-win_amd64.whl',
            'numpy-2.1.0-cp38-cp38-win_amd64.whl',
            'numpy-2.1.0-py3-none-any.whl',
        ]

    @pytest.mark.trio
    async def test_dest_created(self, index, dest):
        """The destination folder is created when it does not exist."""
        index.register('httpx', '0.28.1')
        pypi = make_pypi(index, dest)
        assert not dest.exists()
        with logger.mock_capture_writer():
            await pypi.download('httpx', '0.28.1', 'py3-any')
        assert dest.isdir()

    @pytest.mark.trio
    async def test_invalid_mode(self, index, dest):
        """An unknown mode fails before any request."""
        pypi = make_pypi(index, dest)
        with pytest.raises(ValueError) as e:
            await pypi.download('httpx', '0.28.1', 'everything')
        assert str(e.value) == (
            "Invalid download mode: 'everything', expected one of ('current', 'py3-any', 'all')")
        assert index.requests == []


class TestLocalFiles:
    """The files of an earlier run are reused, TOFU included."""

    @pytest.mark.trio
    async def test_valid_file_skipped(self, index, dest):
        """A file that matches the hash of its link is not downloaded again."""
        content = index.register('httpx', '0.28.1')
        dest.makedirs()
        dest.joinpath('httpx-0.28.1-py3-none-any.whl').atomic_write(content)
        pypi = make_pypi(index, dest)
        with logger.mock_capture_writer() as capture:
            files = await pypi.download('httpx', '0.28.1', 'py3-any')
        assert [file.name for file in files] == ['httpx-0.28.1-py3-none-any.whl']
        assert index.file_requests() == []
        assert capture.concat() == (
            f'INFO | Wheel exists, skipped: "{dest.joinpath("httpx-0.28.1-py3-none-any.whl")}"\n')

    @pytest.mark.trio
    async def test_wrong_file_downloaded(self, index, dest):
        """A file that does not match the hash of its link is downloaded again."""
        content = index.register('httpx', '0.28.1')
        dest.makedirs()
        file = dest.joinpath('httpx-0.28.1-py3-none-any.whl')
        file.atomic_write(b'old content')
        pypi = make_pypi(index, dest)
        with logger.mock_capture_writer():
            await pypi.download('httpx', '0.28.1', 'py3-any')
        assert file.atomic_read_bytes() == content
        assert index.file_requests() == [index.file_url('httpx', 'httpx-0.28.1-py3-none-any.whl')]

    @pytest.mark.trio
    async def test_no_hash_trusts_local_file(self, index, dest):
        """A link without a hash has nothing to check: the local file is the
        authority (TOFU)."""
        index.register('httpx', '0.28.1', digest='', content=b'served content')
        dest.makedirs()
        file = dest.joinpath('httpx-0.28.1-py3-none-any.whl')
        file.atomic_write(b'local content')
        pypi = make_pypi(index, dest)
        with logger.mock_capture_writer():
            await pypi.download('httpx', '0.28.1', 'py3-any')
        assert file.atomic_read_bytes() == b'local content'
        assert index.file_requests() == []


class TestHashCheck:
    """The downloaded bytes are checked against the hash of the link."""

    @pytest.mark.trio
    async def test_mismatch(self, index, dest):
        """A file that does not match the hash is not written."""
        index.register('httpx', '0.28.1', digest='b' * 64)
        pypi = make_pypi(index, dest)
        with logger.mock_capture_writer():
            with pytest.raises(WheelHashError) as e:
                await pypi.download('httpx', '0.28.1', 'py3-any')
        assert str(e.value) == (
            f'Hash of "{index.file_url("httpx", "httpx-0.28.1-py3-none-any.whl")}" '
            f'does not match the index: '
            f'{hashlib.sha256(b"httpx-0.28.1-py3-none-any.whl content").hexdigest()} != {"b" * 64}')
        assert not dest.joinpath('httpx-0.28.1-py3-none-any.whl').exists()
        assert list(dest.iter_entry()) == []


class TestMirrors:
    """The mirror selection and the retry of a failed wheel on another one."""

    @pytest.fixture
    def two_mirrors(self):
        """
        Returns:
            tuple[MockIndex, MockIndex]: Two indexes of the same distribution
        """
        return (
            MockIndex('https://a.example/simple'),
            MockIndex('https://b.example/simple'),
        )

    @pytest.mark.trio
    async def test_page_is_head_probed(self, record_table, two_mirrors, dest):
        """The page is probed with a HEAD and fetched once on the winner: the
        page of a big distribution is not transferred by the probe."""
        first, second = two_mirrors
        first.register('httpx', '0.28.1')
        second.register('httpx', '0.28.1')
        pypi = PypiFile(
            {'cn': {'a': first.base, 'b': second.base}}, dest,
            client=make_client(first, second), target=PY38_WIN)
        with logger.mock_capture_writer():
            files = await pypi.download('httpx', '0.28.1', 'py3-any')
        assert [file.name for file in files] == ['httpx-0.28.1-py3-none-any.whl']
        assert first.requests == [
            ('HEAD', first.page_url('httpx')),
            ('GET', first.page_url('httpx')),
            ('GET', first.file_url('httpx', 'httpx-0.28.1-py3-none-any.whl')),
        ]
        assert second.requests == []
        assert record_table.selects >= 1

    @pytest.mark.trio
    async def test_wheel_from_the_page_url(self, record_table, two_mirrors, dest):
        """A wheel whose hash does not match fails without a second host: the
        file is fetched from the url of its page, no other mirror serves it."""
        first, second = two_mirrors
        first.register('httpx', '0.28.1', content=b'corrupted', digest='c' * 64)
        second.register('httpx', '0.28.1', content=b'good content')
        pypi = PypiFile(
            {'cn': {'a': first.base, 'b': second.base}}, dest,
            client=make_client(first, second), target=PY38_WIN)
        with logger.mock_capture_writer():
            with pytest.raises(WheelHashError):
                await pypi.download('httpx', '0.28.1', 'py3-any')
        assert not dest.joinpath('httpx-0.28.1-py3-none-any.whl').exists()
        assert first.file_requests() == [
            first.file_url('httpx', 'httpx-0.28.1-py3-none-any.whl')]
        # the second page is never read: the file of the first page is the
        # answer, only a page that does not parse is worth another mirror
        assert second.page_requests() == []
        assert second.file_requests() == []

    @pytest.mark.trio
    async def test_broken_page_moves_to_another_mirror(self, record_table, two_mirrors, dest):
        """A page that does not parse is a failure of the mirror: the path is
        probed again without that mirror and the download runs on the winner."""
        first, second = two_mirrors
        first.register('httpx', '0.28.1')
        first.break_page('httpx')
        content = second.register('httpx', '0.28.1')
        mirrors = {'cn': {'a': first.base, 'b': second.base}}
        # the record serves 'a' without a probe: the failure of the page is
        # answered by a probe of the path, which excludes the failed mirror
        record_table.seed(
            scene=PypiFile.SCENE, name='', digest=Mirrors.from_input(mirrors).digest, result='a')
        pypi = PypiFile(mirrors, dest, client=make_client(first, second), target=PY38_WIN)
        with logger.mock_capture_writer():
            files = await pypi.download('httpx', '0.28.1', 'py3-any')
        assert len(files) == 1
        assert files[0].atomic_read_bytes() == content
        # the recorded mirror is asked once, the file of its page never
        assert first.requests == [('GET', first.page_url('httpx'))]
        # the winner is probed with a HEAD, then its page is fetched once
        assert second.requests == [
            ('HEAD', second.page_url('httpx')),
            ('GET', second.page_url('httpx')),
            ('GET', second.file_url('httpx', 'httpx-0.28.1-py3-none-any.whl')),
        ]

    @pytest.mark.trio
    async def test_first_request_moves_to_another_mirror(self, record_table, two_mirrors, dest):
        """No record yet: the probe of the page selects a mirror, and a page
        of it without the version makes the instance probe again without it."""
        first, second = two_mirrors
        # the index of a is behind: it only lists the previous version
        first.register('httpx', '0.28.0')
        content = second.register('httpx', '0.28.1')
        mirrors = {'cn': {'a': first.base, 'b': second.base}}
        pypi = PypiFile(mirrors, dest, client=make_client(first, second), target=PY38_WIN)
        with logger.mock_capture_writer():
            files = await pypi.download('httpx', '0.28.1', 'py3-any')
        assert len(files) == 1
        assert files[0].atomic_read_bytes() == content
        # a wins the probe of the page (a HEAD), its page has no such version
        assert first.requests == [
            ('HEAD', first.page_url('httpx')),
            ('GET', first.page_url('httpx')),
        ]
        # the page is probed again without a, the winner serves the wheel
        assert second.requests == [
            ('HEAD', second.page_url('httpx')),
            ('GET', second.page_url('httpx')),
            ('GET', second.file_url('httpx', 'httpx-0.28.1-py3-none-any.whl')),
        ]
        assert record_table.select_one(scene=PypiFile.SCENE, name='').result == 'b'

    @pytest.mark.trio
    async def test_missing_wheel_moves_to_another_mirror(self, record_table, two_mirrors, dest):
        """A page without a wheel of the version is a failure of the mirror:
        the download continues on a mirror whose page has it."""
        first, second = two_mirrors
        # the index of a is behind: it only lists the previous version
        first.register('httpx', '0.28.0')
        content = second.register('httpx', '0.28.1')
        mirrors = {'cn': {'a': first.base, 'b': second.base}}
        record_table.seed(
            scene=PypiFile.SCENE, name='', digest=Mirrors.from_input(mirrors).digest, result='a')
        pypi = PypiFile(mirrors, dest, client=make_client(first, second), target=PY38_WIN)
        with logger.mock_capture_writer():
            files = await pypi.download('httpx', '0.28.1', 'py3-any')
        assert len(files) == 1
        assert files[0].atomic_read_bytes() == content
        # the recorded mirror serves its page once and has no file to give
        assert first.requests == [('GET', first.page_url('httpx'))]
        # the path is probed again without it, the page of the winner is
        # fetched once and the wheel comes from it
        assert second.requests == [
            ('HEAD', second.page_url('httpx')),
            ('GET', second.page_url('httpx')),
            ('GET', second.file_url('httpx', 'httpx-0.28.1-py3-none-any.whl')),
        ]
        # the record now points at the mirror that served the version
        assert record_table.select_one(scene=PypiFile.SCENE, name='').result == 'b'

    @pytest.mark.trio
    async def test_every_page_without_the_wheel(self, record_table, two_mirrors, dest):
        """No mirror has the version: the failure of the last attempt is the
        answer, and the failed pages are the only requests made."""
        first, second = two_mirrors
        first.register('httpx', '0.28.0')
        second.register('httpx', '0.28.0')
        mirrors = {'cn': {'a': first.base, 'b': second.base}}
        record_table.seed(
            scene=PypiFile.SCENE, name='', digest=Mirrors.from_input(mirrors).digest, result='a')
        pypi = PypiFile(mirrors, dest, client=make_client(first, second), target=PY38_WIN)
        with logger.mock_capture_writer():
            with pytest.raises(WheelNotFoundError) as e:
                await pypi.download('httpx', '0.28.1', 'py3-any')
        assert str(e.value) == 'No wheel of "httpx==0.28.1" in the index'
        assert first.file_requests() == []
        assert second.file_requests() == []
        # one attempt per mirror, no wheel is downloaded
        assert first.requests == [('GET', first.page_url('httpx'))]
        assert second.requests == [
            ('HEAD', second.page_url('httpx')),
            ('GET', second.page_url('httpx')),
        ]


class TestPublicApi:
    """The paths of the fetcher: get_package_index() and get_wheel()."""

    @pytest.mark.trio
    async def test_get_package_index(self, index, dest):
        """The page is requested once and returned as its links."""
        index.register('httpx', '0.28.0')
        index.register('httpx', '0.28.1', requires_python='>=3.8')
        pypi = make_pypi(index, dest)
        with logger.mock_capture_writer():
            links = await pypi.get_package_index('HTTPX')
        assert [link.filename for link in links] == [
            'httpx-0.28.0-py3-none-any.whl',
            'httpx-0.28.1-py3-none-any.whl',
        ]
        assert links[1].requires_python == '>=3.8'
        assert index.page_requests() == [index.page_url('httpx')]

    @pytest.mark.trio
    async def test_get_wheel(self, index, dest):
        """A wheel is downloaded by the url of its link, the hash of the
        fragment is the check."""
        content = index.register('httpx', '0.28.1')
        pypi = make_pypi(index, dest)
        url = f'{index.file_url("httpx", "httpx-0.28.1-py3-none-any.whl")}' \
              f'#sha256={hashlib.sha256(content).hexdigest()}'
        with logger.mock_capture_writer():
            file = await pypi.get_wheel(url)
        assert file == dest.joinpath('httpx-0.28.1-py3-none-any.whl')
        assert file.atomic_read_bytes() == content
        # the fragment is not part of the request, and the transfer is not
        # probed: the file is served by the host of its url, not by a mirror
        assert index.requests == [
            ('GET', index.file_url('httpx', 'httpx-0.28.1-py3-none-any.whl'))]

    @pytest.mark.trio
    async def test_get_wheel_of_a_files_host(self, dest):
        """The url of a link is used as it is: the page may point at a files
        host that is not the mirror."""
        files = MockIndex('https://files.example/packages')
        content = files.register('httpx', '0.28.1')
        mirror = MockIndex('https://mirror.example/simple', files_base=files.base)
        mirror.register('httpx', '0.28.1')
        pypi = PypiFile(mirror.base, dest, client=make_client(mirror, files), target=PY38_WIN)
        with logger.mock_capture_writer():
            link = (await pypi.get_package_index('httpx'))[0]
            file = await pypi.get_wheel(link.url)
        assert link.url.startswith(f'{files.base}/httpx/')
        assert file.atomic_read_bytes() == content
        assert mirror.file_requests() == []
        assert files.file_requests() == [
            files.file_url('httpx', 'httpx-0.28.1-py3-none-any.whl')]

    @pytest.mark.trio
    async def test_get_wheel_of_a_local_file(self, index, dest):
        """A wheel that is already there is not requested again."""
        content = index.register('httpx', '0.28.1')
        pypi = make_pypi(index, dest)
        url = f'{index.file_url("httpx", "httpx-0.28.1-py3-none-any.whl")}' \
              f'#sha256={hashlib.sha256(content).hexdigest()}'
        dest.makedirs()
        file = dest.joinpath('httpx-0.28.1-py3-none-any.whl')
        file.atomic_write(content)
        with logger.mock_capture_writer() as capture:
            assert await pypi.get_wheel(url) == file
        assert index.requests == []
        assert capture.concat() == f'INFO | Wheel exists, skipped: "{file}"\n'


class TestIndexErrors:
    """The failures of the index of a mirror."""

    @pytest.mark.trio
    async def test_no_version(self, index, dest):
        """A page without the version fails with the mode in the message."""
        index.register('httpx', '0.28.0')
        pypi = make_pypi(index, dest)
        with logger.mock_capture_writer():
            with pytest.raises(WheelNotFoundError) as e:
                await pypi.download('httpx', '0.28.1', 'py3-any')
        assert str(e.value) == 'No wheel of "httpx==0.28.1" in the index'

    @pytest.mark.trio
    async def test_no_mode_match(self, index, dest):
        """A version without a wheel of the mode fails with the files it has."""
        index.register('httpx', '0.28.1', tags='cp38-cp38-win_amd64')
        pypi = make_pypi(index, dest)
        with logger.mock_capture_writer():
            with pytest.raises(WheelNotFoundError) as e:
                await pypi.download('httpx', '0.28.1', 'py3-any')
        assert str(e.value) == (
            'No wheel of "httpx==0.28.1" matches "py3-any", the version has: '
            'httpx-0.28.1-cp38-cp38-win_amd64.whl')

    @pytest.mark.trio
    async def test_missing_index(self, index, dest):
        """A 404 of the index page is the answer of the mirror."""
        pypi = make_pypi(index, dest)
        with logger.mock_capture_writer():
            with pytest.raises(httpx2.HTTPStatusError):
                await pypi.download('httpx', '0.28.1', 'py3-any')
