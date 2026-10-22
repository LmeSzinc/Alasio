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
import threading

import pytest
import trio

from alasio.deploy.pack.job import DeployJob
from alasio.deploy.pack.job_reset import ResetJob
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.ext import env
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
    path = env.ALASIO_TEMP.joinpath('deploy_job_lock')
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
    """locked() of one instance: not reentrant, released on exit."""

    def test_nested_locked_raises(self, app_folder):
        """A nested locked() is a reentrancy error, never a deadlock."""
        deploy = DeployJob()
        with deploy.locked():
            assert deploy.lock.is_locked
            with pytest.raises(RuntimeError, match='already held by this thread'):
                with deploy.locked():
                    pass
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

    @pytest.mark.trio
    async def test_unpack_holds_the_lock(self, app_folder, monkeypatch):
        """unpack() holds the lock while its job runs, releases it after."""
        deploy = DeployJob()
        states = []
        original = UnpackJob.run

        async def run(self):
            states.append(deploy.lock.is_locked)
            return await original(self)

        monkeypatch.setattr(UnpackJob, 'run', run)
        with logger.mock_capture_writer():
            await deploy.unpack(WEBSITE_FULL_PACK)
        assert states == [True]
        assert not deploy.lock.is_locked
        assert file_read_bytes(env.PROJECT_ROOT / 'backend/main.py') == \
            WEBSITE_FILES['backend/main.py'][0]

    @pytest.mark.trio
    async def test_update_holds_the_lock(self, app_folder, monkeypatch):
        """update() holds the lock while its job runs, releases it after."""
        deploy = DeployJob(server=WEBSITE_SERVER)
        with logger.mock_capture_writer():
            await deploy.unpack(WEBSITE_FULL_PACK)
        states = []
        original = ResetJob.run

        async def run(self):
            states.append(deploy.lock.is_locked)
            return await original(self)

        monkeypatch.setattr(ResetJob, 'run', run)
        with logger.mock_capture_writer():
            assert await deploy.update()
        assert states == [True]
        assert not deploy.lock.is_locked


class TestLockExclusion:
    """The lock excludes the other threads and instances, on a real directory."""

    def test_second_instance_in_the_same_thread_raises(self, real_root):
        """The instances of one target share the lock of its file in the
        process: a nested acquire of another instance is a reentrancy error."""
        first = DeployJob(root=real_root)
        second = DeployJob(root=real_root)
        with first.locked():
            with pytest.raises(RuntimeError, match='already held by this thread'):
                with second.locked(timeout=0):
                    pass
        # the release of the first instance frees the lock for the process
        with second.locked(timeout=0):
            assert second.lock.is_locked
        assert not second.lock.is_locked

    def test_second_instance_in_another_thread_fails_fast(self, real_root):
        """A held lock rejects the acquire of another thread at once."""
        first = DeployJob(root=real_root)
        second = DeployJob(root=real_root)
        acquired = threading.Event()
        release = threading.Event()

        def hold():
            with first.locked():
                acquired.set()
                release.wait(timeout=10)

        thread = threading.Thread(target=hold)
        thread.start()
        try:
            assert acquired.wait(timeout=10)
            with pytest.raises(FilelockTimeout):
                with second.locked(timeout=0):
                    pass
        finally:
            release.set()
            thread.join(timeout=10)
        assert not second.lock.is_locked

    def test_named_targets_have_their_own_lock(self, real_root):
        """Two targets of one root do not block each other."""
        tree = DeployJob(root=real_root)
        httpx = DeployJob(root=real_root, name='httpx')
        with tree.locked(timeout=0):
            with httpx.locked(timeout=0):
                assert tree.lock.is_locked
                assert httpx.lock.is_locked

    def test_nested_locked_on_the_real_lock_raises(self, real_root):
        """A nested locked() of the real lock is a reentrancy error."""
        deploy = DeployJob(root=real_root)
        with deploy.locked(timeout=0):
            with pytest.raises(RuntimeError, match='already held by this thread'):
                with deploy.locked(timeout=0):
                    pass
            assert deploy.lock.is_locked
        assert not deploy.lock.is_locked


class TestAsyncLocked:
    """alocked() of one instance: the wait runs in the event loop thread."""

    @pytest.mark.trio
    async def test_waits_without_stalling_the_loop(self, real_root):
        """alocked waits for the holder of another thread on its own timeout
        budget, the loop keeps running, the lock is free once it released."""
        first = DeployJob(root=real_root)
        second = DeployJob(root=real_root)
        ticks = []
        acquired = threading.Event()
        release = threading.Event()

        def hold():
            # The lock is not reentrant, the holder has to be another thread
            # than the loop one, which acquires below
            with first.locked():
                acquired.set()
                release.wait(timeout=10)

        thread = threading.Thread(target=hold)
        thread.start()

        async def tick():
            # The loop keeps running while alocked waits for the lock
            for _ in range(3):
                await trio.sleep(0.05)
                ticks.append(True)

        try:
            assert acquired.wait(timeout=10)
            async with trio.open_nursery() as nursery:
                nursery.start_soon(tick)
                with pytest.raises(FilelockTimeout):
                    async with second.alocked(timeout=0.4):
                        pass
        finally:
            release.set()
            thread.join(timeout=10)
        assert ticks == [True, True, True]
        assert not second.lock.is_locked
        # The lock is free after the release of the holder
        async with second.alocked(timeout=0):
            assert second.lock.is_locked
        assert not second.lock.is_locked

    @pytest.mark.trio
    async def test_cancelled_wait_releases_the_late_acquire(self, real_root):
        """A cancelled alocked that was still waiting leaves nothing taken: the
        acquire completes after the cancellation and is released inside the
        block, at the first checkpoint."""
        first = DeployJob(root=real_root)
        second = DeployJob(root=real_root)
        acquired = threading.Event()
        release = threading.Event()

        def hold():
            with first.locked():
                acquired.set()
                release.wait(timeout=10)

        async def release_later():
            # The worker of alocked sits in the SQLite wait, the acquire
            # completes once the holder releases
            await trio.sleep(0.3)
            release.set()

        thread = threading.Thread(target=hold)
        thread.start()
        try:
            assert acquired.wait(timeout=10)
            async with trio.open_nursery() as nursery:
                nursery.start_soon(release_later)
                with trio.move_on_after(0.1) as scope:
                    async with second.alocked(timeout=-1):
                        # The cancellation of the scope is delivered here, the
                        # first checkpoint of the block; the finally of
                        # alocked releases the lock the worker took
                        await trio.sleep_forever()
            assert scope.cancelled_caught
        finally:
            release.set()
            thread.join(timeout=10)
        assert not second.lock.is_locked
        # The lock is free, a new acquire takes it
        async with second.alocked(timeout=0):
            assert second.lock.is_locked
        assert not second.lock.is_locked
