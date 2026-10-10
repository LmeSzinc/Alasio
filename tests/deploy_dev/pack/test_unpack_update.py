"""
Tests for UpdateJob: update pack unpack, interruptible and resumable,
with source repair from the server like ResetJob.

The update pack is built with PackUpdate from the shared
FULL_SCENARIO_OLD / FULL_SCENARIO_NEW of conftest (the same versions
as TestRepoDiffFullScenario on the diff side), the old pack is
unpacked into the fake filesystem with UnpackJob, then the update is
applied with UpdateJob and the result is compared to the new version
(round-trip). The server is an in-memory MockServerFile serving the
old and new packs.

The packs are module level singletons, built before the fake
filesystem is active: MockGitRepo reads the real .gitattributes file,
which the fake filesystem does not provide. Tests performing requests
are async (pytest-trio); the server stubs they monkeypatch are async
functions too (the client awaits them).
"""
import os

import httpx2
import pytest

from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.deploy.pack.job import DeployJob
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.deploy.pack.job_update import UpdateJob
from alasio.deploy.pack.pack_model import IdxInfo
from alasio.deploy_dev.pack.pack_full import PackFull
from alasio.deploy_dev.pack.pack_update import PackUpdate
from alasio.ext import env
from alasio.ext.path.atomic import file_read_bytes
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack.conftest import FULL_SCENARIO_NEW, FULL_SCENARIO_OLD, MockServerFile, make_repo

# ════════════════════════════════════════════════════════════════════════════
#  shared versions
# ════════════════════════════════════════════════════════════════════════════

# The shared full upgrade scenario of conftest, covering every record
# type of the update pack: M (patch / plain / eol-only / mode-only),
# A, C (from an unchanged old file, from an earlier new file, cross
# eol / mode, copy chains), D, R, RM, empty files, binary files and
# CRLF content changes.
OLD = FULL_SCENARIO_OLD
NEW = FULL_SCENARIO_NEW

# ════════════════════════════════════════════════════════════════════════════
#  helpers
# ════════════════════════════════════════════════════════════════════════════


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


def unpack_tree(decoder):
    """
    Extract the working tree of a full pack as {path: content}.

    Args:
        decoder (PackDecodeBase): Decoder of the full pack

    Returns:
        dict[str, bytes]: Working tree content
    """
    return {
        path: bytes(decoder.catfile(info))
        for path, info in decoder.fileinfo.items()
        if info.edit != 2 and not path.startswith('.pack/')
    }


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


def bad_content(return_value=b'bad data'):
    """An async stand-in of a server download serving the given bytes."""
    async def _bad(*args, **kwargs):
        return return_value
    return _bad


# ════════════════════════════════════════════════════════════════════════════
#  module level singletons, built before the fake filesystem is active
# ════════════════════════════════════════════════════════════════════════════

# a repo with both versions of the scenario: the update pack is generated from
# the repo, the new version is a PackFull, the old version is its commit and the
# version its published pack records
SCENARIO_REPO = make_repo({'old': OLD, 'new': NEW})
OLD_PACK = b''.join(PackFull(SCENARIO_REPO, commit='old').iter_pack_data())
NEW_PACK = b''.join(PackFull(SCENARIO_REPO, commit='new').iter_pack_data())
OLD_DECODER = PackDecodeBase(OLD_PACK)
NEW_DECODER = PackDecodeBase(NEW_PACK)
UPDATE = b''.join(PackUpdate(
    PackFull(SCENARIO_REPO, commit='new'), 'old').iter_pack_data())
# the update pack records both versions: the new one as the current
# version and the old one as the old version, which is what tells an
# update pack from a full pack
UPDATE_DECODER = PackDecodeBase(UPDATE)
SERVER = MockServerFile()
SERVER.register_version('old', OLD_PACK, bytes(OLD_DECODER.extract_index_pack()))
SERVER.register_version('new', NEW_PACK, bytes(NEW_DECODER.extract_index_pack()))
OLD_TREE = unpack_tree(OLD_DECODER)
NEW_TREE = unpack_tree(NEW_DECODER)

# an update without any source-dependent record, so a missing or
# corrupt local index does not fail the records
_simple_repo = make_repo({
    'old': {'keep.txt': b'keep\n'},
    'new': {'keep.txt': b'keep\n', 'add.txt': b'hello\n'},
})
_simple_old_pack = b''.join(PackFull(_simple_repo, commit='old').iter_pack_data())
_simple_new_pack = b''.join(PackFull(_simple_repo, commit='new').iter_pack_data())
SIMPLE_UPDATE = b''.join(PackUpdate(
    PackFull(_simple_repo, commit='new'), 'old').iter_pack_data())
SIMPLE_SERVER = MockServerFile()
SIMPLE_SERVER.register_version(
    'old', _simple_old_pack, bytes(PackDecodeBase(_simple_old_pack).extract_index_pack()))
SIMPLE_SERVER.register_version(
    'new', _simple_new_pack, bytes(PackDecodeBase(_simple_new_pack).extract_index_pack()))
SIMPLE_NEW_INDEX = bytes(PackDecodeBase(_simple_new_pack).extract_index_pack())

# a valid index pack of another version: self-consistent, but its
# size + sha1 fails the refinfo check of the update pack
OTHER_INDEX = bytes(
    PackDecodeBase(make_pack({'x.txt': b'x'}, commit='other')).extract_index_pack())

# an update that deletes the last files of pkg/ and of the a/b/c/ chain,
# keep/keep.txt is unchanged and keeps its folder
FOLDER_REPO = make_repo({
    'old': {
        'app.py': b'y\n',
        'pkg/__init__.py': b'',
        'pkg/tool.py': b'x\n',
        'a/b/c/tool.py': b'x\n',
        'keep/keep.txt': b'keep\n',
        'keep/gone.txt': b'gone\n',
    },
    'new': {
        'app.py': b'y\n',
        'keep/keep.txt': b'keep\n',
    },
})
FOLDER_OLD_PACK = b''.join(PackFull(FOLDER_REPO, commit='old').iter_pack_data())
FOLDER_NEW_PACK = b''.join(PackFull(FOLDER_REPO, commit='new').iter_pack_data())
FOLDER_UPDATE = b''.join(PackUpdate(
    PackFull(FOLDER_REPO, commit='new'), 'old').iter_pack_data())
FOLDER_SERVER = MockServerFile()
FOLDER_SERVER.register_version(
    'old', FOLDER_OLD_PACK, bytes(PackDecodeBase(FOLDER_OLD_PACK).extract_index_pack()))
FOLDER_SERVER.register_version(
    'new', FOLDER_NEW_PACK, bytes(PackDecodeBase(FOLDER_NEW_PACK).extract_index_pack()))
# the working tree of the new version, the pack structure excluded
FOLDER_NEW_TREE = {'app.py': b'y\n', 'keep/keep.txt': b'keep\n'}

# a copy chain with an unchanged source: a.txt is the same in both
# versions, b.txt / c.txt / d.txt are new files duplicating it, the
# encoder links them as a copy chain a.txt -> b.txt -> c.txt -> d.txt
CHAIN_REPO = make_repo({
    'old': {'a.txt': b'chained\n'},
    'new': {
        'a.txt': b'chained\n',
        'b.txt': b'chained\n',
        'c.txt': b'chained\n',
        'd.txt': b'chained\n',
    },
})
CHAIN_OLD_PACK = b''.join(PackFull(CHAIN_REPO, commit='old').iter_pack_data())
CHAIN_NEW_PACK = b''.join(PackFull(CHAIN_REPO, commit='new').iter_pack_data())
CHAIN_UPDATE = b''.join(PackUpdate(
    PackFull(CHAIN_REPO, commit='new'), 'old').iter_pack_data())
CHAIN_DECODER = PackDecodeBase(CHAIN_UPDATE)
CHAIN_SERVER = MockServerFile()
CHAIN_SERVER.register_version(
    'old', CHAIN_OLD_PACK, bytes(PackDecodeBase(CHAIN_OLD_PACK).extract_index_pack()))
CHAIN_SERVER.register_version(
    'new', CHAIN_NEW_PACK, bytes(PackDecodeBase(CHAIN_NEW_PACK).extract_index_pack()))
# the working tree of the new version, the pack structure excluded
CHAIN_NEW_TREE = {
    'a.txt': b'chained\n', 'b.txt': b'chained\n',
    'c.txt': b'chained\n', 'd.txt': b'chained\n',
}


async def run_update(update=UPDATE, server=SERVER, tree=NEW_TREE):
    """
    Apply the update and assert the tree equals the new version.

    Args:
        update (bytes): Update pack data
        server (ServerFile): Server of the update
        tree (dict[str, bytes]): Expected working tree

    Returns:
        UpdateJob: The finished job
    """
    job = UpdateJob(update, server=server)
    assert await job.run()
    assert job.error == []
    assert read_tree() == tree
    assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')
    return job


async def setup_app(pack=OLD_PACK):
    """
    Unpack a full pack into the app folder, like the client that has
    been running that version.

    Args:
        pack (bytes): Full pack of the version
    """
    await UnpackJob(pack).run()


# ════════════════════════════════════════════════════════════════════════════
#  version part
# ════════════════════════════════════════════════════════════════════════════


class TestUpdatePackVersions:
    """The version part of the update pack records both versions."""

    def test_update_pack_has_both_versions(self):
        """An update pack carries the new version and the old version,
        the non-empty old version is what tells it from a full pack."""
        assert UPDATE_DECODER.current_version == 'new'
        assert UPDATE_DECODER.old_version == 'old'

    def test_full_pack_has_current_version_only(self):
        """A full pack carries the current version, its old version is
        empty."""
        assert OLD_DECODER.current_version == 'old'
        assert OLD_DECODER.old_version == ''


# ════════════════════════════════════════════════════════════════════════════
#  job file
# ════════════════════════════════════════════════════════════════════════════


class TestJobFile:
    """write()."""

    def test_write_creates_job_file(self, app_folder):
        """write() stores the data to the job file for crash recovery."""
        UpdateJob(UPDATE).write()
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/workspace/job.pack') == UPDATE


# ════════════════════════════════════════════════════════════════════════════
#  unpack phase
# ════════════════════════════════════════════════════════════════════════════


class TestUnpack:
    """unpack() phase: write tmp files, real files untouched."""

    @pytest.mark.trio
    async def test_unpack_writes_tmp_only(self, app_folder):
        """unpack() writes tmp files, real files stay untouched."""
        await setup_app()
        job = UpdateJob(UPDATE, server=SERVER)
        job.write()
        job.unpack()
        # real files are not applied yet, the index is written by
        # replace() like any other file
        assert read_tree() == OLD_TREE
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == \
            bytes(OLD_DECODER.extract_index_pack())
        # the workspace has the job file and the tmp files
        assert os.listdir(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_unpack_does_not_write_job_file(self, app_folder):
        """unpack() does not write the job file, the caller does."""
        await setup_app()
        UpdateJob(UPDATE, server=SERVER).unpack()
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace/job.pack')

    @pytest.mark.trio
    async def test_index_pack_prepared(self, app_folder):
        """unpack() decompresses the index record to a tmp file."""
        await setup_app()
        job = UpdateJob(UPDATE, server=SERVER)
        job.write()
        job.unpack()
        # the index record is always written to the fixed new_index.pack
        # in the workspace, it must be the new index pack
        tmp = env.PROJECT_ROOT / f'.pack/workspace/{UpdateJob.NEW_INDEX}'
        data = file_read_bytes(tmp)
        assert data == bytes(NEW_DECODER.extract_index_pack())
        # the tmp file is a valid index pack of the new version
        index_decoder = PackDecodeBase(data)
        index_decoder.validate_index()
        assert index_decoder.current_version == 'new'
        assert index_decoder.old_version == ''

    @pytest.mark.trio
    async def test_index_pack_written_after_run(self, app_folder):
        """After run() the local index pack is the new index pack."""
        await setup_app()
        await run_update()
        data = file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack')
        assert data == bytes(NEW_DECODER.extract_index_pack())
        # it must be a valid index pack of the new version
        decoder = PackDecodeBase(data)
        decoder.validate_index()
        assert decoder.current_version == 'new'

    @pytest.mark.trio
    async def test_pending_records(self, app_folder):
        """unpack() fills self.pending with the data records and
        pending_index with the index record."""
        await setup_app()
        job = UpdateJob(UPDATE, server=SERVER)
        job.write()
        job.unpack()
        assert job.error == []
        pending = {item.info.path: item for item in job.pending}
        assert all(isinstance(item.info, IdxInfo) for item in job.pending)
        # deleted marker record, its target is removed in replace()
        deleted = pending['backend/legacy.py']
        assert deleted.info.edit == 2
        assert deleted.tmp == ''
        # the R / RM source files are moved: their deletion is scheduled
        # as deleted markers, applied after every write by replace_data()
        assert pending['scripts/run.sh'].info.edit == 2
        assert pending['scripts/old_tool.py'].info.edit == 2
        # the index pack is the commit record, not a pending data file:
        # it is prepared to pending_index, replace_index() commits it
        assert '.pack/index.pack' not in pending
        index_pack = job.pending_index
        assert index_pack is not None
        assert index_pack.info.path == '.pack/index.pack'
        assert index_pack.info.edit == 1
        assert index_pack.tmp
        assert os.path.exists(index_pack.tmp)
        # a normal record carries the file info and the tmp file,
        # backend/a1.py is a 644 record, python writes 666 which is
        # accepted as-is, no mode change is scheduled
        added = pending['backend/a1.py']
        assert added.info.edit == 0
        assert added.tmp
        assert added.mode is None
        assert os.path.exists(added.tmp)


# ════════════════════════════════════════════════════════════════════════════
#  round-trip
# ════════════════════════════════════════════════════════════════════════════


class TestUpdateRoundtrip:
    """The update applies to the old working tree and produces the new one."""

    @pytest.mark.trio
    async def test_full_scenario(self, app_folder):
        """A realistic upgrade covering every record type at once."""
        await setup_app()
        await run_update()
        # the update pack covers every record type
        decoder = PackDecodeBase(UPDATE)
        edits = {info.edit for info in decoder.fileinfo.values()}
        assert edits == {0, 1, 2, 3}
        fileinfo = decoder.fileinfo
        # M with a zstd patch from the old file
        assert fileinfo['backend/main.py'].algo == 2
        assert fileinfo['backend/main.py'].source_lookback > 0
        # R (pure rename) and RM (renamed + modified)
        assert fileinfo['scripts/runner.sh'].edit == 3
        assert fileinfo['scripts/runner.sh'].data_size == 0
        assert fileinfo['scripts/new_tool.py'].edit == 3
        assert fileinfo['scripts/new_tool.py'].source_path == 'scripts/old_tool.py'
        # C records: from an unchanged old file (cross eol), and a copy chain
        assert fileinfo['docs/readme_copy.txt'].source_path == 'docs/readme.md'
        assert fileinfo['docs/readme_copy2.txt'].source_path == 'docs/readme_copy.txt'
        # the index pack is updated like a normal file, the old index
        # is recorded in the refinfo
        assert fileinfo['.pack/index.pack'].edit == 1
        assert fileinfo['.pack/index.pack'].source_path == '.pack/index.pack'
        assert '.pack/index.pack' in decoder.refinfo

    @pytest.mark.trio
    async def test_roundtrip_twice_is_idempotent(self, app_folder):
        """Running into a folder with valid files succeeds and skips."""
        await setup_app()
        await run_update()
        await run_update()

    @pytest.mark.trio
    async def test_unpack_replace_without_run(self, app_folder):
        """unpack() then replace() applies the changes, the caller runs."""
        await setup_app()
        job = UpdateJob(UPDATE, server=SERVER)
        job.write()
        job.unpack()
        job.replace()
        assert read_tree() == NEW_TREE
        # the workspace is kept, run() cleans it up
        assert os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_skip_existing_valid_file(self, app_folder):
        """A valid new-version file is kept as-is."""
        await setup_app()
        notes = env.PROJECT_ROOT / 'docs/notes.txt'
        with open(notes, 'wb') as f:
            f.write(b'updated note\r\n')
        added = env.PROJECT_ROOT / 'backend/a1.py'
        with open(added, 'wb') as f:
            f.write(NEW['backend/a1.py'])
        await run_update()
        assert file_read_bytes(notes) == b'updated note\r\n'
        assert file_read_bytes(added) == NEW['backend/a1.py']

    @pytest.mark.trio
    async def test_empty_file(self, app_folder):
        """An empty added file is created as an empty file."""
        await setup_app()
        await run_update()
        assert file_read_bytes(env.PROJECT_ROOT / 'backend/empty.txt') == b''

    @pytest.mark.trio
    async def test_deleted_marker_removes_file(self, app_folder):
        """D (deleted) marker files must not exist after replace()."""
        await setup_app()
        await run_update()
        assert not os.path.exists(env.PROJECT_ROOT / 'backend/legacy.py')

    @pytest.mark.trio
    async def test_renamed_source_removed(self, app_folder):
        """R / RM records move the source file, it must not exist."""
        await setup_app()
        await run_update()
        assert not os.path.exists(env.PROJECT_ROOT / 'scripts/run.sh')
        assert not os.path.exists(env.PROJECT_ROOT / 'scripts/old_tool.py')

    @pytest.mark.trio
    async def test_rename_source_removed_after_target(self, app_folder, monkeypatch):
        """The target of a rename is written before its source is
        removed: an interruption between the two keeps both files and
        the next run converges."""
        await setup_app()
        import alasio.deploy.pack.job_base as job_base
        original_remove = job_base.atomic_remove

        def _fail(path):
            if str(path).replace('\\', '/').endswith('scripts/run.sh'):
                raise PermissionError('interrupted')
            return original_remove(path)
        monkeypatch.setattr(job_base, 'atomic_remove', _fail)
        job = UpdateJob(UPDATE, server=SERVER)
        with logger.mock_capture_writer() as capture:
            assert not await job.run()
        assert capture.backend.any_contains('Failed to replace file')
        # the target was written first, the source is still in place:
        # the renamed file is never missing on both paths
        assert file_read_bytes(env.PROJECT_ROOT / 'scripts/runner.sh') == NEW['scripts/runner.sh']
        assert file_read_bytes(env.PROJECT_ROOT / 'scripts/run.sh') == OLD['scripts/run.sh']
        # the next run removes the leftover source and converges
        monkeypatch.setattr(job_base, 'atomic_remove', original_remove)
        assert await UpdateJob(UPDATE, server=SERVER).run()
        assert not os.path.exists(env.PROJECT_ROOT / 'scripts/run.sh')
        assert read_tree() == NEW_TREE

    @pytest.mark.trio
    async def test_writes_applied_before_deletions(self, app_folder, monkeypatch):
        """replace_data() applies every write before the first deletion,
        whatever the order of the pending list is: only the index commit
        closes the flow after them."""
        await setup_app()
        import alasio.deploy.pack.job_base as job_base
        original_replace = job_base.atomic_replace
        original_remove = job_base.atomic_remove
        calls = []

        def _replace(tmp, target):
            calls.append(('replace', str(target).replace('\\', '/')))
            return original_replace(tmp, target)

        def _remove(target):
            calls.append(('remove', str(target).replace('\\', '/')))
            return original_remove(target)
        monkeypatch.setattr(job_base, 'atomic_replace', _replace)
        monkeypatch.setattr(job_base, 'atomic_remove', _remove)
        assert await UpdateJob(UPDATE, server=SERVER).run()
        kinds = [kind for kind, target in calls]
        first_remove = kinds.index('remove')
        # every data write lands before the first deletion
        assert kinds[:first_remove] == ['replace'] * first_remove
        # after the deletions the only write is the index commit
        assert all(
            target.endswith('index.pack')
            for kind, target in calls[first_remove:] if kind == 'replace'
        )
        assert calls[-1][0] == 'replace'
        assert calls[-1][1].endswith('index.pack')
        assert read_tree() == NEW_TREE

    @pytest.mark.trio
    async def test_mode_change_applied(self, app_folder):
        """A mode change (755 -> 644) is applied to a file whose
        content is unchanged, without rewriting the content."""
        # tools/tool.sh is 755 in the old version, 644 in the new one
        await setup_app()
        target = env.PROJECT_ROOT / 'tools/tool.sh'
        assert os.stat(target).st_mode & 0o111
        await run_update()
        assert not os.stat(target).st_mode & 0o111
        assert file_read_bytes(target) == NEW['tools/tool.sh'][0]


# ════════════════════════════════════════════════════════════════════════════
#  index pack update
# ════════════════════════════════════════════════════════════════════════════


class TestCopyChain:
    """Copied references: a source may be an earlier new record, the
    chain a.txt -> b.txt -> c.txt -> d.txt is decoded in record order."""

    @pytest.mark.trio
    async def test_chain_encoded_and_decoded(self, app_folder):
        """Every new file references the previous record, the decoder
        resolves the chain in record order."""
        await UnpackJob(CHAIN_OLD_PACK).run()
        # the encoder links b -> a, c -> b, d -> c
        fileinfo = CHAIN_DECODER.fileinfo
        assert fileinfo['b.txt'].source_path == 'a.txt'
        assert fileinfo['c.txt'].source_path == 'b.txt'
        assert fileinfo['d.txt'].source_path == 'c.txt'
        # the decoder reads the source from the tmp file of the earlier
        # record, the whole chain lands
        await run_update(CHAIN_UPDATE, server=CHAIN_SERVER, tree=CHAIN_NEW_TREE)

    @pytest.mark.trio
    async def test_chain_with_matched_source(self, app_folder):
        """A source record that needs no write leaves no tmp file behind:
        the copies that reference it are downloaded from the full pack."""
        await UnpackJob(CHAIN_OLD_PACK).run()
        # the local b.txt already is the new content: the b.txt record
        # is skipped and writes no tmp file
        with open(env.PROJECT_ROOT / 'b.txt', 'wb') as f:
            f.write(b'chained\n')
        job = UpdateJob(CHAIN_UPDATE, server=CHAIN_SERVER)
        job.write()
        job.unpack()
        # the copies of b.txt find no tmp file and fail the unpack
        assert [item.info.path for item in job.error] == ['c.txt', 'd.txt']
        # download() fetches their content from the full pack
        await job.download()
        assert job.error == []
        job.replace()
        assert read_tree() == CHAIN_NEW_TREE


class TestIndexUpdate:
    """The index pack is updated like a normal file of the update,
    the local index is verified against the refinfo."""

    @pytest.mark.trio
    async def test_missing_index_downloaded(self, app_folder):
        """A missing local index pack is downloaded from the server."""
        await setup_app(_simple_old_pack)
        os.remove(env.PROJECT_ROOT / '.pack/index.pack')
        job = UpdateJob(SIMPLE_UPDATE, server=SIMPLE_SERVER)
        assert await job.run()
        assert job.error == []
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == SIMPLE_NEW_INDEX
        assert read_tree() == {'keep.txt': b'keep\n', 'add.txt': b'hello\n'}

    @pytest.mark.trio
    async def test_corrupt_index_downloaded(self, app_folder):
        """A corrupt local index pack is downloaded from the server."""
        await setup_app(_simple_old_pack)
        bad = bytearray(file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack'))
        bad[-5] ^= 0xFF
        with open(env.PROJECT_ROOT / '.pack/index.pack', 'wb') as f:
            f.write(bad)
        job = UpdateJob(SIMPLE_UPDATE, server=SIMPLE_SERVER)
        assert await job.run()
        assert job.error == []
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == SIMPLE_NEW_INDEX
        assert read_tree() == {'keep.txt': b'keep\n', 'add.txt': b'hello\n'}

    @pytest.mark.trio
    async def test_foreign_index_downloaded(self, app_folder):
        """A self-consistent but wrong local index is downloaded: it
        fails the refinfo size + sha1 check of the update pack."""
        await setup_app(_simple_old_pack)
        # the local index is a valid index pack of another version,
        # its own checksum passes but the refinfo check does not
        with open(env.PROJECT_ROOT / '.pack/index.pack', 'wb') as f:
            f.write(OTHER_INDEX)
        job = UpdateJob(SIMPLE_UPDATE, server=SIMPLE_SERVER)
        assert await job.run()
        assert job.error == []
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == SIMPLE_NEW_INDEX
        assert read_tree() == {'keep.txt': b'keep\n', 'add.txt': b'hello\n'}

    @pytest.mark.trio
    async def test_corrupt_index_still_updates(self, app_folder):
        """A corrupt local index does not stop the update, the index
        is downloaded again."""
        await setup_app()
        bad = bytearray(file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack'))
        bad[-5] ^= 0xFF
        with open(env.PROJECT_ROOT / '.pack/index.pack', 'wb') as f:
            f.write(bad)
        job = UpdateJob(UPDATE, server=SERVER)
        assert await job.run()
        assert job.error == []
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == \
            bytes(NEW_DECODER.extract_index_pack())
        assert read_tree() == NEW_TREE

    @pytest.mark.trio
    async def test_missing_index_no_server(self, app_folder):
        """A missing index pack and no server leaves the record in
        error."""
        await setup_app()
        os.remove(env.PROJECT_ROOT / '.pack/index.pack')
        job = UpdateJob(UPDATE)
        with logger.mock_capture_writer() as capture:
            assert not await job.run()
        assert capture.backend.any_contains('no server provided')
        assert [item.info.path for item in job.error] == ['.pack/index.pack']
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')


# ════════════════════════════════════════════════════════════════════════════
#  source repair
# ════════════════════════════════════════════════════════════════════════════


class TestSourceDownload:
    """A source that fails the size + sha1 check: the content of the
    record is downloaded from the new full pack instead."""

    @pytest.mark.trio
    async def test_missing_copied_source_downloaded(self, app_folder):
        """A missing old file of a C record: the copy is downloaded,
        the missing source is repaired by the remaining check."""
        await setup_app()
        os.remove(env.PROJECT_ROOT / 'docs/readme.md')
        job = UpdateJob(UPDATE, server=SERVER)
        assert await job.run()
        assert job.error == []
        # the copies are downloaded from the new full pack
        assert file_read_bytes(env.PROJECT_ROOT / 'docs/readme_copy.txt') == b'# Website\r\n'
        assert file_read_bytes(env.PROJECT_ROOT / 'docs/readme_copy2.txt') == b'# Website\r\n'
        # the missing unchanged source is repaired by the remaining
        # check against the new index
        assert file_read_bytes(env.PROJECT_ROOT / 'docs/readme.md') == b'# Website\n'
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_damaged_patch_source_downloaded(self, app_folder):
        """A wrong old file of an M record: the record is downloaded,
        its target path is the source path, the tree is complete."""
        await setup_app()
        with open(env.PROJECT_ROOT / 'backend/main.py', 'wb') as f:
            f.write(b'corrupt content')
        await run_update()

    @pytest.mark.trio
    async def test_missing_rename_source_downloaded(self, app_folder):
        """A missing old file of an R record: the moved file is
        downloaded, the tree is complete."""
        await setup_app()
        os.remove(env.PROJECT_ROOT / 'scripts/run.sh')
        await run_update()

    @pytest.mark.trio
    async def test_missing_rm_source_downloaded(self, app_folder):
        """A missing old file of an RM record: the moved file is
        downloaded, the tree is complete."""
        await setup_app()
        os.remove(env.PROJECT_ROOT / 'scripts/old_tool.py')
        await run_update()

    @pytest.mark.trio
    async def test_eol_mismatch_source_fixed_without_download(self, app_folder, monkeypatch):
        """A source whose EOL differs is converted, no download happens."""
        await setup_app()
        with open(env.PROJECT_ROOT / 'docs/readme.md', 'wb') as f:
            f.write(b'# Website\r\n')

        async def _fail(*args, **kwargs):
            raise AssertionError('no download expected for an EOL mismatch')
        monkeypatch.setattr(SERVER, 'get_file_content', _fail)
        job = UpdateJob(UPDATE, server=SERVER)
        assert await job.run()
        assert job.error == []
        # the copy records are computed from the converted source blob,
        # the copies keep their own eol (crlf)
        assert file_read_bytes(env.PROJECT_ROOT / 'docs/readme_copy.txt') == b'# Website\r\n'
        assert file_read_bytes(env.PROJECT_ROOT / 'docs/readme_copy2.txt') == b'# Website\r\n'

    @pytest.mark.trio
    async def test_unsolvable_stays_in_error(self, app_folder, monkeypatch):
        """A record that cannot be downloaded stays in error."""
        await setup_app()
        os.remove(env.PROJECT_ROOT / 'docs/readme.md')
        monkeypatch.setattr(SERVER, 'get_file_content', bad_content())
        job = UpdateJob(UPDATE, server=SERVER)
        with logger.mock_capture_writer() as capture:
            assert not await job.run()
        assert capture.backend.any_contains('Failed to download docs/readme_copy.txt:')
        # the failed copies and the missing unchanged source (failed
        # in the remaining check) stay in error
        assert [item.info.path for item in job.error] == \
            ['docs/readme_copy.txt', 'docs/readme_copy2.txt', 'docs/readme.md']
        # the other changes are still applied, the workspace is cleaned
        assert file_read_bytes(env.PROJECT_ROOT / 'backend/a1.py') == NEW['backend/a1.py']
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')
        # the new index pack is not committed: the local version does
        # not advance, the next check still sees the update available
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == \
            bytes(OLD_DECODER.extract_index_pack())

    @pytest.mark.trio
    async def test_no_server_sources_unsolvable(self, app_folder):
        """A missing server leaves the failed records in error."""
        await setup_app()
        os.remove(env.PROJECT_ROOT / 'docs/readme.md')
        job = UpdateJob(UPDATE)
        with logger.mock_capture_writer() as capture:
            assert not await job.run()
        assert capture.backend.any_contains('no server provided')
        assert [item.info.path for item in job.error] == \
            ['docs/readme_copy.txt', 'docs/readme_copy2.txt']


class TestValidateRemaining:
    """UpdateJob verifies the local files not covered by the update pack."""

    @pytest.mark.trio
    async def test_damaged_unchanged_file_repaired(self, app_folder):
        """A damaged unchanged file is repaired with the new index."""
        await setup_app()
        # data/blob.png is unchanged between the versions, it is not a
        # record of the update pack
        target = env.PROJECT_ROOT / 'data/blob.png'
        with open(target, 'wb') as f:
            f.write(b'corrupt content')
        await run_update()
        assert file_read_bytes(target) == NEW['data/blob.png']

    @pytest.mark.trio
    async def test_remaining_download_failed_stays_in_error(self, app_folder, monkeypatch):
        """A remaining file that cannot be downloaded stays in error."""
        await setup_app()
        target = env.PROJECT_ROOT / 'data/blob.png'
        with open(target, 'wb') as f:
            f.write(b'corrupt content')
        monkeypatch.setattr(SERVER, 'get_file_content', bad_content())
        job = UpdateJob(UPDATE, server=SERVER)
        with logger.mock_capture_writer() as capture:
            assert not await job.run()
        assert capture.backend.any_contains('Failed to download data/blob.png:')
        assert [item.info.path for item in job.error] == ['data/blob.png']

    @pytest.mark.trio
    async def test_index_failed_skips_remaining(self, app_folder, monkeypatch):
        """The remaining check is skipped when the index record failed."""
        await setup_app()
        bad = bytearray(file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack'))
        bad[-5] ^= 0xFF
        with open(env.PROJECT_ROOT / '.pack/index.pack', 'wb') as f:
            f.write(bad)
        # the damaged unchanged file stays damaged: the remaining check
        # is skipped, the local index is not the new one
        target = env.PROJECT_ROOT / 'data/blob.png'
        with open(target, 'wb') as f:
            f.write(b'corrupt content')
        monkeypatch.setattr(SERVER, 'get_index_pack', bad_content())
        job = UpdateJob(UPDATE, server=SERVER)
        with logger.mock_capture_writer() as capture:
            assert not await job.run()
        assert capture.backend.any_contains('the index pack record failed')
        assert [item.info.path for item in job.error] == ['.pack/index.pack']
        assert file_read_bytes(target) == b'corrupt content'

    @pytest.mark.trio
    async def test_no_server_skips_remaining(self, app_folder):
        """The remaining check is skipped without a server."""
        await setup_app()
        target = env.PROJECT_ROOT / 'data/blob.png'
        with open(target, 'wb') as f:
            f.write(b'corrupt content')
        job = UpdateJob(UPDATE)
        assert await job.run()
        assert job.error == []
        # the damaged remaining file is left as-is, no server to repair it
        assert file_read_bytes(target) == b'corrupt content'

    @pytest.mark.trio
    async def test_new_fileinfo_keeps_full_records(self, app_folder):
        """_validate_remaining() replaces the fileinfo cache of the new
        index decoder with the filtered view of the remaining check, the
        bound self.new_fileinfo keeps the full records of the new
        version."""
        await setup_app()
        job = UpdateJob(UPDATE, server=SERVER)
        assert await job.run()
        assert job.error == []
        # a file the update pack itself records is still part of the
        # bound dict, the filtered view of the remaining check drops it
        assert 'backend/a1.py' in job.new_fileinfo
        # an unchanged file: no record of the update pack, only the new
        # index records it
        assert 'docs/readme.md' in job.new_fileinfo
        # a path deleted by the update is no record of the new version
        assert 'backend/legacy.py' not in job.new_fileinfo


# ════════════════════════════════════════════════════════════════════════════
#  download phase
# ════════════════════════════════════════════════════════════════════════════


class TestDownload:
    """download(): fetch the content of the failed records from the
    server and write their tmp files."""

    @pytest.mark.trio
    async def test_download_failed_records(self, app_folder):
        """The failed records are downloaded to tmp files."""
        await setup_app()
        os.remove(env.PROJECT_ROOT / 'docs/readme.md')
        job = UpdateJob(UPDATE, server=SERVER)
        job.write()
        job.unpack()
        # the copied records cannot be computed without the source
        assert [item.info.path for item in job.error] == \
            ['docs/readme_copy.txt', 'docs/readme_copy2.txt']
        await job.download()
        assert job.error == []
        # the records are downloaded from the new full pack, the source
        # is not in pending
        paths = {item.info.path for item in job.pending}
        assert 'docs/readme.md' not in paths
        copy = next(item for item in job.pending if item.info.path == 'docs/readme_copy.txt')
        assert copy.tmp
        assert os.path.exists(copy.tmp)
        assert file_read_bytes(copy.tmp) == b'# Website\r\n'
        copy2 = next(item for item in job.pending if item.info.path == 'docs/readme_copy2.txt')
        assert file_read_bytes(copy2.tmp) == b'# Website\r\n'

    @pytest.mark.trio
    async def test_download_reuse_tmp(self, app_folder, monkeypatch):
        """A leftover tmp file that passes the check is reused."""
        await setup_app()
        os.remove(env.PROJECT_ROOT / 'docs/readme.md')
        # write a valid tmp file at the record tmp name, download()
        # should reuse it
        decoder = PackDecodeBase(UPDATE)
        index = list(decoder.fileinfo).index('docs/readme_copy.txt')
        info = decoder.fileinfo['docs/readme_copy.txt']
        tmp = env.PROJECT_ROOT / f'.pack/workspace/{info.size}_{info.sha1.hex()}_{index}.tmp'
        os.makedirs(tmp.uppath(), exist_ok=True)
        with open(tmp, 'wb') as f:
            f.write(b'# Website\r\n')

        # the copy record is served from the leftover tmp without a
        # download; only the missing readme.md is downloaded by the
        # remaining check (its data range is the same as the copy's,
        # the copy restores the data range of its source)
        original = SERVER.get_file_content
        calls = []

        async def _serve(version, offset, size):
            calls.append(offset)
            return await original(version, offset, size)
        monkeypatch.setattr(SERVER, 'get_file_content', _serve)
        job = UpdateJob(UPDATE, server=SERVER)
        assert await job.run()
        assert job.error == []
        assert file_read_bytes(env.PROJECT_ROOT / 'docs/readme_copy.txt') == b'# Website\r\n'
        # the missing unchanged source is repaired by the remaining check
        assert file_read_bytes(env.PROJECT_ROOT / 'docs/readme.md') == b'# Website\n'
        assert len(calls) == 1
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_download_no_error_is_noop(self, app_folder, monkeypatch):
        """A healthy tree needs no download."""
        await setup_app()

        async def _fail(*args, **kwargs):
            raise AssertionError('no download expected for a healthy tree')
        monkeypatch.setattr(SERVER, 'get_file_content', _fail)
        job = UpdateJob(UPDATE, server=SERVER)
        assert await job.run()
        assert job.error == []
        assert read_tree() == NEW_TREE

    @pytest.mark.trio
    async def test_index_download_failed_stays_in_error(self, app_folder, monkeypatch):
        """A broken index pack that cannot be downloaded stays in error."""
        await setup_app()
        # corrupt the local index so the index record fails the
        # refinfo check in unpack()
        bad = bytearray(file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack'))
        bad[-5] ^= 0xFF
        with open(env.PROJECT_ROOT / '.pack/index.pack', 'wb') as f:
            f.write(bad)
        # the server index pack is broken too, the record is unsolvable
        monkeypatch.setattr(SERVER, 'get_index_pack', bad_content())
        job = UpdateJob(UPDATE, server=SERVER)
        with logger.mock_capture_writer() as capture:
            assert not await job.run()
        assert capture.backend.any_contains('Failed to download .pack/index.pack:')
        assert [item.info.path for item in job.error] == ['.pack/index.pack']
        # the local index is not replaced, the workspace is cleaned
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == bytes(bad)
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_index_download_http_error_stays_in_error(self, app_folder, monkeypatch):
        """A network error while downloading the index pack keeps the
        record in error."""
        await setup_app()
        # corrupt the local index so the index record fails the
        # refinfo check in unpack()
        bad = bytearray(file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack'))
        bad[-5] ^= 0xFF
        with open(env.PROJECT_ROOT / '.pack/index.pack', 'wb') as f:
            f.write(bad)

        async def _raise(version):
            request = httpx2.Request('GET', 'http://mock/new/full.pack')
            response = httpx2.Response(500, request=request)
            raise httpx2.HTTPStatusError(
                '500 Internal Server Error', request=request, response=response)
        monkeypatch.setattr(SERVER, 'get_index_pack', _raise)
        job = UpdateJob(UPDATE, server=SERVER)
        with logger.mock_capture_writer() as capture:
            assert not await job.run()
        assert capture.backend.any_contains('Failed to download .pack/index.pack:')
        assert [item.info.path for item in job.error] == ['.pack/index.pack']

    @pytest.mark.trio
    async def test_missing_index_and_download_failed(self, app_folder, monkeypatch):
        """The index pack cannot be downloaded and the local index is
        missing: every failed record stays in error."""
        await setup_app()
        # the local index is missing and the copied records cannot be
        # computed without their source
        os.remove(env.PROJECT_ROOT / '.pack/index.pack')
        os.remove(env.PROJECT_ROOT / 'docs/readme.md')
        monkeypatch.setattr(SERVER, 'get_index_pack', bad_content())
        job = UpdateJob(UPDATE, server=SERVER)
        with logger.mock_capture_writer() as capture:
            assert not await job.run()
        assert capture.backend.any_contains('Failed to download .pack/index.pack:')
        # the offsets are unavailable: the index record and every
        # record that could not be computed locally stay in error
        assert [item.info.path for item in job.error] == [
            '.pack/index.pack', 'docs/readme_copy.txt', 'docs/readme_copy2.txt']
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')


# ════════════════════════════════════════════════════════════════════════════
#  caller flow
# ════════════════════════════════════════════════════════════════════════════


class TestCallerFlow:
    """The exact caller usage of UpdateJob."""

    @pytest.mark.trio
    async def test_get_unfinished_job_update(self, app_folder):
        """An update pack job file is dispatched to a resumed UpdateJob."""
        await setup_app()
        UpdateJob(UPDATE).write()
        job = DeployJob(server=SERVER)._get_unfinished_job()
        assert job is not None
        assert isinstance(job, UpdateJob)
        assert await job.run()
        assert read_tree() == NEW_TREE
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_interrupted_unpack_resumed(self, app_folder):
        """A run interrupted after unpack() is resumed: the local
        index is not touched yet (replace() writes it), the tmp files
        are reused."""
        await setup_app()
        job = UpdateJob(UPDATE, server=SERVER)
        job.write()
        job.unpack()
        # the local index is still the old one, the resumed run
        # verifies it against the refinfo and reuses the tmp files
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == \
            bytes(OLD_DECODER.extract_index_pack())
        job = DeployJob(server=SERVER)._get_unfinished_job()
        assert job is not None
        assert isinstance(job, UpdateJob)
        assert await job.run()
        assert read_tree() == NEW_TREE
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == \
            bytes(NEW_DECODER.extract_index_pack())
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_resume_skips_write(self, app_folder, monkeypatch):
        """A resumed job skips write(), the data is already in the file."""
        await setup_app()
        UpdateJob(UPDATE).write()

        def _fail(self):
            raise AssertionError('write() should not be called on resume')
        monkeypatch.setattr(UpdateJob, 'write', _fail)
        job = DeployJob(server=SERVER)._get_unfinished_job()
        assert job is not None
        assert await job.run()
        assert read_tree() == NEW_TREE

    def test_full_pack_dispatched_to_unpack_job(self, app_folder):
        """A full pack job file is dispatched to UnpackJob, not UpdateJob."""
        UnpackJob(OLD_PACK).write()
        job = DeployJob(server=SERVER)._get_unfinished_job()
        assert job is not None
        assert isinstance(job, UnpackJob)


# ════════════════════════════════════════════════════════════════════════════
#  failure
# ════════════════════════════════════════════════════════════════════════════


class TestFailure:
    """Failure handling: a caught failure cleans the workspace up, only
    a process killed in flight leaves it for the next run to resume."""

    def test_invalid_pack_raises(self, app_folder):
        """Not a pack file raises PackDecodeError."""
        with pytest.raises(PackDecodeError):
            UpdateJob(b'not a pack file').unpack()

    def test_full_pack_rejected(self, app_folder):
        """A full pack without an old version is rejected."""
        with pytest.raises(ValueError, match='update pack'):
            UpdateJob(OLD_PACK).unpack()

    def test_corrupt_update_pack_raises(self, app_folder):
        """An update pack with a corrupted data section fails validation."""
        decoder = PackDecodeBase(UPDATE)
        index_end = 5 + len(decoder.index_section)
        bad = bytearray(UPDATE)
        bad[index_end + 100] ^= 0xFF
        with pytest.raises(PackDecodeError):
            UpdateJob(bytes(bad)).unpack()

    def test_failure_keeps_job_file(self, app_folder):
        """job.pack survives a failed run for crash recovery."""
        decoder = PackDecodeBase(UPDATE)
        index_end = 5 + len(decoder.index_section)
        bad = bytearray(UPDATE)
        bad[index_end + 100] ^= 0xFF
        job = UpdateJob(bytes(bad))
        job.write()
        with pytest.raises(PackDecodeError):
            job.unpack()
        assert os.path.exists(env.PROJECT_ROOT / '.pack/workspace/job.pack')
        # the unfinished job can still be found
        assert DeployJob()._get_unfinished_job() is not None

    @pytest.mark.trio
    async def test_run_failure_logged_and_cleaned(self, app_folder):
        """A failed run logs a warning and cleans the workspace."""
        await setup_app()
        decoder = PackDecodeBase(UPDATE)
        index_end = 5 + len(decoder.index_section)
        bad = bytearray(UPDATE)
        bad[index_end + 100] ^= 0xFF
        job = UpdateJob(bytes(bad), server=SERVER)
        with logger.mock_capture_writer() as capture:
            assert not await job.run()
        assert capture.backend.any_contains('Failed to update:')
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')


# ════════════════════════════════════════════════════════════════════════════
#  empty folder cleanup
# ════════════════════════════════════════════════════════════════════════════


class TestEmptyFolderCleanup:
    """The folders left empty by the deleted files are removed."""

    @pytest.mark.trio
    async def test_deleted_folder_removed(self, app_folder):
        """The update deletes the last files of a folder, the folder is
        removed."""
        await setup_app(FOLDER_OLD_PACK)
        assert os.path.isdir(env.PROJECT_ROOT / 'pkg')
        job = UpdateJob(FOLDER_UPDATE, server=FOLDER_SERVER)
        assert await job.run()
        assert job.error == []
        assert not os.path.exists(env.PROJECT_ROOT / 'pkg')
        assert read_tree() == FOLDER_NEW_TREE
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_empty_folder_chain_removed(self, app_folder):
        """A folder chain that becomes empty is removed bottom-up."""
        await setup_app(FOLDER_OLD_PACK)
        assert os.path.isdir(env.PROJECT_ROOT / 'a/b/c')
        job = UpdateJob(FOLDER_UPDATE, server=FOLDER_SERVER)
        assert await job.run()
        assert not os.path.exists(env.PROJECT_ROOT / 'a')

    @pytest.mark.trio
    async def test_unchanged_file_keeps_folder(self, app_folder):
        """A folder that still holds an unchanged file of the new version
        is kept."""
        await setup_app(FOLDER_OLD_PACK)
        job = UpdateJob(FOLDER_UPDATE, server=FOLDER_SERVER)
        assert await job.run()
        assert not os.path.exists(env.PROJECT_ROOT / 'keep/gone.txt')
        assert file_read_bytes(env.PROJECT_ROOT / 'keep/keep.txt') == b'keep\n'

    @pytest.mark.trio
    async def test_user_file_keeps_folder(self, app_folder):
        """A folder that still holds a file the update does not manage is
        kept."""
        await setup_app(FOLDER_OLD_PACK)
        user = env.PROJECT_ROOT / 'pkg/notes.txt'
        with open(user, 'wb') as f:
            f.write(b'my notes')
        job = UpdateJob(FOLDER_UPDATE, server=FOLDER_SERVER)
        assert await job.run()
        assert not os.path.exists(env.PROJECT_ROOT / 'pkg/tool.py')
        assert file_read_bytes(user) == b'my notes'

    @pytest.mark.trio
    async def test_no_server_still_removes_folder(self, app_folder):
        """No server: the new index is unavailable, the deleted markers
        do not make the folder non-empty and os.rmdir() confirms it, the
        folder is removed too. keep/ is kept by the unchanged file."""
        await setup_app(FOLDER_OLD_PACK)
        job = UpdateJob(FOLDER_UPDATE)
        assert await job.run()
        assert job.error == []
        assert not os.path.exists(env.PROJECT_ROOT / 'pkg')
        assert not os.path.exists(env.PROJECT_ROOT / 'a')
        assert file_read_bytes(env.PROJECT_ROOT / 'keep/keep.txt') == b'keep\n'

    @pytest.mark.trio
    async def test_unknown_new_fileinfo_keeps_folder(self, app_folder):
        """Without the records of the new version (new_fileinfo empty)
        the emptiness is decided by os.rmdir() only: a folder holding a
        file is kept, an empty one is removed."""
        await setup_app(FOLDER_OLD_PACK)
        job = UpdateJob(FOLDER_UPDATE)
        job.write()
        job.unpack()
        job.new_fileinfo = {}
        job.replace()
        assert not os.path.exists(env.PROJECT_ROOT / 'pkg')
        assert not os.path.exists(env.PROJECT_ROOT / 'a')
        assert file_read_bytes(env.PROJECT_ROOT / 'keep/keep.txt') == b'keep\n'
