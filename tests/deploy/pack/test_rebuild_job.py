"""
Tests for RebuildJob: download the latest index pack unconditionally,
remove the leftover files of the old version and rebuild the working
tree to the latest version, interruptible and resumable.

Uses the full upgrade scenario of conftest: OLD_PACK / NEW_PACK are
built at module level like test_deploy_update.py, SERVER serves both
versions without an update pack (the rebuild scenario). Every test
runs in the in-memory fake filesystem, no real files are written: the
app_folder fixture points env.PROJECT_ROOT at the fake filesystem.
Tests performing requests are async (pytest-trio); the server stubs
they monkeypatch are async functions too (the client awaits them).
"""
import os

import pytest

from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.deploy.pack.job_rebuild import RebuildJob
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.deploy_dev.pack.pack_full import PackFull
from alasio.ext import env
from alasio.ext.path.atomic import file_read_bytes, file_write
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack.conftest import FULL_SCENARIO_NEW, FULL_SCENARIO_OLD, MockServerFile, random_bytes


def make_pack(files, commit='c1'):
    """
    Build a full pack of a version.

    Args:
        files (dict[str, bytes | tuple[bytes, int]]): {path: content}
            or {path: (content, mode)}
        commit (str): Version of the pack. Defaults to 'c1'.

    Returns:
        bytes: Full pack data
    """
    repo = MockGitRepo()
    for path, value in files.items():
        if isinstance(value, tuple):
            content, mode = value
        else:
            content, mode = value, 644
        repo.register_file(commit, path, content, mode=mode)
    repo.register_commit(commit, author_name='Author', message='')
    return b''.join(PackFull(repo, commit=commit).iter_pack_data())


def read_tree():
    """
    Read the working tree of the app folder as {path: content}.

    Returns:
        dict[str, bytes]: Working tree content
    """
    tree = {}
    for root, dirs, files in os.walk(env.PROJECT_ROOT):
        # the pack structure and the logger files are not part of the
        # working tree
        dirs[:] = [dir for dir in dirs if dir not in ('.pack', 'log')]
        for name in files:
            path = os.path.join(root, name)
            key = os.path.relpath(path, env.PROJECT_ROOT).replace(os.sep, '/')
            if key.startswith(('.pack/', 'log/')):
                continue
            tree[key] = file_read_bytes(path)
    return tree


# module level singletons, built before the fake filesystem is active
OLD_PACK = make_pack(FULL_SCENARIO_OLD, commit='old')
NEW_PACK = make_pack(FULL_SCENARIO_NEW, commit='new')
OLD_DECODER = PackDecodeBase(OLD_PACK)
NEW_DECODER = PackDecodeBase(NEW_PACK)
OLD_INDEX = bytes(OLD_DECODER.extract_index_pack())
NEW_INDEX = bytes(NEW_DECODER.extract_index_pack())
NEW_TREE = {
    path: bytes(NEW_DECODER.catfile(info))
    for path, info in NEW_DECODER.fileinfo.items()
    if info.edit != 2 and not path.startswith('.pack/')
}
# files of the old version that do not exist in the new one, the
# leftover files deleted by a rebuild
OLD_ONLY = ['backend/legacy.py', 'scripts/old_tool.py', 'scripts/run.sh', 'data/cache.pkl']
SERVER = MockServerFile()
SERVER.register_version('old', OLD_PACK, OLD_INDEX)
SERVER.register_version('new', NEW_PACK, NEW_INDEX)
# no update pack registered: the incremental path is broken, the
# rebuild scenario

# packs of the rebuild matrix: pkg/__init__.py is recorded (added),
# an auto deleted marker (deleted, no __init__.py under pkg/), or
# not recorded (missing)
PKG_ADDED = make_pack({'pkg/__init__.py': b''}, commit='pkg-added')
PKG_DELETED = make_pack({'pkg/tool.py': b'x\n'}, commit='pkg-deleted')
PKG_MISSING = make_pack({'app.py': b'y\n'}, commit='pkg-missing')
PKGS = {'added': PKG_ADDED, 'deleted': PKG_DELETED, 'missing': PKG_MISSING}
# content of pkg/__init__.py, the matrix target file
GOOD = b''
BAD = b'wrong content'


def set_local(state):
    """
    Set the local pkg/__init__.py to a state of the matrix.

    Args:
        state (str): 'missing', 'good' or 'bad'
    """
    target = env.PROJECT_ROOT / 'pkg/__init__.py'
    if state == 'missing':
        try:
            os.remove(target)
        except FileNotFoundError:
            pass
        return
    os.makedirs(target.uppath(), exist_ok=True)
    with open(target, 'wb') as f:
        f.write(GOOD if state == 'good' else BAD)


def set_history(pack):
    """
    Replace the local .pack/history.pack with the one of a pack.

    The packed history is a normal record of the full pack, its
    content differs per version: an unpack of an old version leaves
    the old history, a rebuild to a new version would download the
    new one. Tests that assert "no download" align the local history
    with the target version, the matrix target is pkg/__init__.py
    and the history is out of scope.

    Args:
        pack (bytes): Full pack whose history to write
    """
    decoder = PackDecodeBase(pack)
    info = decoder.fileinfo['.pack/history.pack']
    with open(env.PROJECT_ROOT / '.pack/history.pack', 'wb') as f:
        f.write(bytes(decoder.catfile(info)))


def server_of(old_pack, new_pack):
    """
    A server whose latest version is the new pack.

    The registered version keys are the versions recorded inside the
    packs, the same strings the server requests come with.

    Args:
        old_pack (bytes): Full pack of the old version
        new_pack (bytes): Full pack of the new version, the latest one

    Returns:
        MockServerFile: Server serving both versions, no update pack
    """
    server = MockServerFile()
    old_version = PackDecodeBase(old_pack).current_version
    new_version = PackDecodeBase(new_pack).current_version
    server.register_version(
        old_version, old_pack, bytes(PackDecodeBase(old_pack).extract_index_pack()))
    server.register_version(
        new_version, new_pack, bytes(PackDecodeBase(new_pack).extract_index_pack()))
    return server


async def setup_old():
    """
    Unpack the old pack, so .pack/index.pack and all files exist.
    """
    await UnpackJob(OLD_PACK).run()


def no_file_downloads(server, monkeypatch, message):
    """
    Fail every file download, keep the index pack requests working.

    RebuildJob always downloads the index pack (get_index_pack(), a method of
    its own: the client validates it and returns the decoder): this wrapper
    patches get_file_data() only, the file downloads of download(), and raises
    AssertionError for every call.

    Args:
        server (MockServerFile): Server to patch
        monkeypatch (pytest.MonkeyPatch): Monkeypatch of the test
        message (str): Message of the raised AssertionError
    """
    async def _wrapper(*args, **kwargs):
        raise AssertionError(message)
    monkeypatch.setattr(server, 'get_file_data', _wrapper)


def server_with(pack):
    """
    A server whose latest version is the pack.

    Args:
        pack (bytes): Full pack of the latest version

    Returns:
        MockServerFile: Server serving the version of the pack
    """
    server = MockServerFile()
    decoder = PackDecodeBase(pack)
    server.register_version(
        decoder.current_version, pack, bytes(decoder.extract_index_pack()))
    return server


def no_full_pack(server, monkeypatch):
    """
    Fail every whole-pack download, keep the ranged requests working.

    The presence probe of a mostly supplied tree keeps the per-file way (see
    LocalProbe): this wrapper patches get_full_pack(), the whole-pack download
    of the full pack path, and raises AssertionError for every call.

    Args:
        server (MockServerFile): Server to patch
        monkeypatch (pytest.MonkeyPatch): Monkeypatch of the test
    """
    async def _wrapper(version, checksum=''):
        raise AssertionError('no full pack download expected')
    monkeypatch.setattr(server, 'get_full_pack', _wrapper)


# the files a partial tree is missing in the per-file tests: small records,
# the stored bytes of the large data/new_blob.bin keep the tree above
# PRESENT_RATIO
PARTIAL_MISSING = [
    'backend/a1.py',
    'backend/a2.py',
    'backend/a3.py',
    'docs/guide2.txt',
    'docs/readme_copy.txt',
    'docs/readme_copy2.txt',
    'frontend/App2.svelte',
    'scripts/new_tool.py',
    'tools/run.sh',
]
# the files to repair of the shared block test: a subset of PARTIAL_MISSING
# whose records share stored blocks (the later records must reuse the content
# of the earlier ones)
SHARED_MISSING = [
    'backend/a1.py',
    'backend/a2.py',
    'backend/a3.py',
    'docs/guide2.txt',
    'docs/readme_copy.txt',
    'docs/readme_copy2.txt',
    'frontend/App2.svelte',
]

# the request budget guard: four large blocks and 101 small ones, the
# distribution of a real pack where the bytes and the block count are decided
# by different files (the large blocks stay in the tree, the 101 missing
# small blocks are one over the budget)
GUARD_FILES = {f'big{i}.bin': random_bytes(4000, i) for i in range(4)}
GUARD_FILES.update({f'small{i}.txt': f'guard content {i}\n'.encode() for i in range(101)})
GUARD_PACK = make_pack(GUARD_FILES, commit='guard')
GUARD_DECODER = PackDecodeBase(GUARD_PACK)
GUARD_TREE = {
    path: bytes(GUARD_DECODER.catfile(info))
    for path, info in GUARD_DECODER.fileinfo.items()
    if info.edit != 2 and not path.startswith('.pack/')
}


class TestUnconditionalIndex:
    """The latest index pack is downloaded without any local check."""

    @pytest.mark.trio
    async def test_index_downloaded_when_local_valid(self, app_folder, monkeypatch):
        """A valid and latest local index does not skip the download:
        the download is unconditional, unlike ResetJob."""
        await UnpackJob(NEW_PACK).run()
        calls = []
        original = SERVER.get_index_pack

        async def _counting(version, checksum=''):
            calls.append(version)
            return await original(version, checksum)
        monkeypatch.setattr(SERVER, 'get_index_pack', _counting)
        job = RebuildJob(SERVER)
        assert await job.run()
        assert job.error == []
        # the index pack is downloaded exactly once, even though the
        # local index is valid and latest
        assert calls == ['new']
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == NEW_INDEX
        assert read_tree() == NEW_TREE
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')


class TestCleanRebuild:
    """Rebuild from a tree that is already the new version."""

    @pytest.mark.trio
    async def test_clean_rebuild_outdated_index(self, app_folder, monkeypatch):
        """A clean new tree with an outdated local index is rebuilt
        without any file download: only the index is replaced."""
        await UnpackJob(NEW_PACK).run()
        # the local index is outdated (another version), the tree is new
        with open(env.PROJECT_ROOT / '.pack/index.pack', 'wb') as f:
            f.write(OLD_INDEX)
        no_file_downloads(SERVER, monkeypatch, 'no file download expected, the tree is new')
        job = RebuildJob(SERVER)
        assert await job.run()
        assert job.error == []
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == NEW_INDEX
        assert read_tree() == NEW_TREE
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')


class TestLeftoverCleanup:
    """The leftover files of the old version are removed, user files
    are kept."""

    @pytest.mark.trio
    async def test_leftover_deleted(self, app_folder):
        """Old-only files are deleted, the tree converges to the new
        version."""
        await setup_old()
        job = RebuildJob(SERVER)
        assert await job.run()
        assert read_tree() == NEW_TREE
        for path in OLD_ONLY:
            assert not os.path.exists(env.PROJECT_ROOT / path), path
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_leftover_and_deleted_marker(self, app_folder):
        """The leftover deletion and the deleted marker of the new
        index work together."""
        await UnpackJob(NEW_PACK).run()
        # an outdated local index: legacy.py is old-only then
        with open(env.PROJECT_ROOT / '.pack/index.pack', 'wb') as f:
            f.write(OLD_INDEX)
        # a leftover file of the old version, and a file the new index
        # marks as deleted
        for path in ('backend/legacy.py', 'scripts/__init__.py'):
            target = env.PROJECT_ROOT / path
            os.makedirs(target.uppath(), exist_ok=True)
            with open(target, 'wb') as f:
                f.write(b'stale')
        job = RebuildJob(SERVER)
        assert await job.run()
        assert not os.path.exists(env.PROJECT_ROOT / 'backend/legacy.py')
        assert not os.path.exists(env.PROJECT_ROOT / 'scripts/__init__.py')
        assert read_tree() == NEW_TREE
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_user_file_kept(self, app_folder):
        """A file outside every index is not a managed file and is
        kept."""
        await setup_old()
        target = env.PROJECT_ROOT / 'user/notes.txt'
        os.makedirs(target.uppath(), exist_ok=True)
        with open(target, 'wb') as f:
            f.write(b'my notes')
        job = RebuildJob(SERVER)
        assert await job.run()
        assert file_read_bytes(target) == b'my notes'
        # the user file is not a managed file: the tree is the new
        # tree plus the user file
        tree = read_tree()
        assert all(tree[path] == content for path, content in NEW_TREE.items())
        assert tree['user/notes.txt'] == b'my notes'
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_leftover_removes_empty_folder(self, app_folder):
        """The folder of the last leftover file is removed."""
        await UnpackJob(PKG_ADDED).run()
        assert os.path.isdir(env.PROJECT_ROOT / 'pkg')
        assert await RebuildJob(server_of(PKG_ADDED, PKG_MISSING)).run()
        assert not os.path.exists(env.PROJECT_ROOT / 'pkg')
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_user_file_keeps_folder(self, app_folder):
        """A file of no index keeps its folder, the managed file of the
        folder is still deleted."""
        await UnpackJob(PKG_ADDED).run()
        user = env.PROJECT_ROOT / 'pkg/notes.txt'
        with open(user, 'wb') as f:
            f.write(b'my notes')
        assert await RebuildJob(server_of(PKG_ADDED, PKG_MISSING)).run()
        assert not os.path.exists(env.PROJECT_ROOT / 'pkg/__init__.py')
        assert file_read_bytes(user) == b'my notes'
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_old_index_missing_skips_cleanup(self, app_folder):
        """A missing old index skips the leftover cleanup with a
        warning, the rebuild still converges for the new files."""
        await setup_old()
        os.remove(env.PROJECT_ROOT / '.pack/index.pack')
        with logger.mock_capture_writer() as capture:
            job = RebuildJob(SERVER)
            assert await job.run()
        assert capture.concat() == f"""\
INFO | Rebuilding files to "{env.PROJECT_ROOT}"
INFO | Downloading the full pack of new
WARNING | Failed to read the old index pack: [Errno 2] No such file or directory: '{env.PROJECT_ROOT}/.pack/index.pack'
INFO | Rebuild done
"""
        tree = read_tree()
        assert all(tree[path] == content for path, content in NEW_TREE.items())
        # without the old index the leftovers cannot be computed and
        # are kept
        for path in OLD_ONLY:
            assert os.path.exists(env.PROJECT_ROOT / path), path
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_old_index_corrupted_skips_cleanup(self, app_folder):
        """A corrupted old index skips the leftover cleanup with a
        warning, the rebuild still converges for the new files."""
        await setup_old()
        bad = bytearray(OLD_INDEX)
        # flip a byte inside the checksum digest (the last 20 bytes)
        bad[-5] ^= 0xFF
        with open(env.PROJECT_ROOT / '.pack/index.pack', 'wb') as f:
            f.write(bad)
        with logger.mock_capture_writer() as capture:
            job = RebuildJob(SERVER)
            assert await job.run()
        assert capture.concat() == f"""\
INFO | Rebuilding files to "{env.PROJECT_ROOT}"
INFO | Downloading the full pack of new
WARNING | Failed to read the old index pack: Failed to validate index checksum: checksum mismatch
INFO | Rebuild done
"""
        tree = read_tree()
        assert all(tree[path] == content for path, content in NEW_TREE.items())
        for path in OLD_ONLY:
            assert os.path.exists(env.PROJECT_ROOT / path), path
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')


class TestFileRepair:
    """Failed files are downloaded from the server and replaced."""

    @pytest.mark.trio
    async def test_missing_file_downloaded(self, app_folder):
        """A file of the new index missing on disk is downloaded."""
        await setup_old()
        os.remove(env.PROJECT_ROOT / 'backend/config.py')
        job = RebuildJob(SERVER)
        assert await job.run()
        assert file_read_bytes(env.PROJECT_ROOT / 'backend/config.py') == \
            NEW_TREE['backend/config.py']
        assert read_tree() == NEW_TREE
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_damaged_file_downloaded(self, app_folder):
        """A file with wrong content is downloaded."""
        await setup_old()
        with open(env.PROJECT_ROOT / 'backend/main.py', 'wb') as f:
            f.write(b'wrong content')
        job = RebuildJob(SERVER)
        assert await job.run()
        assert read_tree() == NEW_TREE
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_eol_fix_no_download(self, app_folder, monkeypatch):
        """A fixable EOL mismatch is repaired locally, no download."""
        await UnpackJob(NEW_PACK).run()
        with open(env.PROJECT_ROOT / '.pack/index.pack', 'wb') as f:
            f.write(OLD_INDEX)
        # docs/guide.txt is eol=1 (CRLF) in the new index, the local
        # file is LF
        target = env.PROJECT_ROOT / 'docs/guide.txt'
        with open(target, 'wb') as f:
            f.write(NEW_TREE['docs/guide.txt'].replace(b'\r\n', b'\n'))
        no_file_downloads(SERVER, monkeypatch, 'no download expected for an EOL mismatch')
        job = RebuildJob(SERVER)
        assert await job.run()
        assert file_read_bytes(target) == NEW_TREE['docs/guide.txt']
        assert read_tree() == NEW_TREE
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_mode_fix_no_download(self, app_folder, monkeypatch, fs):
        """A mode-only mismatch is repaired locally, no download."""
        await UnpackJob(NEW_PACK).run()
        with open(env.PROJECT_ROOT / '.pack/index.pack', 'wb') as f:
            f.write(OLD_INDEX)
        # tools/tool.sh is mode 644 in the new index, the local file
        # has the execute bits
        target = env.PROJECT_ROOT / 'tools/tool.sh'
        fs.remove(target)
        fs.create_file(target, st_mode=0o100755, contents=NEW_TREE['tools/tool.sh'])
        no_file_downloads(SERVER, monkeypatch, 'no download expected for a mode mismatch')
        job = RebuildJob(SERVER)
        assert await job.run()
        assert os.stat(target).st_mode & 0o111 == 0
        assert read_tree() == NEW_TREE
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_download_failure_stays_in_error(self, app_folder, monkeypatch):
        """A file that cannot be downloaded stays in error, the run
        fails and the workspace is kept for the next run to resume."""
        # the tree holds the version except the files to repair: the
        # presence probe keeps the per-file way (the full pack path is
        # covered by TestFullPackPath)
        await UnpackJob(NEW_PACK).run()
        for path in PARTIAL_MISSING:
            os.remove(env.PROJECT_ROOT / path)
        server = server_with(NEW_PACK)
        # the file downloads fail: the client validates what it serves (see
        # ServerFile), the record stays in error

        async def _bad_files(decoder, info):
            if not info.data_size:
                # a record without data needs no download (see get_file_data)
                return b''
            raise PackDecodeError('bad content')
        monkeypatch.setattr(server, 'get_file_data', _bad_files)
        job = RebuildJob(server)
        with logger.mock_capture_writer() as capture:
            assert not await job.run()
        assert job.error
        assert capture.concat() == f"""\
INFO | Rebuilding files to "{env.PROJECT_ROOT}"
WARNING | Failed to download backend/a1.py: bad content
WARNING | Failed to download backend/a2.py: bad content
WARNING | Failed to download backend/a3.py: bad content
WARNING | Failed to download docs/guide2.txt: bad content
WARNING | Failed to download docs/readme_copy.txt: bad content
WARNING | Failed to download docs/readme_copy2.txt: bad content
WARNING | Failed to download frontend/App2.svelte: bad content
WARNING | Failed to download scripts/new_tool.py: bad content
WARNING | Failed to download tools/run.sh: bad content
WARNING | Failed to rebuild: 9 file(s) stayed in error, the workspace is kept for the next run
"""
        # the workspace is the resume base of the next run, which retries
        # the failed records
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/workspace/job.pack') == RebuildJob.MARK
        # the new index pack is not committed: the local version does
        # not advance (see JobBase.replace())
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == NEW_INDEX

    @pytest.mark.trio
    async def test_no_server(self, app_folder):
        """A missing server fails the run with a warning, the job file
        is kept for the next run to resume."""
        job = RebuildJob(None)
        with logger.mock_capture_writer() as capture:
            assert not await job.run()
        assert capture.concat() == f"""\
INFO | Rebuilding files to "{env.PROJECT_ROOT}"
WARNING | Failed to rebuild: Failed to download the index pack: no server provided, keeping the workspace for the next run
"""
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/workspace/job.pack') == RebuildJob.MARK


class TestSharedBlockDownload:
    """download(): a stored block shared by several records is fetched once."""

    @pytest.mark.trio
    async def test_every_block_fetched_once(self, app_folder, monkeypatch):
        """A per-file rebuild fetches every stored block of the repaired
        records exactly once: the later records of a shared block (a C
        record copies the data of its source) are written from the content
        of the earlier record instead of being downloaded again."""
        # the records to repair share stored blocks (the readme copies,
        # a1/a2/a3, the App copies), the premise of the test
        all_keys = [
            (info.data_start, info.data_size)
            for info in NEW_DECODER.fileinfo.values()
            if info.edit != 2 and info.data_size
        ]
        assert len(all_keys) > len(set(all_keys))
        missing_blocks = {
            (info.data_start, info.data_size)
            for info in NEW_DECODER.fileinfo.values()
            if info.edit != 2 and info.data_size and info.path in SHARED_MISSING
        }
        assert len(missing_blocks) < len(SHARED_MISSING)
        await UnpackJob(NEW_PACK).run()
        for path in SHARED_MISSING:
            os.remove(env.PROJECT_ROOT / path)
        server = server_with(NEW_PACK)
        no_full_pack(server, monkeypatch)
        calls = []
        original = server.get_file_data

        async def _serve(decoder, info):
            if info.data_size:
                # a record without data needs no range request
                # (see ServerFile.get_file_data)
                calls.append((info.data_start, info.data_size))
            return await original(decoder, info)
        monkeypatch.setattr(server, 'get_file_data', _serve)
        assert await RebuildJob(server).run()
        assert read_tree() == NEW_TREE
        # every block is fetched exactly once, not once per record
        assert sorted(calls) == sorted(missing_blocks)
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')


class TestResume:
    """Interruption and resume."""

    @pytest.mark.trio
    async def test_index_replaced_last(self, app_folder, monkeypatch):
        """The new index pack is the commit record: replace_data()
        runs with the old index still in place, replace_index() commits
        it after the data files. An interruption during the data pass
        keeps the old index, so a resumed run still computes the
        leftover deletion list from it."""
        await setup_old()
        original = RebuildJob.replace_data

        def _check(self):
            # the index is not a pending data file, the commit has not
            # happened yet: the old index is still in place
            assert all(
                item.info.path != '.pack/index.pack' for item in self.pending)
            assert self.pending_index is not None
            assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == OLD_INDEX
            return original(self)
        monkeypatch.setattr(RebuildJob, 'replace_data', _check)
        job = RebuildJob(SERVER)
        assert await job.run()
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == NEW_INDEX
        assert read_tree() == NEW_TREE
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_interrupted_replace_keeps_old_index(self, app_folder, monkeypatch):
        """An interruption during the data pass keeps the old index and is
        logged as error: real files may be partially replaced."""
        await setup_old()
        import alasio.deploy.pack.job_base as job_base
        original = job_base.atomic_replace
        calls = []

        def _fail(tmp, target):
            if calls:
                raise PermissionError('interrupted')
            calls.append(target)
            return original(tmp, target)
        monkeypatch.setattr(job_base, 'atomic_replace', _fail)
        job = RebuildJob(SERVER)
        with logger.mock_capture_writer() as capture:
            assert not await job.run()
        assert capture.concat() == f"""\
INFO | Rebuilding files to "{env.PROJECT_ROOT}"
INFO | Downloading the full pack of new
ERROR | Failed to replace file: interrupted, keeping the workspace for the next run
"""
        # the interrupted pass replaced a data file, never the index pack
        assert len(calls) == 1
        assert not str(calls[0]).endswith('index.pack')
        # the commit never ran: the old index pack is still in place
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == OLD_INDEX


class TestIdempotent:
    """A second rebuild of the new tree."""

    @pytest.mark.trio
    async def test_rebuild_twice(self, app_folder, monkeypatch):
        """Rebuilding a new tree downloads no file."""
        await setup_old()
        assert await RebuildJob(SERVER).run()
        no_file_downloads(SERVER, monkeypatch, 'no file download expected, the tree is new')
        # a fresh job, like a new run of the update flow
        assert await RebuildJob(SERVER).run()
        assert read_tree() == NEW_TREE
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')


class TestRebuildMatrix:
    """
    The rebuild outcome matrix: old index record x new index record x
    local file.

    The outcome of every state combination is decided by two rules.
    The validation against the new index handles the files it records
    (added: keep or download; deleted: remove the existing file),
    and the leftover cleanup handles the files recorded in the old
    index but missing from the new one. The deleted markers
    (edit == 2) of the old index are ignored by the leftover check:
    they describe files that should not exist, not files that exist.
    The matrix below lists all 27 combinations, the expected outcome
    and the test locking it: the tests parametrized over the old
    index states cover 3 rows each, the ones over the local file
    state cover 3 rows each.

    +------------+------------+------------+-------------------------+-------------------------------------------+
    | old index  | new index  | local file | expected                | test                                      |
    +============+============+============+=========================+===========================================+
    | added      | added      | missing    | download (validation)   | test_new_added_local_missing_downloaded   |
    | added      | added      | bad        | download (validation)   | test_new_added_local_bad_downloaded       |
    | added      | added      | good       | keep (matches)          | test_new_added_local_good_kept            |
    | added      | deleted    | missing    | no-op                   | test_new_deleted_local_missing_noop       |
    | added      | deleted    | bad        | remove (D marker)       | test_new_deleted_local_exists_removed     |
    | added      | deleted    | good       | remove (D marker)       | test_new_deleted_local_exists_removed     |
    | added      | missing    | missing    | no-op                   | test_old_added_new_missing_local_deleted  |
    | added      | missing    | bad        | remove (leftover)       | test_old_added_new_missing_local_deleted  |
    | added      | missing    | good       | remove (leftover)       | test_old_added_new_missing_local_deleted  |
    | deleted    | added      | missing    | download (validation)   | test_new_added_local_missing_downloaded   |
    | deleted    | added      | bad        | download (validation)   | test_new_added_local_bad_downloaded       |
    | deleted    | added      | good       | keep (matches)          | test_new_added_local_good_kept            |
    | deleted    | deleted    | missing    | no-op                   | test_new_deleted_local_missing_noop       |
    | deleted    | deleted    | bad        | remove (D marker)       | test_new_deleted_local_exists_removed     |
    | deleted    | deleted    | good       | remove (D marker)       | test_new_deleted_local_exists_removed     |
    | deleted    | missing    | missing    | no-op                   | test_old_deleted_new_missing_local_kept   |
    | deleted    | missing    | bad        | keep (old D ignored)    | test_old_deleted_new_missing_local_kept   |
    | deleted    | missing    | good       | keep (old D ignored)    | test_old_deleted_new_missing_local_kept   |
    | missing    | added      | missing    | download (validation)   | test_new_added_local_missing_downloaded   |
    | missing    | added      | bad        | download (validation)   | test_new_added_local_bad_downloaded       |
    | missing    | added      | good       | keep (matches)          | test_new_added_local_good_kept            |
    | missing    | deleted    | missing    | no-op                   | test_new_deleted_local_missing_noop       |
    | missing    | deleted    | bad        | remove (D marker)       | test_new_deleted_local_exists_removed     |
    | missing    | deleted    | good       | remove (D marker)       | test_new_deleted_local_exists_removed     |
    | missing    | missing    | missing    | no-op                   | test_old_missing_new_missing_local_kept   |
    | missing    | missing    | bad        | keep (user file)        | test_old_missing_new_missing_local_kept   |
    | missing    | missing    | good       | keep (user file)        | test_old_missing_new_missing_local_kept   |
    +------------+------------+------------+-------------------------+-------------------------------------------+

    The old record only matters for the leftover check, so the tests
    parametrized over it share one expected outcome per new index and
    local file state.
    """

    @pytest.mark.trio
    @pytest.mark.parametrize('old', ['added', 'deleted', 'missing'])
    async def test_new_added_local_missing_downloaded(self, app_folder, old):
        """Rows 1/10/19: a missing local file of a new added record
        is downloaded, whatever the old index says."""
        await UnpackJob(PKGS[old]).run()
        set_local('missing')
        assert await RebuildJob(server_of(PKGS[old], PKG_ADDED)).run()
        assert file_read_bytes(env.PROJECT_ROOT / 'pkg/__init__.py') == GOOD
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    @pytest.mark.parametrize('old', ['added', 'deleted', 'missing'])
    async def test_new_added_local_bad_downloaded(self, app_folder, old):
        """Rows 2/11/20: a wrong local file of a new added record is
        repaired by a download."""
        await UnpackJob(PKGS[old]).run()
        set_local('bad')
        assert await RebuildJob(server_of(PKGS[old], PKG_ADDED)).run()
        assert file_read_bytes(env.PROJECT_ROOT / 'pkg/__init__.py') == GOOD
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    @pytest.mark.parametrize('old', ['added', 'deleted', 'missing'])
    async def test_new_added_local_good_kept(self, app_folder, old, monkeypatch):
        """Rows 3/12/21: a local file that matches the new added
        record is kept without a download."""
        await UnpackJob(PKGS[old]).run()
        set_local('good')
        # the packed history differs per version and would trigger a
        # download, align it with the target version
        set_history(PKG_ADDED)
        server = server_of(PKGS[old], PKG_ADDED)
        no_file_downloads(server, monkeypatch, 'no download expected, the file matches')
        assert await RebuildJob(server).run()
        assert file_read_bytes(env.PROJECT_ROOT / 'pkg/__init__.py') == GOOD
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    @pytest.mark.parametrize('old', ['added', 'deleted', 'missing'])
    async def test_new_deleted_local_missing_noop(self, app_folder, old):
        """Rows 4/13/22: a deleted marker of the new index expects the
        file to not exist, a missing local file is a no-op."""
        await UnpackJob(PKGS[old]).run()
        set_local('missing')
        assert await RebuildJob(server_of(PKGS[old], PKG_DELETED)).run()
        assert not os.path.exists(env.PROJECT_ROOT / 'pkg/__init__.py')
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    @pytest.mark.parametrize('old', ['added', 'deleted', 'missing'])
    @pytest.mark.parametrize('local', ['good', 'bad'])
    async def test_new_deleted_local_exists_removed(self, app_folder, old, local):
        """Rows 5/6/14/15/23/24: a deleted marker of the new index
        removes the local file, whatever its content."""
        await UnpackJob(PKGS[old]).run()
        set_local(local)
        assert await RebuildJob(server_of(PKGS[old], PKG_DELETED)).run()
        assert not os.path.exists(env.PROJECT_ROOT / 'pkg/__init__.py')
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    @pytest.mark.parametrize('local', ['missing', 'good', 'bad'])
    async def test_old_added_new_missing_local_deleted(self, app_folder, local):
        """Rows 7/8/9: a leftover file (old record, new index without
        the path) is deleted, a missing one is a no-op."""
        await UnpackJob(PKG_ADDED).run()
        set_local(local)
        assert await RebuildJob(server_of(PKG_ADDED, PKG_MISSING)).run()
        assert not os.path.exists(env.PROJECT_ROOT / 'pkg/__init__.py')
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    @pytest.mark.parametrize('local', ['missing', 'good', 'bad'])
    async def test_old_deleted_new_missing_local_kept(self, app_folder, local):
        """Rows 16/17/18: a deleted marker of the old index is ignored
        by the leftover check, a local file is kept."""
        await UnpackJob(PKG_DELETED).run()
        set_local(local)
        assert await RebuildJob(server_of(PKG_DELETED, PKG_MISSING)).run()
        if local == 'missing':
            assert not os.path.exists(env.PROJECT_ROOT / 'pkg/__init__.py')
        else:
            assert file_read_bytes(env.PROJECT_ROOT / 'pkg/__init__.py') == (
                GOOD if local == 'good' else BAD)
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    @pytest.mark.parametrize('local', ['missing', 'good', 'bad'])
    async def test_old_missing_new_missing_local_kept(self, app_folder, local):
        """Rows 25/26/27: a file of no index is a user file, kept."""
        await UnpackJob(PKG_MISSING).run()
        set_local(local)
        assert await RebuildJob(server_of(PKG_MISSING, PKG_MISSING)).run()
        if local == 'missing':
            assert not os.path.exists(env.PROJECT_ROOT / 'pkg/__init__.py')
        else:
            assert file_read_bytes(env.PROJECT_ROOT / 'pkg/__init__.py') == (
                GOOD if local == 'good' else BAD)
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')


class TestFullPackPath:
    """The full pack path: a tree that supplies too little of the pack is
    rebuilt from one whole-pack download applied by the unpack stages of
    UnpackJob, with no per-record range request."""

    @pytest.mark.trio
    async def test_empty_root_downloads_full_pack(self, app_folder, monkeypatch):
        """An empty root supplies nothing: the whole full pack is
        downloaded with one request and applied."""
        server = server_with(NEW_PACK)
        no_file_downloads(server, monkeypatch, 'no per-file download expected, the full pack is used')
        job = RebuildJob(server)
        with logger.mock_capture_writer() as capture:
            assert await job.run()
        assert capture.concat() == f"""\
INFO | Rebuilding files to "{env.PROJECT_ROOT}"
INFO | Downloading the full pack of new
WARNING | Failed to read the old index pack: [Errno 2] No such file or directory: '{env.PROJECT_ROOT}/.pack/index.pack'
INFO | Rebuild done
"""
        assert read_tree() == NEW_TREE
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == NEW_INDEX
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')
        # the whole pack was transferred once, no range request was made
        # for a record
        assert server.full_bytes == len(NEW_PACK)

    @pytest.mark.trio
    @pytest.mark.parametrize('state', ['missing', 'corrupted'])
    async def test_intact_tree_keeps_per_file(self, app_folder, monkeypatch, state):
        """A tree that holds the version takes the per-file way, a missing
        or corrupted index aside: the files are kept and the whole pack is
        never downloaded."""
        await UnpackJob(NEW_PACK).run()
        if state == 'missing':
            os.remove(env.PROJECT_ROOT / '.pack/index.pack')
        else:
            with open(env.PROJECT_ROOT / '.pack/index.pack', 'wb') as f:
                f.write(b'broken')
        server = server_with(NEW_PACK)
        no_full_pack(server, monkeypatch)
        no_file_downloads(
            server, monkeypatch, 'no file download expected, the tree holds the version')
        job = RebuildJob(server)
        assert await job.run()
        assert read_tree() == NEW_TREE
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == NEW_INDEX
        assert server.full_bytes == 0
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_full_pack_failure_fails_the_flow(self, app_folder, monkeypatch):
        """A failed whole-pack download fails the flow, there is no
        per-file fallback: the range requests of the per-file way would
        fetch the very same bytes from the very same file."""
        server = server_with(NEW_PACK)

        async def _fail(version, checksum=''):
            raise PackDecodeError('bad full pack')
        monkeypatch.setattr(server, 'get_full_pack', _fail)
        no_file_downloads(server, monkeypatch, 'no per-file fallback expected')
        job = RebuildJob(server)
        with logger.mock_capture_writer() as capture:
            assert not await job.run()
        assert job.error == []
        assert capture.concat() == f"""\
INFO | Rebuilding files to "{env.PROJECT_ROOT}"
INFO | Downloading the full pack of new
WARNING | Failed to rebuild: bad full pack, keeping the workspace for the next run
"""
        # no index was committed and the workspace is the resume base of
        # the next run
        assert server.full_bytes == 0
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/index.pack')
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/workspace/job.pack') == RebuildJob.MARK


class TestFullPackReuse:
    """The full pack kept in the workspace: a run interrupted after the
    download reuses it instead of transferring the whole pack again."""

    @staticmethod
    async def interrupt_replace(server, monkeypatch):
        """
        Take the full pack path and interrupt the data pass once, leaving the
        workspace (the kept full pack, the job marker and the tmp files)
        behind.

        Args:
            server (MockServerFile): Server of the run
            monkeypatch (pytest.MonkeyPatch): Monkeypatch of the test
        """
        import alasio.deploy.pack.job_base as job_base
        original = job_base.atomic_replace
        calls = []

        def _fail(tmp, target):
            if calls:
                raise PermissionError('interrupted')
            calls.append(target)
            return original(tmp, target)
        monkeypatch.setattr(job_base, 'atomic_replace', _fail)
        job = RebuildJob(server)
        with logger.mock_capture_writer() as capture:
            assert not await job.run()
        assert capture.concat() == f"""\
INFO | Rebuilding files to "{env.PROJECT_ROOT}"
INFO | Downloading the full pack of new
WARNING | Failed to read the old index pack: [Errno 2] No such file or directory: '{env.PROJECT_ROOT}/.pack/index.pack'
ERROR | Failed to replace file: interrupted, keeping the workspace for the next run
"""
        # the whole pack is kept for the next run, the marker is the
        # record of the task in flight
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/workspace/full.pack') == NEW_PACK
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/workspace/job.pack') == RebuildJob.MARK
        # the index was not committed: the data pass never reached it
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/index.pack')
        monkeypatch.setattr(job_base, 'atomic_replace', original)

    @pytest.mark.trio
    async def test_kept_pack_reused(self, app_folder, monkeypatch):
        """A run interrupted after the pack download reuses the kept pack:
        the whole pack is not transferred again."""
        server = server_with(NEW_PACK)
        await self.interrupt_replace(server, monkeypatch)
        no_full_pack(server, monkeypatch)
        job = RebuildJob(server)
        with logger.mock_capture_writer() as capture:
            result = await job.run()
        assert result, capture.concat()
        assert capture.concat() == f"""\
INFO | Rebuilding files to "{env.PROJECT_ROOT}"
INFO | Reusing the full pack of new kept in the workspace
WARNING | Failed to read the old index pack: [Errno 2] No such file or directory: '{env.PROJECT_ROOT}/.pack/index.pack'
INFO | Rebuild done
"""
        assert read_tree() == NEW_TREE
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == NEW_INDEX
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    @pytest.mark.parametrize('kept', ['damaged', 'foreign'])
    async def test_stale_kept_pack_downloaded(self, app_folder, monkeypatch, kept):
        """A kept pack that is not the pack of the flow (damaged, or the
        pack of another version) is never reused: the whole pack is
        downloaded again."""
        server = server_with(NEW_PACK)
        await self.interrupt_replace(server, monkeypatch)
        if kept == 'damaged':
            data = bytearray(NEW_PACK)
            data[-5] ^= 0xFF
        else:
            data = OLD_PACK
        file_write(env.PROJECT_ROOT / '.pack/workspace/full.pack', bytes(data))
        calls = []
        original = server.get_full_pack

        async def _counting(version, checksum=''):
            calls.append(version)
            return await original(version, checksum)
        monkeypatch.setattr(server, 'get_full_pack', _counting)
        job = RebuildJob(server)
        assert await job.run()
        assert calls == ['new']
        assert read_tree() == NEW_TREE
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')


class TestBudgetGuard:
    """The request budget of the path decision: a tree that holds the
    stored bytes but misses more blocks than the budget is rebuilt from
    the full pack, one block below it keeps the per-file way."""

    @pytest.mark.trio
    async def test_missing_over_budget_full_pack(self, app_folder, monkeypatch):
        """101 missing small blocks: over the budget of 100, the whole
        full pack is downloaded (the present bytes alone would keep the
        per-file way)."""
        await UnpackJob(GUARD_PACK).run()
        for i in range(101):
            os.remove(env.PROJECT_ROOT / f'small{i}.txt')
        server = server_with(GUARD_PACK)
        no_file_downloads(server, monkeypatch, 'no per-file download expected, over the budget')
        job = RebuildJob(server)
        with logger.mock_capture_writer() as capture:
            assert await job.run()
        assert capture.concat() == f"""\
INFO | Rebuilding files to "{env.PROJECT_ROOT}"
INFO | Downloading the full pack of guard
INFO | Rebuild done
"""
        assert read_tree() == GUARD_TREE
        assert server.full_bytes == len(GUARD_PACK)
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_missing_within_budget_per_file(self, app_folder, monkeypatch):
        """100 missing small blocks: inside the budget, the per-file way
        repairs exactly them and the whole pack is not downloaded."""
        await UnpackJob(GUARD_PACK).run()
        for i in range(100):
            os.remove(env.PROJECT_ROOT / f'small{i}.txt')
        server = server_with(GUARD_PACK)
        no_full_pack(server, monkeypatch)
        job = RebuildJob(server)
        with logger.mock_capture_writer() as capture:
            assert await job.run()
        assert capture.concat() == f"""\
INFO | Rebuilding files to "{env.PROJECT_ROOT}"
INFO | Rebuild done
"""
        assert read_tree() == GUARD_TREE
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')
