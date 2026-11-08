"""
Tests for the screenshots of the golden test review page
(alasio/backend/dev/golden_test.py).

The route streams a file of the `collection` folder of a mod, and only the
desktop client may reach it: the electron token is checked before the path is
resolved. The path itself is relative to `collection`, like the path of the
`image` decorator, and it never leaves the folder.
"""

from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
import trio

from alasio.backend.dev.assets import ImageStaticFiles
from alasio.backend.dev.golden_test import MEDIA_TYPES, router
from alasio.backend.mpipe.token_backend import token_table
from alasio.config.entry.loader import MOD_LOADER
from alasio.ext.path import PathStr
from alasio.ext.starapi.param import HTTPExceptionJson
from alasio.testing.filesystem import fs  # noqa: F401

MOD_NAME = 'test_mod'


@pytest.fixture
def project(fs, monkeypatch):
    """
    A fake project with one mod, pointing MOD_LOADER at it

    Returns:
        PathStr: Root of the mod, the folder holding `tests` and `collection`
    """
    root = PathStr.new(fs.root_dir.path).joinpath('project')
    monkeypatch.setattr(MOD_LOADER, 'dict_mod', {
        MOD_NAME: SimpleNamespace(root=root, name=MOD_NAME),
    })
    fs.create_dir(root.joinpath('collection'))
    return root


@pytest.fixture
def electron(monkeypatch):
    """The request comes from the desktop client, which carries the token"""
    monkeypatch.setattr(token_table, 'verify_header', lambda obj: True)


def make_scope(query, headers=None):
    """
    Build a minimal http scope of the route

    Args:
        query (dict[str, str]): The query parameters, url-encoded here
        headers (dict[str, str]): Extra request headers

    Returns:
        dict: The scope
    """
    return {
        'type': 'http',
        'method': 'GET',
        'path': '/golden_test/image',
        'query_string': urlencode(query).encode(),
        'headers': [
            (key.lower().encode('latin-1'), value.encode('latin-1'))
            for key, value in (headers or {}).items()
        ],
        'scheme': 'http',
        'server': ('127.0.0.1', 22267),
        'client': ('127.0.0.1', 123),
        'root_path': '',
    }


async def request(query):
    """
    Run the route through its ASGI layer and collect the response

    The endpoint of the route is the ASGI app built by starapi, so the query
    parameters and the electron dependency are parsed exactly like a request of
    the backend parses them.

    Args:
        query (dict[str, str]): The query parameters

    Returns:
        tuple[int, dict[str, str], bytes]: Status code, headers and body
    """
    route, = router.routes
    asgi = route.endpoint
    messages = []

    async def receive():
        # A real server blocks here until the client disconnects: the only
        # message left of a complete request body is http.disconnect
        await trio.sleep_forever()

    async def send(message):
        messages.append(message)

    await asgi(make_scope(query), receive, send)
    start = next(message for message in messages if message['type'] == 'http.response.start')
    headers = {key.decode('latin-1'): value.decode('latin-1') for key, value in start['headers']}
    body = b''.join(message['body'] for message in messages if message['type'] == 'http.response.body')
    return start['status'], headers, body


class TestGoldenTestImage:
    """The route streams a screenshot of the collection folder of a mod"""

    @pytest.mark.trio
    async def test_streams_the_screenshot(self, fs, project, electron):
        fs.create_file(project.joinpath('collection/map/fleet_bar/not_expanded.webp'), contents=b'webp-data')

        status, headers, body = await request({
            'mod': MOD_NAME,
            'image_path': 'map/fleet_bar/not_expanded.webp',
        })

        assert status == 200
        assert body == b'webp-data'
        assert headers['content-type'] == 'image/webp'
        # A screenshot edited by hand shows up on the next reload
        assert headers['cache-control'] == 'no-cache, no-store, private, must-revalidate, max-age=0'
        assert headers['expires'] == '0'
        assert headers['pragma'] == 'no-cache'

    @pytest.mark.trio
    async def test_missing_screenshot(self, electron, project):
        with pytest.raises(HTTPExceptionJson) as excinfo:
            await request({'mod': MOD_NAME, 'image_path': 'missing.webp'})

        assert excinfo.value.status_code == 404
        assert excinfo.value.detail == (
            b'{"err":"GOLDEN_TEST_IMAGE_NOT_FOUND","data":{"path":"missing.webp"}}'
        )

    @pytest.mark.trio
    async def test_a_folder_is_not_a_screenshot(self, fs, project, electron):
        fs.create_dir(project.joinpath('collection/folder.png'))

        with pytest.raises(HTTPExceptionJson) as excinfo:
            await request({'mod': MOD_NAME, 'image_path': 'folder.png'})

        assert excinfo.value.status_code == 404
        assert b'"err":"GOLDEN_TEST_IMAGE_NOT_FOUND"' in excinfo.value.detail

    @pytest.mark.parametrize('name', [
        'notes.txt',
        'test_a.py',
        'page.html',
        'icon.svg',
        'no_extension',
    ])
    @pytest.mark.trio
    async def test_non_image_rejected(self, fs, project, electron, name):
        fs.create_file(project.joinpath('collection').joinpath(name), contents=b'x')

        with pytest.raises(HTTPExceptionJson) as excinfo:
            await request({'mod': MOD_NAME, 'image_path': name})

        assert excinfo.value.status_code == 403
        assert excinfo.value.detail == (
            b'{"err":"GOLDEN_TEST_IMAGE_ONLY","data":{"path":"' + name.encode() + b'"}}'
        )

    @pytest.mark.parametrize('image_path', [
        '../tests/test_a.py',
        'map/../../secret.webp',
        '/etc/passwd.webp',
        'C:/project/collection/a.webp',
        '',
    ])
    @pytest.mark.trio
    async def test_rejected_paths(self, project, electron, image_path):
        with pytest.raises(HTTPExceptionJson) as excinfo:
            await request({'mod': MOD_NAME, 'image_path': image_path})

        assert excinfo.value.status_code == 403
        assert b'"err":"GOLDEN_TEST_IMAGE_INVALID_PATH"' in excinfo.value.detail

    @pytest.mark.trio
    async def test_symlink_escape(self, fs, project, electron):
        """A screenshot of the mod that links out of the collection is refused"""
        outside = project.joinpath('outside/secret.webp')
        fs.create_file(outside)
        fs.create_symlink(project.joinpath('collection/secret.webp'), outside)

        with pytest.raises(HTTPExceptionJson) as excinfo:
            await request({'mod': MOD_NAME, 'image_path': 'secret.webp'})

        assert excinfo.value.status_code == 403
        assert b'"err":"GOLDEN_TEST_IMAGE_INVALID_PATH"' in excinfo.value.detail
        assert b'outside the allowed directory' in excinfo.value.detail

    @pytest.mark.trio
    async def test_unknown_mod(self, project, electron):
        with pytest.raises(HTTPExceptionJson) as excinfo:
            await request({'mod': 'no_such_mod', 'image_path': 'a.webp'})

        assert excinfo.value.status_code == 403
        assert excinfo.value.detail == (
            b'{"err":"GOLDEN_TEST_IMAGE_INVALID_PATH","data":{"path":"a.webp",'
            b'"reason":"No such mod: \\"no_such_mod\\""}}'
        )

    @pytest.mark.trio
    async def test_requires_the_electron_token(self, fs, project, monkeypatch):
        """A remote browser has no token, and never reads a screenshot"""
        monkeypatch.setattr(token_table, 'verify_header', lambda obj: False)
        fs.create_file(project.joinpath('collection/a.webp'), contents=b'webp-data')

        with pytest.raises(HTTPExceptionJson) as excinfo:
            await request({'mod': MOD_NAME, 'image_path': 'a.webp'})

        assert excinfo.value.status_code == 403
        assert excinfo.value.detail == b'{"err":"AUTH_ELECTRON_ONLY"}'


class TestRouteRegistration:
    """The route sits under the API namespace, before the catch-all"""

    def test_route_path(self):
        assert [route.path for route in router.routes] == ['/golden_test/image']

    def test_the_served_images_are_the_images_of_the_asset_server(self):
        """Both servers serve pictures and neither of them serves svg"""
        assert set(MEDIA_TYPES) == ImageStaticFiles.IMAGE_EXTENSIONS
