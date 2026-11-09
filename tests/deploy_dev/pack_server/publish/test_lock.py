"""
The exclusive lock of a channel: one run generates and publishes a workspace at
a time.

The lock is a SQLite database file, opened in the C layer of the interpreter
where the in-memory filesystem of the other tests cannot serve it. The tests of
the exclusion therefore run on a real folder under the temp folder of the
repository, and the tests of the flow use the in-memory lock that the run
directory fixtures swap in (see tests.deploy_dev.pack_server.conftest).
"""

import os
import secrets
import shutil
import threading

import pytest
import trio

from alasio.deploy_dev.pack_server.publish.lock import LOCK_FOLDER, ChannelBusy, ChannelLock, lock_name
from alasio.deploy_dev.pack_server.publish.upload import UploadRun
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack_server.publish.conftest import CHANNEL

# the lock fixes the order of the runs of one channel, never the order of two
# channels: the files of the tests are isolated in a folder of their own
pytestmark = [
    pytest.mark.parallel,
]

ERROR_BUSY = 'is being generated or published by another run'


@pytest.fixture
def real_root(monkeypatch):
    """
    A real run directory for the tests of the lock.

    The lock is a SQLite database opened in the C layer of the interpreter,
    which the in-memory fake filesystem cannot serve: the exclusion tests need
    a real folder. It lives under the temp folder of the repository, in a folder
    of its own so two workers of the test suite never meet.

    Returns:
        PathStr: Absolute path of the run directory
    """
    root = PathStr.new(env.ALASIO_TEMP).joinpath(f'channel_lock-{secrets.token_hex(4)}')
    os.makedirs(root)
    monkeypatch.setattr(env, 'PROJECT_ROOT', root)
    yield root
    shutil.rmtree(root, ignore_errors=True)


def holder_thread(lock):
    """
    Hold a lock in a thread of its own, as another run would.

    Args:
        lock (ChannelLock): The lock to hold

    Returns:
        tuple[threading.Thread, threading.Event]: The thread and the event the
            test sets to let the thread release the lock
    """
    ready = threading.Event()
    release = threading.Event()

    def hold():
        with lock.locked():
            ready.set()
            release.wait(30)

    thread = threading.Thread(target=hold)
    thread.start()
    assert ready.wait(30)
    return thread, release


class TestLockName:
    """The file name of the lock of a channel."""

    def test_the_name_of_a_channel(self):
        """The lock is named after the workspace, with a suffix of its own."""
        assert lock_name(CHANNEL) == f'{CHANNEL}.lock'

    @pytest.mark.parametrize('workspace', [
        '',
        'a/b',
        'a\\b',
        'a:b',
        '.',
        '..',
        'CON',
        ' name',
    ])
    def test_an_unusable_name_is_refused(self, workspace):
        """A name that cannot be a file name of the publish folder is refused."""
        with pytest.raises(ValueError):
            lock_name(workspace)

    def test_a_name_at_the_file_name_limit_is_hashed(self):
        """A name of the full 255 bytes leaves no room for the '.lock' suffix.

        The workspace name is valid (validate_filename accepts it), the suffixed
        name is not, so the name is replaced by its digest.
        """
        workspace = 'a' * 255
        name = lock_name(workspace)
        assert name != f'{workspace}.lock'
        assert name.endswith('.lock')
        assert len(name.encode('utf-8')) <= 255

    def test_a_name_that_is_invalid_by_itself_is_still_refused(self):
        """A reserved name is not hidden by the digest fallback.

        'CON.lock' is not a usable file name, and neither is 'CON': the fallback
        is for a name the suffix made too long, never for a broken config.
        """
        with pytest.raises(ValueError):
            lock_name('CON')


class TestLockFile:
    """The lock file of a channel, in the run directory."""

    def test_the_lock_is_in_the_publish_folder(self, real_root):
        """The lock of a channel is {run}/publish/{workspace}.lock."""
        lock = ChannelLock(CHANNEL)
        assert str(lock.file) == str(
            real_root.joinpath(LOCK_FOLDER).joinpath(f'{CHANNEL}.lock'))

    def test_the_lock_is_outside_the_pack_folder(self, real_root):
        """The lock is not a file of the workspace.

        serve.py serves the whole pack folder over http, and the cleanup of the
        generator removes every entry of the workspace the run did not touch:
        a lock file in there would be served or removed under the lock.
        """
        lock = ChannelLock(CHANNEL)
        workspace = real_root.joinpath('pack').joinpath(CHANNEL)
        assert not str(lock.file).startswith(str(workspace))
        assert not str(lock.file).startswith(str(real_root.joinpath('pack')))

    def test_acquire_creates_the_folder_and_keeps_the_file(self, real_root):
        """The folder is created on the first acquire, the file survives the release."""
        folder = real_root.joinpath(LOCK_FOLDER)
        assert not folder.exists()
        lock = ChannelLock(CHANNEL)
        with lock.locked():
            assert lock.file.exists()
        assert lock.file.exists()
        assert not lock.is_locked


class TestLocked:
    """locked(), the synchronous form of the lock."""

    def test_acquire_and_release(self, real_root):
        """The lock is held inside the block, and only inside it."""
        lock = ChannelLock(CHANNEL)
        assert not lock.is_locked
        with lock.locked():
            assert lock.is_locked
            # a second handle of the channel sees the lock of the file
            assert ChannelLock(CHANNEL).is_locked
        assert not lock.is_locked

    def test_a_nested_acquire_is_a_reentrancy_error(self, real_root):
        """The lock is not reentrant: a second acquire of the holder raises."""
        lock = ChannelLock(CHANNEL)
        with lock.locked():
            with pytest.raises(RuntimeError, match='already held by this thread'):
                with lock.locked():
                    pass
            assert lock.is_locked
        assert not lock.is_locked

    def test_a_lock_of_another_thread_is_busy(self, real_root):
        """Another run holding the channel is reported, never waited for."""
        thread, release = holder_thread(ChannelLock(CHANNEL))
        try:
            with pytest.raises(ChannelBusy, match=ERROR_BUSY):
                with ChannelLock(CHANNEL, timeout=0).locked():
                    pass
        finally:
            release.set()
            thread.join(30)
        assert not ChannelLock(CHANNEL).is_locked

    def test_the_lock_is_released_when_the_block_raises(self, real_root):
        """A failed block does not keep the channel locked."""
        lock = ChannelLock(CHANNEL)
        with pytest.raises(RuntimeError, match='boom'):
            with lock.locked():
                raise RuntimeError('boom')
        assert not lock.is_locked


class TestAsyncLock:
    """alocked(), the form the publication uses."""

    @pytest.mark.trio
    async def test_acquire_and_release(self, real_root):
        """The lock is held inside the block, and only inside it."""
        lock = ChannelLock(CHANNEL)
        async with lock.alocked():
            assert lock.is_locked
        assert not lock.is_locked

    @pytest.mark.trio
    async def test_a_lock_of_another_thread_is_busy(self, real_root):
        """Another run holding the channel is reported, never waited for."""
        thread, release = holder_thread(ChannelLock(CHANNEL))
        try:
            with pytest.raises(ChannelBusy, match=ERROR_BUSY):
                async with ChannelLock(CHANNEL, timeout=0).alocked():
                    pass
        finally:
            release.set()
            thread.join(30)
        assert not ChannelLock(CHANNEL).is_locked

    @pytest.mark.trio
    async def test_a_cancelled_block_releases_the_lock(self, real_root):
        """A cancelled publication does not keep the channel locked."""
        lock = ChannelLock(CHANNEL)
        with trio.move_on_after(0.05):
            async with lock.alocked():
                await trio.sleep(10)
        assert not lock.is_locked

    @pytest.mark.trio
    async def test_the_lock_is_released_when_the_block_raises(self, real_root):
        """A failed block does not keep the channel locked."""
        lock = ChannelLock(CHANNEL)
        with pytest.raises(RuntimeError, match='boom'):
            async with lock.alocked():
                raise RuntimeError('boom')
        assert not lock.is_locked


class TestUploadRun:
    """The publication takes no lock of its own: the entry point holds it."""

    @pytest.mark.trio
    async def test_the_publisher_does_not_take_the_lock(self, workspace, credentials_env,
                                                        config):
        """A publisher inside a held channel lock never acquires it a second time.

        PackRun locks the channel once for the generation and the publication; a
        second acquire of the same thread is a reentrancy error, so the
        publication must leave the lock alone.
        """
        async with ChannelLock(CHANNEL).alocked():
            results = await UploadRun(config, client=credentials_env.client()).run()
        assert [result.status for result in results] == ['ok']
