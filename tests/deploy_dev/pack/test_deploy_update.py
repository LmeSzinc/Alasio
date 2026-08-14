"""
Tests for DeployJob.update: the unified entry of the update flow.

The server is an in-memory MockServerFile serving two versions and the
update pack between them. The flow follows the draft in PackEncodeBase:
latest.pack is compared with the local version, a version mismatch
downloads the update pack /{new}/from_{old}.pack and applies it with
UpdateJob, the same version continues with ResetJob.

The packs are module level singletons, built before the fake
filesystem is active: MockGitRepo reads the real .gitattributes file,
which the fake filesystem does not provide.
"""
import os

from conftest import FULL_SCENARIO_NEW, FULL_SCENARIO_OLD, MockServerFile

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job import DeployJob
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.deploy_dev.pack.pack_repo import PackFull
from alasio.deploy_dev.pack.pack_update import PackUpdate
from alasio.ext import env
from alasio.ext.path.atomic import file_read_bytes
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401


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
UPDATE = b''.join(PackUpdate(OLD_DECODER, NEW_DECODER).iter_pack_data())
NEW_TREE = {
    path: bytes(NEW_DECODER.catfile(info))
    for path, info in NEW_DECODER.fileinfo.items()
    if info.edit != 2 and not path.startswith('.pack/')
}
SERVER = MockServerFile()
SERVER.register_version('old', OLD_PACK, bytes(OLD_DECODER.extract_index_pack()))
SERVER.register_version('new', NEW_PACK, bytes(NEW_DECODER.extract_index_pack()))
SERVER.register_update('old', 'new', UPDATE)


class TestDeployUpdate:
    """The unified update entry of DeployJob."""

    def test_update_to_new_version(self, app_folder):
        """A version mismatch downloads the update pack and applies it."""
        with logger.mock_capture_writer():
            UnpackJob(OLD_PACK).run()
            assert DeployJob.update(SERVER)
        assert read_tree() == NEW_TREE
        # the local index pack is the new one
        decoder = PackDecodeBase(file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack'))
        assert decoder.version == 'new'
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_up_to_date(self, app_folder):
        """The same version continues with ResetJob, nothing changes."""
        with logger.mock_capture_writer():
            UnpackJob(NEW_PACK).run()
            assert DeployJob.update(SERVER)
        assert read_tree() == NEW_TREE
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_missing_local_index(self, app_folder):
        """A missing local index falls back to ResetJob, the tree is
        rebuilt from the server."""
        with logger.mock_capture_writer():
            assert DeployJob.update(SERVER)
        assert read_tree() == NEW_TREE
        decoder = PackDecodeBase(file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack'))
        assert decoder.version == 'new'
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_unfinished_job_finished_first(self, app_folder):
        """An unfinished job is finished before the update."""
        with logger.mock_capture_writer():
            UnpackJob(OLD_PACK).write()
            assert DeployJob.update(SERVER)
        assert read_tree() == NEW_TREE
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')
