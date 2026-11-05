"""
Tests for the path type conflicts of the deploy jobs.

A pack record only names files: a folder is the prefix of a record path, never
a record of its own. Two conflicts can only be decided on the client then:

- a deleted record (D) whose path is a folder by the time the deletion runs.
  The writes of replace_data() run before the deletions and BatchMakedirs
  removes a file in the way of a folder, so a new version that replaces
  "x" (a file) with "x/y.py" leaves a folder at the deleted path. The folder
  is kept when the new version records a file inside it, and removed
  otherwise, see JobBase._remove_deleted();
- a record that writes a file whose parent is a file, e.g. "x/y.py" while "x"
  is the file of the old version: BatchMakedirs removes the file in the
  way, and _read_current() answers "no file" for the path instead of failing
  on the missing parent folder.

The packs are module level singletons, built before the fake filesystem is
active: the pack encoder reads the builtin .gitattributes from the real disk.
"""
import os

import pytest

from alasio.deploy.pack import job_base
from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job_base import JobBase
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.deploy.pack.job_update import UpdateJob
from alasio.deploy_dev.pack.pack_full import PackFull
from alasio.deploy_dev.pack.pack_update import PackUpdate
from alasio.ext import env
from alasio.ext.path.atomic import file_read_bytes, file_write
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack.conftest import MockServerFile


def build(old_tree, new_tree):
    """
    Build a repo of two versions.

    Args:
        old_tree (dict[str, bytes]): Files of the old version
        new_tree (dict[str, bytes]): Files of the new version

    Returns:
        MockGitRepo: Repo, its branch master points at the new version
    """
    repo = MockGitRepo()
    for path, content in old_tree.items():
        repo.register_file('old', path, content)
    repo.register_commit('old', author_name='Author', author_time=1, message='old')
    for path, content in new_tree.items():
        repo.register_file('new', path, content)
    repo.register_commit('new', parents=['old'], author_name='Author', author_time=2, message='new')
    repo.register_branch('master', 'new')
    repo.register_head('new')
    return repo


def build_packs(old_tree, new_tree):
    """
    Build the full packs of both versions and the update pack between them.

    Args:
        old_tree (dict[str, bytes]): Files of the old version
        new_tree (dict[str, bytes]): Files of the new version

    Returns:
        tuple[bytes, bytes, bytes, MockServerFile, bytes]: Old full pack, new
            full pack, update pack, server of the two versions, and the index
            checksum of the new version
    """
    repo = build(old_tree, new_tree)
    old_pack = b''.join(PackFull(repo, commit='old').iter_pack_data())
    new_pack = b''.join(PackFull(repo, commit='new').iter_pack_data())
    update = b''.join(PackUpdate(PackFull(repo, commit='new'), 'old').iter_pack_data())
    server = MockServerFile()
    server.register_version('old', old_pack, bytes(PackDecodeBase(old_pack).extract_index_pack()))
    server.register_version('new', new_pack, bytes(PackDecodeBase(new_pack).extract_index_pack()))
    return old_pack, new_pack, update, server, PackDecodeBase(new_pack).index_checksum


# ════════════════════════════════════════════════════════════════════════════
#  the versions of the tests, built before the fake filesystem is active
# ════════════════════════════════════════════════════════════════════════════

# "x" is a file of the old version, the new version replaces it with a folder
# of the same name: the update pack carries "D x" and "A x/y.py"
FILE_TO_FOLDER_OLD = {'x': b'the old file\n', 'keep.py': b'keep\n'}
FILE_TO_FOLDER_NEW = {'x/y.py': b'the new folder\n', 'keep.py': b'keep\n'}
F2D_OLD_PACK, F2D_NEW_PACK, F2D_UPDATE, F2D_SERVER, F2D_CHECKSUM = build_packs(
    FILE_TO_FOLDER_OLD, FILE_TO_FOLDER_NEW)

# "gone.py" is a file the new version removes, and the new version has no file
# at or below "x": a folder at either path has no owner in the new version
REMOVED_OLD = {'x': b'the old file\n', 'gone.py': b'bye\n', 'keep.py': b'keep\n'}
REMOVED_NEW = {'keep.py': b'keep\n'}
RM_OLD_PACK, RM_NEW_PACK, RM_UPDATE, RM_SERVER, RM_CHECKSUM = build_packs(
    REMOVED_OLD, REMOVED_NEW)


def is_folder(path):
    """
    Returns:
        bool: True when the path is a folder
    """
    return os.path.isdir(env.PROJECT_ROOT.joinpath(path))


def exists(path):
    """
    Returns:
        bool: True when the path exists, whatever its kind
    """
    return os.path.exists(env.PROJECT_ROOT.joinpath(path))


def read(path):
    """
    Returns:
        bytes: Content of a file of the app folder
    """
    return file_read_bytes(env.PROJECT_ROOT.joinpath(path))


# ════════════════════════════════════════════════════════════════════════════
#  a deleted path that is a folder
# ════════════════════════════════════════════════════════════════════════════


class TestDeletedPathIsFolder:
    """The deleted record of a path the new version turned into a folder."""

    @pytest.mark.trio
    async def test_file_replaced_by_folder(self, app_folder):
        """The new version replaces a file with a folder of the same name: the
        update applies, the folder is kept and the file is gone."""
        await UnpackJob(F2D_OLD_PACK).run()
        assert not is_folder('x')

        job = UpdateJob(F2D_UPDATE, server=F2D_SERVER)
        job.index_checksum = F2D_CHECKSUM
        with logger.mock_capture_writer() as capture:
            assert await job.run()

        assert is_folder('x')
        assert read('x/y.py') == FILE_TO_FOLDER_NEW['x/y.py']
        assert read('keep.py') == b'keep\n'
        # the deletion of "x" is answered, not failed: the folder holds a record
        # of the new version
        assert 'INFO | Skipped the deletion of "x": the new version has a folder there\n' \
            in capture.concat()

    @pytest.mark.trio
    async def test_file_replaced_by_folder_full_pack(self, app_folder):
        """The full pack of the new version applies to the old tree too: the
        same records, the same folder."""
        await UnpackJob(F2D_OLD_PACK).run()
        assert await UnpackJob(F2D_NEW_PACK).run()

        assert is_folder('x')
        assert read('x/y.py') == FILE_TO_FOLDER_NEW['x/y.py']
        assert read('keep.py') == b'keep\n'

    @pytest.mark.trio
    async def test_folder_from_a_previous_run_is_kept(self, app_folder):
        """A folder the new version owns is kept whatever it already holds: a
        previous run wrote the same record, the deletion must not remove it."""
        await UnpackJob(F2D_OLD_PACK).run()
        # the tree of an interrupted run: the folder exists with its file
        os.remove(env.PROJECT_ROOT.joinpath('x'))
        os.makedirs(env.PROJECT_ROOT.joinpath('x'))
        file_write(env.PROJECT_ROOT.joinpath('x/y.py'), FILE_TO_FOLDER_NEW['x/y.py'])

        job = UpdateJob(F2D_UPDATE, server=F2D_SERVER)
        job.index_checksum = F2D_CHECKSUM
        with logger.mock_capture_writer():
            assert await job.run()

        assert read('x/y.py') == FILE_TO_FOLDER_NEW['x/y.py']

    @pytest.mark.trio
    async def test_missing_path_is_a_no_op(self, app_folder):
        """A deleted file that is already gone is not an error."""
        await UnpackJob(RM_OLD_PACK).run()
        os.remove(env.PROJECT_ROOT.joinpath('gone.py'))

        job = UpdateJob(RM_UPDATE, server=RM_SERVER)
        job.index_checksum = RM_CHECKSUM
        with logger.mock_capture_writer() as capture:
            assert await job.run()

        assert not exists('gone.py')
        assert exists('keep.py')
        assert 'INFO | Update done\n' in capture.concat()

    @pytest.mark.trio
    async def test_unowned_folder_is_removed(self, app_folder):
        """A folder on the path of a deleted file that the new version does not
        own is a leftover: the path must not exist, the folder goes."""
        await UnpackJob(RM_OLD_PACK).run()
        # a folder (and its content) on the path a file of the old version had,
        # e.g. a leftover of an interrupted run
        os.remove(env.PROJECT_ROOT.joinpath('gone.py'))
        os.makedirs(env.PROJECT_ROOT.joinpath('gone.py'))
        file_write(env.PROJECT_ROOT.joinpath('gone.py/junk.txt'), b'leftover\n')

        job = UpdateJob(RM_UPDATE, server=RM_SERVER)
        job.index_checksum = RM_CHECKSUM
        with logger.mock_capture_writer() as capture:
            assert await job.run()

        assert not exists('gone.py')
        assert 'INFO | Removing the folder of a deleted path: ' in capture.concat()

    @pytest.mark.trio
    async def test_file_is_still_removed(self, app_folder):
        """The common case is unchanged: a deleted file is unlinked."""
        await UnpackJob(RM_OLD_PACK).run()
        assert exists('gone.py')

        job = UpdateJob(RM_UPDATE, server=RM_SERVER)
        job.index_checksum = RM_CHECKSUM
        with logger.mock_capture_writer():
            assert await job.run()

        assert not exists('gone.py')
        assert exists('keep.py')


class TestRemoveDeleted:
    """JobBase._remove_deleted() and _owns_folder() on their own."""

    def test_file_is_removed_without_a_lookup(self, app_folder, monkeypatch):
        """A deleted file is one unlink: the kind of the path is only looked up
        when the removal fails, so the common case costs no extra io."""
        file_write(env.PROJECT_ROOT.joinpath('gone.py'), b'bye\n')

        def no_stat(*args, **kwargs):
            raise AssertionError('the common case must not look the path up')

        monkeypatch.setattr(job_base.os, 'lstat', no_stat)
        job = UpdateJob(b'')

        assert job._remove_deleted('gone.py', set()) is True
        assert not exists('gone.py')

    def test_missing_file_is_not_a_lookup(self, app_folder, monkeypatch):
        """A deleted file that is already gone costs one failed unlink, no lookup."""
        def no_stat(*args, **kwargs):
            raise AssertionError('the common case must not look the path up')

        monkeypatch.setattr(job_base.os, 'lstat', no_stat)
        job = UpdateJob(b'')

        assert job._remove_deleted('gone.py', set()) is False

    def test_posix_directory_needs_no_lookup(self, app_folder, monkeypatch):
        """On POSIX the unlink of a folder raises IsADirectoryError, which
        already names the kind of the path: the folder must not be stat-ed."""
        os.makedirs(env.PROJECT_ROOT.joinpath('x'))
        file_write(env.PROJECT_ROOT.joinpath('x/y.py'), b'new\n')

        def posix_unlink(path):
            # the POSIX answer of unlink() on a folder, which the fake
            # filesystem (posixpath based) may answer differently
            if os.path.isdir(path):
                raise IsADirectoryError(21, 'Is a directory', str(path))
            raise FileNotFoundError(2, 'No such file or directory', str(path))

        def no_lstat(*args, **kwargs):
            raise AssertionError('IsADirectoryError already names the kind')

        monkeypatch.setattr(job_base, 'file_remove', posix_unlink)
        monkeypatch.setattr(job_base.os, 'lstat', no_lstat)
        job = UpdateJob(b'')

        # the folder holds a record of the new version: it is kept, and the
        # kind came from the exception alone
        assert job._remove_deleted('x', {'x'}) is False
        assert is_folder('x')

    def test_posix_unowned_folder_removed_without_lookup(self, app_folder, monkeypatch):
        """An unowned folder is removed from the exception alone too."""
        os.makedirs(env.PROJECT_ROOT.joinpath('gone'))
        file_write(env.PROJECT_ROOT.joinpath('gone/junk.txt'), b'leftover\n')

        def posix_unlink(path):
            if os.path.isdir(path):
                raise IsADirectoryError(21, 'Is a directory', str(path))
            raise FileNotFoundError(2, 'No such file or directory', str(path))

        def no_lstat(*args, **kwargs):
            raise AssertionError('IsADirectoryError already names the kind')

        monkeypatch.setattr(job_base, 'file_remove', posix_unlink)
        monkeypatch.setattr(job_base.os, 'lstat', no_lstat)
        job = UpdateJob(b'')

        assert job._remove_deleted('gone', set()) is True
        assert not exists('gone')

    def test_ledger_folder_is_never_removed(self, app_folder):
        """A record that names the ledger folder must not remove it: the index
        pack and the workspace of the job live there."""
        os.makedirs(env.PROJECT_ROOT.joinpath('.pack'))
        file_write(env.PROJECT_ROOT.joinpath('.pack/index.pack'), b'index')
        job = UpdateJob(b'')

        with logger.mock_capture_writer() as capture:
            assert job._remove_deleted('.pack', set()) is False

        assert is_folder('.pack')
        assert read('.pack/index.pack') == b'index'
        assert ('WARNING | Skipped the deletion of ".pack": '
                'the ledger folder of the target is inside it\n') in capture.concat()

    def test_ancestor_of_the_ledger_is_never_removed(self, app_folder):
        """A record that names an ancestor of the ledger folder is skipped too."""
        os.makedirs(env.PROJECT_ROOT.joinpath('.pack/httpx'))
        job = UpdateJob(b'', name='httpx')

        with logger.mock_capture_writer() as capture:
            assert job._remove_deleted('', set()) is False
        # '' is the root of the target: no record names it, and the guard
        # answers before any file operation
        assert is_folder('.pack')
        assert 'Skipped the deletion' in capture.concat()


class TestOccupiedFolders:
    """JobBase.occupied_folders(): the folders a version's files occupy."""

    def test_nested_paths(self):
        """Every prefix of a path is a folder of the version."""
        assert JobBase.occupied_folders(['x/y/z.py']) == {'x/y', 'x'}

    def test_shared_prefixes(self):
        """A folder shared by several files is in the set once."""
        assert JobBase.occupied_folders(['a/b.py', 'a/c/d.py', 'a/e.py']) == {'a', 'a/c'}

    def test_top_level_file(self):
        """A file at the root of the target occupies no folder."""
        assert JobBase.occupied_folders(['mod.py', 'keep.py']) == set()

    def test_no_paths(self):
        """Nothing to write, no folder occupied."""
        assert JobBase.occupied_folders([]) == set()

    def test_folder_name_is_not_a_prefix(self):
        """A sibling that only shares the text of a name is not inside it."""
        assert 'x' not in JobBase.occupied_folders(['xy/z.py'])

    def test_delete_does_not_scan_the_records(self, app_folder):
        """The answer of a deleted path is the set, never a walk of the records.

        The records of the job are a sentinel that fails on any use: the
        deletion of a path has to answer from the set alone.
        """
        os.makedirs(env.PROJECT_ROOT.joinpath('x'))
        file_write(env.PROJECT_ROOT.joinpath('x/y.py'), b'new\n')
        job = UpdateJob(b'')

        class NoWalk:
            def items(self):
                raise AssertionError('the records must not be walked')

            def __iter__(self):
                raise AssertionError('the records must not be walked')

        job.new_fileinfo = NoWalk()
        job.pending = NoWalk()

        assert job._remove_deleted('x', {'x'}) is False
        assert is_folder('x')
        assert job._remove_deleted('x', set()) is True
        assert not exists('x')


class TestReadCurrent:
    """JobBase._read_current() on a path whose parent is a file."""

    def test_parent_is_a_file(self, app_folder, monkeypatch):
        """A path below a file has no file of its own: the answer is "missing",
        not an error, so the record can be applied after BatchMakedirs
        removed the file in the way."""
        def raiser(file, mode='r', **kwargs):
            # the POSIX answer for "x" being a file, open("x/y.py") raises
            # ENOTDIR; the fake filesystem answers FileNotFoundError
            raise NotADirectoryError(20, 'Not a directory', str(file))

        monkeypatch.setattr(job_base, 'atomic_open', raiser)
        current = JobBase._read_current(env.PROJECT_ROOT.joinpath('x/y.py'))

        assert current.exist is False
        assert current.data == b''
        assert current.mode == 0
