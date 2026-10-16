"""
Tests for the pack client against the real development server, in-process.

The standard ASGI flow of httpx2 (httpx2.ASGITransport): the Starlette app of
the pack server (serve.create_app) runs in the event loop of the test, no
socket is bound and no handler mimics the server - the counterpart of the
MockTransport tests, which pin the client to canned responses. The client
(ServerFile) walks the packs of a version with its real range requests, so the
206 / 404 answers of starlette's StaticFiles and the client are exercised
together.

The pack folder of the tests is laid out as a deployment serves it
({mirror}/latest.pack, {mirror}/{version}/full.pack), the layout of the
packdep channels of dep_gen, and it lives in the pack folder the app serves
({run directory}/pack, see serve.pack_folder).
"""
import os

import httpx2
import pytest

from alasio.deploy.pack.server_file import ServerFile
from alasio.deploy_dev.pack_server import serve
from alasio.deploy_dev.pack_server.serve import create_app, pack_folder
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack.conftest import COMMIT, WEBSITE_FILES, WEBSITE_FULL_PACK, WEBSITE_INDEX_PACK, FakeMirrorTable

# folder of the packdep channel of the tests, below the pack folder
CHANNEL_FOLDER = 'Author_Repo_master/packdep/demo'
# url prefix of the channel: the app serves the pack folder under serve.PACK_URL
CHANNEL_URL = f'{serve.PACK_URL}/{CHANNEL_FOLDER}'
# mirror url of the channel: the app is the mirror
MIRROR_URL = f'http://pack{CHANNEL_URL}'
# url of a channel the app does not serve
MISSING_URL = f'http://pack{serve.PACK_URL}/Author_Repo_master'
# content of latest.pack: the version in bytes + the 20 bytes checksum of the
# index pack of the version, see LatestInfo
LATEST_PACK = COMMIT.encode() + WEBSITE_INDEX_PACK[-20:]
# range of the first request of the client, see ServerFile.HEADER_REQUEST_SIZE
HEADER_RANGE = 'bytes=0-63'
# range of the index pack, the second request of the client
INDEX_RANGE = f'bytes=0-{len(WEBSITE_INDEX_PACK) - 1}'


@pytest.fixture(autouse=True)
def align_commonpath(monkeypatch):
    """
    Align os.path.commonpath with the separator style of the mock.

    The filesystem mock's realpath returns forward slashes while the real
    os.path.commonpath returns backslashes on Windows; the path containment
    check of starlette's StaticFiles then mismatches inside the mock, like
    the same fixture of test_serve.py.
    """
    real_commonpath = os.path.commonpath

    def commonpath(paths):
        return real_commonpath(paths).replace('\\', '/')

    monkeypatch.setattr(os.path, 'commonpath', commonpath)


@pytest.fixture
def run_dir(fs, monkeypatch):
    """
    A run directory holding a packdep channel in the deployed layout.

    Returns:
        PathStr: Absolute path of the run directory
    """
    root = PathStr.new(fs.root_dir.path)
    channel = root.joinpath(serve.PACK_FOLDER).joinpath(CHANNEL_FOLDER)
    fs.create_dir(channel.joinpath(COMMIT))
    fs.create_file(channel.joinpath(COMMIT).joinpath('full.pack'), contents=WEBSITE_FULL_PACK)
    fs.create_file(channel.joinpath('latest.pack'), contents=LATEST_PACK)
    monkeypatch.setattr(env, 'PROJECT_ROOT', root)
    return root


def recording_app(app, requests):
    """
    Wrap an ASGI app to record the requests it answers.

    Args:
        app (Starlette): App to wrap
        requests (list): Appended with (method, path, Range header) of every
            http request

    Returns:
        Callable: The wrapping ASGI app
    """
    async def wrapped(scope, receive, send):
        if scope['type'] == 'http':
            headers = dict(scope['headers'])
            requests.append((
                scope['method'],
                scope['path'],
                headers.get(b'range', b'').decode('latin-1'),
            ))
        await app(scope, receive, send)

    return wrapped


def make_client(run_dir, requests=None):
    """
    The client of the tests: an httpx2.AsyncClient that talks to the app of
    the run directory in-process.

    Args:
        run_dir (PathStr): Run directory of the packs, see run_dir
        requests (list, optional): Requests of the app are recorded there,
            None for no recording

    Returns:
        httpx2.AsyncClient: The client (created inside the test, closed with
            `async with`)
    """
    app = create_app(pack_folder())
    if requests is not None:
        app = recording_app(app, requests)
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url='http://pack')


class TestPackClientAgainstTheServer:
    """ServerFile against the app of the run directory, in-process."""

    @pytest.mark.trio
    async def test_latest_info_is_served(self, run_dir):
        """latest.pack of the channel is read from the app and parsed."""
        async with make_client(run_dir) as client:
            server = ServerFile(MIRROR_URL, client=client)
            info = await server.get_latest_info()
        assert info.version == COMMIT
        assert info.checksum == WEBSITE_INDEX_PACK[-20:].hex()

    @pytest.mark.trio
    async def test_the_index_pack_is_assembled_from_ranges(self, run_dir):
        """The index pack is fetched with the two range requests of the
        client, the full pack is never transferred whole."""
        requests = []
        async with make_client(run_dir, requests) as client:
            server = ServerFile(MIRROR_URL, client=client)
            info = await server.get_latest_info()
            decoder = await server.get_index_pack(info.version, info.checksum)
        assert decoder.data.tobytes() == WEBSITE_INDEX_PACK
        # the walk of the client: latest.pack, then the ranges of the full
        # pack (the header, then the index section)
        assert requests == [
            ('GET', f'{CHANNEL_URL}/latest.pack', ''),
            ('GET', f'{CHANNEL_URL}/{COMMIT}/full.pack', HEADER_RANGE),
            ('GET', f'{CHANNEL_URL}/{COMMIT}/full.pack', INDEX_RANGE),
        ]

    @pytest.mark.trio
    async def test_the_full_pack_is_head_probed_when_no_record_serves(self, run_dir):
        """Without a record the path is probed first: a HEAD of the full pack
        (the app answers it), then the ranges of the flow."""
        requests = []
        mirrors = {'g': {'served': MIRROR_URL, 'missing': MISSING_URL}}
        async with make_client(run_dir, requests) as client:
            server = ServerFile(mirrors, client=client)
            server.RECORD_TABLE = FakeMirrorTable()
            decoder = await server.get_index_pack(COMMIT)
        assert decoder.data.tobytes() == WEBSITE_INDEX_PACK
        assert requests == [
            ('HEAD', f'{CHANNEL_URL}/{COMMIT}/full.pack', ''),
            ('GET', f'{CHANNEL_URL}/{COMMIT}/full.pack', HEADER_RANGE),
            ('GET', f'{CHANNEL_URL}/{COMMIT}/full.pack', INDEX_RANGE),
        ]

    @pytest.mark.trio
    async def test_a_file_is_fetched_with_a_range(self, run_dir):
        """The content of a file record is the range of the full pack the
        record describes, validated against the record."""
        async with make_client(run_dir) as client:
            server = ServerFile(MIRROR_URL, client=client)
            info = await server.get_latest_info()
            decoder = await server.get_index_pack(info.version, info.checksum)
            record = next(record for record in decoder.idx_info if record.path == 'docs/README.md')
            content = await server.get_file_data(decoder, record)
        assert content == WEBSITE_FILES['docs/README.md'][0]

    @pytest.mark.trio
    async def test_a_mirror_the_app_does_not_serve_is_skipped(self, run_dir):
        """The members of a group are probed in order: the first one is not
        served (a 404), the second one serves the channel."""
        requests = []
        mirrors = {'g': {'missing': MISSING_URL, 'served': MIRROR_URL}}
        async with make_client(run_dir, requests) as client:
            server = ServerFile(mirrors, client=client)
            server.RECORD_TABLE = FakeMirrorTable()
            info = await server.get_latest_info()
        assert info.version == COMMIT
        assert requests == [
            ('GET', f'{serve.PACK_URL}/Author_Repo_master/latest.pack', ''),
            ('GET', f'{CHANNEL_URL}/latest.pack', ''),
        ]

    @pytest.mark.trio
    async def test_a_missing_update_pack_is_a_404(self, run_dir):
        """A file the app does not serve is a 404: the failure is the answer
        of the server, the caller keeps its rebuild fallback."""
        async with make_client(run_dir) as client:
            server = ServerFile(MIRROR_URL, client=client)
            with pytest.raises(httpx2.HTTPStatusError) as e:
                await server.get_update_pack('c0', COMMIT)
        assert e.value.response.status_code == 404
