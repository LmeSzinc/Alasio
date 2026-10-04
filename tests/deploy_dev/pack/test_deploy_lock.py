"""
Tests for the exclusive lock of DeployJob.

The lock is a SQLite file lock on the lock file of the target ledger
({ledger}/lock). SQLite opens its file in the C layer, so the in-memory
fake filesystem cannot serve it: the exclusion tests run on a real
directory, the integration tests inside the fake filesystem use the
in-memory lock swapped in by the app_folder fixture (see
conftest.MemoryLock).
"""
import os
import shutil

import pytest

from alasio.deploy.pack.job import DeployJob
from alasio.deploy.pack.job_reset import ResetJob
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.ext import env
from alasio.ext.env import ALASIO_ROOT
from alasio.ext.file.filelock import FilelockTimeout
from alasio.ext.path.atomic import file_read_bytes
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack.conftest import WEBSITE_FILES, WEBSITE_FULL_PACK, WEBSITE_SERVER


@pytest.fixture
def real_root():
    """
    Real filesystem folder for the exclusion tests of the lock.

    The lock is a SQLite file lock opened in the C layer, the in-memory
    fake filesystem cannot serve it: the exclusion tests need a real
    directory. It lives under the repo's temp/ folder and is removed
    after the test.
    """
    path = ALASIO_ROOT.joinpath('temp/deploy_job_lock')
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path)
    yield path
    shutil.rmtree(path, ignore_errors=True)


class TestLockFile:
    """The lock file of a target, inside its ledger folder."""

    def test_lock_file_of_the_tree_target(self, app_folder):
        """The tree target locks {root}/.pack/lock."""
        assert DeployJob().lock_file == env.PROJECT_ROOT / '.pack/lock'

    def test_lock_file_of_a_named_target(self, app_folder):
        """A named target locks {root}/.pack/{name}/lock."""
        assert DeployJob(name='httpx').lock_file == env.PROJECT_ROOT / '.pack/httpx/lock'


class TestLocked:
    """locked() of one instance: re-entrant, released on exit."""

    def test_reentrant(self, app_folder):
        """A nested locked() counts up, it never deadlocks."""
        deploy = DeployJob()
        with deploy.locked():
            assert deploy.lock.is_locked
            with deploy.locked():
                assert deploy.lock.is_locked
            assert deploy.lock.is_locked
        assert not deploy.lock.is_locked

    def test_released_on_exception(self, app_folder):
        """The lock is released when the block raises."""
        deploy = DeployJob()
        with pytest.raises(RuntimeError):
            with deploy.locked():
                raise RuntimeError('boom')
        assert not deploy.lock.is_locked


class TestFlowLocked:
    """update() and unpack() hold the lock for their whole flow."""

    def test_unpack_holds_the_lock(self, app_folder, monkeypatch):
        """unpack() holds the lock while its job runs, releases it after."""
        deploy = DeployJob()
        states = []
        original = UnpackJob.run

        def run(self):
            states.append(deploy.lock.is_locked)
            return original(self)

        monkeypatch.setattr(UnpackJob, 'run', run)
        with logger.mock_capture_writer():
            deploy.unpack(WEBSITE_FULL_PACK)
        assert states == [True]
        assert not deploy.lock.is_locked
        assert file_read_bytes(env.PROJECT_ROOT / 'backend/main.py') == \
            WEBSITE_FILES['backend/main.py'][0]

    def test_update_holds_the_lock(self, app_folder, monkeypatch):
        """update() holds the lock while its job runs, releases it after."""
        deploy = DeployJob(server=WEBSITE_SERVER)
        with logger.mock_capture_writer():
            deploy.unpack(WEBSITE_FULL_PACK)
        states = []
        original = ResetJob.run

        def run(self):
            states.append(deploy.lock.is_locked)
            return original(self)

        monkeypatch.setattr(ResetJob, 'run', run)
        with logger.mock_capture_writer():
            assert deploy.update()
        assert states == [True]
        assert not deploy.lock.is_locked


class TestLockExclusion:
    """The lock excludes other instances, on a real directory."""

    def test_second_instance_fails_fast(self, real_root):
        """A held lock rejects another instance with timeout=0."""
        first = DeployJob(root=real_root)
        second = DeployJob(root=real_root)
        with first.locked():
            with pytest.raises(FilelockTimeout):
                with second.locked(timeout=0):
                    pass
        # the first instance released it, the second one acquires
        with second.locked(timeout=0):
            assert second.lock.is_locked
        assert not second.lock.is_locked

    def test_named_targets_have_their_own_lock(self, real_root):
        """Two targets of one root do not block each other."""
        tree = DeployJob(root=real_root)
        httpx = DeployJob(root=real_root, name='httpx')
        with tree.locked(timeout=0):
            with httpx.locked(timeout=0):
                assert tree.lock.is_locked
                assert httpx.lock.is_locked

    def test_reentrant_on_the_real_lock(self, real_root):
        """A nested acquire of the same real lock counts up."""
        deploy = DeployJob(root=real_root)
        with deploy.locked(timeout=0):
            with deploy.locked(timeout=0):
                assert deploy.lock.is_locked
        assert not deploy.lock.is_locked
