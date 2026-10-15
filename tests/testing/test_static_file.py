"""
Tests for MockStaticFile: the in-memory static file server of the tests.

The requests run in-process on the ASGI app of the server (httpx2 with
httpx2.ASGITransport), no socket is bound and no filesystem is involved: the
files live in the registry of the server.
"""
import httpx2
import pytest
from starlette.applications import Starlette
from starlette.routing import Mount

from alasio.testing.static_file import MockRangeNotSatisfiable, MockStaticFile

CONTENT = b'0123456789'


def make_client(server):
    """An httpx2.AsyncClient that talks to the app of the server in-process."""
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=server))


class TestParseRange:
    """parse_range(): the single range forms and the errors."""

    @pytest.mark.parametrize('http_range, size, expected', [
        ('bytes=0-3', 10, (0, 3)),
        ('bytes=2-5', 10, (2, 5)),
        ('bytes=0-9', 10, (0, 9)),
        # the end beyond the file is clamped, like a static file server
        ('bytes=8-100', 10, (8, 9)),
        # an open end reaches the last byte
        ('bytes=7-', 10, (7, 9)),
        # the last n bytes
        ('bytes=-3', 10, (7, 9)),
        ('bytes=-100', 10, (0, 9)),
        # the whitespace of a header value is not significant
        (' bytes=0-3 ', 10, (0, 3)),
    ])
    def test_ranges(self, http_range, size, expected):
        assert MockStaticFile.parse_range(http_range, size) == expected

    @pytest.mark.parametrize('http_range, size', [
        # outside the file
        ('bytes=10-', 10),
        ('bytes=10-12', 10),
        ('bytes=5-4', 10),
        ('bytes=-0', 10),
    ])
    def test_unsatisfiable(self, http_range, size):
        with pytest.raises(MockRangeNotSatisfiable):
            MockStaticFile.parse_range(http_range, size)

    @pytest.mark.parametrize('http_range', [
        # another unit
        'items=0-3',
        # several ranges: the multipart form the mock does not implement
        'bytes=0-1,3-4',
        # no range at all
        'bytes=-',
        '',
    ])
    def test_not_supported(self, http_range):
        with pytest.raises(ValueError):
            MockStaticFile.parse_range(http_range, 10)


class TestAnswers:
    """The files, the paths and the answers of the server."""

    @pytest.mark.trio
    async def test_the_whole_file(self):
        """A registered file is served with its content and its length."""
        server = MockStaticFile({'1.0/full.pack': CONTENT})
        async with make_client(server) as client:
            response = await client.get('/1.0/full.pack')
        assert response.status_code == 200
        assert response.content == CONTENT
        assert response.headers['content-length'] == str(len(CONTENT))
        assert response.headers['accept-ranges'] == 'bytes'

    @pytest.mark.trio
    async def test_a_missing_file_is_a_404(self):
        """A path nothing was registered for is a 404."""
        server = MockStaticFile({'latest.pack': CONTENT})
        async with make_client(server) as client:
            response = await client.get('/1.0/full.pack')
        assert response.status_code == 404

    @pytest.mark.trio
    async def test_register_file_replaces(self):
        """Registering a path again replaces the file."""
        server = MockStaticFile()
        server.register_file('latest.pack', b'old')
        server.register_file('/latest.pack', b'new')
        async with make_client(server) as client:
            response = await client.get('/latest.pack')
        assert response.content == b'new'

    @pytest.mark.trio
    async def test_a_directory_like_path_keeps_the_slash(self):
        """The index page of a mirror is registered at its path with the
        trailing slash the client asks."""
        server = MockStaticFile({'httpx/': CONTENT})
        async with make_client(server) as client:
            assert (await client.get('/httpx/')).content == CONTENT
            # the paths are exact: the page is not served without the slash
            assert (await client.get('/httpx')).status_code == 404

    @pytest.mark.trio
    async def test_head(self):
        """A HEAD answer carries the headers of the GET, no body."""
        server = MockStaticFile({'latest.pack': CONTENT})
        async with make_client(server) as client:
            response = await client.head('/latest.pack')
        assert response.status_code == 200
        assert response.content == b''
        assert response.headers['content-length'] == str(len(CONTENT))

    @pytest.mark.trio
    async def test_mounted_under_a_prefix(self):
        """The app is mountable: the file is looked up below the prefix."""
        server = MockStaticFile({'latest.pack': CONTENT})
        app = Starlette(routes=[Mount('/pack/demo', app=server)])
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)) as client:
            response = await client.get('/pack/demo/latest.pack')
        assert response.status_code == 200
        assert response.content == CONTENT

    @pytest.mark.trio
    async def test_a_range(self):
        """A Range request is answered with the bytes of the range."""
        server = MockStaticFile({'latest.pack': CONTENT})
        async with make_client(server) as client:
            response = await client.get('/latest.pack', headers={'Range': 'bytes=2-5'})
        assert response.status_code == 206
        assert response.content == CONTENT[2:6]
        assert response.headers['content-range'] == f'bytes 2-5/{len(CONTENT)}'
        assert response.headers['content-length'] == '4'

    @pytest.mark.trio
    async def test_the_last_bytes(self):
        """The range form of a trailing digest read: the last n bytes."""
        server = MockStaticFile({'latest.pack': CONTENT})
        async with make_client(server) as client:
            response = await client.get('/latest.pack', headers={'Range': 'bytes=-3'})
        assert response.status_code == 206
        assert response.content == CONTENT[-3:]

    @pytest.mark.trio
    async def test_a_range_outside_is_a_416(self):
        """A range outside the file is a 416 with the size of the file."""
        server = MockStaticFile({'latest.pack': CONTENT})
        async with make_client(server) as client:
            response = await client.get('/latest.pack', headers={'Range': 'bytes=100-200'})
        assert response.status_code == 416
        assert response.headers['content-range'] == f'bytes */{len(CONTENT)}'
