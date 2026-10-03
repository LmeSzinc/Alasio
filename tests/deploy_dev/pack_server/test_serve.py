"""
Tests for the development server of the packs (serve.py).

The app is driven through its ASGI interface: the scope of a request is built
by hand and the response messages are collected, no socket is bound, see
test_assets.py. The url mirrors the pack folder of the run directory, so the
packs of every config are served under the name and the layout they have on
the disk; a pack that a run has not generated is a 404 and a range request is
a 206, the client walks the packs of a version with range requests.
"""
import os

import pytest

from alasio.deploy_dev.pack_server import serve
from alasio.deploy_dev.pack_server.serve import create_app, pack_folder
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.testing.filesystem import fs  # noqa: F401

# folder of the packs of the tests, one config of the run directory
FOLDER_NAME = 'Author_Repo_master'

# the packs of the tests: the version c3, the update pack of c2 and latest.pack
LATEST_PACK = b'c3' + bytes(range(20))
FULL_PACK = b'full-pack-bytes-' * 4
UPDATE_PACK = b'update-pack-bytes'


@pytest.fixture(autouse=True)
def align_commonpath(monkeypatch):
    """
    Align os.path.commonpath with the separator style of the mock.

    The filesystem mock's realpath returns forward slashes while the real
    os.path.commonpath returns backslashes on Windows; the path containment
    check of starlette's StaticFiles then mismatches inside the mock, like
    the same fixture of test_assets.py.
    """
    real_commonpath = os.path.commonpath

    def commonpath(paths):
        return real_commonpath(paths).replace('\\', '/')

    monkeypatch.setattr(os.path, 'commonpath', commonpath)


@pytest.fixture
def run_dir(fs, monkeypatch):
    """
    A run directory of the pack server in the fake filesystem.

    Returns:
        PathStr: Absolute path of the run directory
    """
    root = PathStr.new(fs.root_dir.path)
    folder = root.joinpath(serve.PACK_FOLDER).joinpath(FOLDER_NAME)
    version = folder.joinpath('c3')
    fs.create_dir(version)
    fs.create_file(version.joinpath('full_c3.pack'), contents=FULL_PACK)
    fs.create_file(version.joinpath('update_c2.pack'), contents=UPDATE_PACK)
    fs.create_file(folder.joinpath('latest.pack'), contents=LATEST_PACK)
    monkeypatch.setattr(env, 'PROJECT_ROOT', root)
    return root


def pack_url(*names):
    """Url of a file of the pack folder of the tests."""
    return '/'.join((serve.PACK_URL, FOLDER_NAME, *names))


def make_scope(path, headers=None):
    """
    Build a minimal http scope of a request.

    Args:
        path (str): The request path
        headers (dict[str, str], optional): Request headers

    Returns:
        dict: The scope
    """
    return {
        'type': 'http',
        'http_version': '1.1',
        'method': 'GET',
        'scheme': 'http',
        'path': path,
        'raw_path': path.encode('latin-1'),
        'query_string': b'',
        'root_path': '',
        'server': ('127.0.0.1', 8000),
        'client': ('127.0.0.1', 1234),
        'headers': [
            (key.lower().encode('latin-1'), value.encode('latin-1'))
            for key, value in (headers or {}).items()
        ],
    }


async def call_app(app, scope):
    """
    Call the app with a hand built scope and collect the response.

    Args:
        app (Starlette): App to call
        scope (dict): Scope of the request

    Returns:
        tuple[int, dict[str, str], bytes]: (status code, headers, body)
    """
    messages = []

    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    start = next(message for message in messages if message['type'] == 'http.response.start')
    headers = {key.decode('latin-1'): value.decode('latin-1') for key, value in start['headers']}
    body = b''.join(
        message['body'] for message in messages if message['type'] == 'http.response.body')
    return start['status'], headers, body


class TestPackFolder:
    """The folder to serve, the pack folder of the run directory."""

    def test_pack_folder(self, run_dir):
        """The whole pack folder of the run directory is served."""
        assert pack_folder() == run_dir.joinpath(serve.PACK_FOLDER)

    def test_no_pack_folder(self, fs, monkeypatch):
        """A run directory without a pack folder is refused."""
        empty = PathStr.new(fs.root_dir.path).joinpath('empty')
        fs.create_dir(empty)
        monkeypatch.setattr(env, 'PROJECT_ROOT', empty)
        with pytest.raises(ValueError, match='No pack folder'):
            pack_folder()


class TestPackRoutes:
    """The urls of the development server, the path of the packs on the disk."""

    @pytest.fixture
    def app(self, run_dir):
        """The app of the pack folder of the tests."""
        return create_app(pack_folder())

    @pytest.mark.trio
    async def test_latest_pack(self, app):
        """latest.pack of a repo is served under its own name."""
        status, headers, body = await call_app(app, make_scope(pack_url('latest.pack')))
        assert status == 200
        assert body == LATEST_PACK
        assert headers['content-length'] == str(len(LATEST_PACK))

    @pytest.mark.trio
    async def test_full_pack(self, app):
        """The full pack of a version is served under the name it has on the disk."""
        status, headers, body = await call_app(app, make_scope(pack_url('c3', 'full_c3.pack')))
        assert status == 200
        assert body == FULL_PACK
        # the client walks the packs of a version with range requests
        assert headers['accept-ranges'] == 'bytes'

    @pytest.mark.trio
    async def test_update_pack(self, app):
        """An update pack is served under the name it has on the disk."""
        status, headers, body = await call_app(app, make_scope(pack_url('c3', 'update_c2.pack')))
        assert status == 200
        assert body == UPDATE_PACK

    @pytest.mark.trio
    async def test_range_request(self, app):
        """A range request is a 206 of the requested bytes."""
        status, headers, body = await call_app(
            app, make_scope(pack_url('c3', 'full_c3.pack'), {'Range': 'bytes=2-5'}))
        assert status == 206
        assert body == FULL_PACK[2:6]
        assert headers['content-range'] == f'bytes 2-5/{len(FULL_PACK)}'

    @pytest.mark.trio
    async def test_missing_pack(self, app):
        """A pack that a run has not generated is a 404."""
        for path in (
                # no update pack of the old version c1 in the folder
                pack_url('c3', 'update_c1.pack'),
                # no folder of another version
                pack_url('c4', 'full_c4.pack'),
                # no folder of another config
                '/'.join((serve.PACK_URL, 'Author_Repo_dev', 'latest.pack')),
                # latest.pack is per repo, the pack folder itself has none
                f'{serve.PACK_URL}/latest.pack',
                # the packs live under the pack url
                '/latest.pack'):
            status, headers, body = await call_app(app, make_scope(path))
            assert status == 404, path

    @pytest.mark.trio
    async def test_walks_nowhere(self, app):
        """A path that would walk out of the pack folder is a 404."""
        for path in (
                f'{serve.PACK_URL}/{FOLDER_NAME}/../../../latest.pack',
                f'{serve.PACK_URL}/../log/main.txt'):
            status, headers, body = await call_app(app, make_scope(path))
            assert status == 404, path


class TestCommandLine:
    """The command line of the development server."""

    def test_create_config(self, run_dir):
        """The bind address comes from the command line, the folder from the run directory."""
        config, folder = serve.create_config(['--host', '0.0.0.0', '--port', '8123'])
        assert folder == run_dir.joinpath(serve.PACK_FOLDER)
        assert config.bind == ['0.0.0.0:8123']
        # a development server shows the requests of the client
        assert config.accesslog == '-'

    def test_defaults(self, run_dir):
        """The run directory and the bind address have defaults."""
        config, folder = serve.create_config([])
        assert folder == run_dir.joinpath(serve.PACK_FOLDER)
        assert config.bind == ['127.0.0.1:8000']
