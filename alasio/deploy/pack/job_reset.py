import trio

from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.deploy.pack.job_base import JobBase, PendingFile
from alasio.deploy.pack.pack_model import IdxInfo
from alasio.ext.cache import InstanceCacheOperation, cached_property
from alasio.ext.path.atomic import atomic_read_bytes, file_write
from alasio.logger import logger

# httpx2 is imported lazily in the places that use it: the package resolves
# its own version with importlib.metadata at import time, which the in-memory
# filesystem of the tests cannot answer


class ResetJob(JobBase):
    """
    A local file validation and repair task, interruptible and
    resumable.

    The job writes the marker to the job file
    ({ledger}/workspace/job.pack) with write() before validating, so
    an interrupted run can be resumed by the next run:

        job = DeployJob(server=server)._get_unfinished_job()
        if job is None:
            job = ResetJob(server)
        await job.run()

    The local index pack (index.pack in the ledger folder of the
    target) is read once and cached.
    validate_index() checks the index pack itself, a failed index pack
    is prepared again from the server (download_index()). Then
    validate_latest() compares the local index pack checksum with the
    latest index pack checksum of the server, an outdated (self-
    consistent but not the latest) index pack is prepared again too.
    The new index pack is downloaded to the workspace new_index.tmp
    instead of replacing the local index directly, replace() applies
    it together with the repaired files, so the real files are
    touched only once. Then validate_files() checks every file
    recorded in the index, failed files are downloaded to tmp files
    by download() and replaced to the real files by replace(). Files
    that cannot be downloaded or fail the size + sha1 check stay in
    self.error with an empty tmp, this is an unsolvable problem per
    the draft of PackEncodeBase.

    Note: the exclusive lock of the target ledger (the lock file, see
    DeployJob.locked()) is held by DeployJob around the whole update
    flow (full pack, update pack and file check); a direct user of
    this job must take it itself.
    """

    # marker of a validation task in the job file
    MARK = b'REST\x00'

    def __init__(self, server, resume=False, root=None, name=''):
        """
        Args:
            server (ServerFile): Server to download the index pack and
                the failed files
            resume (bool): True if the job was resumed from the job
                file, run() does not write the job file again then
            root (str, optional): Folder to update. Defaults to None,
                env.PROJECT_ROOT
            name (str, optional): Ledger key of the target. Defaults to
                '', the project tree target
        """
        super().__init__(b'', root=root, name=name)
        self.server = server
        self._resume = resume
        self.error: "list[PendingFile]" = []
        # latest.pack of the flow: fetched once per job by
        # _get_latest_info(), or seeded by DeployJob.update() with the
        # snapshot it fetched for the flow (the server is not requested
        # again then). None until then.
        self._latest_info = None

    async def run(self):
        """
        Execute the full reset flow.

        Writes the job marker first unless the job was resumed from it,
        then validates and repairs: a failed index pack is downloaded
        again from the server, an outdated index pack (self-consistent
        but not the latest, see validate_latest()) is downloaded again
        too, failed files are downloaded to tmp files and replaced to
        the real files. The network phases await on the event loop, the
        local phases run in a worker thread (see JobBase.run()). On
        failure the workspace is cleaned up: errors during write() and
        validate() are safe and are logged as warning.

        Returns:
            bool: True if every file is repaired, False otherwise
        """
        try:
            if not self._resume:
                await trio.to_thread.run_sync(self.write)
            logger.info(f'Resetting files to "{self.root}", name="{self.name}"')
            if not await trio.to_thread.run_sync(self.validate_index):
                # the index pack is broken, download it again
                await self.download_index()
            elif not await self.validate_latest():
                # the index pack is self-consistent but outdated,
                # download the latest index pack
                await self.download_index()
            await trio.to_thread.run_sync(self.validate_files)
            await self.download()
            # the new index records every file of the new version, the
            # emptiness base of replace()
            self.new_fileinfo = self._index_pack.fileinfo
            await trio.to_thread.run_sync(self.replace)
        except Exception as e:
            # no real file was written, safe to clean up
            logger.warning(f'Failed to reset: {e}')
            await trio.to_thread.run_sync(self.cleanup)
            return False
        # the job is finished, clean the workspace atomically
        await trio.to_thread.run_sync(self.cleanup)
        logger.info(f'Reset done')
        return not self.error

    def write(self):
        """
        Write the job marker to the job file, marking a validation task
        in progress, so that a future run can resume from it if this
        run gets interrupted.

        The job file lives in the workspace, a corrupted one is
        detected by _get_unfinished_job() on the next run, so a plain
        write is enough.
        """
        file_write(self.job_file, self.MARK)

    @cached_property
    def _index_pack(self):
        """
        The local index pack, read and decoded once per job.

        The index is read from the ledger folder of the target and
        rewritten to its local namespace, see JobBase.localize().
        validate_index() and validate_files() share this decoder, so
        the file is read only once.

        Returns:
            PackDecodeBase: Decoder of the local index pack

        Raises:
            FileNotFoundError: If the index pack does not exist
            PackDecodeError: If the index pack is malformed
        """
        data = atomic_read_bytes(self.index_file)
        return self.localize(PackDecodeBase(data))

    async def _get_latest_info(self):
        """
        The latest version and index pack checksum from the server.

        Fetched once per job and shared by validate_latest() and
        download_index(), so the latest info is requested only once
        even when both the local index and the workspace tmp file are
        checked. DeployJob.update() hands the snapshot it fetched for
        the flow in (the attribute is seeded before the job runs), the
        server is not requested again then. The fetch is a network
        request on the event loop like every ServerFile call: a
        cancelled task interrupts it immediately.

        Returns:
            LatestInfo: Latest version and index pack checksum

        Raises:
            PackDecodeError: If the server is missing
        """
        if self._latest_info is not None:
            return self._latest_info
        server = self.server
        if server is None:
            raise PackDecodeError('Failed to validate the latest index: no server provided')
        self._latest_info = await server.get_latest_info()
        return self._latest_info

    def validate_index(self):
        """
        Validate the local index pack (index.pack in the ledger folder
        of the target) itself.

        The index pack must exist, decode and pass its checksum,
        otherwise the files recorded in it cannot be trusted. A failed
        index pack is repaired by download_index(), which differs from
        repairing the failed files.

        Returns:
            bool: True if the index pack is valid
        """
        try:
            self._index_pack.validate_index()
            return True
        except (FileNotFoundError, PackDecodeError) as e:
            logger.warning(f'Failed to validate the index pack: {e}')
            return False

    async def validate_latest(self):
        """
        Check the local index pack against the latest index pack of
        the server.

        The local index pack must be self-consistent first (see
        validate_index): a self-consistent but outdated index pack
        passes its own checksum and is only detected by comparing its
        checksum with the checksum of the latest index pack recorded
        in latest.pack (fetched once per job, see _get_latest_info()).
        The comparison uses the checksum of the pack format itself:
        the trailing 20 bytes of the index section, the same digest
        validate_index() verifies, not a checksum of the whole index
        pack file. A mismatch means the local index is not the latest
        one, the caller repairs it with download_index().

        Returns:
            bool: True if the local index pack is the latest one

        Raises:
            PackDecodeError: If the server is missing
        """
        info = await self._get_latest_info()
        # the checksum of the pack format: the trailing 20 bytes of
        # the index section, kept in the decoder cache
        local = self._index_pack.index_checksum
        if local == info.checksum:
            return True
        logger.warning(
            f'Failed to validate the latest index: local checksum {local} != {info.checksum}'
        )
        return False

    def validate_files(self):
        """
        Validate every file recorded in the local index pack.

        The caller must validate the index pack itself first with
        validate_index(): a failed index pack is repaired differently
        from failed files, so validate_files() does not check it and
        assumes the records are trustworthy. Each record is compared
        against the file at its path: size and sha1 must match (line
        endings are normalized like unpack), the file mode must match
        the record, a deleted marker expects the file to not exist.
        A file whose content matches only after converting its EOL to
        the record EOL is written to a tmp file with the converted
        content and recorded with the tmp set, download() moves it to
        pending without a download. A file whose content matches but
        whose mode differs is written to a tmp file with the current
        content, replace() chmod-ed the target to the record mode,
        no download is needed either. Other failed files are
        collected in self.error with an empty tmp, the caller repairs
        them.

        Returns:
            bool: True if every file matches its record, False
                otherwise
        """
        self.error = []
        for path, info in self._index_pack.fileinfo.items():
            current = self._read_current(self.root.joinpath(path))
            if info.edit == 2:
                # deleted marker, the file should not exist
                if current.exist:
                    # the file should be removed by the caller
                    self.error.append(PendingFile(info=info, tmp=''))
                continue
            result = self._matches(info, current)
            if result.match:
                if result.mode_matched:
                    continue
                # only the mode differs, the content is verified:
                # write the current content to a tmp file, download()
                # moves it to pending without a download, replace()
                # chmod-ed the target to the record mode
                # the tmp name is built from the index of the record
                # in self.error, matching the download() convention
                tmp = self.workspace.joinpath(
                    f'{info.size}_{info.sha1.hex()}_{len(self.error)}.tmp')
                if not self._matches(info, self._read_current(tmp)).match:
                    file_write(tmp, current.data)
                self.error.append(PendingFile(info=info, tmp=tmp, mode=info.mode_decoded))
                continue
            if result.match_data:
                # only the EOL differs, write the converted content
                # to a tmp file, download() moves it to pending
                # the tmp name is built from the index of the record
                # in self.error, matching the download() convention
                tmp = self.workspace.joinpath(f'{info.size}_{info.sha1.hex()}_{len(self.error)}.tmp')
                file_write(tmp, result.match_data)
                self.error.append(PendingFile(
                    info=info, tmp=tmp, mode=info.mode_decoded if info.mode == 1 else None))
                continue
            # missing or wrong size + sha1, the file is rewritten
            # by python with the default mode 666
            self.error.append(PendingFile(
                info=info, tmp='', mode=info.mode_decoded if info.mode == 1 else None))
        return not self.error

    async def download_index(self):
        """
        Prepare the new index pack of the latest version in the
        workspace.

        The index pack is downloaded to {ledger}/workspace/new_index.tmp
        instead of replacing the local index pack directly:
        replace() applies it together with the repaired files, so the
        real files are touched only once. A leftover tmp file that is
        self-consistent and matches the latest checksum is reused, a
        missing or broken one is downloaded again. The version and the
        checksum come from latest.pack, fetched once per job (see
        _get_latest_info()). The decoder of the new index pack is set into
        the cache directly, the next validation reads it without the
        file again, and a pending record replaces the local index pack
        in replace(). The request awaits on the event loop; the file
        reads and writes run in worker threads.

        Raises:
            PackDecodeError: If the server is missing, or the new
                index pack fails to decode or validate
        """
        server = self.server
        if server is None:
            raise PackDecodeError('Failed to download the index pack: no server provided')
        info = await self._get_latest_info()
        tmp = self.workspace.joinpath(self.NEW_INDEX)
        # reuse a leftover tmp file that is self-consistent and matches
        # the latest checksum, download again otherwise
        try:
            data = await trio.to_thread.run_sync(atomic_read_bytes, tmp)
            decoder = PackDecodeBase(data)
            decoder.validate_index()
        except (FileNotFoundError, PackDecodeError):
            decoder = None
        if decoder is None or decoder.index_checksum != info.checksum:
            # the index pack is self-validating, the trailing checksum
            # covers the header, the length and the whole index section
            data = await server.get_index_pack(info.version)
            decoder = PackDecodeBase(data)
            decoder.validate_index()
            if decoder.index_checksum != info.checksum:
                # a downloaded index pack that mismatches the latest
                # checksum is not the latest one, this is unsolvable
                raise PackDecodeError(
                    f'Failed to download the index pack: checksum mismatch, '
                    f'expected {info.checksum}, got {decoder.index_checksum}'
                )
            await trio.to_thread.run_sync(file_write, tmp, data)
        # rewrite the pack area paths of the new index and set its
        # decoder into the cache, the next validation reads it without
        # the file again
        self.localize(decoder)
        InstanceCacheOperation.set(self, '_index_pack', decoder)
        # replace() moves the tmp file to the local index pack
        self.pending.append(PendingFile(info=IdxInfo(path=self.index_rel), tmp=tmp))

    async def download(self):
        """
        Download the failed files recorded in self.error to tmp files.

        Every failed file is fetched from the full pack of the index
        pack version with a range request, decompressed and written to
        {ledger}/workspace/{size}_{sha1}_{index}.tmp, the record is
        moved to self.pending for replace(). Records that already carry a
        tmp (an EOL or mode mismatch fixed in validate_files()) and
        deleted markers need no download and are moved to pending
        directly. The requests await on the event loop; the file reads
        and the tmp writes run in worker threads. Files that cannot be
        downloaded or fail the size + sha1 check stay in self.error
        with an empty tmp, this is an unsolvable problem per the draft
        of PackEncodeBase.

        Raises:
            PackDecodeError: If the server is missing
        """
        import httpx2
        server = self.server
        if server is None:
            raise PackDecodeError('Failed to download the files: no server provided')
        decoder = self._index_pack
        pending = []
        error = []
        for index, item in enumerate(self.error):
            info = item.info
            if info.edit == 2:
                # deleted marker, no download, its target is removed
                pending.append(item)
                continue
            if item.tmp:
                # the tmp was already written during validation, the
                # EOL or mode of the file was fixed, no download is
                # needed
                pending.append(item)
                continue
            try:
                tmp = await self._download_file(decoder, server, info, index)
            except (PackDecodeError, httpx2.HTTPError) as e:
                # cannot be downloaded or fails the size + sha1 check,
                # keep the record in error, this is unsolvable
                logger.warning(f'Failed to download {info.path}: {e}')
                error.append(item)
                continue
            # the file is written by python with the default mode 666,
            # a 755 record is chmod-ed in replace()
            pending.append(PendingFile(
                info=info, tmp=tmp, mode=info.mode_decoded if info.mode == 1 else None))

        # keep the pending records prepared before download(), e.g.
        # the new index pack of download_index()
        self.pending += pending
        self.error = error

    async def _download_file(self, decoder, server, info, index):
        """
        Download and decompress a file from the full pack, write the
        content to a tmp file.

        The range request awaits on the event loop; the leftover tmp
        read and the tmp write run in worker threads.

        Args:
            decoder (PackDecodeBase): Decoder of the local index pack
            server (ServerFile): Server to download from
            info (IdxInfo): Record of the file to download
            index (int): Index of the record in self.error, used to
                build the tmp file name

        Returns:
            str: Path of the tmp file

        Raises:
            PackDecodeError: If the downloaded data fails the size +
                sha1 check
        """
        tmp = self.workspace.joinpath(f'{info.size}_{info.sha1.hex()}_{index}.tmp')
        current = await trio.to_thread.run_sync(self._read_current, tmp)
        if self._matches(info, current).match:
            # a leftover tmp file passes the size + sha1 check, reuse it
            return tmp
        # data_start is an offset into the full pack file, range requests
        # use it directly
        data = await server.get_file_content(decoder.current_version, info.data_start, info.data_size)
        content = decoder.decode_content(info, data)
        await trio.to_thread.run_sync(file_write, tmp, content)
        return tmp
