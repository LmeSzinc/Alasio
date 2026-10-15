from contextlib import asynccontextmanager, contextmanager

import trio
from msgspec import Struct

from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.deploy.pack.job_base import DeployTarget
from alasio.deploy.pack.job_rebuild import RebuildJob
from alasio.deploy.pack.job_reset import ResetJob
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.deploy.pack.job_update import UpdateJob
from alasio.deploy.pack.server_file import LatestInfo
from alasio.ext.file.filelock import SQLiteFileLock
from alasio.ext.path.atomic import atomic_read_bytes
from alasio.logger import logger

# httpx2 is imported lazily in the places that use it: the package resolves
# its own version with importlib.metadata at import time, which the in-memory
# filesystem of the tests cannot answer


class DeployCheck(Struct):
    """
    Result of DeployJob.check(): the local version of the target and the
    latest one of its server.

    A local version of '' means the local index pack is missing or
    malformed: the version is unknown and every latest is an update
    (the caller treats the target as updatable).
    """
    # version of the local index pack, '' when it is missing or malformed
    local: str
    # latest info of the server, the snapshot the update flow converges to
    info: LatestInfo

    @property
    def latest(self):
        """
        Returns:
            str: Version of the latest index pack on the server
        """
        return self.info.version

    @property
    def checksum(self):
        """
        Returns:
            str: sha1 checksum of the latest index pack, hex string
        """
        return self.info.checksum

    def uptodate(self):
        """
        Check whether the target is up to date: a known local version
        equal to the latest one (a missing local index pack is never up
        to date, the target has to be rebuilt).

        Returns:
            bool: True when the local version is the latest one
        """
        return bool(self.local) and self.local == self.info.version


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

    Both entry points take the exclusive lock of the target ledger for the
    whole flow, see alocked(): two updaters of the same target never
    interleave, the second one waits (or fails at once with
    locked(timeout=0)). The server of the instance is handed to the job of
    the flow, the downloads of the job (the index pack, the failed records)
    use it.
    """

    def __init__(self, root=None, name='', server=None):
        """
        Args:
            root (str, optional): Folder to update. Defaults to None,
                env.PROJECT_ROOT
            name (str, optional): Ledger key of the target. Defaults to
                '', the project tree target
            server (ServerFile, optional): Server to check and download
                from, used by update(). Defaults to None.
        """
        super().__init__(root=root, name=name)
        self.server = server
        # whether the last flow of this instance changed real files
        # (writes, deletions or mode changes; a committed index alone
        # does not count): the startup round restarts the backend only
        # when the flow changed something, see update()
        self.changed = False
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

    async def _run_job(self, job):
        """
        Run one job of the flow and remember whether it changed files.

        The startup round restarts the backend only when the flow it ran
        changed real files (a repair that changed nothing needs no
        restart): every job records that in JobBase.changed, the flow
        collects it here for its caller, see update().

        Args:
            job (JobBase): Job to run

        Returns:
            bool: The result of job.run(), False when records stayed in
                error
        """
        result = await job.run()
        self.changed = job.changed
        return result

    async def unpack(self, data):
        """
        Unpack a full pack, unified wrapper of UnpackJob.

        Unpacking is a local rebuild: the leftover files of the old
        version (recorded in the old local index pack, not in this
        pack) are removed, the new index pack (index.pack in the ledger
        folder of the target) replaces the local one last. The unpack
        itself needs no server. The exclusive lock of the target ledger
        is held for the whole flow, see alocked(). The local phases run
        in worker threads, see JobBase.run().

        Args:
            data (bytes): Full pack data
        """
        async with self.alocked():
            # a flow is always a new one: the workspace a failed run left
            # (job.pack and its tmp files) is only a record and a cache,
            # the unpack re-derives everything from the new pack and
            # cleans the workspace up at the end
            job = UnpackJob(data, root=self.root, name=self.name)
            await self._run_job(job)

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

    async def check(self, version=''):
        """
        Check the local version of this target against the server of the
        instance (self.server, set in __init__):

            check = await DeployJob(server=server).check()
            check = await DeployJob(server=server).check(version='0.28.1')

        Read-only: the local version comes from the local index pack
        (index.pack in the ledger folder of the target), the target comes
        from latest.pack on the server. Nothing is written and no lock
        is taken - a check never conflicts with an update of the same
        target (the index pack of a target being updated is read
        atomically, it is never seen half written). The version pair is
        logged the same way the version step of update() logs it
        (CurrentVersion / LatestVersion), so every caller of the check
        shows the same pair. The network request awaits on the event
        loop, a cancelled task interrupts it immediately. The comparison
        is left to the caller, see DeployCheck.uptodate().

        When version is given, the target is verified additionally: the
        server must publish that version as its latest one (the dependency
        install of a mod passes the pin of its requirements file, see
        alasio.deploy.simple_pip.pip_pack). A server that publishes another
        version provides no valid update: the check is refused with a
        ValueError and nothing was changed.

        Args:
            version (str, optional): Version the server must publish as its
                latest, verified additionally, see the docstring. Defaults
                to '', no extra verification

        Returns:
            DeployCheck: The local version and the latest info of the
                server

        Raises:
            ValueError: If the target was created without a server, or the
                server does not publish the given version
            httpx2.HTTPError: If the request fails
            AllMirrorsFailedError: If no mirror is usable
        """
        if self.server is None:
            raise ValueError('Failed to check: no server provided')
        local = await trio.to_thread.run_sync(self._local_version)
        logger.attr('CurrentVersion', local)
        info = await self.server.get_latest_info()
        logger.attr('LatestVersion', info.version)
        if version and info.version != version:
            # the extra verification of a given version: the server must
            # publish it as its latest target, a server that publishes
            # another one provides no valid update
            raise ValueError(
                f'The server provides no valid update: latest is "{info.version}", '
                f'the requested version is "{version}"'
            )
        check = DeployCheck(local=local, info=info)
        # the verdict, one line either way: the update flow reads the same
        # pair from this same call
        if check.uptodate():
            logger.info(f'Already up to date: {local}')
        elif local:
            logger.info(f'Update available: {local} -> {info.version}')
        else:
            logger.info(f'Update available: no local version, latest is {info.version}')
        return check

    async def _job_phase(self, on_job_phase, phase):
        """
        [锁内] Report a phase of the flow, see update().

        Args:
            on_job_phase (callable, optional): The async callback of update()
            phase (str): 'checking' (the version check), 'downloading' (the
                update pack download) or 'updating' (the local changes)
        """
        if on_job_phase is None:
            return
        await on_job_phase(phase)

    async def _get_update_pack(self, old_version, new_version):
        """
        Get the update pack of the flow, reusing the kept one when the server
        confirms it.

        The workspace of an interrupted run keeps the update pack it
        downloaded (job.pack): the download is skipped when the kept pack is
        the pack of this version pair and the server still serves the very
        same file. The confirmation is the server's: the digest kept as the
        trailing 20 bytes of the pack (the sha1 of everything before it, see
        ServerFile.get_update_pack_checksum) must be the digest of the kept
        pack, and only those 20 bytes are transferred, not the body.

        The reuse cannot apply content the server does not serve: equal
        digests mean the two packs are byte-identical (the digest covers the
        whole file, a damaged or crafted file cannot match the server's). The
        local checks (_load_kept_pack) only tell that the kept file is a pack
        of this version pair worth asking about, they are never a trust
        anchor.

        Args:
            old_version (str): Version of the local files
            new_version (str): Version to update to

        Returns:
            tuple[PackDecodeBase, bool]: Decoder of the update pack, validated,
                and whether it was read back from the job file (the kept pack:
                the file already holds exactly these bytes, the caller keeps it
                as-is)

        Raises:
            httpx2.HTTPStatusError: If the request fails
        """
        import httpx2
        kept, checksum = await trio.to_thread.run_sync(
            self._load_kept_pack, old_version, new_version)
        if kept is not None:
            try:
                server_checksum = await self.server.get_update_pack_checksum(
                    old_version, new_version, len(kept.data))
            except httpx2.HTTPError as e:
                # the probe failed (a missing pack, a mirror that went down):
                # the download below reports the server's state as usual and
                # the caller falls back like any failed download
                logger.warning(f'Failed to read the checksum of the update pack '
                               f'{old_version} -> {new_version}: {e}, downloading it')
                server_checksum = ''
            if server_checksum == checksum:
                logger.info(f'Update pack unchanged, reusing the kept pack: '
                            f'{old_version} -> {new_version}')
                return kept, True
        return await self.server.get_update_pack(old_version, new_version), False

    def _load_kept_pack(self, old_version, new_version):
        """
        The update pack kept in the workspace when it is the one of this
        version pair, with the digest of its content.

        The kept pack is verified before it is offered for reuse (the
        checksums of both sections, the version pair): a truncated, damaged or
        foreign file is never offered, the pack is downloaded as usual. The
        digest returned is the trailing 20 bytes of the pack, proven by
        validate() to be the sha1 of every byte before it: whether the kept
        pack is still the one the server serves is decided by the server
        itself, see _get_update_pack.

        Args:
            old_version (str): Version of the local files
            new_version (str): Version to update to

        Returns:
            tuple[PackDecodeBase, str]: Decoder of the kept pack and its
                whole-pack checksum, hex string, (None, '') when there is
                nothing to reuse
        """
        try:
            data = atomic_read_bytes(self.job_file)
        except FileNotFoundError:
            return None, ''
        try:
            decoder = PackDecodeBase(data)
            decoder.validate()
        except PackDecodeError:
            return None, ''
        if decoder.old_version != old_version or decoder.current_version != new_version:
            return None, ''
        return decoder, data[-20:].hex()

    async def update(self, on_job_phase=None, version=''):
        """
        Update the local working tree of this target to the latest version
        of the server of the instance (self.server, set in __init__):

            await DeployJob(server=server).update()
            await DeployJob(server=server).update(update_manager)
            await DeployJob(server=server).update(version='0.28.1')

        A version given by the caller is an extra verification of the
        target: the server must publish it as its latest one, a server that
        publishes another version provides no valid update and the update
        is refused (check(version) raises). The dependency install of a mod
        passes the pin of its requirements file this way
        (alasio.deploy.simple_pip.pip_pack).

        The unified entry of the file check flow in the draft of
        PackEncodeBase

        1. the latest version and its index pack checksum are read
           from latest.pack by check(), the local version comes from the
           local index pack (index.pack in the ledger folder of the
           target); both are logged as in check()
        2. a version mismatch downloads the update pack
           /{new_version}/from_{old_version}.pack and applies it with
           UpdateJob, a missing update pack (out of the update window
           or removed) falls back to RebuildJob, a failed update also
           falls back to RebuildJob. The download reuses the pack kept in
           the workspace when the server still serves it: the trailing 20
           bytes digests of the two packs are compared and only they
           travel, see _get_update_pack
        3. the same version continues with ResetJob: the local index
           pack is checked against the latest checksum (an outdated
           self-consistent index is downloaded again), then every
           recorded file is verified and repaired

        The latest info fetched by check() is handed to the job created
        for the chosen path (the _latest_info attribute of the job is
        seeded with it): latest.pack is requested once per flow and the
        flow converges to this snapshot, a version published mid-flow
        is picked up by the next update.

        A missing or malformed local index pack has an unknown
        version, the update cannot be incremental: RebuildJob
        downloads the latest index unconditionally and rebuilds the
        working tree from it. The exclusive lock of the target ledger is
        held for the whole flow, see alocked():
        the whole download and replace flow of one caller is one
        critical section (a second updater of the same target waits,
        see locked()) - a caller must never split the flow to hold the
        lock around its parts. The network requests await on the event
        loop (a cancelled task interrupts them immediately), the local
        phases run in worker threads, see JobBase.run().

        Args:
            on_job_phase (callable, optional): Async callback of the update
                transaction, called before each phase of the flow with
                'checking' (the version check), 'downloading' (the update pack
                download) or 'updating' (the local changes start). The callback
                only reports: the caller stops the flow by cancelling the task
                running it (the backend transaction cancels it from the
                cancellation window of its flow). A cancelled task interrupts
                the request in flight and starts no further phase (the version
                check and the download await on the event loop, every local
                phase opens with a checkpoint), no real file is touched and the
                temporary files are kept for the next run, see the class
                docstring. Defaults to None, the whole flow runs without a
                callback (the CLI)
            version (str, optional): Version the server must publish as its
                latest, verified additionally by the check, see the
                docstring (the dependency install of a mod passes the pin
                of its requirements file). Defaults to '', no extra
                verification

        Returns:
            bool: True if every file is up to date, False if some
                records stay in error

        Raises:
            ValueError: If the target was created without a server, or the
                server does not publish the given version
        """
        import httpx2
        if self.server is None:
            raise ValueError('Failed to update: no server provided')
        async with self.alocked():
            # the version check opens the flow: its phase is reported before
            # the first request, see _job_phase
            await self._job_phase(on_job_phase, 'checking')

            check = await self.check(version)
            local = check.local
            info = check.info

            if local and local == info.version:
                # the same version: check the index and the files, the local
                # repair path (nothing to download, no version to compare)
                await self._job_phase(on_job_phase, 'updating')
                job = ResetJob(self.server, root=self.root, name=self.name)
                job._latest_info = info
                return await self._run_job(job)
            if not local:
                # the local index is missing or malformed, the version is
                # unknown: rebuild from the latest index. There is no version
                # to compare and no update pack to download, the rebuild is one
                # apply (its own index download happens inside it)
                logger.warning('Failed to read the local version, rebuilding from the latest index')
                await self._job_phase(on_job_phase, 'updating')
                job = RebuildJob(self.server, root=self.root, name=self.name)
                job._latest_info = info
                return await self._run_job(job)

            # the main branch: the version mismatches, apply the update pack
            # incrementally. The download has its own phase (the transaction
            # stays cancellable and shows the download), the local changes come
            # after it; the download reuses the kept pack of this version pair
            # when the server still serves it, see _get_update_pack
            await self._job_phase(on_job_phase, 'downloading')
            try:
                decoder, kept = await self._get_update_pack(local, info.version)
            except (httpx2.HTTPStatusError, PackDecodeError) as e:
                # the update pack of the local version is not on the server (out
                # of the update window or removed), or the server serves a
                # broken one (the client validates it, see
                # ServerFile.get_update_pack): the incremental path is broken,
                # rebuild from the latest index
                logger.warning(
                    f'Failed to get the update pack {local} -> {info.version}: {e}, '
                    f'rebuilding from the latest index'
                )
                await self._job_phase(on_job_phase, 'updating')
                job = RebuildJob(self.server, root=self.root, name=self.name)
                job._latest_info = info
                return await self._run_job(job)
            # the download is over, the local changes start: the workers are
            # stopped before them (the manager phase)
            await self._job_phase(on_job_phase, 'updating')
            job = UpdateJob(decoder, server=self.server, root=self.root, name=self.name)
            # the pack came back from the job file (the server confirmed the
            # kept one): the file already holds it, write() keeps it as-is
            job.job_file_kept = kept
            if await self._run_job(job):
                return True
            # the update pack failed to apply, rebuild from the latest index: a
            # corrupt pack is bypassed, the latest index and the files are
            # downloaded directly
            logger.warning('Failed to apply the update pack, rebuilding from the latest index')
            job = RebuildJob(self.server, root=self.root, name=self.name)
            job._latest_info = info
            return await self._run_job(job)
