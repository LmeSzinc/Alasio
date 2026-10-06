"""
Tests for ServerFile: async HTTP client of the update server.

Uses conftest.WEBSITE_SERVER (in-memory MockServerFile) and an
httpx2.MockTransport client to exercise the http request logic of
ServerFile without a real server. Every test performing a request is
async (pytest-trio): the requests await on the test event loop.
"""
import httpx2
import pytest
import trio

from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.deploy.pack.server_file import LatestInfo, ServerFile
from alasio.ext.cache import cached_property
from alasio.logger import logger
from tests.deploy_dev.pack.conftest import (
    COMMIT, WEBSITE_FULL_PACK, WEBSITE_INDEX_PACK, WEBSITE_SERVER, FakeMirrorTable, make_server_url
)


def range_handler(requests, data):
    """A MockTransport handler that serves range requests from data."""
    def handler(request):
        requests.append(request)
        start, _, end = request.headers['Range'].partition('=')[2].partition('-')
        return httpx2.Response(206, content=data[int(start):int(end) + 1])
    return handler


def make_aclient(handler):
    """A httpx2.AsyncClient with a MockTransport handler."""
    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


class TestMockServerFile:
    """MockServerFile runs the whole ServerFile logic through the mock
    transport, serving the packs from the memory."""

    @pytest.mark.trio
    async def test_get_latest_info(self):
        """latest.pack data: version and the index pack checksum."""
        info = await WEBSITE_SERVER.get_latest_info()
        assert isinstance(info, LatestInfo)
        assert info.version == COMMIT
        # the checksum of the pack format: the trailing 20 bytes of
        # the index section, not a checksum of the whole index file
        checksum = bytes.fromhex(PackDecodeBase(WEBSITE_INDEX_PACK).index_checksum)
        assert info.checksum == checksum.hex()

    @pytest.mark.trio
    async def test_get_file_content(self):
        """A range of the full pack is sliced from the memory."""
        assert await WEBSITE_SERVER.get_file_content(COMMIT, 0, 10) == WEBSITE_FULL_PACK[0:10]
        assert await WEBSITE_SERVER.get_file_content(COMMIT, 5, 10) == WEBSITE_FULL_PACK[5:15]
        assert await WEBSITE_SERVER.get_file_content(COMMIT, 100, 5) == WEBSITE_FULL_PACK[100:105]

    @pytest.mark.trio
    async def test_get_index_pack(self):
        """get_index_pack() downloads the index pack with two range
        requests through the mock transport."""
        index_pack = await WEBSITE_SERVER.get_index_pack(COMMIT)
        assert index_pack == WEBSITE_INDEX_PACK
        # it must be a valid index pack
        decoder = PackDecodeBase(index_pack)
        decoder.validate_index()
        assert decoder.current_version == COMMIT


class TestServerFile:
    """ServerFile http requests, with a MockTransport client."""

    @pytest.mark.trio
    async def test_get_latest_info(self):
        """latest.pack is parsed as version + 20 bytes checksum."""
        requests = []
        # the checksum of the pack format: the trailing 20 bytes of
        # the index section
        checksum = bytes.fromhex(PackDecodeBase(WEBSITE_INDEX_PACK).index_checksum)

        def handler(request):
            requests.append(request)
            content = COMMIT.encode() + checksum
            return httpx2.Response(200, content=content)

        server = ServerFile('http://test', client=make_aclient(handler))
        info = await server.get_latest_info()
        assert info.version == COMMIT
        assert info.checksum == checksum.hex()
        assert str(requests[0].url) == 'http://test/latest.pack'

    @pytest.mark.trio
    async def test_get_latest_info_too_short(self):
        """A response without the 20 bytes checksum fails."""
        def handler(request):
            return httpx2.Response(200, content=b'c1')
        server = ServerFile('http://test', client=make_aclient(handler))
        with pytest.raises(PackDecodeError):
            await server.get_latest_info()

    @pytest.mark.trio
    async def test_get_file_content_range(self):
        """A range request returns the range of the full pack."""
        requests = []
        server = ServerFile(
            'http://test', client=make_aclient(range_handler(requests, WEBSITE_FULL_PACK)))
        assert await server.get_file_content(COMMIT, 5, 10) == WEBSITE_FULL_PACK[5:15]
        assert str(requests[0].url) == f'http://test/{COMMIT}/full.pack'
        assert requests[0].headers['Range'] == 'bytes=5-14'

    @pytest.mark.trio
    async def test_get_file_content_range_ignored(self):
        """A 200 response means the server ignored the range request."""
        def handler(request):
            return httpx2.Response(200, content=WEBSITE_FULL_PACK)
        server = ServerFile('http://test', client=make_aclient(handler))
        assert await server.get_file_content(COMMIT, 5, 10) == WEBSITE_FULL_PACK[5:15]

    @pytest.mark.trio
    async def test_get_file_content_error(self):
        """A 404 response raises HTTPStatusError."""
        def handler(request):
            return httpx2.Response(404)
        server = ServerFile('http://test', client=make_aclient(handler))
        with pytest.raises(httpx2.HTTPStatusError):
            await server.get_file_content(COMMIT, 0, 10)

    @pytest.mark.trio
    async def test_get_index_pack(self):
        """Two range requests download the self-validating index pack."""
        requests = []
        server = ServerFile(
            'http://test', client=make_aclient(range_handler(requests, WEBSITE_FULL_PACK)))
        index_pack = await server.get_index_pack(COMMIT)
        assert index_pack == WEBSITE_INDEX_PACK
        # the trailing checksum is included, the index pack validates
        # itself with PackDecodeBase
        decoder = PackDecodeBase(index_pack)
        decoder.validate_index()
        assert decoder.current_version == COMMIT
        # first request: the header and the index section length
        assert requests[0].headers['Range'] == f'bytes=0-{ServerFile.HEADER_REQUEST_SIZE - 1}'
        # second request: the exact range of the index pack
        assert requests[1].headers['Range'] == f'bytes=0-{len(WEBSITE_INDEX_PACK) - 1}'

    @pytest.mark.trio
    async def test_get_index_pack_invalid_header(self):
        """An unterminated length vint fails."""
        def handler(request):
            return httpx2.Response(206, content=b'\x80' * 64)
        server = ServerFile('http://test', client=make_aclient(handler))
        with pytest.raises(PackDecodeError):
            await server.get_index_pack(COMMIT)

    @pytest.mark.trio
    async def test_get_update_pack(self):
        """The update pack url is {new}/from_{old}.pack."""
        requests = []

        def handler(request):
            requests.append(request)
            return httpx2.Response(200, content=b'update pack data')

        server = ServerFile('http://test', client=make_aclient(handler))
        assert await server.get_update_pack('old', 'new') == b'update pack data'
        assert str(requests[0].url) == 'http://test/new/from_old.pack'

    @pytest.mark.trio
    async def test_get_update_pack_error(self):
        """A 404 response raises HTTPStatusError."""
        def handler(request):
            return httpx2.Response(404)
        server = ServerFile('http://test', client=make_aclient(handler))
        with pytest.raises(httpx2.HTTPStatusError):
            await server.get_update_pack('old', 'new')


class TestConstruction:
    """ServerFile accepts the raw mirror input and builds its ServerUrl."""

    def test_mirror_input_builds_the_server_url(self):
        """A raw mirror input and a scope build the ServerUrl."""
        server = ServerFile(
            {'cn': {'123pan': 'http://pan'}, 'global': 'http://global'}, scope='pack')
        assert server.server_url.scope == 'pack'
        assert server.server_url.single == ''
        assert server.server_url.url_of('123pan') == 'http://pan'
        assert server.server_url.url_of('global') == 'http://global'

    def test_str_input_is_a_single_mirror(self):
        """A str input stays the plain single mirror shortcut."""
        server = ServerFile('http://only')
        assert server.server_url.single == 'default'
        assert server.server_url.url_of('default') == 'http://only'

    def test_server_url_input_is_used_as_is(self):
        """A given ServerUrl is used as-is, the scope is not applied."""
        server_url = make_server_url('http://only')
        server = ServerFile(server_url, scope='ignored')
        assert server.server_url is server_url
        assert server_url.scope == ''

    def test_scope_reaches_the_record(self):
        """The scope given to ServerFile is the scope of the gui.db
        record of its ServerUrl."""
        table = FakeMirrorTable()
        server = ServerFile({'g': {'b': 'http://b', 'a': 'http://a'}}, scope='pack')
        cached_property.set(server.server_url, '_db', table)
        server.server_url.set_name('a')
        assert table.select_one(scope='pack').name == 'a'
        assert table.select_one(scope='') is None


class FakeClient:
    """Stand-in of httpx2.AsyncClient for the reuse tests: every
    instance is tracked, latest.pack is served from the memory."""

    instances = []

    def __init__(self, *args, **kwargs):
        FakeClient.instances.append(self)
        self.requests = []
        self.closed = False

    async def get(self, url, headers=None, **kwargs):
        self.requests.append(str(url))
        return httpx2.Response(
            200, content=b'v1' + b'\x00' * 20, request=httpx2.Request('GET', url))

    async def aclose(self):
        self.closed = True


class TestClientReuse:
    """The async http client of an instance: the injected one is used
    as-is, a created one is reused by every request (keep-alive)."""

    @pytest.mark.trio
    async def test_created_once_and_reused(self, monkeypatch):
        """Without an injected client, the first request creates the
        client of the instance and every later request reuses it."""
        FakeClient.instances.clear()
        monkeypatch.setattr(httpx2, 'AsyncClient', FakeClient)

        server = ServerFile('http://only')
        assert (await server.get_latest_info()).version == 'v1'
        assert (await server.get_latest_info()).version == 'v1'
        assert len(FakeClient.instances) == 1
        assert FakeClient.instances[0].requests == [
            'http://only/latest.pack',
            'http://only/latest.pack',
        ]

    @pytest.mark.trio
    async def test_created_client_is_closed(self, monkeypatch):
        """aclose() closes the client the instance created, closing
        again is a no-op, a close before the first request is one."""
        FakeClient.instances.clear()
        monkeypatch.setattr(httpx2, 'AsyncClient', FakeClient)

        server = ServerFile('http://only')
        # nothing was created yet, aclose is a no-op
        await server.aclose()
        assert FakeClient.instances == []
        await server.get_latest_info()
        client = FakeClient.instances[0]
        assert not client.closed
        await server.aclose()
        assert client.closed
        await server.aclose()
        assert client.closed

    @pytest.mark.trio
    async def test_context_manager_closes(self, monkeypatch):
        """`async with ServerFile(...) as server:` closes the created
        client on exit."""
        FakeClient.instances.clear()
        monkeypatch.setattr(httpx2, 'AsyncClient', FakeClient)

        async with ServerFile('http://only') as server:
            assert (await server.get_latest_info()).version == 'v1'
            assert not FakeClient.instances[0].closed
        assert FakeClient.instances[0].closed

    @pytest.mark.trio
    async def test_injected_client_is_used_and_never_closed(self):
        """An injected client serves the requests and is left alone by
        aclose(): its lifetime belongs to the caller."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return httpx2.Response(200, content=b'v1' + b'\x00' * 20)

        client = make_aclient(handler)
        server = ServerFile('http://only', client=client)
        assert (await server.get_latest_info()).version == 'v1'
        await server.aclose()
        assert not client.is_closed
        # the injected client is still usable
        assert (await server.get_latest_info()).version == 'v1'
        assert requests == ['http://only/latest.pack', 'http://only/latest.pack']

    @pytest.mark.trio
    async def test_probe_creates_one_client(self, monkeypatch):
        """The probe of a first request runs the groups in tasks on one
        thread: the client is still created once and shared."""
        FakeClient.instances.clear()
        monkeypatch.setattr(httpx2, 'AsyncClient', FakeClient)

        server = ServerFile(
            make_server_url({'a': 'http://a', 'b': 'http://b'}, table=FakeMirrorTable()))
        with logger.mock_capture_writer():
            name, info = await server.probe()
        assert name in ('a', 'b')
        assert info.version == 'v1'
        assert len(FakeClient.instances) == 1


class TestCancellation:
    """A cancelled task interrupts the in-flight request immediately."""

    @pytest.mark.trio
    async def test_in_flight_request_is_cancelled(self):
        """The await of an in-flight request is a cancellation point:
        cancelling the task drops the request instead of waiting for
        its timeout."""
        entered = trio.Event()

        async def handler(request):
            entered.set()
            # the request never answers: only a cancellation ends it
            await trio.sleep_forever()

        server = ServerFile('http://test', client=make_aclient(handler))
        with logger.mock_capture_writer():
            async with trio.open_nursery() as nursery:
                nursery.start_soon(server.get_latest_info)
                await entered.wait()
                nursery.cancel_scope.cancel()
        # the cancelled call ended on the cancellation, not on the
        # request timeout, and the nursery exited cleanly
        assert entered.is_set()
