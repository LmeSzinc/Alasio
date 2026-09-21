"""
Tests for the static asset servers (alasio/backend/dev/assets.py).

ImageStaticFiles (mod dev assets): no-cache + image-only — non-image
content is rejected with 403, no CSP is attached, the response is never
compressed (the files are already compressed images).
"""

import gzip
import os as os_module
from hashlib import sha1

import pytest
from starlette.exceptions import HTTPException

from alasio.backend.dev.assets import ImageStaticFiles
from alasio.testing.filesystem import fs  # noqa: F401

HTML_WITH_META_CSP = """<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta http-equiv="Content-Security-Policy"
          content="default-src 'self'; script-src 'self' 'sha256-abc='; style-src 'self' 'unsafe-inline'" />
  </head>
  <body>hi</body>
</html>
"""


@pytest.fixture(autouse=True)
def align_commonpath(monkeypatch):
    """
    The filesystem mock's realpath returns forward slashes while the
    real os.path.commonpath returns backslashes on Windows; starlette's
    StaticFiles path containment check then mismatches inside the mock.
    Align commonpath with the mock's separator style.
    """
    real_commonpath = os_module.path.commonpath

    def commonpath(paths):
        return real_commonpath(paths).replace('\\', '/')

    monkeypatch.setattr(os_module.path, 'commonpath', commonpath)


def make_scope(path, headers=None):
    """
    Build a minimal http scope for StaticFiles.get_response.

    Args:
        path (str): The request path
        headers (dict[str, str], optional): Request headers

    Returns:
        Scope:
    """
    return {
        'type': 'http',
        'method': 'GET',
        'path': path,
        'headers': [
            (key.lower().encode('latin-1'), value.encode('latin-1'))
            for key, value in (headers or {}).items()
        ],
        'query_string': b'',
        'scheme': 'http',
        'server': ('127.0.0.1', 22267),
        'client': ('127.0.0.1', 123),
        'root_path': '',
    }


async def run_response(resp, scope):
    """
    Run a static response through the ASGI pipeline and collect it (a
    FileResponse streams the file, the body comes as one or more
    http.response.body messages).

    Args:
        resp: The response object returned by StaticFiles.get_response
        scope (Scope):

    Returns:
        tuple[int, dict[str, str], bytes]: (status code, headers, body)
    """
    messages = []

    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}

    async def send(message):
        messages.append(message)

    await resp(scope, receive, send)
    start = next(message for message in messages if message['type'] == 'http.response.start')
    headers = {key.decode('latin-1'): value.decode('latin-1') for key, value in start['headers']}
    body = b''.join(
        message['body'] for message in messages if message['type'] == 'http.response.body')
    return start['status'], headers, body


class TestDevAssetsImageOnly:
    """ImageStaticFiles serves only images, no CSP."""

    @pytest.mark.parametrize('name', ['a.png', 'b.jpg', 'c.jpeg', 'd.gif', 'e.webp', 'f.bmp'])
    @pytest.mark.trio
    async def test_image_served(self, fs, name):
        fs.create_file(f'/assets/{name}', contents=b'img-data')
        app = ImageStaticFiles(directory='/assets', check_dir=False)
        resp = await app.get_response(name, make_scope(f'/{name}'))
        status, headers, body = await run_response(resp, make_scope(f'/{name}'))
        assert status == 200
        assert body == b'img-data'
        # no CSP on image responses
        assert 'content-security-policy' not in headers

    @pytest.mark.trio
    async def test_image_is_never_compressed(self, fs):
        """
        The files of this server are images, already compressed: the response
        must not be compressed, and must never carry a Content-Encoding the
        client did not ask for (RFC 9110, the F1 bug of the old GZipResponder
        wrap, which compressed every response over 500 bytes).
        """
        # incompressible content, a gzip pass could only make it larger
        content = b''.join(sha1(f'pixel-{i}'.encode()).digest() for i in range(64))
        assert len(gzip.compress(content)) > len(content)
        fs.create_file('/assets/big.png', contents=content)
        app = ImageStaticFiles(directory='/assets', check_dir=False)
        scope = make_scope('/big.png', headers={'Accept-Encoding': 'gzip'})
        resp = await app.get_response('big.png', scope)
        status, headers, body = await run_response(resp, scope)
        assert status == 200
        assert 'content-encoding' not in headers
        assert body == content

    @pytest.mark.parametrize('name', ['a.json', 'b.py', 'c.html', 'd.svg', 'e.js', 'f.txt', 'g'])
    @pytest.mark.trio
    async def test_non_image_rejected(self, fs, name):
        fs.create_file(f'/assets/{name}', contents=b'x')
        app = ImageStaticFiles(directory='/assets', check_dir=False)
        with pytest.raises(HTTPException) as excinfo:
            await app.get_response(name, make_scope(f'/{name}'))
        assert excinfo.value.status_code == 403

    @pytest.mark.trio
    async def test_uppercase_extension_served(self, fs):
        fs.create_file('/assets/A.PNG', contents=b'img')
        app = ImageStaticFiles(directory='/assets', check_dir=False)
        resp = await app.get_response('A.PNG', make_scope('/A.PNG'))
        status, _, _ = await run_response(resp, make_scope('/A.PNG'))
        assert status == 200

    @pytest.mark.trio
    async def test_upload_like_html_rejected(self, fs):
        """An html file smuggled into the asset dir must never be served."""
        fs.create_file('/assets/evil.html', contents=HTML_WITH_META_CSP)
        app = ImageStaticFiles(directory='/assets', check_dir=False)
        with pytest.raises(HTTPException) as excinfo:
            await app.get_response('evil.html', make_scope('/evil.html'))
        assert excinfo.value.status_code == 403
