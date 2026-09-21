"""
The frontend page server.

The frontend build is delivered inside the library package: the wheel carries
the files under <package folder>/deploy_data/frontend/** and a manifest table
of them at <package folder>/deploy_data/frontend-manifest.pack (the layout is
recorded by alasio/deploy_dev/wheel/alasio_wheel.py). The backend serves the
frontend from memory instead of from disk:

- the manifest table is the served file set: a path outside of it is never
  served, a file that appears in the folder after the deployment is invisible;
- one worker thread reads everything: the warmup task started by the lifespan
  (warm_up) reads the manifest first and publishes it at once, then walks the
  table and publishes every body as soon as it is prepared, so requests are
  served while the warmup is still walking and the event loop never touches the
  disk;
- a request for a file that is not prepared yet registers in `wanted` and
  waits for its event: the warmup thread serves the wanted files before moving
  to the next file of its walk, so the wait is bounded by one file, and the
  tree is still read by a single thread (a cold mechanical disk or an
  antivirus scanning the files is served best by one sequential reader);
- only a request that misses while no warmup is running (the site is mounted
  without the lifespan, or the warmup was abandoned at shutdown) prepares its
  own file in a worker thread, so a request never waits for a warmup that is
  not coming.

The manifest is the version authority of the frontend: it is delivered in the
same pack as the files it lists, so it can only disagree with the disk when a
deployment is broken. A file that does not match its record is reported (a
warning with the expected / actual size and sha1) and served anyway: repairing
files is the job of the deployment layer, not of the server. A missing or
invalid manifest only warns: every path answers 404 then (a source checkout has
no manifest, its frontend is served by the vite dev server).
"""
import gzip
import re
import threading
from hashlib import sha1
from mimetypes import guess_type

import trio
from msgspec import Struct
from starlette import status
from starlette._utils import get_route_path
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import NotModifiedResponse

from alasio.deploy.pack.decode_base import PackDecodeError
from alasio.deploy.pack.decode_manifest import PackDecodeManifest
from alasio.ext import env
from alasio.ext.cache.resource import ResourceCacheTTL
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_bytes
from alasio.logger import logger

# The frontend files and their manifest, as recorded in the wheel, relative to
# the root that contains the package folder (env.ALASIO_ROOT): site-packages
# in a deployment, the repository root in a checkout.
DEPLOY_DATA = 'alasio/deploy_data'
FRONTEND_FOLDER = f'{DEPLOY_DATA}/frontend'
MANIFEST_FILE = f'{DEPLOY_DATA}/frontend-manifest.pack'

# The SPA entry of the frontend build, the page served for every route
INDEX_HTML = 'index.html'

# frame-ancestors can only be set through a response header (the meta tag
# ignores it): it allows the electron host (app://bundle, production) and the
# local loopback dev hosts (the vite dev server, any port) to embed the page.
FRAME_ANCESTORS = "frame-ancestors 'self' app://bundle http://127.0.0.1:* http://localhost:*"

# Fallback Content-Security-Policy for a page without a CSP meta (the served
# page normally carries its own meta, kept in sync with the inline scripts by
# the build-time csp-inline-hash plugin; the response header then mirrors that
# meta so the browser enforces their intersection).
CSP = (
    "default-src 'self'; "
    "script-src 'self' 'sha256-/c574zxOUzzzs52yM/ATmZ7eBGoJ3nHgHTc8O5t7jRw='; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "connect-src 'self' ws: wss:; "
    "font-src 'self' data:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-src 'self'; "
    f"{FRAME_ANCESTORS}"
)


class FrontendBody(Struct):
    """
    The prepared body of one frontend file, the bytes the server sends.

    Attributes:
        body (bytes): The body to send, gzip compressed when encoding is set.
        encoding (str): Content-Encoding of body: 'gzip', or '' for the raw
            content. A file whose gzip form is not smaller than its content is
            served raw, a file is never sent with a header that does not pay
            for itself (already compressed formats, tiny files).
        media_type (str): Media type of the file, the Content-Type header.
        csp (str): Content-Security-Policy of the file, '' for every non-html
            file (only a page needs one).
        digest (str): sha1 hex digest of the file content, the base of the
            ETag. The gzip and the identity bytes are different
            representations of the same file, their ETags differ by a suffix.
    """
    body: bytes
    encoding: str
    media_type: str
    csp: str
    digest: str


class FrontendIdentityCache(ResourceCacheTTL[FrontendBody]):
    """
    Cache of the identity bodies of the frontend files, for clients that do not
    accept gzip.

    The fill is a gzip decompression of a body that is already in memory (no
    I/O, under a millisecond), so it runs inline in the request. gc() releases
    the decoded bodies of the clients that stopped asking; the gzip bodies (the
    payload) stay resident in FrontendSite.bodies and never participate in a
    gc.
    """

    def load_resource(self, path, body, **kwargs):
        """
        Decode the prepared body of one file.

        A file whose gzip form was not smaller is already served raw, its body
        is shared with the caller instead of being copied.

        Args:
            path (str): Frontend-relative path of the file
            body (FrontendBody): The prepared body of the file, passed by the
                site: this cache never touches the file system

        Returns:
            FrontendBody: The body without Content-Encoding
        """
        if not body.encoding:
            return body
        return FrontendBody(
            body=gzip.decompress(body.body),
            encoding='',
            media_type=body.media_type,
            csp=body.csp,
            digest=body.digest,
        )


class _Failed:
    """
    Sentinel published in FrontendSite.bodies for a file that cannot be read
    (missing on disk, unreadable). The warning is logged once by the
    preparation, every later request answers 404 without touching the disk.
    """

    def __repr__(self):
        return 'FAILED'


# A file that cannot be prepared. The published value is a singleton so the
# request path recognizes it with `is`.
_FAILED = _Failed()


class FrontendSite:
    """
    The frontend page server, the ASGI app mounted at "/".

    Attributes:
        root (PathStr): Root the manifest paths are relative to, the folder that
            contains the package folder (env.ALASIO_ROOT).
        manifest_file (str): Path of the manifest file, relative to root.
        folder (PathStr): Folder the frontend files are stored in, the frontend
            folder of the deployment (derived from the manifest layout).
        manifest (dict[str, RefInfo] | None): The manifest table keyed by the
            frontend-relative path. None until a reader publishes it, {} when
            the manifest is unusable (missing, corrupt, or listing no file of
            the frontend folder): the site answers 404 to every path then.
        bodies (dict[str, FrontendBody | _Failed]): Prepared bodies, published
            per file by the warmup thread (or by the request that prepares its
            own file when no warmup is running).
        wanted (dict[str, trio.Event]): Events of the requests waiting for a
            file the warmup has not published yet. Written by the event loop
            only, read as a snapshot by the warmup thread. Entries are not
            removed (the count is bounded by the files requested before the
            warmup published them), so the warmup of a later run can wake a
            waiter that is long gone.
        mismatched (set[str]): Files whose content did not pass the manifest
            check, diagnostics of the preparation reported by the warmup.
        unreadable (set[str]): Files that could not be read, diagnostics of the
            preparation reported by the warmup.
        identity_cache (FrontendIdentityCache): Decoded bodies of the clients
            without gzip, released by gc().
    """

    def __init__(self, root, manifest):
        """
        Args:
            root (str): Root the manifest paths are relative to, the folder
                that contains the package folder (env.ALASIO_ROOT)
            manifest (str): Path of the manifest file, relative to root
        """
        self.root = PathStr.new(root)
        self.manifest_file = manifest
        # every record path is relative to the frontend folder of the manifest
        self.folder = self.root.joinpath(FRONTEND_FOLDER)

        # nothing is read here: the table and the bodies are loaded by warm_up()
        # or by the first request, create_app() (called inside the 5s startup
        # window of the supervisor) never waits for the disk
        self.manifest = None
        self.bodies = {}
        self.wanted = {}
        # diagnostics, written by the thread that prepares a file (a set add is
        # atomic) and reported once by the warmup summary
        self.mismatched = set()
        self.unreadable = set()

        self.identity_cache = FrontendIdentityCache()
        self._loaded_event = trio.Event()
        # whether a warmup thread is walking, written by the event loop only
        self._warming = False
        # set to tell the warmup thread to stop at the next file
        self._stop = threading.Event()

    async def warm_up(self):
        """
        Prepare the whole frontend, in one worker thread.

        One to_thread.run_sync for the complete warmup: reading the manifest,
        preparing every file and serving the on-demand requests are one
        synchronous call in one thread. A thread hop per file would cost more
        than the work itself (the 94 files of the frontend build measured 53 ms
        with one hop per file, 30 ms with one call).

        The thread publishes the table and every body as soon as they are
        ready, so a request never waits for the whole tree; a request that
        misses while the warmup is walking is served by the same thread (see
        _warm_all), so no thread is added.

        Cancellation abandons the thread (it stops at the next file through the
        stop flag) and wakes every waiter: they prepare their own file.
        """
        self._stop.clear()
        self._warming = True
        token = trio.lowlevel.current_trio_token()
        try:
            summary = await trio.to_thread.run_sync(self._warm_all, token, abandon_on_cancel=True)
        except Exception as e:
            # a bug in the preparation must not take the backend down: the
            # requests prepare their own file from now on
            logger.error(f'Frontend warmup failed: {e}')
            logger.exception(e)
            return
        finally:
            # whatever ended the warmup (end of the tree, cancellation, a
            # crash), no waiter may be left on an event or on a table that
            # nobody will publish: the flags turn every later request to its own
            # work, and the waiters of this run are woken to do the same
            self._warming = False
            self._stop.set()
            self._loaded_event.set()
            for event in self.wanted.values():
                event.set()
        if summary:
            logger.info(
                f'Frontend warmed up: {summary} files, '
                f'{len(self.mismatched)} mismatched, {len(self.unreadable)} unreadable'
            )

    def _warm_all(self, token):
        """
        The whole warmup in one synchronous call, the body of warm_up().

        Args:
            token (trio.lowlevel.TrioToken): Token of the event loop, used to
                wake the waiters (a trio.Event must be set from the loop thread)

        Returns:
            int: Number of files of the manifest, 0 when the manifest is
                unusable (the reason is logged by _read_manifest)
        """
        records = _read_manifest(self.root, self.manifest_file)
        self.manifest = records
        token.run_sync_soon(self._loaded_event.set)

        woken = set()
        for path, record in records.items():
            self._serve_wanted(token, woken)
            if self._stop.is_set():
                # abandoned (shutdown): the files left are prepared by the
                # requests themselves, see _body_of
                break
            if path in self.bodies:
                # an early request (or _serve_wanted) prepared it already
                continue
            self.bodies[path] = self._prepare(path, record)
        self._serve_wanted(token, woken)
        return len(records)

    def _serve_wanted(self, token, woken):
        """
        Prepare the files the requests are waiting for, before the next file of
        the walk.

        A missed request registers in self.wanted and waits: the tree must not
        be read twice, so this thread prepares the file. self.wanted is written
        by the event loop only (a request adds its event), the snapshot taken
        here needs no lock, and the waiters are woken with the loop token
        because a trio.Event must not be set from another thread.

        Args:
            token (trio.lowlevel.TrioToken): Token of the event loop
            woken (set[str]): Files already served by this warmup, so a file is
                prepared and woken once
        """
        for path in list(self.wanted):
            if path in woken:
                continue
            woken.add(path)
            record = self.manifest.get(path)
            if record is not None and path not in self.bodies:
                self.bodies[path] = self._prepare(path, record)
            token.run_sync_soon(self.wanted[path].set)

    def _prepare(self, path, record):
        """
        Read, check and compress one frontend file.

        Runs in the warmup thread (or in the worker thread of a request that
        prepares its own file). The manifest check only warns: repairing a file
        is the job of the update layer, the server serves what the deployment
        has. A file that cannot be read is reported the same way and becomes
        _FAILED, so the disk is not touched again for it.

        Args:
            path (str): Frontend-relative path of the file
            record (RefInfo): Manifest record of the file

        Returns:
            FrontendBody | _Failed: The body to serve, or _FAILED when the file
                cannot be read
        """
        try:
            content = atomic_read_bytes(self.folder.joinpath(path))
        except OSError as e:
            self.unreadable.add(path)
            logger.warning(f'Failed to read the frontend file "{path}": {e}')
            return _FAILED

        digest = sha1(content).digest()
        if len(content) != record.size or digest != record.sha1:
            self.mismatched.add(path)
            logger.warning(
                f'Frontend file does not match the manifest: "{path}" is '
                f'{len(content)} bytes with sha1 {digest.hex()}, '
                f'expected {record.size} bytes with sha1 {record.sha1.hex()}'
            )

        media_type = guess_type(path)[0] or 'application/octet-stream'
        # only a page carries a CSP meta, non-html files need no policy
        csp = _html_csp(content) if media_type == 'text/html' else ''
        compressed = gzip.compress(content, compresslevel=9)
        if len(compressed) < len(content):
            return FrontendBody(body=compressed, encoding='gzip',
                                media_type=media_type, csp=csp, digest=digest.hex())
        # gzip only pays for itself when it is smaller
        return FrontendBody(body=content, encoding='',
                            media_type=media_type, csp=csp, digest=digest.hex())

    async def __call__(self, scope, receive, send):
        """
        The ASGI entry point: build the response and send it.

        Args:
            scope (Scope):
            receive (Receive):
            send (Send)

        Raises:
            RuntimeError: If the scope is not an http request (the site is
                mounted at "/", so it sees every path that no route matched)
        """
        if scope['type'] != 'http':
            raise RuntimeError(f'{type(self).__name__} serves http requests only, got "{scope["type"]}"')

        path = get_route_path(scope)
        response = await self.get_response(path, scope)
        await response(scope, receive, send)

    async def get_response(self, path, scope):
        """
        Build the response of a request.

        Args:
            path (str): Request path, with the leading slash (the site is
                mounted at "/")
            scope (Scope):

        Returns:
            Response: The file, the page for a route, or a 304 when the client
                already has this representation

        Raises:
            HTTPException: 405 for a method other than GET / HEAD, 404 for a
                path without a file
        """
        if scope['method'] not in ('GET', 'HEAD'):
            raise HTTPException(status_code=status.HTTP_405_METHOD_NOT_ALLOWED)
        if self.manifest is None:
            # the warmup task reads the table and publishes it: wait for it
            # instead of reading the file here (see _ensure_manifest for the
            # case where no warmup is running)
            await self._ensure_manifest()
        path = path.lstrip('/')
        if not path:
            # "/" is the entry of the SPA
            path = INDEX_HTML
        records = self.manifest
        if path not in records:
            path = self._route_page(path, records)

        body = self.bodies.get(path)
        if body is None:
            body = await self._body_of(path)
        if body is _FAILED:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        if 'gzip' not in Headers(scope=scope).get('Accept-Encoding', ''):
            # never send an encoding the client did not ask for (RFC 9110);
            # the decoded body is a decompression of a body in memory
            body = self.identity_cache.get(path, body=body)
        return self._response(body, scope)

    async def _ensure_manifest(self):
        """
        Get the manifest table, reading it when no warmup is going to.

        The table is normally published by the warmup task (_warm_all). When no
        warmup is running -- the site is mounted without the lifespan, or the
        warmup was cancelled before it read the manifest -- the first request
        reads it, so a request never waits for a table nobody will publish.
        """
        if self._warming:
            await self._loaded_event.wait()
        if self.manifest is not None:
            return
        # the event loop thread is the only writer in this branch, no lock is
        # needed; a table published by the warmup meanwhile stays (same table)
        records = await trio.to_thread.run_sync(_read_manifest, self.root, self.manifest_file)
        if self.manifest is None:
            self.manifest = records
        self._loaded_event.set()

    def _route_page(self, path, records):
        """
        Translate a path without a file into the page to serve.

        The SPA router of the frontend handles its routes: a path without a
        file extension is a route and is answered with the page. A path with an
        extension is a resource of the page (js / css / image): a missing
        resource must stay a 404, answering it with the page would turn a
        missing file into a MIME type error in the browser.

        Args:
            path (str): Request path, the leading slash already stripped
            records (dict[str, RefInfo]): The manifest table

        Returns:
            str: INDEX_HTML

        Raises:
            HTTPException: 404 for a resource, and when the frontend has no
                usable manifest or no page
        """
        name = path.rpartition('/')[2]
        if '.' in name or INDEX_HTML not in records:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return INDEX_HTML

    async def _body_of(self, path):
        """
        The prepared body of a file that is not in memory yet.

        Two cases:

        1. a warmup thread is walking the tree: register in self.wanted and
           wait for it. The file is prepared by the thread that is already
           reading the tree, before the next file of its walk, so the tree is
           read once and the wait is bounded by one file;
        2. no warmup is running (the site is mounted without the lifespan, or
           the warmup was abandoned at shutdown): prepare the file in a worker
           thread, the request is served like any other.

        Args:
            path (str): Path of a file of the manifest

        Returns:
            FrontendBody | _Failed: The body to serve, or _FAILED when the file
                cannot be read
        """
        if self._warming:
            event = self.wanted.get(path)
            if event is None:
                event = self.wanted[path] = trio.Event()
            await event.wait()
            body = self.bodies.get(path)
            if body is not None:
                return body

        record = self.manifest.get(path)
        if record is None:
            # a file the table does not have cannot be served (the warmup was
            # abandoned before its walk reached the file)
            return _FAILED
        body = await trio.to_thread.run_sync(self._prepare, path, record)
        self.bodies[path] = body
        return body

    def _response(self, body, scope):
        """
        Build the response of a prepared body.

        Args:
            body (FrontendBody): Prepared body of the file
            scope (Scope):

        Returns:
            Response: The response, a 304 Not Modified when the client already
                has this representation
        """
        headers = {
            # No cache for static files
            # We've seen too many styling issues in ALAS. We use electron as client and chromium caches
            # static files on user's disk. Those files may get broke for unknown reason, causing the styling
            # issues. To fix that, we tell the browsers don't cache any. Bandwidth increase should be
            # acceptable on local service.
            'Cache-Control': 'no-cache, no-store, private, must-revalidate, max-age=0',
            'Expires': '0',
            'Pragma': 'no-cache',
            # the body depends on Accept-Encoding (gzip or identity), no cache
            # may hand a client the other representation
            'Vary': 'Accept-Encoding',
            # the gzip and the identity body are different representations of
            # the same file, a strong ETag must tell them apart
            'ETag': f'"{body.digest}-gzip"' if body.encoding else f'"{body.digest}"',
        }
        if body.encoding:
            headers['Content-Encoding'] = body.encoding
        if body.csp:
            headers['Content-Security-Policy'] = body.csp

        response = Response(body.body, media_type=body.media_type, headers=headers)
        if _not_modified(response.headers, scope):
            return NotModifiedResponse(response.headers)
        return response


def _read_manifest(root, manifest):
    """
    Read and validate the frontend manifest.

    A manifest that cannot be read or does not validate only logs a warning:
    the served files come from the manifest table, a broken table is not
    guessed at, so the frontend answers 404 to every path (a checkout has no
    manifest at all, its frontend is served by the vite dev server).

    Args:
        root (str): Root the manifest path is relative to
        manifest (str): Path of the manifest file, relative to root

    Returns:
        dict[str, RefInfo]: Records keyed by the frontend-relative path, {} when
            the manifest is unusable
    """
    file = PathStr.new(root).joinpath(manifest)
    try:
        data = atomic_read_bytes(file)
    except OSError as e:
        logger.warning(f'Failed to read the frontend manifest: {e}, frontend not served')
        return {}
    try:
        decoder = PackDecodeManifest(data)
        decoder.validate()
        records = decoder.files
    except PackDecodeError as e:
        logger.warning(f'Failed to decode the frontend manifest "{file}": {e}, frontend not served')
        return {}

    records = _frontend_records(records)
    if not records:
        logger.warning(f'Frontend manifest lists no file of "{FRONTEND_FOLDER}", frontend not served')
        return {}
    if INDEX_HTML not in records:
        logger.warning(f'Frontend build "{FRONTEND_FOLDER}" has no {INDEX_HTML}, routes cannot be served')
    logger.info(f'Frontend loaded: {len(records)} files from "{FRONTEND_FOLDER}"')
    return records


def _frontend_records(manifest):
    """
    Records of the frontend folder, keyed by the frontend-relative path.

    Args:
        manifest (dict[str, RefInfo]): Records of the manifest, keyed by the
            path as recorded in the wheel

    Returns:
        dict[str, RefInfo]: {frontend-relative path: RefInfo}, the records
            outside of the frontend folder are dropped
    """
    prefix = f'{FRONTEND_FOLDER}/'
    out = {}
    for path, record in manifest.items():
        folder, sep, relative = path.partition(prefix)
        if folder or not sep:
            continue
        out[relative] = record
    return out


def _html_csp(content):
    """
    Content-Security-Policy of an html page.

    The build-time csp-inline-hash plugin keeps the meta of the page in sync
    with the inline scripts, so the meta is mirrored into the response header
    and the browser enforces their intersection. The policy is extended with
    frame-ancestors, which the meta tag ignores. A page without a meta falls
    back to the constant policy.

    Args:
        content (bytes): Content of the html page

    Returns:
        str: CSP value of the response header
    """
    match = re.search(
        r'<meta[^>]*http-equiv="Content-Security-Policy"[^>]*content="([^"]*)"',
        content.decode('utf-8', errors='replace'),
        re.IGNORECASE,
    )
    csp = match.group(1).strip() if match else ''
    if not csp:
        return CSP
    if 'frame-ancestors' not in csp:
        return f'{csp}; {FRAME_ANCESTORS}'
    return csp


def _not_modified(response_headers, scope):
    """
    Check the If-None-Match request header against the ETag of a response.

    Args:
        response_headers (Headers): Headers of the response being built
        scope (Scope):

    Returns:
        bool: True when the client already has this representation
    """
    etag = response_headers.get('ETag')
    if not etag:
        return False
    if_none_match = Headers(scope=scope).get('If-None-Match', '')
    return etag in [tag.strip(' W/') for tag in if_none_match.split(',')]


# The frontend site of the backend process, the app create_app() mounts at "/".
# The constructor only stores the paths, every read happens in warm_up() or in
# a request, so importing this module never touches the disk.
SITE = FrontendSite(root=env.ALASIO_ROOT, manifest=MANIFEST_FILE)
