import trio

from alasio.deploy.pack.job_reset import ResetJob
from alasio.deploy.pack.pack_model import IdxInfo
from alasio.logger import logger


class RebuildJob(ResetJob):
    """
    A rebuild task from the server: download the latest index pack,
    remove the leftover files of the old version and rebuild the
    working tree to the latest version, interruptible and resumable.

    Unlike ResetJob, the local index pack is never trusted: the latest
    index pack is downloaded unconditionally (download_index()), then
    the working tree is verified against it. The leftover files are the
    difference between the old local index and the new one, so only
    managed files are removed, files the user placed by hand are
    untouched. The old fileinfo is read before the new index is
    downloaded, and the new index pack is committed last by
    replace_index(), only when every file of it landed: an
    interruption during replace() leaves the old index pack in
    place, so a resumed run still computes the leftover deletion list
    from it.

    write() stores the RBIL marker to the job file: it is the record of
    the task in flight, a marker is never dispatched (the next flow
    derives its work from the tree and the local index, see
    JobBase.residue_fileinfo()).

    Note: the exclusive lock of the target ledger (the lock file, see
    DeployJob.locked()) is held by DeployJob around the whole update
    flow (full pack, update pack and file check); a direct user of
    this job must take it itself.
    """

    # marker of a rebuild task in the job file
    MARK = b'RBIL\x00'

    def __init__(self, server, root=None, name=''):
        """
        Args:
            server (ServerFile): Server to download the index pack and
                the failed files
            root (str, optional): Folder to update. Defaults to None,
                env.PROJECT_ROOT
            name (str, optional): Ledger key of the target. Defaults to
                '', the project tree target
        """
        super().__init__(server, root=root, name=name)
        # {path: IdxInfo} of the old local index pack plus the records of
        # the job file of a previous run (R19), the deletion base of the
        # leftover cleanup, {} when both are empty/missing
        self._old_fileinfo: "dict[str, IdxInfo]" = {}

    async def run(self):
        """
        Execute the full rebuild flow.

        Writes the job marker first, then reads the old local index for the leftover cleanup and
        downloads the latest index pack unconditionally: the local
        index is never trusted, unlike ResetJob which validates it
        first. Every file is verified against the new index, the
        leftover files of the old version are deleted, the failed
        files are downloaded to tmp files and replaced to the real
        files. The new index pack is committed last by replace_index()
        and only when every file was rebuilt, see the class docstring.
        The network phases await on the event loop, the local phases
        run in a worker thread (see JobBase.run()). On failure the
        workspace is kept so the next run resumes the job: errors
        during write() and validation are safe because no real file
        was written and are logged as warning, errors during replace()
        leave partially replaced files and are logged as error. The
        workspace is cleaned up only when every file was rebuilt, no
        file stayed in self.error and the new index pack was
        committed.

        Returns:
            bool: True if every file is rebuilt, False otherwise
        """
        try:
            # the records of the job file of a previous run join the
            # deletion base (R19): read them before write() replaces it
            await trio.to_thread.run_sync(self._warm_residue)
            await self.tmp_manager_init()
            await trio.to_thread.run_sync(self.write)
            logger.info(f'Rebuilding files to {self.described_target}')
            # the deletion base of the leftover cleanup: the old local
            # index plus the records of the job file of a previous run
            # (R19), so a file that only an intermediate version has is
            # removed too
            old_fileinfo = await trio.to_thread.run_sync(self._old_fileinfo_from_index)
            self._old_fileinfo = {**old_fileinfo, **self.residue_fileinfo}
            await self.download_index()
            await trio.to_thread.run_sync(self.validate_files)
            await self.download()
            # the new index records every file of the new version, the
            # emptiness base of replace_data()
            self.new_fileinfo = self._index_pack.fileinfo
        except Exception as e:
            # no real file was written: the workspace (the job file
            # and the tmp files written so far) is kept as the resume
            # base of the next run
            logger.warning(f'Failed to rebuild: {e}, keeping the workspace for the next run')
            return False
        try:
            # the new index pack is committed after the rebuild and
            # only when every file of it landed: an interruption keeps
            # the old index pack in place, so a resumed run still
            # computes the leftover deletion list from it (the index
            # is prepared to pending_index, see JobBase.replace_index())
            await trio.to_thread.run_sync(self.replace, not self.error)
        except Exception as e:
            # real files may be partially replaced, the workspace is
            # kept for the next run to resume and repair the targets
            logger.error(f'Failed to replace file: {e}, keeping the workspace for the next run')
            return False
        if self.error:
            # some records could not be downloaded or verified: the new
            # index pack was not committed, the workspace is kept and
            # the next run retries the records
            logger.warning(
                f'Failed to rebuild: {len(self.error)} file(s) stayed in error, '
                f'the workspace is kept for the next run'
            )
            return False
        # every file was rebuilt and the new index pack is committed:
        # the workspace is done, clean it up atomically
        await trio.to_thread.run_sync(self.cleanup)
        logger.info(f'Rebuild done')
        return True

    def validate_files(self):
        """
        Collect the changes to apply against the new index.

        The records of the new index are verified like ResetJob
        (missing or mismatching files are recorded in self.error,
        fixable EOL or mode mismatches are written to tmp files
        without a download), then the leftover files are appended to
        self.pending: paths recorded in the old index (or in the job
        file of a previous run, R19) but not in the new one become
        deleted markers, removed by replace_data(). The
        leftover deletions never enter self.error: self.error is the
        download list of download(), the leftover files are deleted,
        not downloaded. They are appended after the validation:
        ResetJob.validate_files() resets self.error at its start and
        applies for the tmp file names of the records only where they
        are needed (see tmp_file()), the appended deletions carry no
        tmp file and affect neither.

        Returns:
            bool: True if every file matches the new index, False
                otherwise
        """
        super().validate_files()
        # the leftover files are deleted, not downloaded: they go to
        # self.pending directly, self.error stays the download list
        self.pending += self._leftover_deletions(
            self._old_fileinfo, self._index_pack.fileinfo)
        return not self.error
