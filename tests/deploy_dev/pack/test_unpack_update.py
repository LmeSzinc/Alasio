"""
Tests for UpdateJob: update pack unpack, interruptible and resumable,
with source repair from the server like ResetJob.

The update pack is built with PackUpdate from the shared
FULL_SCENARIO_OLD / FULL_SCENARIO_NEW of conftest (the same versions
as TestPackDiffFullScenario on the diff side), the old pack is
unpacked into the fake filesystem with UnpackJob, then the update is
applied with UpdateJob and the result is compared to the new version
(round-trip). The server is an in-memory MockServerFile serving the
old and new packs.

The packs are module level singletons, built before the fake
filesystem is active: MockGitRepo reads the real .gitattributes file,
which the fake filesystem does not provide.
"""
import os

import pytest
from conftest import FULL_SCENARIO_NEW, FULL_SCENARIO_OLD, MockServerFile

from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.deploy.pack.job import DeployJob
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.deploy.pack.job_update import UpdateJob
from alasio.deploy.pack.pack_model import IdxInfo
from alasio.deploy_dev.pack.pack_repo import PackFull
from alasio.deploy_dev.pack.pack_update import PackUpdate
from alasio.ext import env
from alasio.ext.path.atomic import file_read_bytes
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401

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
        if info.edit != 2
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


# ════════════════════════════════════════════════════════════════════════════
#  module level singletons, built before the fake filesystem is active
# ════════════════════════════════════════════════════════════════════════════

OLD_PACK = make_pack(OLD, commit='old')
NEW_PACK = make_pack(NEW, commit='new')
OLD_DECODER = PackDecodeBase(OLD_PACK)
NEW_DECODER = PackDecodeBase(NEW_PACK)
UPDATE = b''.join(PackUpdate(OLD_DECODER, NEW_DECODER).iter_pack_data())
SERVER = MockServerFile()
SERVER.register_version('old', OLD_PACK, bytes(OLD_DECODER.extract_index_pack()))
SERVER.register_version('new', NEW_PACK, bytes(NEW_DECODER.extract_index_pack()))
OLD_TREE = unpack_tree(OLD_DECODER)
NEW_TREE = unpack_tree(NEW_DECODER)

# an update without any source-dependent record, so a missing or
# corrupt local index does not fail the records
_simple_old_pack = make_pack({'keep.txt': b'keep\n'}, commit='old')
_simple_new_pack = make_pack({'keep.txt': b'keep\n', 'add.txt': b'hello\n'}, commit='new')
SIMPLE_UPDATE = b''.join(PackUpdate(
    PackDecodeBase(_simple_old_pack), PackDecodeBase(_simple_new_pack)).iter_pack_data())
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


def run_update(update=UPDATE, server=SERVER, tree=NEW_TREE):
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
    assert job.run()
    assert job.error == []
    assert read_tree() == tree
    assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')
    return job


def setup_app(pack=OLD_PACK):
    """
    Unpack a full pack into the app folder, like the client that has
    been running that version.

    Args:
        pack (bytes): Full pack of the version
    """
    UnpackJob(pack).run()


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

    def test_unpack_writes_tmp_only(self, app_folder):
        """unpack() writes tmp files, real files stay untouched."""
        setup_app()
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

    def test_unpack_does_not_write_job_file(self, app_folder):
        """unpack() does not write the job file, the caller does."""
        setup_app()
        UpdateJob(UPDATE, server=SERVER).unpack()
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace/job.pack')

    def test_index_pack_prepared(self, app_folder):
        """unpack() decompresses the index record to a tmp file."""
        setup_app()
        job = UpdateJob(UPDATE, server=SERVER)
        job.write()
        job.unpack()
        # find the tmp file of the index record, it must be the new
        # index pack
        decoder = PackDecodeBase(UPDATE)
        index = list(decoder.fileinfo).index('.pack/index.pack')
        info = decoder.fileinfo['.pack/index.pack']
        tmp = env.PROJECT_ROOT / f'.pack/workspace/{info.size}_{info.sha1}_{index}.tmp'
        data = file_read_bytes(tmp)
        assert data == bytes(NEW_DECODER.extract_index_pack())
        # the tmp file is a valid index pack of the new version
        index_decoder = PackDecodeBase(data)
        index_decoder.validate_index()
        assert index_decoder.version == 'new'

    def test_index_pack_written_after_run(self, app_folder):
        """After run() the local index pack is the new index pack."""
        setup_app()
        run_update()
        data = file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack')
        assert data == bytes(NEW_DECODER.extract_index_pack())
        # it must be a valid index pack of the new version
        decoder = PackDecodeBase(data)
        decoder.validate_index()
        assert decoder.version == 'new'

    def test_pending_records(self, app_folder):
        """unpack() fills self.pending with PendingFile records."""
        setup_app()
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
        # the R / RM source files are moved, their deletion is scheduled
        assert pending['scripts/run.sh'].info.edit == 2
        assert pending['scripts/old_tool.py'].info.edit == 2
        # the index pack is updated like a normal file
        index_pack = pending['.pack/index.pack']
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

    def test_full_scenario(self, app_folder):
        """A realistic upgrade covering every record type at once."""
        setup_app()
        run_update()
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

    def test_roundtrip_twice_is_idempotent(self, app_folder):
        """Running into a folder with valid files succeeds and skips."""
        setup_app()
        run_update()
        run_update()

    def test_unpack_replace_without_run(self, app_folder):
        """unpack() then replace() applies the changes, the caller runs."""
        setup_app()
        job = UpdateJob(UPDATE, server=SERVER)
        job.write()
        job.unpack()
        job.replace()
        assert read_tree() == NEW_TREE
        # the workspace is kept, run() cleans it up
        assert os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_skip_existing_valid_file(self, app_folder):
        """A valid new-version file is kept as-is."""
        setup_app()
        notes = env.PROJECT_ROOT / 'docs/notes.txt'
        with open(notes, 'wb') as f:
            f.write(b'updated note\r\n')
        added = env.PROJECT_ROOT / 'backend/a1.py'
        with open(added, 'wb') as f:
            f.write(NEW['backend/a1.py'])
        run_update()
        assert file_read_bytes(notes) == b'updated note\r\n'
        assert file_read_bytes(added) == NEW['backend/a1.py']

    def test_empty_file(self, app_folder):
        """An empty added file is created as an empty file."""
        setup_app()
        run_update()
        assert file_read_bytes(env.PROJECT_ROOT / 'backend/empty.txt') == b''

    def test_deleted_marker_removes_file(self, app_folder):
        """D (deleted) marker files must not exist after replace()."""
        setup_app()
        run_update()
        assert not os.path.exists(env.PROJECT_ROOT / 'backend/legacy.py')

    def test_renamed_source_removed(self, app_folder):
        """R / RM records move the source file, it must not exist."""
        setup_app()
        run_update()
        assert not os.path.exists(env.PROJECT_ROOT / 'scripts/run.sh')
        assert not os.path.exists(env.PROJECT_ROOT / 'scripts/old_tool.py')

    def test_mode_change_applied(self, app_folder):
        """A mode change (755 -> 644) is applied to a file whose
        content is unchanged, without rewriting the content."""
        # tools/tool.sh is 755 in the old version, 644 in the new one
        setup_app()
        target = env.PROJECT_ROOT / 'tools/tool.sh'
        assert os.stat(target).st_mode & 0o111
        run_update()
        assert not os.stat(target).st_mode & 0o111
        assert file_read_bytes(target) == NEW['tools/tool.sh'][0]


# ════════════════════════════════════════════════════════════════════════════
#  index pack update
# ════════════════════════════════════════════════════════════════════════════


class TestIndexUpdate:
    """The index pack is updated like a normal file of the update,
    the local index is verified against the refinfo."""

    def test_missing_index_downloaded(self, app_folder):
        """A missing local index pack is downloaded from the server."""
        setup_app(_simple_old_pack)
        os.remove(env.PROJECT_ROOT / '.pack/index.pack')
        job = UpdateJob(SIMPLE_UPDATE, server=SIMPLE_SERVER)
        assert job.run()
        assert job.error == []
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == SIMPLE_NEW_INDEX
        assert read_tree() == {'keep.txt': b'keep\n', 'add.txt': b'hello\n'}

    def test_corrupt_index_downloaded(self, app_folder):
        """A corrupt local index pack is downloaded from the server."""
        setup_app(_simple_old_pack)
        bad = bytearray(file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack'))
        bad[-5] ^= 0xFF
        with open(env.PROJECT_ROOT / '.pack/index.pack', 'wb') as f:
            f.write(bad)
        job = UpdateJob(SIMPLE_UPDATE, server=SIMPLE_SERVER)
        assert job.run()
        assert job.error == []
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == SIMPLE_NEW_INDEX
        assert read_tree() == {'keep.txt': b'keep\n', 'add.txt': b'hello\n'}

    def test_foreign_index_downloaded(self, app_folder):
        """A self-consistent but wrong local index is downloaded: it
        fails the refinfo size + sha1 check of the update pack."""
        setup_app(_simple_old_pack)
        # the local index is a valid index pack of another version,
        # its own checksum passes but the refinfo check does not
        with open(env.PROJECT_ROOT / '.pack/index.pack', 'wb') as f:
            f.write(OTHER_INDEX)
        job = UpdateJob(SIMPLE_UPDATE, server=SIMPLE_SERVER)
        assert job.run()
        assert job.error == []
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == SIMPLE_NEW_INDEX
        assert read_tree() == {'keep.txt': b'keep\n', 'add.txt': b'hello\n'}

    def test_corrupt_index_still_updates(self, app_folder):
        """A corrupt local index does not stop the update, the index
        is downloaded again."""
        setup_app()
        bad = bytearray(file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack'))
        bad[-5] ^= 0xFF
        with open(env.PROJECT_ROOT / '.pack/index.pack', 'wb') as f:
            f.write(bad)
        job = UpdateJob(UPDATE, server=SERVER)
        assert job.run()
        assert job.error == []
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == \
            bytes(NEW_DECODER.extract_index_pack())
        assert read_tree() == NEW_TREE

    def test_missing_index_no_server(self, app_folder):
        """A missing index pack and no server leaves the record in
        error."""
        setup_app()
        os.remove(env.PROJECT_ROOT / '.pack/index.pack')
        job = UpdateJob(UPDATE)
        with logger.mock_capture_writer() as capture:
            assert not job.run()
        assert capture.backend.any_contains('no server provided')
        assert [item.info.path for item in job.error] == ['.pack/index.pack']
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')


# ════════════════════════════════════════════════════════════════════════════
#  source repair
# ════════════════════════════════════════════════════════════════════════════


class TestSourceDownload:
    """A source that fails the size + sha1 check: the content of the
    record is downloaded from the new full pack instead."""

    def test_missing_copied_source_downloaded(self, app_folder):
        """A missing old file of a C record: the copy is downloaded,
        the source file is left as-is."""
        setup_app()
        os.remove(env.PROJECT_ROOT / 'docs/readme.md')
        job = UpdateJob(UPDATE, server=SERVER)
        assert job.run()
        assert job.error == []
        # the copies are downloaded from the new full pack, the source
        # is not repaired (it is checked by ResetJob)
        assert not os.path.exists(env.PROJECT_ROOT / 'docs/readme.md')
        assert file_read_bytes(env.PROJECT_ROOT / 'docs/readme_copy.txt') == b'# Website\r\n'
        assert file_read_bytes(env.PROJECT_ROOT / 'docs/readme_copy2.txt') == b'# Website\r\n'
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_damaged_patch_source_downloaded(self, app_folder):
        """A wrong old file of an M record: the record is downloaded,
        its target path is the source path, the tree is complete."""
        setup_app()
        with open(env.PROJECT_ROOT / 'backend/main.py', 'wb') as f:
            f.write(b'corrupt content')
        run_update()

    def test_missing_rename_source_downloaded(self, app_folder):
        """A missing old file of an R record: the moved file is
        downloaded, the tree is complete."""
        setup_app()
        os.remove(env.PROJECT_ROOT / 'scripts/run.sh')
        run_update()

    def test_missing_rm_source_downloaded(self, app_folder):
        """A missing old file of an RM record: the moved file is
        downloaded, the tree is complete."""
        setup_app()
        os.remove(env.PROJECT_ROOT / 'scripts/old_tool.py')
        run_update()

    def test_eol_mismatch_source_fixed_without_download(self, app_folder, monkeypatch):
        """A source whose EOL differs is converted, no download happens."""
        setup_app()
        with open(env.PROJECT_ROOT / 'docs/readme.md', 'wb') as f:
            f.write(b'# Website\r\n')

        def _fail(self, *a, **k):
            raise AssertionError('no download expected for an EOL mismatch')
        monkeypatch.setattr(SERVER, 'get_file_content', _fail)
        job = UpdateJob(UPDATE, server=SERVER)
        assert job.run()
        assert job.error == []
        # the copy records are computed from the converted source blob,
        # the copies keep their own eol (crlf)
        assert file_read_bytes(env.PROJECT_ROOT / 'docs/readme_copy.txt') == b'# Website\r\n'
        assert file_read_bytes(env.PROJECT_ROOT / 'docs/readme_copy2.txt') == b'# Website\r\n'

    def test_unsolvable_stays_in_error(self, app_folder, monkeypatch):
        """A record that cannot be downloaded stays in error."""
        setup_app()
        os.remove(env.PROJECT_ROOT / 'docs/readme.md')
        monkeypatch.setattr(SERVER, 'get_file_content', lambda *a, **k: b'bad data')
        job = UpdateJob(UPDATE, server=SERVER)
        with logger.mock_capture_writer() as capture:
            assert not job.run()
        assert capture.backend.any_contains('Failed to download docs/readme_copy.txt:')
        assert [item.info.path for item in job.error] == \
            ['docs/readme_copy.txt', 'docs/readme_copy2.txt']
        # the other changes are still applied, the workspace is cleaned
        assert file_read_bytes(env.PROJECT_ROOT / 'backend/a1.py') == NEW['backend/a1.py']
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_no_server_sources_unsolvable(self, app_folder):
        """A missing server leaves the failed records in error."""
        setup_app()
        os.remove(env.PROJECT_ROOT / 'docs/readme.md')
        job = UpdateJob(UPDATE)
        with logger.mock_capture_writer() as capture:
            assert not job.run()
        assert capture.backend.any_contains('no server provided')
        assert [item.info.path for item in job.error] == \
            ['docs/readme_copy.txt', 'docs/readme_copy2.txt']


# ════════════════════════════════════════════════════════════════════════════
#  download phase
# ════════════════════════════════════════════════════════════════════════════


class TestDownload:
    """download(): fetch the content of the failed records from the
    server and write their tmp files."""

    def test_download_failed_records(self, app_folder):
        """The failed records are downloaded to tmp files."""
        setup_app()
        os.remove(env.PROJECT_ROOT / 'docs/readme.md')
        job = UpdateJob(UPDATE, server=SERVER)
        job.write()
        job.unpack()
        # the copied records cannot be computed without the source
        assert [item.info.path for item in job.error] == \
            ['docs/readme_copy.txt', 'docs/readme_copy2.txt']
        job.download()
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

    def test_download_reuse_tmp(self, app_folder, monkeypatch):
        """A leftover tmp file that passes the check is reused."""
        setup_app()
        os.remove(env.PROJECT_ROOT / 'docs/readme.md')
        # write a valid tmp file at the record tmp name, download()
        # should reuse it
        decoder = PackDecodeBase(UPDATE)
        index = list(decoder.fileinfo).index('docs/readme_copy.txt')
        info = decoder.fileinfo['docs/readme_copy.txt']
        tmp = env.PROJECT_ROOT / f'.pack/workspace/{info.size}_{info.sha1}_{index}.tmp'
        os.makedirs(tmp.uppath(), exist_ok=True)
        with open(tmp, 'wb') as f:
            f.write(b'# Website\r\n')

        def _fail(self, *a, **k):
            raise AssertionError('no download expected, the tmp file is reused')
        monkeypatch.setattr(SERVER, 'get_file_content', _fail)
        job = UpdateJob(UPDATE, server=SERVER)
        assert job.run()
        assert job.error == []
        assert file_read_bytes(env.PROJECT_ROOT / 'docs/readme_copy.txt') == b'# Website\r\n'
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_download_no_error_is_noop(self, app_folder, monkeypatch):
        """A healthy tree needs no download."""
        setup_app()

        def _fail(self, *a, **k):
            raise AssertionError('no download expected for a healthy tree')
        monkeypatch.setattr(SERVER, 'get_file_content', _fail)
        job = UpdateJob(UPDATE, server=SERVER)
        assert job.run()
        assert job.error == []
        assert read_tree() == NEW_TREE


# ════════════════════════════════════════════════════════════════════════════
#  caller flow
# ════════════════════════════════════════════════════════════════════════════


class TestCallerFlow:
    """The exact caller usage of UpdateJob."""

    def test_get_unfinished_job_update(self, app_folder):
        """An update pack job file is dispatched to a resumed UpdateJob."""
        setup_app()
        UpdateJob(UPDATE).write()
        job = DeployJob.get_unfinished_job(SERVER)
        assert job is not None
        assert isinstance(job, UpdateJob)
        assert job.run()
        assert read_tree() == NEW_TREE
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_interrupted_unpack_resumed(self, app_folder):
        """A run interrupted after unpack() is resumed: the local
        index is not touched yet (replace() writes it), the tmp files
        are reused."""
        setup_app()
        job = UpdateJob(UPDATE, server=SERVER)
        job.write()
        job.unpack()
        # the local index is still the old one, the resumed run
        # verifies it against the refinfo and reuses the tmp files
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == \
            bytes(OLD_DECODER.extract_index_pack())
        job = DeployJob.get_unfinished_job(SERVER)
        assert job is not None
        assert isinstance(job, UpdateJob)
        assert job.run()
        assert read_tree() == NEW_TREE
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == \
            bytes(NEW_DECODER.extract_index_pack())
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_resume_skips_write(self, app_folder, monkeypatch):
        """A resumed job skips write(), the data is already in the file."""
        setup_app()
        UpdateJob(UPDATE).write()

        def _fail(self):
            raise AssertionError('write() should not be called on resume')
        monkeypatch.setattr(UpdateJob, 'write', _fail)
        job = DeployJob.get_unfinished_job(SERVER)
        assert job is not None
        assert job.run()
        assert read_tree() == NEW_TREE

    def test_full_pack_dispatched_to_unpack_job(self, app_folder):
        """A full pack job file is dispatched to UnpackJob, not UpdateJob."""
        UnpackJob(OLD_PACK).write()
        job = DeployJob.get_unfinished_job(SERVER)
        assert job is not None
        assert isinstance(job, UnpackJob)


# ════════════════════════════════════════════════════════════════════════════
#  failure
# ════════════════════════════════════════════════════════════════════════════


class TestFailure:
    """Failure keeps the workspace for the next run to resume."""

    def test_invalid_pack_raises(self, app_folder):
        """Not a pack file raises PackDecodeError."""
        with pytest.raises(PackDecodeError):
            UpdateJob(b'not a pack file').unpack()

    def test_full_pack_rejected(self, app_folder):
        """A full pack without refinfo is rejected."""
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
        assert DeployJob.get_unfinished_job() is not None

    def test_run_failure_logged_and_cleaned(self, app_folder):
        """A failed run logs a warning and cleans the workspace."""
        setup_app()
        decoder = PackDecodeBase(UPDATE)
        index_end = 5 + len(decoder.index_section)
        bad = bytearray(UPDATE)
        bad[index_end + 100] ^= 0xFF
        job = UpdateJob(bytes(bad), server=SERVER)
        with logger.mock_capture_writer() as capture:
            assert not job.run()
        assert capture.backend.any_contains('Failed to update:')
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')
