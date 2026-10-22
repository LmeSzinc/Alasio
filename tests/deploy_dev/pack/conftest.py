"""
Shared test data: a mock modern full-stack website (python backend + svelte 5 frontend).

The fixtures are shared by the encoder tests (tests/deploy_dev/pack) and the
decoder and job tests (tests/deploy/pack), which re-exports the fixtures in
scope, see its conftest.

The file list is designed to cover every record type produced by PackFull:

- edit: A (added), C (copied, same content as a previous file), D (deleted marker,
  auto-added for folders without __init__.py)
- eol: 0 (LF), 1 (CRLF via .gitattributes), 2 (binary, contains b'\\x00')
- mode: 644 (0) and 755 (1) files, mode comes from the git entry mode
- algo: 0 (raw, small/incompressible files), 1 (lzma, big compressible files)
- empty files (size = 0, sha1 = b''), deep paths, duplicate contents (C)
"""
import sqlite3
from hashlib import sha1
from random import Random
from time import time
from urllib.parse import urlsplit

import httpx2
import pytest
import trio
from starlette.applications import Starlette
from starlette.routing import Mount

from alasio.db.table import AlasioTable
from alasio.deploy.httpclient.client_async import AsyncHttpClient
from alasio.deploy.httpclient.probe_db import RECORD_TTL, MirrorRecord, MirrorRecordTable
from alasio.deploy.pack import job as job_module
from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.server_file import ServerFile
from alasio.deploy_dev.pack import _pack_cache
from alasio.deploy_dev.pack._pack_cache import PackCache
from alasio.deploy_dev.pack.pack_full import PackFull
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.testing.static_file import MockStaticFile

COMMIT = 'c1'
# The app folder of a test is a directory of the fake root, never the root
# itself: the expected log texts of the deploy tests are built from
# env.PROJECT_ROOT with f-strings, and the root "/" of POSIX would turn every
# "{root}/.pack" into "//.pack". A project is a directory of the filesystem
# anyway.
APP_FOLDER = 'app'


class MemoryLock:
    """
    In-memory stand-in of SQLiteFileLock for the fake filesystem tests.

    The job lock is a SQLite file lock, SQLite opens its file in the C
    layer where the in-memory fake filesystem cannot intercept it (the
    real lock is covered by test_deploy_lock.py on a real directory).
    The interface used by DeployJob is implemented, not reentrant like
    SQLiteFileLock: a second acquire() of the holding thread raises.
    """

    def __init__(self, lock_file, timeout=-1):
        """
        Args:
            lock_file (str): Path of the lock file
            timeout (float): Kept for the SQLiteFileLock interface.
                Defaults to -1.
        """
        self.lock_file = str(lock_file)
        self.timeout = timeout
        self._held = False

    @property
    def is_locked(self):
        """
        Returns:
            bool: True if the lock is held
        """
        return self._held

    def acquire(self, timeout=None, owner=None):
        """
        Args:
            timeout (float, optional): Ignored, the lock is in memory.
            owner (int, optional): Ignored, kept for the SQLiteFileLock
                interface

        Returns:
            MemoryLock: self

        Raises:
            RuntimeError: If this thread already holds the lock
        """
        if self._held:
            raise RuntimeError(
                f'The lock "{self.lock_file}" is already held by this thread, '
                f'a reentrant acquire would wait for itself'
            )
        self._held = True
        return self

    def release(self, force=False):
        """
        Args:
            force (bool): Ignored, kept for the SQLiteFileLock interface
        """
        self._held = False


@pytest.fixture
def app_folder(fs, monkeypatch):
    """Set PROJECT_ROOT to a fresh folder in the fake filesystem.

    The folder is a directory of the fake root (APP_FOLDER), not the root
    itself, so the paths built from env.PROJECT_ROOT in the expected log
    texts are the ones the job logs on every platform.

    The fs fixture is imported explicitly in the test modules, see
    the usage notes in alasio/testing/filesystem/__init__.py. The lock
    of DeployJob is swapped for an in-memory lock: the SQLite file lock
    opens its file in the C layer, which the in-memory fake filesystem
    cannot serve, see test_deploy_lock.py for the real lock on a real
    directory.
    """
    monkeypatch.setattr(env, 'PROJECT_ROOT', PathStr.new(fs.root_dir.path).joinpath(APP_FOLDER))
    fs.create_dir(env.PROJECT_ROOT)
    monkeypatch.setattr(job_module, 'SQLiteFileLock', MemoryLock)


@pytest.fixture(autouse=True)
def _posix_exec_bits(monkeypatch):
    """
    Run the mode matching in the POSIX branch on every host.

    The fake filesystem simulates the POSIX file modes, so the mode
    behavior of the tests is POSIX; the Windows branch (the exec
    bits are not applicable) is covered by the tests that override
    env.POSIX.
    """
    monkeypatch.setattr(env, 'POSIX', True)


@pytest.fixture
def cache(monkeypatch):
    """
    Fresh pack cache for one test

    The pack modules share the process wide _pack_cache.PACK_CACHE, a test that
    reads the counters, wants cold tables or starts a new run swaps it for a
    cache of its own, see _pack_cache.

    Returns:
        PackCache: Cache of this test
    """
    fresh = PackCache()
    monkeypatch.setattr(_pack_cache, 'PACK_CACHE', fresh)
    return fresh


# {path: (content, mode)}
WEBSITE_FILES = {
    '.gitattributes': (
        b'*.py text eol=lf\n*.txt text eol=crlf\n*.bat text eol=crlf\n*.png binary\n',
        644,
    ),
    # python backend
    'backend/__init__.py': (b'', 644),  # empty file
    'backend/main.py': (
        b'import uvicorn\n\nif __name__ == "__main__":\n    uvicorn.run("app:app")\n',
        644,
    ),
    'backend/config.py': (b'HOST = "0.0.0.0"\nPORT = 8000\nDEBUG = False\n', 644),
    # same content as config.py -> C (copied)
    'backend/utils.py': (b'HOST = "0.0.0.0"\nPORT = 8000\nDEBUG = False\n', 644),
    # same content again -> C (copied), references utils.py -> config.py chain
    'backend/settings.py': (b'HOST = "0.0.0.0"\nPORT = 8000\nDEBUG = False\n', 644),
    # CRLF text -> eol = 1
    'backend/requirements.txt': (b'fastapi==0.111.0\r\nuvicorn==0.30.1\r\n', 644),
    'backend/api/__init__.py': (b'from .routes import router\n', 644),
    'backend/api/routes.py': (
        b'from starlette.routing import Route\n\nasync def index(request):\n    return {}\n',
        644,
    ),
    # binary content (contains b'\\x00') -> eol = 2
    'backend/static/logo.png': (bytes(range(256)) * 100, 644),
    # backend/tools/ has no __init__.py -> auto D marker added
    'backend/tools/helper.py': (b'def helper():\n    return 42\n', 644),
    # svelte 5 frontend
    'frontend/package.json': (b'{\n  "name": "website",\n  "type": "module"\n}\n', 644),
    'frontend/tsconfig.json': (b'{\n  "compilerOptions": {\n    "strict": true\n  }\n}\n', 644),
    'frontend/src/App.svelte': (
        b'<script lang="ts">\n  let count = $state(0);\n</script>\n\n'
        b'<button onclick={() => count++}>{count}</button>\n',
        644,
    ),
    # same content as App.svelte -> C (copied)
    'frontend/src/lib/Button.svelte': (
        b'<script lang="ts">\n  let count = $state(0);\n</script>\n\n'
        b'<button onclick={() => count++}>{count}</button>\n',
        644,
    ),
    # big & compressible -> algo = 1 (lzma)
    'frontend/src/lib/styles.css': (b'.btn {\n  color: red;\n  padding: 1rem;\n}\n' * 3000, 644),
    'frontend/src/routes/+page.svelte': (
        b'<script lang="ts">\n  import App from "$lib/App.svelte";\n</script>\n\n<App />\n',
        644,
    ),
    # executable scripts (mode 755)
    'scripts/deploy.sh': (b'#!/bin/sh\nset -e\necho "deploy"\n', 755),
    # same content as deploy.sh -> C (copied)
    'scripts/run.sh': (b'#!/bin/sh\nset -e\necho "deploy"\n', 755),
    # windows batch file, CRLF line endings -> eol = 1
    'scripts/run.bat': (b'@echo off\r\npython -m website\r\n', 644),
    'docs/README.md': (b'# Website\n\nModern full-stack site.\n', 644),
}

# Module level singletons shared by all tests of the pack conftest (the
# encoder tests of tests/deploy_dev/pack and the decoder and job tests of
# tests/deploy/pack).
# The repo and the encoded pack are treated as read-only test data:
# registering more files or mutating the pack bytes would affect every
# test, create a fresh MockGitRepo instead. Building the pack once here
# avoids re-encoding it in every test.
WEBSITE_REPO = MockGitRepo()
for path, (content, mode) in WEBSITE_FILES.items():
    WEBSITE_REPO.register_file(COMMIT, path, content, mode=mode)
WEBSITE_REPO.register_commit(
    COMMIT, author_name='Author', author_time=0, message='Initial commit'
)
WEBSITE_FULL_PACK = b''.join(PackFull(WEBSITE_REPO, commit=COMMIT).iter_pack_data())
# index pack: header + index section only, no data section
WEBSITE_INDEX_PACK = b''.join(PackFull(WEBSITE_REPO, commit=COMMIT).iter_packidx_data())


class MockHttpClient(AsyncHttpClient):
    """
    The AsyncHttpClient of MockServerFile: an httpx2.AsyncClient that talks to
    the app of the mock (httpx2.ASGITransport), one client per trio run.

    httpx clients are bound to the event loop they are used in, and
    pytest-trio runs every test case in a fresh trio.run(), so a
    module level MockServerFile (WEBSITE_SERVER) serves every test with
    one client per run, created on first use. This class replaces the
    client of the instance after construction (MockServerFile.__init__).
    """

    def __init__(self, server):
        """
        Args:
            server (MockServerFile): The server the requests serve
        """
        # no injected client: the clients of this instance are created
        # by it, the tests never close a shared mock server
        super().__init__()
        self.server = server
        # {trio token: client} of the runs this instance served
        self._clients = {}

    def _get_aclient(self):
        """
        The client of the current trio run, created on first use.

        Returns:
            httpx2.AsyncClient: The client to send the requests with
        """
        token = trio.lowlevel.current_trio_token()
        client = self._clients.get(token)
        if client is None:
            client = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=self.server.app()))
            self._clients[token] = client
        return client


def mock_serving_app(server, app):
    """
    The ASGI app of a MockServerFile: the packs of the registry, served by the
    static file server of the mock.

    The wrapper registers the packs into the static file server before every
    request (a pack registered after the last request is served from the next
    one on, latest.pack follows latest_version) and keeps what the tests of
    the packs assert:

    - the bytes of an update pack the server transfers are counted, see
      MockServerFile.update_bytes;
    - the bytes of a full pack fetched without a range request (the full
      pack path of a rebuild) are counted, see MockServerFile.full_bytes;
    - the urls of the requests are recorded in order, see
      MockServerFile.requests.

    Args:
        server (MockServerFile): The mock the packs belong to
        app (Starlette): The app serving the registered packs

    Returns:
        Callable: The ASGI app to send the requests to
    """
    async def serving(scope, receive, send):
        if scope['type'] != 'http':
            await app(scope, receive, send)
            return
        server.static.register_files(server._packs())
        path = scope['path']
        name = path.rpartition('/')[2]
        headers = dict(scope['headers'])
        server.requests.append(
            f"{scope['scheme']}://{headers.get(b'host', b'').decode()}{path}")
        if scope['method'] == 'GET' and name == 'full.pack' and b'range' not in headers:
            # the whole-pack download of the full pack path: the bytes are
            # counted for the tests that assert what was transferred
            async def counting_send(message):
                if message['type'] == 'http.response.body':
                    server.full_bytes += len(message.get('body', b''))
                await send(message)

            await app(scope, receive, counting_send)
            return
        if not name.startswith('update_'):
            await app(scope, receive, send)
            return

        async def counting_send(message):
            # the bytes of the update pack the client reads: a body, the range
            # of a kept pack (the trailing 20 bytes digest), nothing for an
            # unregistered pack (the 404 the caller rebuilds on)
            if message['type'] == 'http.response.body':
                server.update_bytes += len(message.get('body', b''))
            await send(message)

        await app(scope, receive, counting_send)

    return serving


class MockServerFile(ServerFile):
    """
    In-memory ServerFile for tests: the registered packs, served by the static
    file server of the mock.

    register_version() / register_update() keep the packs in memory, the app
    of the instance serves them the way a deployment serves them:
    {base url}/latest.pack, {base url}/{version}/full.pack and
    {base url}/{new_version}/update_{old_version}.pack (the path of the base url
    is the mount point, so the channel base of a deployment works as it is).
    The files are served by MockStaticFile, the in-memory static file server
    of the tests: the range requests, the 404s and the headers the client sees
    are the ones of a static file server and no request is parsed by hand
    here; the packs stay in memory, nothing is written to the filesystem of
    the test.

    The client of the instance is replaced by MockHttpClient: one client is
    created per trio run when it is first requested, httpx clients are bound
    to the event loop they are used in, and pytest-trio runs every test case
    in a fresh trio.run(), so a module level MockServerFile (WEBSITE_SERVER)
    can serve every test without crossing loops.

    Attributes:
        full_packs (dict[str, bytes]): {version: full pack}
        index_packs (dict[str, bytes]): {version: index pack}
        update_packs (dict[tuple[str, str], bytes]): {(old, new): update pack}
        update_bytes (int): Bytes of the update packs served, the tests assert
            what was transferred (a body, a range, a digest only)
        full_bytes (int): Bytes of the full packs served without a range
            request, the tests assert the whole-pack transfer
        latest_version (str): The latest registered version
        requests (list[str]): Urls of the requests served, in order
        static (MockStaticFile): The static file server of the packs
    """

    def __init__(self, base_url='http://mock'):
        super().__init__(base_url)
        # {version: full pack}
        self.full_packs = {}
        # {version: index pack}
        self.index_packs = {}
        # {(old_version, new_version): update pack}
        self.update_packs = {}
        # bytes of the update pack served (full bodies and ranges): the tests
        # assert what was transferred, the reuse of a kept pack reads the
        # trailing 20 bytes digest only
        self.update_bytes = 0
        # bytes of the full packs served without a range request (the whole
        # pack download of the full pack path): the tests assert the transfer
        self.full_bytes = 0
        # the latest registered version
        self.latest_version = ''
        # urls of the requests served, in order
        self.requests = []
        # path of the base url, the mount point of the packs
        self.prefix = urlsplit(base_url).path.rstrip('/')
        # the static file server the packs are registered into
        self.static = MockStaticFile()
        # the ASGI app of the instance, built on first use
        self._app = None
        # the http client of the base serves nothing, replace it by one
        # that talks to the app of this mock, one client per trio run,
        # see MockHttpClient
        self._http = MockHttpClient(self)

    def register_version(self, version, full_pack, index_pack):
        """
        Register the packs of a version, it becomes the latest one.

        Args:
            version (str): Version to register
            full_pack (bytes): Full pack of the version
            index_pack (bytes): Index pack of the version
        """
        self.full_packs[version] = full_pack
        self.index_packs[version] = index_pack
        self.latest_version = version

    def register_update(self, old_version, new_version, update_pack):
        """
        Register the update pack from an old version to a new version.

        Args:
            old_version (str): Version of the local files
            new_version (str): Version to update to
            update_pack (bytes): Update pack data
        """
        self.update_packs[(old_version, new_version)] = update_pack

    def app(self):
        """
        The ASGI app of the mock: the packs of the registry, served by the
        static file server of the mock.

        The registry is registered into the static file server before every
        request, see mock_serving_app().

        Returns:
            Callable: The ASGI app to send the requests to
        """
        if self._app is None:
            routes = [Mount(self.prefix or '/', app=self.static)]
            self._app = mock_serving_app(self, Starlette(routes=routes))
        return self._app

    def _packs(self):
        """
        The files the app serves: {path below the folder: content}.

        Returns:
            dict[str, bytes]: The files of the mock, the layout of a
                deployment
        """
        packs = {
            f'{version}/full.pack': content
            for version, content in self.full_packs.items()
        }
        packs.update({
            f'{new_version}/update_{old_version}.pack': content
            for (old_version, new_version), content in self.update_packs.items()
        })
        # latest.pack: the version in bytes, then the 20 bytes checksum of the
        # index pack of the version, see LatestInfo
        index_pack = self.index_packs[self.latest_version]
        checksum = bytes.fromhex(PackDecodeBase(index_pack).index_checksum)
        packs['latest.pack'] = self.latest_version.encode() + checksum
        return packs


# MockServerFile serving the website packs in memory, read-only test data
WEBSITE_SERVER = MockServerFile()
WEBSITE_SERVER.register_version(COMMIT, WEBSITE_FULL_PACK, WEBSITE_INDEX_PACK)


class FakeMirrorTable(AlasioTable):
    """
    MirrorRecordTable on the in-memory sqlite database, with IO counters.

    The real table SQL of MirrorRecordTable (CREATE_TABLE, select_one,
    upsert_row) runs against the ':memory:' database of the connection
    pool: the pack tests never touch the real gui.db. The database is
    shared by every connection, the table is dropped on construction,
    so every instance starts from an empty table (auto created on
    first use).
    """

    TABLE_NAME = MirrorRecordTable.TABLE_NAME
    CREATE_TABLE = MirrorRecordTable.CREATE_TABLE
    MODEL = MirrorRecord

    def __init__(self):
        super().__init__(':memory:')
        # the pool shares one in-memory database across the connections:
        # every instance starts from an empty table
        self.drop_table()
        # IO counters, the tests assert the reads and writes made
        self.selects = 0
        self.upserts = 0

    def select_one(self, *args, **kwargs):
        self.selects += 1
        return super().select_one(*args, **kwargs)

    def upsert_row(self, rows, conflicts='', updates='', _cursor_=None):
        self.upserts += 1
        return super().upsert_row(rows, conflicts=conflicts, updates=updates, _cursor_=_cursor_)

    def seed(self, scene='repo', name='', digest='', result='', expire=None):
        """
        Write a record as fixture data, not counted in the IO counters.

        Args:
            scene (str): Usage domain of the record. Defaults to 'repo'
            name (str): Identity of the mirror set. Defaults to ''
            digest (str): Fingerprint of the mirror structure the
                result was selected for. Defaults to ''
            result (str): Recorded mirror name. Defaults to ''
            expire (int): Expiration of the record, unix seconds.
                Defaults to None, a fresh record
                (now + the max of RECORD_TTL); tests of the
                expiration pass a value
        """
        if expire is None:
            expire = int(time()) + max(RECORD_TTL)
        super().upsert_row(
            MirrorRecord(scene=scene, name=name, digest=digest, result=result, expire=expire),
            conflicts=('scene', 'name'), updates=('digest', 'result', 'expire'))


class BrokenMirrorTable:
    """
    A mirror table that fails every operation, like a broken gui.db.
    """

    def select_one(self, **kwargs):
        raise sqlite3.OperationalError('unable to open database file')

    def upsert_row(self, row, conflicts='', updates=''):
        raise sqlite3.OperationalError('unable to open database file')


def make_server_file(mirrors, name='', table=None, client=None):
    """
    A ServerFile whose selection record is an injected table.

    The record table of a probe is the real gui.db table by default
    (ProbeBase.RECORD_TABLE is None): the pack tests inject their in-memory
    table here, they never touch the real gui.db.

    Args:
        mirrors (str | dict): Mirror input of the ServerFile
        name (str): Identity of the mirror set in the record. Defaults to ''
        table (AlasioTable-like, optional): Table to record in, e.g.
            FakeMirrorTable / BrokenMirrorTable. Defaults to None,
            the real MirrorRecordTable is created when it is first used
        client (httpx2.AsyncClient | AsyncHttpClient, optional): Client to
            send the requests with. Defaults to None

    Returns:
        ServerFile: The server file
    """
    server = ServerFile(mirrors, name=name, client=client)
    if table is not None:
        server.RECORD_TABLE = table
    return server


# ════════════════════════════════════════════════════════════════════════════
#  path validation
# ════════════════════════════════════════════════════════════════════════════

# unsafe paths: traversal, absolute, reserved system names, illegal
# characters, trailing dot / space, too long. The encoder and the decoder
# both validate with validate_filepath, the two ends of the pack must reject
# the same paths, the table is shared by test_path_validate.py of both pack
# test folders.
INVALID_PATHS = [
    '../evil.txt',
    'a/../../evil.txt',
    '/etc/passwd',
    '\\evil.txt',
    'CON',
    'CON.txt',
    'a/CON',
    'LPT1',
    'a/*.txt',
    'a/b?.txt',
    'a/b<c.txt',
    'a/b.txt.',
    'a/b.txt ',
    'a' * 300,
]


# ════════════════════════════════════════════════════════════════════════════
#  shared content helpers
# ════════════════════════════════════════════════════════════════════════════


def make_repo(versions, message=None):
    """
    Build a MockGitRepo from {commit: {path: content | (content, mode)}}.

    Every version also carries generated extra files (e.g. .pack/history.pack,
    the history of the latest commits). Their content includes the commit sha1,
    so it always differs between two commits: a diff of two versions gets an
    extra record for it, tests that assert exact records filter it with
    real_records().

    Args:
        versions (dict[str, dict]): Files of every version
        message (str, optional): Commit message of every version. Defaults to
            None, a per-commit message

    Returns:
        MockGitRepo:
    """
    repo = MockGitRepo()
    for commit, files in versions.items():
        for path, value in files.items():
            if isinstance(value, tuple):
                content, mode = value
            else:
                content, mode = value, 644
            repo.register_file(commit, path, content, mode=mode)
        repo.register_commit(
            commit, author_name='Author',
            message=message if message is not None else f'commit {commit}')
    return repo


def damage(content, ratio, seed=0):
    """
    Modify a ratio of the bytes of a content with a fixed random seed.

    Args:
        content (bytes): Content to damage
        ratio (float): Ratio of bytes to change
        seed (int): Random seed. Defaults to 0.

    Returns:
        bytes: Damaged content
    """
    rng = Random(seed)
    out = bytearray(content)
    for _ in range(int(len(out) * ratio)):
        index = rng.randrange(len(out))
        out[index] = rng.randrange(256)
    return bytes(out)


def random_bytes(size, seed='random'):
    """
    Generate deterministic incompressible content.

    Args:
        size (int): Content size
        seed (str): Seed of the content. Defaults to 'random'.

    Returns:
        bytes: Pseudo random content
    """
    count = (size + 19) // 20
    return b''.join(sha1(f'{seed}-{i}'.encode()).digest() for i in range(count))[:size]


def code_lines(count):
    """
    Generate realistic code lines with unique identifiers per block.

    Each block has its own identifiers, so plain compression cannot
    exploit repetition across blocks and a zstd patch from the old
    version wins for small edits.

    Args:
        count (int): Number of code blocks

    Returns:
        list[bytes]: One 4-line code block per entry
    """
    return [
        (
            f'    # handler {i}\n'
            f'    value_{i} = compute_{i}(input_{i}, offset={i})\n'
            f'    result_{i} = value_{i} * {i} + {i}\n'
            f'    store_{i}(result_{i})\n'
        ).encode()
        for i in range(count)
    ]


def damage_lines(lines, ratio, seed=0):
    """
    Replace a ratio of the code blocks with different blocks, like a real edit.

    Args:
        lines (list[bytes]): Code blocks
        ratio (float): Ratio of blocks to replace
        seed (int): Random seed. Defaults to 0.

    Returns:
        bytes: Damaged content
    """
    rng = Random(seed)
    out = list(lines)
    count = max(1, int(len(out) * ratio))
    for _ in range(count):
        index = rng.randrange(len(out))
        number = rng.randrange(100000)
        out[index] = (
            f'    # handler {number}\n'
            f'    value_{number} = compute_{number}(input_{number}, offset={number})\n'
            f'    result_{number} = value_{number} * {number} + {number}\n'
            f'    store_{number}(result_{number})\n'
        ).encode()
    return b''.join(out)


# ════════════════════════════════════════════════════════════════════════════
#  full upgrade scenario
# ════════════════════════════════════════════════════════════════════════════

# A real upgrade between two full packs, shared by TestRepoDiffFullScenario
# (the diff side) and test_unpack_update (the update job side). Covers every
# diff type: M (patch / plain / eol-only / mode-only), A, C (from an
# unchanged old file, from an earlier new file, cross eol / mode, copy
# chains), D, R, RM, empty files, binary files and CRLF content changes.
FULL_SCENARIO_OLD = {
    '.gitattributes':
        b'*.py text eol=lf\n*.txt text eol=crlf\n*.bat text eol=crlf\n*.sh text eol=lf\n*.png binary\n',
    'backend/__init__.py': b'',
    'backend/main.py':
        b'import uvicorn\n\nVERSION = 1\n\nif __name__ == "__main__":\n    uvicorn.run("app:app", port=8000)\n',
    'backend/tiny.py': b'x',
    'backend/config.py': b'HOST = "0.0.0.0"\nPORT = 8000\nDEBUG = False\n',
    'backend/utils.py': b'HOST = "0.0.0.0"\nPORT = 8000\nDEBUG = False\n',
    'backend/legacy.py': b'def legacy():\n    return 42\n',
    'docs/guide.txt': b'# Guide\n\nstep 1\nstep 2\nstep 3\n',
    'docs/notes.txt': b'old note\r\n',
    'docs/readme.md': b'# Website\n',
    'frontend/App.svelte': b'<script>let count = 0</script>\n<button>{count}</button>\n',
    'frontend/Button.svelte': b'<script>let count = 0</script>\n<button>{count}</button>\n',
    'scripts/run.sh': b'#!/bin/sh\nset -e\necho "run"\n',
    'scripts/old_tool.py': b'def tool():\n    return 1\n' * 30,
    'scripts/run.bat': b'@echo off\npython -m website\n',
    'tools/tool.sh': (b'#!/bin/sh\nset -e\necho "tool"\n', 755),
    'tools/deploy.sh': (b'#!/bin/sh\nset -e\necho "deploy"\n', 755),
    'data/blob.png': bytes(range(256)) * 100,
    'data/cache.pkl': random_bytes(6400, 42),
}

FULL_SCENARIO_NEW = {
    '.gitattributes':
        b'*.py text eol=lf\n*.txt text eol=crlf\n*.bat text eol=lf\n*.sh text eol=lf\n*.png binary\n',
    'backend/__init__.py': b'',
    'backend/main.py':
        b'import uvicorn\n\nVERSION = 2\n\nif __name__ == "__main__":\n    uvicorn.run("app:app", port=9000)\n',
    'backend/tiny.py': b'y',
    'backend/config.py': b'HOST = "0.0.0.0"\nPORT = 8000\nDEBUG = False\n',
    'backend/utils.py': b'HOST = "0.0.0.0"\nPORT = 8000\nDEBUG = False\n',
    'backend/copy.py': b'HOST = "0.0.0.0"\nPORT = 8000\nDEBUG = False\n',
    'backend/empty.txt': b'',
    'backend/a1.py': b'def shared():\n    return 0\n' * 20,
    'backend/a2.py': b'def shared():\n    return 0\n' * 20,
    'backend/a3.py': b'def shared():\n    return 0\n' * 20,
    'docs/guide.txt': b'# Guide\n\nstep 1\nstep 2\nstep 3\n',
    'docs/guide2.txt': b'# Guide\n\nstep 1\nstep 2\nstep 3\n',
    'docs/notes.txt': b'updated note\r\n',
    'docs/readme.md': b'# Website\n',
    # copied from the unchanged LF readme.md, keeps its own eol (crlf)
    'docs/readme_copy.txt': b'# Website\n',
    # copied from the earlier new file, a copy chain
    'docs/readme_copy2.txt': b'# Website\n',
    'frontend/App.svelte': b'<script>let count = 1</script>\n<button>new</button>\n',
    'frontend/App2.svelte': b'<script>let count = 1</script>\n<button>new</button>\n',
    'frontend/Button.svelte': b'<script>let count = 0</script>\n<button>{count}</button>\n',
    'scripts/runner.sh': b'#!/bin/sh\nset -e\necho "run"\n',
    'scripts/new_tool.py': b'def tool():\n    return 2\n' * 30,
    'scripts/run.bat': b'@echo off\npython -m website\n',
    'tools/tool.sh': (b'#!/bin/sh\nset -e\necho "tool"\n', 644),
    'tools/deploy.sh': (b'#!/bin/sh\nset -e\necho "deploy"\n', 755),
    'tools/run.sh': (b'#!/bin/sh\nset -e\necho "deploy"\n', 755),
    'data/blob.png': bytes(range(256)) * 100,
    'data/new_blob.bin': random_bytes(12800, 43),
}

# ════════════════════════════════════════════════════════════════════════════
#  mock decoder is gone: the diff reads the git repo, the tests build MockGitRepo
#  versions (see make_repo above) instead of mocking a decoder
# ════════════════════════════════════════════════════════════════════════════
