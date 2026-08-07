"""
Tests for ServerFile: HTTP client of the update server.

Uses conftest.WEBSITE_SERVER (in-memory MockServerFile) and an
httpx.MockTransport client to exercise the http request logic of
ServerFile without a real server.
"""
from hashlib import sha1

import httpx
import pytest
from conftest import COMMIT, WEBSITE_FULL_PACK, WEBSITE_INDEX_PACK, WEBSITE_SERVER

from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.deploy.pack.server_file import LatestInfo, ServerFile


def range_handler(requests, data):
    """A MockTransport handler that serves range requests from data."""
    def handler(request):
        requests.append(request)
        start, _, end = request.headers['Range'].partition('=')[2].partition('-')
        return httpx.Response(206, content=data[int(start):int(end) + 1])
    return handler


def make_client(handler):
    """A httpx.Client with a MockTransport handler."""
    return httpx.Client(transport=httpx.MockTransport(handler))


class TestMockServerFile:
    """MockServerFile runs the whole ServerFile logic through the mock
    transport, serving the packs from the memory."""

    def test_get_latest_info(self):
        """latest.pack data: version and the index pack checksum."""
        info = WEBSITE_SERVER.get_latest_info()
        assert isinstance(info, LatestInfo)
        assert info.version == COMMIT
        assert info.checksum == sha1(WEBSITE_INDEX_PACK).hexdigest()

    def test_get_file_content(self):
        """A range of the full pack is sliced from the memory."""
        assert WEBSITE_SERVER.get_file_content(COMMIT, 0, 10) == WEBSITE_FULL_PACK[0:10]
        assert WEBSITE_SERVER.get_file_content(COMMIT, 5, 10) == WEBSITE_FULL_PACK[5:15]
        assert WEBSITE_SERVER.get_file_content(COMMIT, 100, 5) == WEBSITE_FULL_PACK[100:105]

    def test_get_index_pack(self):
        """get_index_pack() downloads the index pack with two range
        requests through the mock transport."""
        index_pack = WEBSITE_SERVER.get_index_pack(COMMIT)
        assert index_pack == WEBSITE_INDEX_PACK
        # it must be a valid index pack
        decoder = PackDecodeBase(index_pack)
        decoder.validate_index()
        assert decoder.version == COMMIT


class TestServerFile:
    """ServerFile http requests, with a MockTransport client."""

    def test_get_latest_info(self):
        """latest.pack is parsed as version + 20 bytes checksum."""
        requests = []

        def handler(request):
            requests.append(request)
            content = COMMIT.encode() + sha1(WEBSITE_INDEX_PACK).digest()
            return httpx.Response(200, content=content)

        server = ServerFile('http://test', client=make_client(handler))
        info = server.get_latest_info()
        assert info.version == COMMIT
        assert info.checksum == sha1(WEBSITE_INDEX_PACK).hexdigest()
        assert str(requests[0].url) == 'http://test/latest.pack'

    def test_get_latest_info_too_short(self):
        """A response without the 20 bytes checksum fails."""
        def handler(request):
            return httpx.Response(200, content=b'c1')
        server = ServerFile('http://test', client=make_client(handler))
        with pytest.raises(PackDecodeError):
            server.get_latest_info()

    def test_get_file_content_range(self):
        """A range request returns the range of the full pack."""
        requests = []
        server = ServerFile(
            'http://test', client=make_client(range_handler(requests, WEBSITE_FULL_PACK)))
        assert server.get_file_content(COMMIT, 5, 10) == WEBSITE_FULL_PACK[5:15]
        assert str(requests[0].url) == f'http://test/{COMMIT}/full.pack'
        assert requests[0].headers['Range'] == 'bytes=5-14'

    def test_get_file_content_range_ignored(self):
        """A 200 response means the server ignored the range request."""
        def handler(request):
            return httpx.Response(200, content=WEBSITE_FULL_PACK)
        server = ServerFile('http://test', client=make_client(handler))
        assert server.get_file_content(COMMIT, 5, 10) == WEBSITE_FULL_PACK[5:15]

    def test_get_file_content_error(self):
        """A 404 response raises HTTPStatusError."""
        def handler(request):
            return httpx.Response(404)
        server = ServerFile('http://test', client=make_client(handler))
        with pytest.raises(httpx.HTTPStatusError):
            server.get_file_content(COMMIT, 0, 10)

    def test_get_index_pack(self):
        """Two range requests download the self-validating index pack."""
        requests = []
        server = ServerFile(
            'http://test', client=make_client(range_handler(requests, WEBSITE_FULL_PACK)))
        index_pack = server.get_index_pack(COMMIT)
        assert index_pack == WEBSITE_INDEX_PACK
        # the trailing checksum is included, the index pack validates
        # itself with PackDecodeBase
        decoder = PackDecodeBase(index_pack)
        decoder.validate_index()
        assert decoder.version == COMMIT
        # first request: the header and the index section length
        assert requests[0].headers['Range'] == f'bytes=0-{ServerFile.HEADER_REQUEST_SIZE - 1}'
        # second request: the exact range of the index pack
        assert requests[1].headers['Range'] == f'bytes=0-{len(WEBSITE_INDEX_PACK) - 1}'

    def test_get_index_pack_invalid_header(self):
        """An unterminated length vint fails."""
        def handler(request):
            return httpx.Response(206, content=b'\x80' * 64)
        server = ServerFile('http://test', client=make_client(handler))
        with pytest.raises(PackDecodeError):
            server.get_index_pack(COMMIT)
