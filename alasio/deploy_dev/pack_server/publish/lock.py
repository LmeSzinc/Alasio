"""
The exclusive lock of one channel: one run generates and publishes a workspace
at a time.

The generation of a channel rewrites the whole workspace -- the packs, the
wheels, `packrepo/latest.pack` and `manifest.json` -- and removes the files the
run no longer serves, while the publication reads that same tree, digests it and
uploads it. The two must not overlap: an uploader that reads a workspace halfway
through its replacement publishes the record of one generation with the content
of another, or a barrier and a manifest that do not name the same version.

The lock is a SQLite file lock on ``{run}/publish/{workspace}.lock``. The folder
is outside the workspace on purpose:

- ``serve.py`` mounts the whole ``{run}/pack`` folder as static files, so a lock
  file inside it would be served over http like a pack;
- ``RepoBase.cleanup()`` removes every entry of the workspace the run did not
  touch, the lock file included, and a deleted lock file breaks the exclusion
  (see alasio.ext.file.filelock).

One lock per channel, not one per run: the channels of a run share neither the
workspace nor the git clone, and a run-level lock would serialise publications
that do not meet.

The lock is held once, by the entry point that runs the whole channel, from the
clone to the last upload of the last remote:

- ``PackRun.run_config()`` holds it across the generation and the publication;
- ``PackServer.run_config()`` holds it across the generation when the generate
  only entry runs on its own.

The three stages of a channel — generate, publish, retire — are one run under
one lock: the entry point that chains them takes the lock once around the whole
sequence, and the stages it calls take none. The retirement is a step of the
publication (`ChannelUpload._retire`), not an entry of its own, so there is no
second place that could take the lock and no sequence to interleave with.

The steps inside that boundary take no lock of their own: ``UploadRun`` is a
publisher, not an entry point, and a nested acquire of the holding thread is an
error (the lock is not reentrant, see alasio.deploy.pack.job.DeployJob for the
same rule). A caller that publishes a workspace on its own wraps the call in a
``ChannelLock`` itself, so the channel is locked once, around both halves when
both are run.

A channel that is busy is not a failure of the run: the holder is generating or
publishing the same workspace, and the scheduler retries the channel on its next
round. ``ChannelBusy`` says so, and the other channels still run.

The lock is local to the run directory. It serialises the processes of one
machine that share the directory; two machines that publish the same bucket from
two copies of the run directory do not meet in it, and nothing here can make
them, because a network file system does not guarantee an SQLite lock.
"""

import functools
import hashlib
import threading
from contextlib import asynccontextmanager, contextmanager

import trio

from alasio.deploy_dev.pack_server.publish.common import PublishError
from alasio.ext import env
from alasio.ext.file.filelock import FilelockTimeout, SQLiteFileLock
from alasio.ext.path import PathStr
from alasio.ext.path.validate import validate_filename
from alasio.logger import logger

# the folder of the locks in the run directory, see the module docstring
LOCK_FOLDER = 'publish'

# seconds a run waits for the lock of a channel. A scheduler retries the
# channel on its next round, so a run never queues behind a publication of
# minutes, and a busy channel is reported instead of silently delaying the
# other channels
LOCK_TIMEOUT = 0


class ChannelBusy(PublishError):
    """
    Raised when another run holds the lock of the channel.

    A busy channel is not a failure of the publication: the holder is
    generating or publishing the same workspace, and the scheduler retries the
    channel on its next round. It is a PublishError so a caller that handles
    the failures of the flow sees it with them.
    """


def lock_name(workspace):
    """
    The file name of the lock of a channel.

    The name of the workspace is a single path component (see
    PackRepoModel.workspace_name), so the lock is named after it with a '.lock'
    suffix, and the file name is validated as a whole: the suffix may be the
    reason a name stops being usable. A workspace name long enough that its
    suffix no longer fits is named by the digest of the name instead, which is
    still one file per channel; a workspace name that is not usable on its own
    is refused, because the digest would hide the broken config.

    Args:
        workspace (str): Name of the workspace, e.g. 'LmeSzinc_Repo_master'

    Returns:
        str: File name of the lock, e.g. 'LmeSzinc_Repo_master.lock'

    Raises:
        ValueError: If the workspace name is not a single safe path component
    """
    validate_filename(workspace)
    name = f'{workspace}.lock'
    try:
        validate_filename(name)
        return name
    except ValueError:
        # the suffix is the only part that may be wrong: a workspace name that
        # is not usable by itself was refused above
        return f'{hashlib.sha1(workspace.encode("utf-8")).hexdigest()[:32]}.lock'


class ChannelLock:
    """
    The exclusive lock of one channel, see the module docstring.

    One lock per workspace, released by the operating system when the process
    exits (a crashed run never leaves a stale lock). The lock file itself is
    never deleted: deleting it would let a second process lock a new file while
    the first one still holds the old one.

    Usage:
        lock = ChannelLock('LmeSzinc_Repo_master')
        async with lock.alocked():
            ...                                    # generate and publish

        with lock.locked():
            ...                                    # a synchronous caller

    Attributes:
        workspace (str): Name of the workspace the lock belongs to
        timeout (float): Seconds an acquire waits for the holder
        file (PathStr): Path of the lock file
    """

    def __init__(self, workspace, timeout=LOCK_TIMEOUT):
        """
        Args:
            workspace (str): Name of the workspace, a single path component
            timeout (float): Seconds an acquire waits for the holder. Defaults
                to LOCK_TIMEOUT, which fails at once with ChannelBusy. A
                negative value waits indefinitely

        Raises:
            ValueError: If the workspace name is not a single safe path
                component
        """
        self.workspace = workspace
        self.timeout = timeout
        self.file = PathStr.new(env.PROJECT_ROOT).joinpath(LOCK_FOLDER).joinpath(
            lock_name(workspace))
        # the lock of the file is shared by every handle of it in the process,
        # see SQLiteFileLock
        self._lock = SQLiteFileLock(self.file, timeout=timeout)

    @property
    def is_locked(self):
        """
        Whether a thread of this process holds the lock of the channel.

        The state belongs to the lock file, not to this handle: a handle built
        while another one holds the channel answers True.

        Returns:
            bool: True when the channel is locked
        """
        return self._lock.is_locked

    def _busy(self):
        """
        The message of a channel that is held elsewhere.

        Returns:
            str: The reason, with the lock file for a person who has to find
        """
        return (
            f'Channel "{self.workspace}" is being generated or published by '
            f'another run, lock="{self.file}"')

    @contextmanager
    def locked(self):
        """
        Hold the lock for a synchronous block.

        Yields:
            ChannelLock: This lock

        Raises:
            ChannelBusy: If the lock is held elsewhere and the wait is over
            RuntimeError: If the calling thread already holds the lock, a
                reentrancy that would wait for itself
        """
        try:
            self._lock.acquire(timeout=self.timeout)
        except FilelockTimeout:
            raise ChannelBusy(self._busy()) from None
        logger.debug(f'Channel "{self.workspace}" locked, "{self.file}"')
        try:
            yield self
        finally:
            self._lock.release()

    @asynccontextmanager
    async def alocked(self):
        """
        Hold the lock for an async block, the form the publication uses.

        The wait runs in a worker thread: the SQLite busy handler blocks, and
        the event loop of the caller has the transfers of other channels to
        run. The owner of the lock is the event loop thread, the thread that
        releases in the finally below, and the acquire is shielded so a
        cancelled block never leaves the lock held: the acquisition completes,
        the block is entered, and the cancellation is delivered inside it. The
        wait is bounded by the timeout of the channel (0 by default, so the
        acquire is immediate); a negative timeout waits indefinitely and is
        not interruptible until the holder releases, the same rule as
        DeployJob.alocked().

        Yields:
            ChannelLock: This lock

        Raises:
            ChannelBusy: If the lock is held elsewhere and the wait is over
            RuntimeError: If the event loop thread already holds the lock, a
                reentrancy that would wait for itself
        """
        owner = threading.get_ident()
        with trio.CancelScope(shield=True):
            try:
                await trio.to_thread.run_sync(
                    functools.partial(self._lock.acquire, self.timeout, owner=owner))
            except FilelockTimeout:
                raise ChannelBusy(self._busy()) from None
        logger.debug(f'Channel "{self.workspace}" locked, "{self.file}"')
        try:
            yield self
        finally:
            self._lock.release()
