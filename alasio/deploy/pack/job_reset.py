from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.deploy.pack.job_base import JobBase, PendingFile
from alasio.ext import env
from alasio.ext.cache import cached_property
from alasio.ext.path.atomic import atomic_read_bytes, atomic_write
from alasio.logger import logger


class ResetJob(JobBase):
    """
    A local file validation task, interruptible and resumable.

    The job writes the marker to the job file .pack/workspace/job.pack
    with write() before validating, so an interrupted run can be
    resumed by the next run:

        job = DeployJob.get_unfinished_job()
        if job is None:
            job = ResetJob()
            job.write()
        if not job.validate_index():
            # repair the index pack itself, then retry
            return
        job.validate_files()
        # repair the failed files in job.error, then clean the workspace

    The local index pack .pack/index.pack is read once and cached.
    validate_index() checks the index pack itself, validate_files()
    checks every file recorded in it, real files are untouched. The
    two failures are repaired differently, so they are reported
    separately: a failed index pack is re-downloaded by range request,
    failed files are collected in self.error for the download flow in
    the draft of PackEncodeBase.

    Note: the exclusive lock on .pack/index.pack in the draft is shared
    by the whole update flow (full pack, update pack and file check),
    the caller is responsible for it.
    """

    # marker of a validation task in the job file
    MARK = b'REST\x00'

    def __init__(self, resume=False):
        """
        Args:
            resume (bool): True if the job was resumed from the job
                file, run() does not write the job file again then
        """
        super().__init__(b'')
        self._resume = resume
        self.error: "list[PendingFile]" = []

    def run(self):
        """
        Execute the full reset flow.

        Writes the job marker first unless the job was resumed from it,
        then validates the local index pack and every file recorded in
        it. Failed files are collected in self.error, no real file is
        written. On failure the workspace is cleaned up: errors during
        write() and validate() are safe and are logged as warning.

        Returns:
            bool: True if the index pack is valid and every file
                matches its record
        """
        try:
            if not self._resume:
                self.write()
            result = self.validate_index() and self.validate_files()
        except Exception as e:
            # no real file was written, safe to clean up
            logger.warning(f'Failed to validate: {e}')
            self.cleanup()
            return False
        # the job is finished, clean the workspace atomically
        self.cleanup()
        return result

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

    def validate_index(self):
        """
        Validate the local index pack .pack/index.pack itself.

        The index pack must exist, decode and pass its checksum,
        otherwise the files recorded in it cannot be trusted. A failed
        index pack is repaired by re-downloading the index section (the
        range request flow in the draft of PackEncodeBase), which
        differs from repairing the failed files.

        Returns:
            bool: True if the index pack is valid
        """
        try:
            self._index_pack.validate_index()
            return True
        except (FileNotFoundError, PackDecodeError) as e:
            logger.warning(f'Failed to validate the index pack: {e}')
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
        Failed files are collected in self.error with an empty tmp,
        the caller repairs them.

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
                    self.error.append(PendingFile(info=info, tmp='', current_mode=0))
                continue
            if not self._matches(info, current):
                # missing or wrong size + sha1, the file is rewritten
                # by python with the default mode 666
                self.error.append(PendingFile(info=info, tmp='', current_mode=0o666))
                continue
            if not self._mode_matches(info, current):
                # the mode differs, the current mode guides the fix
                self.error.append(PendingFile(info=info, tmp='', current_mode=current.mode))
        return not self.error

    @staticmethod
    def _mode_matches(info, current):
        """
        Check if the file mode of a current file matches a record.

        A 644 record (mode == 0) accepts any current mode without
        execute bits, e.g. 666/646/664, a 755 record (mode == 1)
        accepts any with execute bits, e.g. 777/757/775. Any other
        mode is a mismatch.

        Args:
            info (IdxInfo): Record to check against
            current (CurrentFile): Current file read from the path

        Returns:
            bool: True if the file mode matches the record
        """
        current_exec = current.mode & 0o111
        if info.mode == 1:
            return current_exec == 0o111
        return current_exec == 0
