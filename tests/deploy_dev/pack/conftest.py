"""
Shared test data: a mock modern full-stack website (python backend + svelte 5 frontend).

The file list is designed to cover every record type produced by PackFull:

- edit: A (added), C (copied, same content as a previous file), D (deleted marker,
  auto-added for folders without __init__.py)
- eol: 0 (LF), 1 (CRLF via .gitattributes), 2 (binary, contains b'\\x00')
- mode: 644 (0) and 755 (1) files, mode comes from the git entry mode
- algo: 0 (raw, small/incompressible files), 1 (lzma, big compressible files)
- empty files (size = 0, sha1 = ''), deep paths, duplicate contents (C)
"""
from hashlib import sha1
from random import Random

import httpx
import pytest

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.pack_model import IdxInfo
from alasio.deploy.pack.server_file import ServerFile
from alasio.deploy_dev.pack.pack_repo import PackFull
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.git.mock.mock_repo import MockGitRepo

COMMIT = 'c1'


@pytest.fixture
def app_folder(fs, monkeypatch):
    """Set PROJECT_ROOT to a fresh folder in the fake filesystem.

    The fs fixture is imported explicitly in the test modules, see
    the usage notes in alasio/testing/filesystem/__init__.py.
    """
    monkeypatch.setattr(env, 'PROJECT_ROOT', PathStr.new(fs.root_dir.path))


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

# Module level singletons shared by all tests in this folder.
# The repo and the encoded pack are treated as read-only test data:
# registering more files or mutating the pack bytes would affect every
# test, create a fresh MockGitRepo instead. Building the pack once here
# avoids re-encoding it in every test.
WEBSITE_REPO = MockGitRepo()
for path, (content, mode) in WEBSITE_FILES.items():
    WEBSITE_REPO.register_file(COMMIT, path, content, mode=mode)
WEBSITE_FULL_PACK = b''.join(PackFull(WEBSITE_REPO, commit=COMMIT).iter_pack_data())
# index pack: header + index section only, no data section
WEBSITE_INDEX_PACK = b''.join(PackFull(WEBSITE_REPO, commit=COMMIT).iter_packidx_data())


class MockServerFile(ServerFile):
    """
    In-memory ServerFile for tests, serves the pack data without http.

    The http requests are intercepted by an httpx.MockTransport client
    created in __init__, so the whole ServerFile logic (range requests,
    index pack assembly) runs as-is and only the transport differs.
    register_version() stores the full pack and the index pack of a
    version, the transport handler serves latest.pack and the range
    requests from the memory.
    """

    def __init__(self, base_url='http://mock'):
        super().__init__(
            base_url, client=httpx.Client(transport=httpx.MockTransport(self._handle)))
        # {version: full pack}
        self.full_packs = {}
        # {version: index pack}
        self.index_packs = {}
        # {(old_version, new_version): update pack}
        self.update_packs = {}
        # the latest registered version
        self.latest_version = ''

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

    def _handle(self, request):
        """
        MockTransport handler, serves the packs from the memory.

        Args:
            request (httpx.Request): The request

        Returns:
            httpx.Response: The response
        """
        path = request.url.path
        if path.endswith('/latest.pack'):
            index_pack = self.index_packs[self.latest_version]
            # the checksum of the pack format: the trailing 20 bytes
            # of the index section, the same digest the decoder
            # validates
            checksum = bytes.fromhex(PackDecodeBase(index_pack).index_checksum)
            content = self.latest_version.encode() + checksum
            return httpx.Response(200, content=content)
        # base_url/{new_version}/from_{old_version}.pack
        version, _, old = path.strip('/').partition('/from_')
        if old:
            content = self.update_packs[(old[: -len('.pack')], version)]
            return httpx.Response(200, content=content)
        # base_url/{version}/full.pack, served as a range request
        version = path.strip('/').partition('/')[0]
        start, _, end = request.headers['Range'].partition('=')[2].partition('-')
        content = self.full_packs[version][int(start):int(end) + 1]
        return httpx.Response(206, content=content)


# MockServerFile serving the website packs in memory, read-only test data
WEBSITE_SERVER = MockServerFile()
WEBSITE_SERVER.register_version(COMMIT, WEBSITE_FULL_PACK, WEBSITE_INDEX_PACK)


# ════════════════════════════════════════════════════════════════════════════
#  shared content helpers
# ════════════════════════════════════════════════════════════════════════════


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

# A real upgrade between two full packs, shared by TestPackDiffFullScenario
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
#  mock decoder
# ════════════════════════════════════════════════════════════════════════════


class MockDecodeBase:
    """
    Mock of PackDecodeBase for tests: idx_info records and catdata content.

    The records are built from {path: content} test data with
    from_data, catdata returns the content directly. Records are
    stored raw (algo=0), the mock has no compressed data.
    """

    def __init__(self, idx_info, data):
        """
        Args:
            idx_info (list[IdxInfo]): Records of the version
            data (dict[str, bytes]): {path: content} of the version
        """
        self.idx_info = idx_info
        self._data = data

    def catdata(self, info):
        """
        Get the raw bytes of a file, the content in the mock.

        Args:
            info (IdxInfo): Record of the file

        Returns:
            bytes: File content
        """
        return self._data[info.path]

    @classmethod
    def from_data(cls, files, eols=None, modes=None, edits=None):
        """
        Build a mock decoder from {path: content} test data.

        Args:
            files (dict[str, bytes]): {path: blob content}
            eols (dict[str, int], optional): Per-path eol values.
                Defaults to None, all files are eol=0.
            modes (dict[str, int], optional): Per-path mode values.
                Defaults to None, all files are mode=0.
            edits (dict[str, int], optional): Per-path edit values.
                Defaults to None, all files are edit=0.

        Returns:
            MockDecodeBase:
        """
        eols = eols or {}
        modes = modes or {}
        edits = edits or {}
        idx_info = []
        data = {}
        for path, content in files.items():
            info = IdxInfo(
                path=path,
                size=len(content),
                sha1=sha1(content).hexdigest() if content else '',
                eol=eols.get(path, 0),
                mode=modes.get(path, 0),
                edit=edits.get(path, 0),
            )
            idx_info.append(info)
            data[path] = content
        return cls(idx_info=idx_info, data=data)
