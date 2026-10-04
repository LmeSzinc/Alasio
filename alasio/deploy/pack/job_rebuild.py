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
    downloaded, and the new index pack is replaced last in replace():
    an interruption during replace() leaves the old index pack in
    place, so a resumed run still computes the leftover deletion list
    from it.

    write() stores the RBIL marker to the job file, the marker is
    dispatched to a resumed RebuildJob by
    DeployJob().get_unfinished_job().

    Note: the exclusive lock on the local index pack (index.pack in
    the ledger folder) in the draft is shared by the whole update flow
    (full pack, update pack and file check), the caller is responsible
    for it.
    """

    # marker of a rebuild task in the job file
    MARK = b'RBIL\x00'

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
        super().__init__(server, resume=resume, root=root, name=name)
        # {path: IdxInfo} of the old local index pack, the deletion
        # base of the leftover cleanup, {} when it is missing or
        # malformed
        self._old_fileinfo: "dict[str, IdxInfo]" = {}

    def run(self):
        """
        Execute the full rebuild flow.

        Writes the job marker first unless the job was resumed from it,
        then reads the old local index for the leftover cleanup and
        downloads the latest index pack unconditionally: the local
        index is never trusted, unlike ResetJob which validates it
        first. Every file is verified against the new index, the
        leftover files of the old version are deleted, the failed
        files are downloaded to tmp files and replaced to the real
        files. The new index pack is replaced last, see the class
        docstring. On failure the workspace is cleaned up: errors
        during write() and validation are safe and are logged as
        warning.

        Returns:
            bool: True if every file is rebuilt, False otherwise
        """
        try:
            if not self._resume:
                self.write()
            logger.info(f'Rebuilding files to "{self.root}", name="{self.name}"')
            self._old_fileinfo = self._old_fileinfo_from_index()
            self.download_index()
            self.validate_files()
            self.download()
            # the new index records every file of the new version, the
            # emptiness base of replace()
            self.new_fileinfo = self._index_pack.fileinfo
            # the new index pack is replaced last: an interruption
            # during replace() leaves the old index pack in place, so
            # a resumed run still computes the leftover deletion list
            # from it
            self.pending = [
                p for p in self.pending if p.info.path != self.index_rel
            ] + [p for p in self.pending if p.info.path == self.index_rel]
            self.replace()
        except Exception as e:
            # no real file was written, safe to clean up
            logger.warning(f'Failed to rebuild: {e}')
            self.cleanup()
            return False
        # the job is finished, clean the workspace atomically
        self.cleanup()
        logger.info(f'Rebuild done')
        return not self.error

    def validate_files(self):
        """
        Collect the changes to apply against the new index.

        The records of the new index are verified like ResetJob
        (missing or mismatching files are recorded in self.error,
        fixable EOL or mode mismatches are written to tmp files
        without a download), then the leftover files are appended to
        self.pending: paths recorded in the old index but not in the
        new one become deleted markers, removed by replace(). The
        leftover deletions never enter self.error: self.error is the
        download list of download(), the leftover files are deleted,
        not downloaded. They are appended after the validation:
        ResetJob.validate_files() resets self.error at its start, and
        the tmp file names built during the validation keep their
        positions in self.error.

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
