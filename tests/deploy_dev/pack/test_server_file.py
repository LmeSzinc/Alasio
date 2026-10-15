"""
Tests for ServerFile: the pack API of the async HTTP client of the update
server.

Uses conftest.WEBSITE_SERVER (in-memory MockServerFile) and an
httpx2.MockTransport client to exercise the pack requests without a real
server. Every test performing a request is async (pytest-trio).
"""
import httpx2
import pytest

from alasio.deploy.httpclient.probe_mirrors import RangeUnsupportedError
from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.deploy.pack.server_file import LatestInfo, ServerFile
from tests.deploy_dev.pack.conftest import (
    COMMIT, WEBSITE_FULL_PACK, WEBSITE_INDEX_PACK, WEBSITE_SERVER, FakeMirrorTable, make_server_file
)

# the checksum of the pack format: the trailing 20 bytes of the index section
CHECKSUM = bytes.fromhex(PackDecodeBase(WEBSITE_INDEX_PACK).index_checksum)


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
    """MockServerFile runs the whole pack API through the mock transport,
    serving the packs from the memory (a single mirror set: no probe)."""

    @pytest.mark.trio
    async def test_get_latest_info(self):
        """latest.pack data: the version and the index pack checksum."""
        info = await WEBSITE_SERVER.get_latest_info()
        assert isinstance(info, LatestInfo)
        assert info.version == COMMIT
        assert info.checksum == CHECKSUM.hex()

    @pytest.mark.trio
    async def test_get_index_pack(self):
        """The index pack is downloaded with two range requests and returned
        as a validated decoder (no second decode by the caller)."""
        decoder = await WEBSITE_SERVER.get_index_pack(COMMIT)
        assert isinstance(decoder, PackDecodeBase)
        assert decoder.data.tobytes() == WEBSITE_INDEX_PACK
        assert decoder.current_version == COMMIT

    @pytest.mark.trio
    async def test_get_file_data(self):
        """The content of a record is fetched, decoded and validated against
        the record."""
        decoder = await WEBSITE_SERVER.get_index_pack(COMMIT)
        info = decoder.fileinfo['backend/main.py']
        content = await WEBSITE_SERVER.get_file_data(decoder, info)
        assert content == b'import uvicorn\n\nif __name__ == "__main__":\n    uvicorn.run("app:app")\n'

    @pytest.mark.trio
    async def test_get_file_data_empty(self):
        """A record without data (an empty file) needs no request."""
        decoder = await WEBSITE_SERVER.get_index_pack(COMMIT)
        info = decoder.fileinfo['backend/__init__.py']
        assert await WEBSITE_SERVER.get_file_data(decoder, info) == b''


class TestPackRequests:
    """The request shapes and the payload validation of the pack API, with a
    MockTransport client."""

    @pytest.mark.trio
    async def test_get_latest_info(self):
        """latest.pack is parsed as version + 20 bytes checksum."""
        requests = []

        def handler(request):
            requests.append(request)
            return httpx2.Response(200, content=COMMIT.encode() + CHECKSUM)

        server = ServerFile('http://test', client=make_aclient(handler))
        info = await server.get_latest_info()
        assert info.version == COMMIT
        assert info.checksum == CHECKSUM.hex()
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
    async def test_get_index_pack_ranges(self):
        """Two range requests download the self-validating index pack."""
        requests = []
        server = ServerFile(
            'http://test', client=make_aclient(range_handler(requests, WEBSITE_FULL_PACK)))
        decoder = await server.get_index_pack(COMMIT)
        assert decoder.data.tobytes() == WEBSITE_INDEX_PACK
        assert decoder.current_version == COMMIT
        # first request: the header and the index section length
        assert requests[0].headers['Range'] == f'bytes=0-{ServerFile.HEADER_REQUEST_SIZE - 1}'
        # second request: the exact range of the index pack
        assert requests[1].headers['Range'] == f'bytes=0-{len(WEBSITE_INDEX_PACK) - 1}'
        assert str(requests[0].url) == f'http://test/{COMMIT}/full.pack'

    @pytest.mark.trio
    async def test_get_index_pack_checksum_mismatch(self):
        """The expected checksum is verified: a mirror that serves an index
        pack latest.pack does not advertise is not usable."""
        server = ServerFile(
            'http://test',
            client=make_aclient(range_handler([], WEBSITE_FULL_PACK)))
        with pytest.raises(PackDecodeError, match='checksum mismatch'):
            await server.get_index_pack(COMMIT, checksum='00' * 20)

    @pytest.mark.trio
    async def test_get_index_pack_invalid_header(self):
        """An unterminated length vint fails."""
        def handler(request):
            return httpx2.Response(206, content=b'\x80' * 64)
        server = ServerFile('http://test', client=make_aclient(handler))
        with pytest.raises(PackDecodeError):
            await server.get_index_pack(COMMIT)

    @pytest.mark.trio
    async def test_range_ignored_raises(self):
        """A mirror that ignores the range request (a 200 answer) is not
        usable: the whole pack would be transferred for every file."""
        def handler(request):
            return httpx2.Response(200, content=WEBSITE_FULL_PACK)
        server = ServerFile('http://test', client=make_aclient(handler))
        with pytest.raises(RangeUnsupportedError):
            await server.get_index_pack(COMMIT)

    @pytest.mark.trio
    async def test_get_update_pack(self):
        """The update pack url is {new}/from_{old}.pack, the pack is validated
        before it is returned."""
        requests = []

        def handler(request):
            requests.append(request)
            return httpx2.Response(200, content=b'not a pack')

        server = ServerFile('http://test', client=make_aclient(handler))
        with pytest.raises(PackDecodeError):
            await server.get_update_pack('old', 'new')
        assert str(requests[0].url) == 'http://test/new/from_old.pack'

    @pytest.mark.trio
    async def test_get_update_pack_error(self):
        """A 404 response raises HTTPStatusError (the caller falls back to a
        rebuild)."""
        def handler(request):
            return httpx2.Response(404)
        server = ServerFile('http://test', client=make_aclient(handler))
        with pytest.raises(httpx2.HTTPStatusError):
            await server.get_update_pack('old', 'new')

    @pytest.mark.trio
    async def test_get_update_pack_checksum(self):
        """The trailing whole-pack digest is read with one 20 bytes range
        request, the body is not transferred."""
        requests = []
        size = len(WEBSITE_FULL_PACK)
        server = ServerFile(
            'http://test',
            client=make_aclient(range_handler(requests, WEBSITE_FULL_PACK)))
        checksum = await server.get_update_pack_checksum('old', 'new', size)
        assert checksum == WEBSITE_FULL_PACK[-20:].hex()
        assert str(requests[0].url) == 'http://test/new/from_old.pack'
        assert requests[0].headers['Range'] == f'bytes={size - 20}-{size - 1}'


class TestConstruction:
    """ServerFile accepts the mirror input and the identity of the set."""

    def test_mirror_input(self):
        """A raw mirror input and a name build the structure of the set."""
        server = ServerFile(
            {'cn': {'123pan': 'http://pan'}, 'global': 'http://global'}, name='alas')
        assert server.name == 'alas'
        assert server.SCENE == 'repo'
        assert server.mirrors.single == ''
        assert server.mirrors.url_of('123pan') == 'http://pan'
        assert server.mirrors.url_of('global') == 'http://global'

    def test_str_input_is_a_single_mirror(self):
        """A str input stays the plain single mirror shortcut."""
        server = ServerFile('http://only')
        assert server.mirrors.single == 'default'
        assert server.mirrors.url_of('default') == 'http://only'
        assert server.name == ''

    @pytest.mark.trio
    async def test_name_reaches_the_record(self):
        """The name given to ServerFile is the name column of the gui.db
        record of its set."""
        table = FakeMirrorTable()
        server = make_server_file({'g': {'b': 'http://b', 'a': 'http://a'}}, name='alas',
                                  table=table,
                                  client=make_aclient(lambda request: httpx2.Response(
                                      200, content=COMMIT.encode() + CHECKSUM)))
        await server.get_latest_info()
        row = table.select_one(scene='repo', name='alas')
        # the first member of the group wins the race
        assert row.result == 'b'
        assert table.select_one(scene='repo', name='') is None


class TrackingClient(httpx2.AsyncClient):
    """The real client of the instance, with the instances tracked: the
    transport is the mock of the test."""

    instances = []

    def __init__(self, handler, *args, **kwargs):
        super().__init__(*args, transport=httpx2.MockTransport(handler), **kwargs)
        self.handler_requests = []
        self.closed = False
        TrackingClient.instances.append(self)

    async def aclose(self):
        self.closed = True
        await super().aclose()


class TestClientReuse:
    """The async http client of an instance: an injected one is used as-is, a
    created one is reused by every request and closed by aclose()."""

    @pytest.mark.trio
    async def test_created_once_and_reused(self, monkeypatch):
        """Without an injected client, the first request creates the client
        of the instance and every later request reuses it."""
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return httpx2.Response(200, content=COMMIT.encode() + CHECKSUM)

        TrackingClient.instances.clear()
        monkeypatch.setattr(
            httpx2, 'AsyncClient',
            lambda *args, **kwargs: TrackingClient(handler, *args, **kwargs))

        server = ServerFile('http://only')
        assert (await server.get_latest_info()).version == COMMIT
        assert (await server.get_latest_info()).version == COMMIT
        assert len(TrackingClient.instances) == 1
        assert requests == ['http://only/latest.pack', 'http://only/latest.pack']
        # aclose() closes the client the instance created
        await server.aclose()
        assert TrackingClient.instances[0].closed is True

    @pytest.mark.trio
    async def test_injected_client_is_never_closed(self):
        """An injected client belongs to the caller: aclose() leaves it
        alone."""
        client = make_aclient(lambda request: httpx2.Response(
            200, content=b'v1' + b'\x00' * 20))
        server = ServerFile('http://only', client=client)
        assert (await server.get_latest_info()).version == 'v1'
        await server.aclose()
        # the injected client still works
        assert (await client.get('http://only/latest.pack')).status_code == 200


class TestRequestTimeout:
    """latest.pack uses the short probe budget, the pack downloads use the
    default timeout of the http client."""

    @pytest.mark.trio
    async def test_latest_pack_timeout(self):
        """A latest.pack request uses PROBE_TIMEOUT on every phase."""
        from alasio.deploy.httpclient.async_client import AsyncHttpClient
        timeouts = []

        def handler(request):
            timeouts.append(request.extensions['timeout'])
            return httpx2.Response(200, content=COMMIT.encode() + CHECKSUM)

        server = ServerFile('http://only', client=make_aclient(handler))
        await server.get_latest_info()
        assert timeouts == [{
            'connect': AsyncHttpClient.PROBE_TIMEOUT,
            'read': AsyncHttpClient.PROBE_TIMEOUT,
            'write': AsyncHttpClient.PROBE_TIMEOUT,
            'pool': AsyncHttpClient.PROBE_TIMEOUT,
        }]

    @pytest.mark.trio
    async def test_download_timeout(self):
        """A pack range uses the default timeout of the http client: the
        connection budget stays short, the read/write budget is longer."""
        from alasio.deploy.httpclient.async_client import AsyncHttpClient
        timeouts = []

        def handler(request):
            timeouts.append(request.extensions['timeout'])
            return httpx2.Response(206, content=b'\x81')

        server = ServerFile('http://only', client=make_aclient(handler))
        with pytest.raises(PackDecodeError):
            await server.get_index_pack(COMMIT)
        assert timeouts == [{
            'connect': AsyncHttpClient.PROBE_TIMEOUT,
            'read': AsyncHttpClient.DEFAULT_TIMEOUT.read,
            'write': AsyncHttpClient.DEFAULT_TIMEOUT.write,
            'pool': AsyncHttpClient.PROBE_TIMEOUT,
        }]
        assert AsyncHttpClient.DEFAULT_TIMEOUT.read > AsyncHttpClient.PROBE_TIMEOUT
