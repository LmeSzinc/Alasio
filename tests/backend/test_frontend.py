"""
Tests for the frontend page server (alasio/backend/frontend.py).

The server is driven by the delivered frontend manifest
(deploy_data/frontend-manifest.pack): the test data is a mock frontend build
encoded with PackEncodeManifest, written to the in-memory filesystem together
with its manifest, then loaded by FrontendSite. The files are read by the
warmup thread (warm_up) and served from memory, the tests check the returned
response objects directly: the bytes, the encoding negotiation and the headers.
"""
import gzip
import threading
from hashlib import sha1

import pytest
import trio
from starlette.exceptions import HTTPException

from alasio.backend import frontend
from alasio.backend.frontend import (
    CSP, FRAME_ANCESTORS, FRONTEND_FOLDER, INDEX_HTML, MANIFEST_FILE, FrontendBody, FrontendSite
)
from alasio.backport.patch import patch_mimetype
from alasio.deploy_dev.pack.encode_manifest import PackEncodeManifest
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401

# the builtin mimetype table, as the backend applies it at startup: the media
# type of a served file must not depend on the environment of the host
patch_mimetype()

# wheel root of the fake filesystem: the folder that contains the package
# folder (env.ALASIO_ROOT in a deployment)
ROOT = '/site'


# ════════════════════════════════════════════════════════════════════════════
#  test data
# ════════════════════════════════════════════════════════════════════════════

# a page with the meta CSP the build-time csp-inline-hash plugin writes
HTML_META = (
    '<!doctype html>\n'
    '<html>\n'
    '  <head>\n'
    '    <meta http-equiv="Content-Security-Policy"\n'
    "          content=\"default-src 'self'; script-src 'self' 'sha256-abc='\" />\n"
    '  </head>\n'
    '  <body>hi</body>\n'
    '</html>\n'
)
HTML_NO_META = '<!doctype html><html><head><title>x</title></head><body>hi</body></html>\n'

# compressible: the gzip form is far smaller than the content
SCRIPT = b'export const chunk = 1;\n' * 200


def noise(size, seed='noise'):
    """
    Deterministic incompressible content, gzip only makes it larger.

    Args:
        size (int): Content size
        seed (str): Seed of the content. Defaults to 'noise'.

    Returns:
        bytes: Pseudo random content
    """
    count = (size + 19) // 20
    return b''.join(sha1(f'{seed}-{i}'.encode()).digest() for i in range(count))[:size]


# the mock frontend build of the wheel: a page, a compressible script and an
# incompressible binary (a file is served raw when gzip does not pay for it)
FILES = {
    INDEX_HTML: HTML_META.encode(),
    '_app/immutable/entry/start.js': SCRIPT,
    '_app/immutable/assets/app.css': b'.app { display: flex; }\n' * 20,
    'favicon.png': noise(1024),
}


# ════════════════════════════════════════════════════════════════════════════
#  helpers
# ════════════════════════════════════════════════════════════════════════════


def write_frontend(fs, files=None, corrupt=(), extra=None, root=ROOT, manifest=True):
    """
    Write a frontend build and its manifest to the fake filesystem.

    Args:
        fs (FakeFilesystem): The fake filesystem
        files (dict[str, bytes]): {frontend-relative path: content} of the
            build, every file is recorded in the manifest. Defaults to None,
            the FILES of the module.
        corrupt (Iterable[str]): Paths written to disk with a content that
            does not match the manifest record
        extra (dict[str, bytes], optional): Extra records of the manifest, not
            inside the frontend folder, written at the wheel root
        root (str): Wheel root. Defaults to ROOT.
        manifest (bool): Write the manifest file. Defaults to True.

    Returns:
        str: The wheel root
    """
    files = FILES if files is None else files
    corrupt = set(corrupt)

    encoder = PackEncodeManifest()
    for path, content in files.items():
        encoder.add_file(f'{FRONTEND_FOLDER}/{path}', content)
        if path in corrupt:
            content = b'the content on disk does not match the manifest'
        fs.create_file(f'{root}/{FRONTEND_FOLDER}/{path}', contents=content)
    for path, content in (extra or {}).items():
        encoder.add_file(path, content)
        fs.create_file(f'{root}/{path}', contents=content)

    if manifest:
        fs.create_file(f'{root}/{MANIFEST_FILE}', contents=b''.join(encoder.iter_manifest_data()))
    return root


def make_site(fs, files=None, corrupt=(), extra=None, root=ROOT):
    """
    Build a frontend build with its manifest and return the site serving it.

    The constructor reads nothing: the table and the bodies are loaded by
    warm_up() or by the first request.

    Args:
        fs (FakeFilesystem): The fake filesystem
        files (dict[str, bytes], optional): Build files, defaults to FILES
        corrupt (Iterable[str]): Paths whose content does not match the
            manifest record
        extra (dict[str, bytes], optional): Extra records of the manifest
        root (str): Wheel root. Defaults to ROOT.

    Returns:
        FrontendSite:
    """
    write_frontend(fs, files=files, corrupt=corrupt, extra=extra, root=root)
    return FrontendSite(root=root, manifest=MANIFEST_FILE)


def make_scope(path='/', method='GET', headers=None):
    """
    Build a minimal http scope.

    Args:
        path (str): Request path
        method (str): Request method. Defaults to 'GET'.
        headers (dict[str, str], optional): Request headers

    Returns:
        dict: ASGI scope
    """
    return {
        'type': 'http',
        'method': method,
        'path': path,
        'root_path': '',
        'headers': [
            (key.lower().encode('latin-1'), value.encode('latin-1'))
            for key, value in (headers or {}).items()
        ],
        'query_string': b'',
        'scheme': 'http',
        'server': ('127.0.0.1', 22267),
        'client': ('127.0.0.1', 123),
    }


async def get(site, path='/', headers=None, method='GET'):
    """
    Request a path from the site.

    Args:
        site (FrontendSite):
        path (str): Request path. Defaults to '/'.
        headers (dict[str, str], optional): Request headers
        method (str): Request method. Defaults to 'GET'.

    Returns:
        Response
    """
    return await site.get_response(path, make_scope(path, method=method, headers=headers))


@pytest.fixture
def site(fs):
    """An unwarmed site of the mock frontend build."""
    return make_site(fs)


# ════════════════════════════════════════════════════════════════════════════
#  manifest loading
# ════════════════════════════════════════════════════════════════════════════


class TestManifestLoad:
    """Nothing is read before warm_up()/the first request, a broken manifest only warns."""

    def test_constructor_reads_nothing(self, fs):
        """create_app() must not touch the disk, even without a manifest."""
        site = FrontendSite(root=ROOT, manifest=MANIFEST_FILE)
        assert site.manifest is None
        assert site.bodies == {}
        assert site.wanted == {}

    @pytest.mark.trio
    async def test_missing_manifest_warns_and_404s(self, fs):
        site = FrontendSite(root=ROOT, manifest=MANIFEST_FILE)
        with logger.mock_capture_writer() as capture:
            with pytest.raises(HTTPException) as excinfo:
                await get(site, '/')
        assert excinfo.value.status_code == 404
        assert capture.fd.any_contains('Failed to read the frontend manifest')
        assert site.manifest == {}

    @pytest.mark.trio
    async def test_invalid_manifest_warns_and_404s(self, fs):
        """A manifest whose checksum does not match must not be guessed at."""
        write_frontend(fs)
        fs.remove(f'{ROOT}/{MANIFEST_FILE}')
        fs.create_file(f'{ROOT}/{MANIFEST_FILE}', contents=b'MANI\x00garbage that does not checksum')
        site = FrontendSite(root=ROOT, manifest=MANIFEST_FILE)
        with logger.mock_capture_writer() as capture:
            with pytest.raises(HTTPException) as excinfo:
                await get(site, '/')
        assert excinfo.value.status_code == 404
        assert capture.fd.any_contains('Failed to decode the frontend manifest')
        assert site.manifest == {}

    @pytest.mark.trio
    async def test_foreign_file_warns_and_404s(self, fs):
        """A file that is not a manifest is rejected the same way."""
        fs.create_file(f'{ROOT}/{MANIFEST_FILE}', contents=b'PACK\x00' + b'\x00' * 64)
        site = FrontendSite(root=ROOT, manifest=MANIFEST_FILE)
        with logger.mock_capture_writer() as capture:
            with pytest.raises(HTTPException) as excinfo:
                await get(site, '/')
        assert excinfo.value.status_code == 404
        assert capture.fd.any_contains('not a manifest file')

    @pytest.mark.trio
    async def test_records_are_keyed_by_the_relative_path(self, site):
        await site.warm_up()
        assert sorted(site.manifest) == sorted(FILES)
        assert site.manifest[INDEX_HTML].size == len(FILES[INDEX_HTML])
        assert site.manifest[INDEX_HTML].sha1 == sha1(FILES[INDEX_HTML]).digest()

    @pytest.mark.trio
    async def test_records_outside_the_frontend_folder_are_dropped(self, fs):
        """A wheel has one frontend manifest, other records are not served."""
        site = make_site(fs, extra={'alasio/deploy_data/webapp/app.asar': b'asar'})
        await site.warm_up()
        assert sorted(site.manifest) == sorted(FILES)

    @pytest.mark.trio
    async def test_manifest_without_frontend_file_warns_and_404s(self, fs):
        site = make_site(fs, files={}, extra={'alasio/deploy_data/webapp/app.asar': b'asar'})
        with logger.mock_capture_writer() as capture:
            with pytest.raises(HTTPException) as excinfo:
                await get(site, '/')
        assert excinfo.value.status_code == 404
        assert capture.fd.any_contains('lists no file')

    @pytest.mark.trio
    async def test_build_without_page_warns(self, fs):
        """The assets are served, the routes of the SPA cannot be."""
        files = {path: content for path, content in FILES.items() if path != INDEX_HTML}
        site = make_site(fs, files=files)
        with logger.mock_capture_writer() as capture:
            await site.warm_up()
        assert capture.fd.any_contains('routes cannot be served')
        resp = await get(site, '/_app/immutable/entry/start.js')
        assert resp.status_code == 200
        with pytest.raises(HTTPException) as excinfo:
            await get(site, '/config/lo2/overview')
        assert excinfo.value.status_code == 404


# ════════════════════════════════════════════════════════════════════════════
#  warm up
# ════════════════════════════════════════════════════════════════════════════


class TestWarmUp:
    """One worker thread reads the manifest and every file, publishing as it goes."""

    @pytest.mark.trio
    async def test_one_thread_call(self, site, monkeypatch):
        """The whole warmup is one run_sync, not one hop per file."""
        calls = []
        real = trio.to_thread.run_sync

        def counting_run_sync(*args, **kwargs):
            calls.append(args[0])
            return real(*args, **kwargs)

        monkeypatch.setattr(trio.to_thread, 'run_sync', counting_run_sync)
        await site.warm_up()
        assert len(calls) == 1

    @pytest.mark.trio
    async def test_prepares_every_file(self, site, fs):
        """
        After the warmup the whole tree is in memory: the requests are served
        with the files gone from the disk.
        """
        with logger.mock_capture_writer() as capture:
            await site.warm_up()
        assert capture.fd.any_contains(f'Frontend warmed up: {len(FILES)} files')
        assert sorted(site.bodies) == sorted(FILES)

        for path in FILES:
            fs.remove(f'{ROOT}/{FRONTEND_FOLDER}/{path}')
        resp = await get(site, f'/{INDEX_HTML}')
        assert resp.status_code == 200
        assert resp.body == FILES[INDEX_HTML]

    @pytest.mark.trio
    async def test_steady_state_uses_no_thread(self, site, monkeypatch):
        """After the warmup a request is a dict lookup, no thread and no await."""
        await site.warm_up()
        calls = []
        real = trio.to_thread.run_sync

        def counting_run_sync(*args, **kwargs):
            calls.append(args[0])
            return real(*args, **kwargs)

        monkeypatch.setattr(trio.to_thread, 'run_sync', counting_run_sync)
        assert (await get(site, '/')).status_code == 200
        assert (await get(site, '/_app/immutable/entry/start.js',
                          headers={'Accept-Encoding': 'identity'})).status_code == 200
        assert calls == []
        # no request is waiting for the warmup either
        assert site.wanted == {}

    @pytest.mark.trio
    async def test_missing_file_warns_once_and_404s(self, fs):
        site = make_site(fs)
        fs.remove(f'{ROOT}/{FRONTEND_FOLDER}/favicon.png')
        with logger.mock_capture_writer() as capture:
            await site.warm_up()
        assert capture.fd.any_contains('Failed to read the frontend file')
        assert capture.fd.any_contains('1 unreadable')
        assert site.unreadable == {'favicon.png'}
        # the failure is published: no second read, no second warning
        assert site.bodies['favicon.png'] is frontend._FAILED
        for _ in range(2):
            with pytest.raises(HTTPException) as excinfo:
                await get(site, '/favicon.png')
            assert excinfo.value.status_code == 404

    @pytest.mark.trio
    async def test_mismatch_warns_and_serves(self, fs):
        """A file that does not match the manifest is reported, not blocked."""
        site = make_site(fs, corrupt=['favicon.png'])
        with logger.mock_capture_writer() as capture:
            await site.warm_up()
        assert capture.fd.any_contains('Frontend file does not match the manifest')
        assert capture.fd.any_contains('favicon.png')
        assert capture.fd.any_contains(sha1(FILES['favicon.png']).hexdigest())
        assert capture.fd.any_contains('1 mismatched')
        assert site.mismatched == {'favicon.png'}
        resp = await get(site, '/favicon.png')
        assert resp.body == b'the content on disk does not match the manifest'

    @pytest.mark.trio
    async def test_stopped_walk_leaves_the_files_to_the_requests(self, fs):
        """An abandoned walk (shutdown) stops without leaving a waiter hanging."""
        site = make_site(fs)
        site._stop.set()
        total = site._warm_all(trio.lowlevel.current_trio_token())
        # the table is published, no body is prepared
        assert total == len(FILES)
        assert site.bodies == {}
        # the requests prepare their own file, in a worker thread
        resp = await get(site, '/')
        assert resp.status_code == 200
        assert site.bodies[INDEX_HTML].body

    @pytest.mark.trio
    async def test_cancelled_warmup_releases_everyone(self, fs):
        """A cancelled warmup sets the flags, later requests still work."""
        site = make_site(fs)
        with trio.move_on_after(0):
            await site.warm_up()
        assert site._warming is False
        assert site._stop.is_set()
        assert site._loaded_event.is_set()
        resp = await get(site, '/')
        assert resp.status_code == 200
        assert resp.body == FILES[INDEX_HTML]

    @pytest.mark.trio
    async def test_wanted_file_is_served_before_the_walk_reaches_it(self, fs):
        """
        A request waiting for a file is served by the warmup thread before the
        next file of the walk, so the tree is read once.
        """
        site = make_site(fs)
        prepared = []
        original = site._prepare

        def recording_prepare(path, record):
            prepared.append(path)
            return original(path, record)

        site._prepare = recording_prepare
        # the request for the last file of the build arrives first
        last = list(FILES)[-1]
        site.wanted[last] = trio.Event()
        site._warming = True
        try:
            total = site._warm_all(trio.lowlevel.current_trio_token())
        finally:
            site._warming = False
        # the waiters are woken through the loop, the callbacks need a tick
        for _ in range(50):
            if site.wanted[last].is_set():
                break
            await trio.sleep(0.01)
        assert total == len(FILES)
        assert site.wanted[last].is_set()
        assert prepared.count(last) == 1
        # served by the first check, long before the walk reached it
        assert prepared.index(last) <= 1

    @pytest.mark.trio
    async def test_request_in_flight_is_served_by_the_warmup_thread(self, fs, monkeypatch):
        """
        A request that misses while the warmup walks waits for the warmup
        thread (bounded by one file) instead of starting a second reader.
        """
        site = make_site(fs)
        first_file_done = threading.Event()
        gate = threading.Event()
        prepared = []
        original = site._prepare

        def slow_prepare(path, record):
            prepared.append(path)
            if path == INDEX_HTML:
                first_file_done.set()
                # hold the walk while the request registers
                gate.wait(10)
            return original(path, record)

        monkeypatch.setattr(site, '_prepare', slow_prepare)
        last = list(FILES)[-1]
        responses = []

        async def request():
            responses.append(await get(site, f'/{last}'))

        async with trio.open_nursery() as nursery:
            nursery.start_soon(site.warm_up)
            while not first_file_done.is_set():
                await trio.sleep(0.01)
            nursery.start_soon(request)
            # the request registers and waits while the walk is held
            while not site.wanted:
                await trio.sleep(0.01)
            assert site.wanted[last] is not None
            gate.set()

        assert len(responses) == 1
        assert responses[0].status_code == 200
        assert responses[0].body == FILES[last]
        # prepared once, by the warmup thread
        assert prepared.count(last) == 1
        assert site.wanted[last].is_set()


# ════════════════════════════════════════════════════════════════════════════
#  identity bodies
# ════════════════════════════════════════════════════════════════════════════


class TestIdentityBody:
    """The decoded bodies of the clients without gzip, released by gc()."""

    @pytest.mark.trio
    async def test_decompresses_the_gzip_body(self, site):
        await site.warm_up()
        body = site.identity_cache.get('_app/immutable/entry/start.js',
                                      body=site.bodies['_app/immutable/entry/start.js'])
        assert isinstance(body, FrontendBody)
        assert body.encoding == ''
        assert body.body == SCRIPT
        assert body.media_type == 'application/javascript'

    @pytest.mark.trio
    async def test_page_keeps_its_csp(self, site):
        await site.warm_up()
        body = site.identity_cache.get(INDEX_HTML, body=site.bodies[INDEX_HTML])
        assert body.csp == site.bodies[INDEX_HTML].csp
        assert body.csp.endswith(FRAME_ANCESTORS)

    @pytest.mark.trio
    async def test_raw_body_is_shared(self, site):
        """A file that is already served raw is not copied for the client."""
        await site.warm_up()
        assert site.identity_cache.get('favicon.png', body=site.bodies['favicon.png']) \
            is site.bodies['favicon.png']

    @pytest.mark.trio
    async def test_gc_releases_the_decoded_body(self, site):
        await site.warm_up()
        path = '_app/immutable/entry/start.js'
        first = site.identity_cache.get(path, body=site.bodies[path])
        site.identity_cache.gc(idle=0)
        second = site.identity_cache.get(path, body=site.bodies[path])
        # decoded again, the first copy is gone
        assert second is not first
        assert second.body == first.body
        # the payload is not touched by the gc
        assert site.bodies[path].encoding == 'gzip'


# ════════════════════════════════════════════════════════════════════════════
#  requests
# ════════════════════════════════════════════════════════════════════════════


class TestRequest:
    """The mounted server: files, routes, encodings and headers."""

    @pytest.mark.trio
    async def test_root_serves_the_page(self, site):
        await site.warm_up()
        resp = await get(site, '/')
        assert resp.status_code == 200
        assert resp.headers['content-type'] == 'text/html; charset=utf-8'
        assert resp.headers['content-security-policy'].endswith(FRAME_ANCESTORS)

    @pytest.mark.trio
    @pytest.mark.parametrize('path', ['/index.html', '/config/lo2/overview', '/config/lo2/', '/a/b/c'])
    async def test_routes_serve_the_page(self, site, path):
        await site.warm_up()
        resp = await get(site, path)
        assert resp.status_code == 200
        assert resp.headers['content-type'].startswith('text/html')

    @pytest.mark.trio
    @pytest.mark.parametrize('path', ['/_app/missing.js', '/favicon.ico', '/a/missing.css'])
    async def test_missing_resource_is_404(self, site, path):
        await site.warm_up()
        with pytest.raises(HTTPException) as excinfo:
            await get(site, path)
        assert excinfo.value.status_code == 404

    @pytest.mark.trio
    async def test_file_outside_the_manifest_is_not_served(self, fs):
        """The manifest table is the served file set, the disk does not add."""
        site = make_site(fs)
        await site.warm_up()
        fs.create_file(f'{ROOT}/{FRONTEND_FOLDER}/_app/immutable/extra.js', contents=b'new file')
        fs.create_file(f'{ROOT}/secret.txt', contents=b'outside of the frontend')
        for path in ['/_app/immutable/extra.js', '/secret.txt', '/../secret.txt']:
            with pytest.raises(HTTPException) as excinfo:
                await get(site, path)
            assert excinfo.value.status_code == 404

    @pytest.mark.trio
    @pytest.mark.parametrize('method', ['POST', 'PUT', 'DELETE', 'OPTIONS'])
    async def test_method_not_allowed(self, site, method):
        await site.warm_up()
        with pytest.raises(HTTPException) as excinfo:
            await get(site, '/', method=method)
        assert excinfo.value.status_code == 405

    @pytest.mark.trio
    async def test_gzip_client_gets_the_gzip_body(self, site):
        await site.warm_up()
        resp = await get(site, '/_app/immutable/entry/start.js', headers={'Accept-Encoding': 'gzip'})
        assert resp.headers['content-encoding'] == 'gzip'
        assert gzip.decompress(resp.body) == SCRIPT

    @pytest.mark.trio
    @pytest.mark.parametrize('headers', [{}, {'Accept-Encoding': 'identity'}, {'Accept-Encoding': 'br'}])
    async def test_other_clients_get_the_content(self, site, headers):
        """Never send an encoding the client did not ask for (the F1 bug)."""
        await site.warm_up()
        resp = await get(site, '/_app/immutable/entry/start.js', headers=headers)
        assert 'content-encoding' not in resp.headers
        assert resp.body == SCRIPT

    @pytest.mark.trio
    async def test_raw_file_is_served_as_is(self, site):
        await site.warm_up()
        resp = await get(site, '/favicon.png', headers={'Accept-Encoding': 'gzip'})
        assert 'content-encoding' not in resp.headers
        assert resp.body == FILES['favicon.png']
        assert resp.headers['content-type'] == 'image/png'

    @pytest.mark.trio
    async def test_no_cache_headers(self, site):
        await site.warm_up()
        resp = await get(site, '/')
        assert resp.headers['cache-control'] == 'no-cache, no-store, private, must-revalidate, max-age=0'
        assert resp.headers['expires'] == '0'
        assert resp.headers['pragma'] == 'no-cache'
        # the body depends on Accept-Encoding, whatever the cache policy
        assert resp.headers['vary'] == 'Accept-Encoding'

    @pytest.mark.trio
    async def test_etag_tells_the_encodings_apart(self, site):
        await site.warm_up()
        path = '/_app/immutable/entry/start.js'
        gzip_resp = await get(site, path, headers={'Accept-Encoding': 'gzip'})
        raw_resp = await get(site, path, headers={'Accept-Encoding': 'identity'})
        assert gzip_resp.headers['etag'] != raw_resp.headers['etag']
        assert sha1(SCRIPT).hexdigest() in raw_resp.headers['etag']

    @pytest.mark.trio
    async def test_not_modified(self, site):
        await site.warm_up()
        first = await get(site, '/')
        resp = await get(site, '/', headers={'If-None-Match': first.headers['etag']})
        assert resp.status_code == 304
        assert resp.body == b''
        assert resp.headers['etag'] == first.headers['etag']

    @pytest.mark.trio
    async def test_changed_encoding_is_not_304(self, site):
        """A client that has the gzip body must get the raw one on request."""
        await site.warm_up()
        path = '/_app/immutable/entry/start.js'
        gzip_resp = await get(site, path, headers={'Accept-Encoding': 'gzip'})
        resp = await get(site, path, headers={'If-None-Match': gzip_resp.headers['etag']})
        assert resp.status_code == 200
        assert resp.body == SCRIPT

    @pytest.mark.trio
    async def test_page_without_meta_uses_the_fallback_csp(self, fs):
        site = make_site(fs, files=dict(FILES, **{INDEX_HTML: HTML_NO_META.encode()}))
        await site.warm_up()
        resp = await get(site, '/')
        assert resp.headers['content-security-policy'] == CSP

    @pytest.mark.trio
    async def test_asgi_call_serves_the_response(self, site):
        """The mounted app: a full request / response round trip."""
        await site.warm_up()
        messages = []

        async def receive():
            return {'type': 'http.request', 'body': b'', 'more_body': False}

        async def send(message):
            messages.append(message)

        await site(make_scope('/index.html'), receive, send)
        start = messages[0]
        assert start['type'] == 'http.response.start'
        assert start['status'] == 200
        body = messages[1]
        assert body['type'] == 'http.response.body'
        assert body['body'] == FILES[INDEX_HTML]

    @pytest.mark.trio
    async def test_non_http_scope_is_rejected(self, site):
        scope = make_scope('/')
        scope['type'] = 'websocket'
        with pytest.raises(RuntimeError):
            await site(scope, None, None)
