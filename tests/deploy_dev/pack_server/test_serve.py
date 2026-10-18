"""
Tests for the development server of the packs (serve.py).

The app is driven with starlette's TestClient (starlette.testclient, backed by
httpx2 in this project): the requests run in-process on the app, no socket is
bound and the app runs on a trio backend, like the production server
(hypercorn.trio). The url mirrors the pack folder of the run directory, so the
packs of every config are served under the name and the layout they have on
the disk; a pack that a run has not generated is a 404 and a range request is
a 206, the client walks the packs of a version with range requests.

One test is the exception: the Client of the TestClient normalizes the '..'
segments of a url before the request is sent, so a path that walks out of the
pack folder only reaches the app through a raw ASGI scope - it is built by
hand there, see raw_get().
"""
import os

import pytest
from starlette.testclient import TestClient

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
    packrepo = folder.joinpath(serve.PACKREPO_FOLDER)
    version = packrepo.joinpath('c3')
    fs.create_dir(version)
    fs.create_file(version.joinpath('full.pack'), contents=FULL_PACK)
    fs.create_file(version.joinpath('update_c2.pack'), contents=UPDATE_PACK)
    fs.create_file(packrepo.joinpath('latest.pack'), contents=LATEST_PACK)
    monkeypatch.setattr(env, 'PROJECT_ROOT', root)
    return root


@pytest.fixture
def app(run_dir):
    """The app of the pack folder of the tests."""
    return create_app(pack_folder())


@pytest.fixture
def client(app):
    """
    The TestClient of the app of the tests, the app runs on a background trio
    loop (the backend of the production server) and is closed on exit.
    """
    with TestClient(app, backend='trio') as client:
        yield client


def pack_url(*names):
    """Url of a file of the pack folder of the tests."""
    return '/'.join((serve.PACK_URL, FOLDER_NAME, serve.PACKREPO_FOLDER, *names))


async def raw_get(app, path):
    """
    Call the app with a hand built scope and collect the response.

    The Client of the TestClient normalizes the '..' segments of a url before
    the request is sent, a traversal path only reaches the app through a raw
    scope: the scope is built by hand and the app is called directly here.

    Args:
        app (Starlette): App to call
        path (str): Path of the request, sent as it is

    Returns:
        tuple[int, dict[str, str], bytes]: (status code, headers, body)
    """
    scope = {
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
        'headers': [],
    }
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

    def test_latest_pack(self, client):
        """latest.pack of a repo is served under its own name."""
        response = client.get(pack_url('latest.pack'))
        assert response.status_code == 200
        assert response.content == LATEST_PACK
        assert response.headers['content-length'] == str(len(LATEST_PACK))

    def test_full_pack(self, client):
        """The full pack of a version is served under the name it has on the disk."""
        response = client.get(pack_url('c3', 'full.pack'))
        assert response.status_code == 200
        assert response.content == FULL_PACK
        # the client walks the packs of a version with range requests
        assert response.headers['accept-ranges'] == 'bytes'

    def test_update_pack(self, client):
        """An update pack is served under the name it has on the disk."""
        response = client.get(pack_url('c3', 'update_c2.pack'))
        assert response.status_code == 200
        assert response.content == UPDATE_PACK

    def test_range_request(self, client):
        """A range request is a 206 of the requested bytes."""
        response = client.get(pack_url('c3', 'full.pack'), headers={'Range': 'bytes=2-5'})
        assert response.status_code == 206
        assert response.content == FULL_PACK[2:6]
        assert response.headers['content-range'] == f'bytes 2-5/{len(FULL_PACK)}'

    def test_missing_pack(self, client):
        """A pack that a run has not generated is a 404."""
        for path in (
                # no update pack of the old version c1 in the folder
                pack_url('c3', 'update_c1.pack'),
                # no folder of another version
                pack_url('c4', 'full.pack'),
                # no folder of another config
                '/'.join((serve.PACK_URL, 'Author_Repo_dev', serve.PACKREPO_FOLDER, 'latest.pack')),
                # latest.pack is per repo, the pack folder itself has none
                f'{serve.PACK_URL}/latest.pack',
                # the packs live under the pack url
                '/latest.pack'):
            assert client.get(path).status_code == 404, path

    @pytest.mark.trio
    async def test_walks_nowhere(self, app):
        """A path that would walk out of the pack folder is a 404."""
        for path in (
                f'{serve.PACK_URL}/{FOLDER_NAME}/../../../latest.pack',
                f'{serve.PACK_URL}/../log/main.txt'):
            status, headers, body = await raw_get(app, path)
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
