"""
Tests for DeployJob.run_unfinished_job(): finish the unfinished job of
a target left behind by a killed run.

The job file ({ledger}/workspace/job.pack) is written before any real
file is changed and cleaned up when the run ends, so a process killed
in flight leaves it behind; the next startup finishes the job from it.
The three outcomes are covered: nothing to do (False), an unfinished
job completed (True, a backend restart is needed), a job that could
not complete (the rebuild fallback first, then the error).

The upgrade uses the shared FULL_SCENARIO_OLD / FULL_SCENARIO_NEW of
conftest: the old version is unpacked into the fake filesystem, the
update pack is left in the job file like an interrupted apply, the
resumed run finishes it to the new version.
"""
import os

import pytest

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job import DeployJob
from alasio.deploy.pack.job_rebuild import RebuildJob
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.deploy.pack.job_update import UpdateJob
from alasio.deploy_dev.pack.pack_full import PackFull
from alasio.deploy_dev.pack.pack_update import PackUpdate
from alasio.ext import env
from alasio.ext.path.atomic import file_read_bytes
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack.conftest import (
    FULL_SCENARIO_NEW, FULL_SCENARIO_OLD, WEBSITE_FILES, WEBSITE_FULL_PACK, MockServerFile, make_repo
)

# ════════════════════════════════════════════════════════════════════════════
#  shared versions
# ════════════════════════════════════════════════════════════════════════════

# The shared upgrade scenario of conftest: the update pack is generated
# from the repo of both versions, the server serves both of them.
SCENARIO_REPO = make_repo({'old': FULL_SCENARIO_OLD, 'new': FULL_SCENARIO_NEW})
OLD_PACK = b''.join(PackFull(SCENARIO_REPO, commit='old').iter_pack_data())
NEW_PACK = b''.join(PackFull(SCENARIO_REPO, commit='new').iter_pack_data())
UPDATE_PACK = b''.join(PackUpdate(PackFull(SCENARIO_REPO, commit='new'), 'old').iter_pack_data())
SERVER = MockServerFile()
SERVER.register_version('old', OLD_PACK, bytes(PackDecodeBase(OLD_PACK).extract_index_pack()))
SERVER.register_version('new', NEW_PACK, bytes(PackDecodeBase(NEW_PACK).extract_index_pack()))


def unpack_tree(data):
    """
    Extract the working tree of a full pack as {path: content}.

    Args:
        data (bytes): Full pack data

    Returns:
        dict[str, bytes]: Working tree content
    """
    decoder = PackDecodeBase(data)
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


NEW_TREE = unpack_tree(NEW_PACK)


class TestRunUnfinishedJob:
    """run_unfinished_job(): finish the job a killed run left behind."""

    @pytest.mark.trio
    async def test_nothing_to_do(self, app_folder):
        """No unfinished job: nothing is done, False is returned."""
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')
        with logger.mock_capture_writer() as capture:
            assert await DeployJob().run_unfinished_job() is False
        assert not capture.backend.any_contains('Found unfinished job')
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_finish_unpack_job(self, app_folder):
        """An interrupted full unpack is finished, True is returned."""
        UnpackJob(WEBSITE_FULL_PACK).write()
        assert await DeployJob().run_unfinished_job() is True
        for path, (content, _) in WEBSITE_FILES.items():
            assert file_read_bytes(env.PROJECT_ROOT / path) == content, path
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')
        # the job is gone, a second call has nothing to do
        assert await DeployJob().run_unfinished_job() is False

    @pytest.mark.trio
    async def test_finish_update_job(self, app_folder):
        """An update interrupted in flight is finished to the new version."""
        await UnpackJob(OLD_PACK).run()
        UpdateJob(UPDATE_PACK, server=SERVER).write()
        with logger.mock_capture_writer() as capture:
            assert await DeployJob(server=SERVER).run_unfinished_job() is True
        assert capture.backend.any_contains('Found unfinished job')
        assert read_tree() == NEW_TREE
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_failed_job_without_server(self, app_folder):
        """A job that cannot finish and has no server for the fallback
        rebuild raises ValueError."""
        UpdateJob(UPDATE_PACK).write()
        with pytest.raises(ValueError):
            await DeployJob().run_unfinished_job()

    @pytest.mark.trio
    async def test_failed_unpack_without_server(self, app_folder, monkeypatch):
        """An unpack job that cannot finish and has no server for the
        fallback rebuild raises ValueError."""
        def _raise(self):
            raise RuntimeError('replace failed')
        monkeypatch.setattr(UnpackJob, 'replace', _raise)
        UnpackJob(WEBSITE_FULL_PACK).write()
        with pytest.raises(ValueError):
            await DeployJob().run_unfinished_job()

    @pytest.mark.trio
    async def test_failed_job_falls_back_to_rebuild(self, app_folder, monkeypatch):
        """A job that cannot finish falls back to a rebuild from the
        latest index, the unfinished job completes and True is returned."""
        await UnpackJob(OLD_PACK).run()

        async def _fail(self):
            return False
        monkeypatch.setattr(UpdateJob, 'run', _fail)
        UpdateJob(UPDATE_PACK, server=SERVER).write()

        with logger.mock_capture_writer() as capture:
            assert await DeployJob(server=SERVER).run_unfinished_job() is True
        assert capture.backend.any_contains('rebuilding from the latest index')
        assert read_tree() == NEW_TREE
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_failed_rebuild_raises(self, app_folder, monkeypatch):
        """A job that cannot finish and a rebuild that cannot either
        raise RuntimeError."""
        async def _fail(self):
            return False
        monkeypatch.setattr(UpdateJob, 'run', _fail)
        monkeypatch.setattr(RebuildJob, 'run', _fail)
        UpdateJob(UPDATE_PACK, server=SERVER).write()
        with pytest.raises(RuntimeError):
            await DeployJob(server=SERVER).run_unfinished_job()
