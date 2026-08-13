import httpx

from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.deploy.pack.job_base import JobBase, PendingFile
from alasio.deploy.pack.pack_model import IdxInfo
from alasio.ext import env
from alasio.ext.cache import InstanceCacheOperation, cached_property
from alasio.ext.path.atomic import atomic_read_bytes, atomic_write
from alasio.logger import logger


class ResetJob(JobBase):
    """
    A local file validation and repair task, interruptible and
    resumable.

    The job writes the marker to the job file .pack/workspace/job.pack
    with write() before validating, so an interrupted run can be
    resumed by the next run:

        job = DeployJob.get_unfinished_job(server)
        if job is None:
            job = ResetJob(server)
            job.write()
        job.run()

    The local index pack .pack/index.pack is read once and cached.
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

    Note: the exclusive lock on .pack/index.pack in the draft is shared
    by the whole update flow (full pack, update pack and file check),
    the caller is responsible for it.
    """

    # marker of a validation task in the job file
    MARK = b'REST\x00'

    def __init__(self, server, resume=False):
        """
        Args:
            server (ServerFile): Server to download the index pack and
                the failed files
            resume (bool): True if the job was resumed from the job
                file, run() does not write the job file again then
        """
        super().__init__(b'')
        self.server = server
        self._resume = resume
        self.error: "list[PendingFile]" = []

    def run(self):
        """
        Execute the full reset flow.

        Writes the job marker first unless the job was resumed from it,
        then validates and repairs: a failed index pack is downloaded
        again from the server, an outdated index pack (self-consistent
        but not the latest, see validate_latest) is downloaded again
        too, failed files are downloaded to tmp files and replaced to
        the real files. On failure the workspace is cleaned up: errors
        during write() and validate() are safe and are logged as
        warning.

        Returns:
            bool: True if every file is repaired, False otherwise
        """
        try:
            if not self._resume:
                self.write()
            logger.info(f'Resetting files to "{env.PROJECT_ROOT}"')
            if not self.validate_index():
                # the index pack is broken, download it again
                self.download_index()
            elif not self.validate_latest():
                # the index pack is self-consistent but outdated,
                # download the latest index pack
                self.download_index()
            self.validate_files()
            self.download()
            self.replace()
        except Exception as e:
            # no real file was written, safe to clean up
            logger.warning(f'Failed to reset: {e}')
            self.cleanup()
            return False
        # the job is finished, clean the workspace atomically
        self.cleanup()
        logger.info(f'Reset done')
        return not self.error

    def write(self):
        """
        Write the job marker to the job file, marking a validation task
        in progress, so that a future run can resume from it if this
        run gets interrupted.
        """
        atomic_write(env.PROJECT_ROOT.joinpath(self.JOB_FILE), self.MARK)

    @cached_property
    def _index_pack(self):
        """
        The local index pack, read and decoded once per job.

        validate_index() and validate_files() share this decoder, so
        the file is read only once.

        Returns:
            PackDecodeBase: Decoder of the local index pack

        Raises:
            FileNotFoundError: If the index pack does not exist
            PackDecodeError: If the index pack is malformed
        """
        data = atomic_read_bytes(env.PROJECT_ROOT.joinpath(self.INDEX_PACK))
        return PackDecodeBase(data)

    @cached_property
    def _latest_info(self):
        """
        The latest version and index pack checksum from the server.

        Fetched once per job and shared by validate_latest() and
        download_index(), so the latest info is requested only once
        even when both the local index and the workspace tmp file are
        checked.

        Returns:
            LatestInfo: Latest version and index pack checksum

        Raises:
            PackDecodeError: If the server is missing
        """
        server = self.server
        if server is None:
            raise PackDecodeError('Failed to validate the latest index: no server provided')
        return server.get_latest_info()

    def validate_index(self):
        """
        Validate the local index pack .pack/index.pack itself.

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

    def validate_latest(self):
        """
        Check the local index pack against the latest index pack of
        the server.

        The local index pack must be self-consistent first (see
        validate_index): a self-consistent but outdated index pack
        passes its own checksum and is only detected by comparing its
        checksum with the checksum of the latest index pack recorded
        in latest.pack (fetched once per job, see _latest_info). The
        comparison uses the checksum of the pack format itself: the
        trailing 20 bytes of the index section, the same digest
        validate_index() verifies, not a checksum of the whole index
        pack file. A mismatch means the local index is not the latest
        one, the caller repairs it with download_index().

        Returns:
            bool: True if the local index pack is the latest one

        Raises:
            PackDecodeError: If the server is missing
        """
        info = self._latest_info
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
            current = self._read_current(env.PROJECT_ROOT.joinpath(path))
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
                    f'{info.size}_{info.sha1}_{len(self.error)}.tmp')
                if not self._matches(info, self._read_current(tmp)).match:
                    atomic_write(tmp, current.data)
                self.error.append(PendingFile(info=info, tmp=tmp, mode=info.mode_decoded))
                continue
            if result.match_data:
                # only the EOL differs, write the converted content
                # to a tmp file, download() moves it to pending
                # the tmp name is built from the index of the record
                # in self.error, matching the download() convention
                tmp = self.workspace.joinpath(f'{info.size}_{info.sha1}_{len(self.error)}.tmp')
                atomic_write(tmp, result.match_data)
                self.error.append(PendingFile(
                    info=info, tmp=tmp, mode=info.mode_decoded if info.mode == 1 else None))
                continue
            # missing or wrong size + sha1, the file is rewritten
            # by python with the default mode 666
            self.error.append(PendingFile(
                info=info, tmp='', mode=info.mode_decoded if info.mode == 1 else None))
        return not self.error

    def download_index(self):
        """
        Prepare the new index pack of the latest version in the
        workspace.

        The index pack is downloaded to .pack/workspace/new_index.tmp
        instead of replacing the local .pack/index.pack directly:
        replace() applies it together with the repaired files, so the
        real files are touched only once. A leftover tmp file that is
        self-consistent and matches the latest checksum is reused, a
        missing or broken one is downloaded again. The version and the
        checksum come from latest.pack, fetched once per job (see
        _latest_info). The decoder of the new index pack is set into
        the cache directly, the next validation reads it without the
        file again, and a pending record replaces the local index pack
        in replace().

        Raises:
            PackDecodeError: If the server is missing, or the new
                index pack fails to decode or validate
        """
        server = self.server
        if server is None:
            raise PackDecodeError('Failed to download the index pack: no server provided')
        info = self._latest_info
        tmp = self.workspace.joinpath(self.NEW_INDEX)
        # reuse a leftover tmp file that is self-consistent and matches
        # the latest checksum, download again otherwise
        try:
            data = atomic_read_bytes(tmp)
            decoder = PackDecodeBase(data)
            decoder.validate_index()
        except (FileNotFoundError, PackDecodeError):
            decoder = None
        if decoder is None or decoder.index_checksum != info.checksum:
            # the index pack is self-validating, the trailing checksum
            # covers the header, the length and the whole index section
            data = server.get_index_pack(info.version)
            decoder = PackDecodeBase(data)
            decoder.validate_index()
            if decoder.index_checksum != info.checksum:
                # a downloaded index pack that mismatches the latest
                # checksum is not the latest one, this is unsolvable
                raise PackDecodeError(
                    f'Failed to download the index pack: checksum mismatch, '
                    f'expected {info.checksum}, got {decoder.index_checksum}'
                )
            atomic_write(tmp, data)
        # set the decoder of the new index pack into the cache, the
        # next validation reads it without the file again
        InstanceCacheOperation.set(self, '_index_pack', decoder)
        # replace() moves the tmp file to the local index pack
        self.pending.append(PendingFile(info=IdxInfo(path=self.INDEX_PACK), tmp=tmp))

    def download(self):
        """
        Download the failed files recorded in self.error to tmp files.

        Every failed file is fetched from the full pack of the index
        pack version with a range request, decompressed and written to
        .pack/workspace/{size}_{sha1}_{index}.tmp, the record is moved
        to self.pending for replace(). Records that already carry a
        tmp (an EOL or mode mismatch fixed in validate_files()) and
        deleted markers need no download and are moved to pending
        directly.
        Files that cannot be downloaded or fail the size + sha1 check
        stay in self.error with an empty tmp, this is an unsolvable
        problem per the draft of PackEncodeBase.

        Raises:
            PackDecodeError: If the server is missing
        """
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
                tmp = self._download_file(decoder, server, info, index)
            except (PackDecodeError, httpx.HTTPError) as e:
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

    def _download_file(self, decoder, server, info, index):
        """
        Download and decompress a file from the full pack, write the
        content to a tmp file.

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
        tmp = self.workspace.joinpath(f'{info.size}_{info.sha1}_{index}.tmp')
        if self._matches(info, self._read_current(tmp)).match:
            # a leftover tmp file passes the size + sha1 check, reuse it
            return tmp
        # data_start is an offset into the full pack file, range requests
        # use it directly
        data = server.get_file_content(decoder.version, info.data_start, info.data_size)
        content = decoder.decode_content(info, data)
        atomic_write(tmp, content)
        return tmp
