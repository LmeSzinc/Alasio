from contextlib import asynccontextmanager, contextmanager

import httpx2
import trio

from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.deploy.pack.job_base import DeployTarget
from alasio.deploy.pack.job_rebuild import RebuildJob
from alasio.deploy.pack.job_reset import ResetJob
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.deploy.pack.job_update import UpdateJob
from alasio.ext.file.filelock import SQLiteFileLock
from alasio.ext.path.atomic import atomic_read_bytes, atomic_rmtree
from alasio.logger import logger


class DeployJob(DeployTarget):
    """
    Unified entry of deploy jobs, one instance per deploy target.

    A target is a folder being updated and a ledger key (see
    DeployTarget):

        DeployJob()                                # env.PROJECT_ROOT, name=''
        DeployJob(name='httpx')                    # {root}/.pack/httpx
        DeployJob(root=site_packages, name='httpx')

    DeployJob() is the project tree target and behaves like the old
    classmethods: the ledger folder is {root}/.pack. A named target
    shares its root with other targets, e.g. the python dists of a
    site-packages folder, and keeps its ledger folder in
    {root}/.pack/{name}. The server to update from is an instance
    attribute too, it is set once in __init__:

        await DeployJob().unpack(data)             # no server needed
        await DeployJob(name='httpx', server=server).update()

    The unfinished job is finished inside unpack() and update(), the
    caller does not need to care about it; the server of the instance
    is handed to it, so a resumed job downloads its missing files
    from it when set. Both take the exclusive lock of the target
    ledger for the whole flow, see alocked(): two updaters of the same
    target never interleave, the second one waits (or fails at once
    with locked(timeout=0)).
    """

    def __init__(self, root=None, name='', server=None):
        """
        Args:
            root (str, optional): Folder to update. Defaults to None,
                env.PROJECT_ROOT
            name (str, optional): Ledger key of the target. Defaults to
                '', the project tree target
            server (ServerFile, optional): Server to check and download
                from, used by update() and handed to the unfinished job
                that unpack() / update() finish. Defaults to None.
        """
        super().__init__(root=root, name=name)
        self.server = server
        # the exclusive lock of the target ledger, held by update() and
        # unpack() for their whole flow, see locked(). The wait default
        # is the lock default: timeout=-1 waits forever, 0 fails at once
        self.lock = SQLiteFileLock(self.lock_file)

    @contextmanager
    def locked(self, timeout=None):
        """
        Hold the exclusive lock of the target ledger for a block.

        The lock is a SQLite file lock on the lock file of the ledger
        folder ({ledger}/lock): exclusive across processes and threads,
        released by the operating system when the process exits, so a
        crashed updater never leaves a stale lock. The lock file itself
        is never deleted, deleting it would break the exclusion. The
        acquire is re-entrant for this instance: a block nested in one
        the instance already holds (update() and unpack() hold the lock
        for their whole flow) counts up instead of deadlocking.

        The caller does not need the lock for update() and unpack(); it
        is taken explicitly to put several calls of one instance into
        one critical section that another updater cannot interleave:

            with deploy.locked(timeout=0):
                await deploy.update()

        Args:
            timeout (float, optional): Seconds to wait for the lock,
                overrides the wait default of the lock. Defaults to
                None, the default of the lock (-1 waits forever).

        Yields:
            SQLiteFileLock: The lock object

        Raises:
            FilelockTimeout: If the lock is held elsewhere and the wait
                is over, timeout=0 fails at once
        """
        self.lock.acquire(timeout=timeout)
        try:
            yield self.lock
        finally:
            self.lock.release()

    @asynccontextmanager
    async def alocked(self, timeout=None):
        """
        Hold the exclusive lock of the target ledger for an async
        block, the async form of locked() used by update() and
        unpack().

        The acquire blocks inside the sqlite layer while the lock is
        held elsewhere (a wait bounded by the timeout), so it runs in
        a worker thread and never stalls the event loop; the release
        is a quick rollback and close of the sqlite connection and
        stays inline, so it still runs when the block is unwound by a
        cancellation.

        Args:
            timeout (float, optional): Seconds to wait for the lock,
                overrides the wait default of the lock. Defaults to
                None, the default of the lock (-1 waits forever).

        Yields:
            SQLiteFileLock: The lock object

        Raises:
            FilelockTimeout: If the lock is held elsewhere and the wait
                is over, timeout=0 fails at once
        """
        await trio.to_thread.run_sync(self.lock.acquire, timeout)
        try:
            yield self.lock
        finally:
            self.lock.release()

    def _get_unfinished_job(self):
        """
        Check if there is an unfinished job of this target, read it
        and create the job object of the corresponding type.

        The job type is decided by the job file content: the REST
        marker is a validation job (ResetJob), the RBIL marker is a
        rebuild job (RebuildJob), a pack with a non-empty old version
        is an update pack (UpdateJob), a pack without one (empty old
        version) is a full pack (UnpackJob). A corrupted job file is
        cleaned up with a warning.

        The lock of the target is not taken here: the returned job
        mutates the working tree, run it under locked() (update() and
        unpack() take the lock around their whole flow). The server of
        the instance (self.server) is handed to the returned job: a
        resumed job downloads its missing files from it when set,
        None when the target was created without a server.

        Returns:
            JobBase: The unfinished job, or None if there is no
                unfinished job
        """
        try:
            data = atomic_read_bytes(self.job_file)
        except FileNotFoundError:
            return None
        if data == ResetJob.MARK:
            # a validation job, its data comes from the local index pack
            return ResetJob(self.server, resume=True, root=self.root, name=self.name)
        if data == RebuildJob.MARK:
            # a rebuild job, its data comes from the local index pack
            return RebuildJob(self.server, resume=True, root=self.root, name=self.name)
        try:
            decoder = PackDecodeBase(data)
        except PackDecodeError as e:
            # the job file is corrupted, clean it up
            logger.warning(f'Failed to read the unfinished job: {e}')
            atomic_rmtree(self.workspace)
            return None
        # the version part tells the job type: a non-empty old version
        # means an update pack, an empty one a full pack
        if decoder.old_version:
            # an update pack, resume the update job
            return UpdateJob(data, server=self.server, resume=True, root=self.root, name=self.name)
        return UnpackJob(data, resume=True, root=self.root, name=self.name)

    async def unpack(self, data):
        """
        Unpack a full pack, unified wrapper of UnpackJob.

        Unpacking is a local rebuild: the leftover files of the old
        version (recorded in the old local index pack, not in this
        pack) are removed, the new index pack (index.pack in the ledger
        folder of the target) replaces the local one last. The unpack
        itself needs no server: the server of the instance
        (self.server) is only handed to the unfinished job finished
        first, a resumed job downloads its missing files from it when
        set. Finishes the unfinished job first, then unpacks the new
        data. The exclusive lock of the target ledger is held for the
        whole flow, see alocked(). The local phases run in worker
        threads, see JobBase.run().

        Args:
            data (bytes): Full pack data
        """
        async with self.alocked():
            # finish the unfinished job first, its run() skips write()
            job = await trio.to_thread.run_sync(self._get_unfinished_job)
            if job is not None:
                logger.info(f'Found unfinished job: {job.__class__}')
                await job.run()
            # unpack the new data
            await UnpackJob(data, root=self.root, name=self.name).run()

    def _local_version(self):
        """
        The version of the local index pack (index.pack in the ledger
        folder of the target).

        Returns:
            str: The version of the local index pack, '' when it is
                missing or malformed
        """
        try:
            decoder = PackDecodeBase(atomic_read_bytes(self.index_file))
        except (FileNotFoundError, PackDecodeError):
            return ''
        return decoder.current_version

    async def update(self):
        """
        Check the latest version on the server of the instance
        (self.server, set in __init__) and update the local working
        tree of this target to it:

            await DeployJob(server=server).update()

        The unified entry of the file check flow in the draft of
        PackEncodeBase:

        1. the latest version and its index pack checksum are read
           from latest.pack, the local version comes from the local
           index pack (index.pack in the ledger folder of the target)
        2. a version mismatch downloads the update pack
           /{new_version}/from_{old_version}.pack and applies it with
           UpdateJob, a missing update pack (out of the update window
           or removed) falls back to RebuildJob, a failed update also
           falls back to RebuildJob
        3. the same version continues with ResetJob: the local index
           pack is checked against the latest checksum (an outdated
           self-consistent index is downloaded again), then every
           recorded file is verified and repaired

        The latest info fetched here is handed to the job created for
        the chosen path (the _latest_info attribute of the job is
        seeded with it): latest.pack is requested once per flow and
        the flow converges to this snapshot, a version published
        mid-flow is picked up by the next update.

        A missing or malformed local index pack has an unknown
        version, the update cannot be incremental: RebuildJob
        downloads the latest index unconditionally and rebuilds the
        working tree from it. The unfinished job is finished inside,
        the caller does not need to care about it. The exclusive lock
        of the target ledger is held for the whole flow, see alocked().
        The network requests await on the event loop (a cancelled task
        interrupts them immediately), the local phases run in worker
        threads, see JobBase.run().

        Returns:
            bool: True if every file is up to date, False if some
                records stay in error

        Raises:
            ValueError: If the target was created without a server
        """
        if self.server is None:
            raise ValueError('Failed to update: no server provided')
        async with self.alocked():
            # finish the unfinished job first, its run() skips write()
            job = await trio.to_thread.run_sync(self._get_unfinished_job)
            if job is not None:
                logger.info(f'Found unfinished job: {job.__class__}')
                await job.run()

            local = await trio.to_thread.run_sync(self._local_version)
            logger.attr('CurrentVersion', local)
            info = await self.server.get_latest_info()
            logger.attr('LatestVersion', info.version)

            if not local:
                # the local index is missing or malformed, the version is
                # unknown: rebuild from the latest index
                logger.warning('Failed to read the local version, rebuilding from the latest index')
                job = RebuildJob(self.server, root=self.root, name=self.name)
                job._latest_info = info
                return await job.run()
            if local != info.version:
                # a version mismatch, apply the update pack incrementally
                try:
                    data = await self.server.get_update_pack(local, info.version)
                except httpx2.HTTPStatusError as e:
                    # the update pack of the local version is not on the
                    # server (out of the update window or removed), the
                    # incremental path is broken: rebuild from the latest index
                    logger.warning(
                        f'Failed to get the update pack {local} -> {info.version}: {e}, '
                        f'rebuilding from the latest index'
                    )
                    job = RebuildJob(self.server, root=self.root, name=self.name)
                    job._latest_info = info
                    return await job.run()
                job = UpdateJob(data, server=self.server, root=self.root, name=self.name)
                if await job.run():
                    return True
                # the update pack failed to apply, rebuild from the latest
                # index: a corrupt pack is bypassed, the latest index and
                # the files are downloaded directly
                logger.warning('Failed to apply the update pack, rebuilding from the latest index')
                job = RebuildJob(self.server, root=self.root, name=self.name)
                job._latest_info = info
                return await job.run()
            # the same version, check the index and the files
            job = ResetJob(self.server, root=self.root, name=self.name)
            job._latest_info = info
            return await job.run()

    async def run_unfinished_job(self):
        """
        Finish the unfinished job of this target, if any.

        The unfinished job is the one recorded in the job file of the
        target ({ledger}/workspace/job.pack, see _get_unfinished_job):
        it is written before any real file is changed and cleaned up
        when the run ends, so only a process killed in flight leaves
        it behind. Called at the backend startup, before the first
        update check: an update interrupted mid-apply is finished here
        instead of waiting for the next user click.

        The found job is resumed (its missing pieces are downloaded
        from the server of the instance, self.server set in __init__)
        and every change is applied in one pass. A job that does not
        finish falls back to a rebuild from the latest index, the
        fallback of update(), so a pack corrupted in flight still
        converges.

        The exclusive lock of the target ledger is held for the whole
        flow, see alocked().

        Returns:
            bool: True when an unfinished job was found and completed
                (a backend restart is needed to load the new files),
                False when there was no unfinished job (nothing is
                done)

        Raises:
            ValueError: When a job was found, did not finish, and the
                target has no server for the fallback rebuild
            RuntimeError: When a job was found and neither the job nor
                the fallback rebuild completed; the target may be in a
                partial state, a later run continues from there
        """
        async with self.alocked():
            job = await trio.to_thread.run_sync(self._get_unfinished_job)
            if job is None:
                return False
            logger.info(f'Found unfinished job: {job.__class__}')
            if await job.run():
                return True
            # the job did not finish (records left in error, a pack
            # corrupted in flight): fall back to a rebuild from the
            # latest index, the fallback of the update flow
            if self.server is None:
                raise ValueError(
                    'Failed to finish the unfinished job: no server for the fallback rebuild'
                )
            logger.warning('Failed to finish the unfinished job, rebuilding from the latest index')
            rebuild = RebuildJob(self.server, root=self.root, name=self.name)
            if await rebuild.run():
                return True
            raise RuntimeError(
                'Failed to finish the unfinished job: the rebuild from the latest index did not complete'
            )
