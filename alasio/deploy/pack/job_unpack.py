import trio

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job_base import JobBase, PendingFile
from alasio.deploy.pack.pack_model import IdxInfo
from alasio.ext.path.atomic import file_write
from alasio.logger import logger


class UnpackJob(JobBase):
    """
    A full pack unpack task, interruptible and resumable.

    Unpacking is a local rebuild: the working tree is rebuilt from the
    full pack data passed in __init__, the leftover files of the old
    version (recorded in the old local index pack, not in this pack)
    are removed, the new index pack (index.pack in the ledger folder
    of the target) is replaced last. No server is involved. The pack
    data is passed in __init__, the caller stores it to the job file
    ({ledger}/workspace/job.pack) with write() before unpacking, so an
    interrupted run can be resumed by the next run:

        job = DeployJob()._get_unfinished_job()
        if job is not None:
            await job.run()
        await UnpackJob(data).run()

    All files unpack into the root of the target; the pack area paths
    of the pack (.pack/**) resolve inside the ledger folder of the
    target, see JobBase.localize(). The unpack flow follows the draft
    in PackEncodeBase:

    1. unpack() reads the old local index pack, writes the index
       section to {ledger}/workspace/new_index.tmp and decompresses
       all files to {ledger}/workspace/{size}_{sha1}_{index}.tmp,
       real files are untouched. The leftover files of the old version, recorded
       in the old index but not in this pack, are appended as deleted
       markers, and the new index pack is committed last by
       replace_index() when every data file landed: an interruption
       during replace() leaves the old index pack in place, so a
       resumed run still computes the leftover deletion list from it.
       Files that exist and pass the size + sha1 check
       are skipped, leftover tmp files that pass the check are reused.
    2. replace() moves every tmp file to the target path atomically,
       removes the deleted markers and commits the new index pack last,
       see JobBase.replace(). Real file operations only start
       after every tmp file is ready, so an interruption never leaves a
       half-mixed set of old and new files.
    3. cleanup() cleans {ledger}/workspace atomically: the folder is
       renamed first, then removed slowly, so an interrupted cleanup
       never leaves a workspace that looks unfinished.

    On failure the workspace is cleaned up: errors during write() and
    unpack() are safe, no real file was written, errors during
    replace() leave partially replaced files. Only a process killed in
    flight leaves the workspace behind for the next run to resume from
    (the job file and the tmp files written so far).

    Note: the exclusive lock of the target ledger (the lock file, see
    DeployJob.locked()) is held by DeployJob around the whole update
    flow (full pack, update pack and file check); a direct user of
    this job must take it itself.
    """

    def __init__(self, data, resume=False, root=None, name=''):
        """
        Args:
            data (bytes): Full pack data
            resume (bool): True if the data was read from the job file,
                run() does not write the job file again then
            root (str, optional): Folder to update. Defaults to None,
                env.PROJECT_ROOT
            name (str, optional): Ledger key of the target. Defaults to
                '', the project tree target
        """
        super().__init__(data, root=root, name=name)
        self._resume = resume

    async def run(self):
        """
        Execute the full unpack flow.

        Writes the job file first unless the job was resumed from it,
        then unpacks and replaces all files. The job is async like
        every job (see JobBase.run()): its local phases run in a
        worker thread phase by phase, so the event loop stays
        responsive. On failure the workspace is cleaned up: errors
        during write() and unpack() are safe because no real file was
        written and are logged as warning, errors during replace()
        leave partially replaced files and are logged as error.

        Returns:
            bool: True if every file is unpacked, False otherwise
        """
        try:
            if not self._resume:
                await trio.to_thread.run_sync(self.write)
            logger.info(f'Unpacking data to "{self.root}", name="{self.name}"')
            await trio.to_thread.run_sync(self.unpack)
        except Exception as e:
            # no real file was written, safe to clean up
            logger.warning(f'Failed to unpack: {e}')
            await trio.to_thread.run_sync(self.cleanup)
            return False
        try:
            logger.info(f'Replacing files to "{self.root}", name="{self.name}"')
            await trio.to_thread.run_sync(self.replace)
        except Exception as e:
            # real files may be partially replaced
            logger.error(f'Failed to replace file: {e}')
            await trio.to_thread.run_sync(self.cleanup)
            return False
        # all changes applied, clean the workspace atomically
        await trio.to_thread.run_sync(self.cleanup)
        logger.info(f'Unpack done')
        return True

    def write(self):
        """
        Write the data to the job file, so that a future run can resume
        from it if this run gets interrupted.

        The job file lives in the workspace, a corrupted one is
        detected by _get_unfinished_job() on the next run, so a plain
        write is enough.
        """
        file_write(self.job_file, self._data)

    def unpack(self):
        """
        Prepare all files in the workspace, real files are untouched.

        Writes the index section to {ledger}/workspace/new_index.tmp and
        decompresses every file to
        {ledger}/workspace/{size}_{sha1}_{index}.tmp, filling self.pending
        with the changes to apply in replace_data(). The old local index
        pack is read first: files recorded in it but not in this pack
        are the leftover files of the old version, removed by
        replace_data() like the deleted markers. A target file whose
        content matches the record only after converting its EOL is
        written to the tmp file with the converted content, no
        decompression is needed. The new index pack is committed last
        by replace_index() when every data file landed: an
        interruption during replace() leaves the old index pack in
        place, so a resumed run still computes the leftover deletion
        list from it.
        """
        decoder = PackDecodeBase(self._data)
        decoder.validate()
        # rewrite the pack area paths of the pack into the ledger
        # folder of the target before any comparison
        self.localize(decoder)
        # the full pack records every file of the new version, the
        # emptiness base of replace_data()
        self.new_fileinfo = decoder.fileinfo
        # the old index is read before the new index pack replaces it
        old_fileinfo = self._old_fileinfo_from_index()

        # unpack index
        index_tmp = self.workspace.joinpath(self.NEW_INDEX)
        index_pack = decoder.extract_index_pack()
        current = self._read_current(index_tmp)
        if not current.exist or current.data != index_pack:
            file_write(index_tmp, index_pack)

        # unpack files, the loop body is unchanged
        pending = []
        for index, (path, info) in enumerate(decoder.fileinfo.items()):
            target = self.root.joinpath(path)
            if info.edit == 2:
                # deleted marker, its target is removed in replace()
                pending.append(PendingFile(info=info, tmp=''))
                continue
            current = self._read_current(target)
            result = self._matches(info, current)
            tmp = self.workspace.joinpath(f'{info.size}_{info.sha1.hex()}_{index}.tmp')
            if result.match:
                # the target file exists and passes the size + sha1 check
                if result.mode_matched:
                    continue
                # only the mode differs, the content is verified:
                # write the current content to the tmp file, replace()
                # chmod-ed the target to the record mode
                if not self._matches(info, self._read_current(tmp)).match:
                    file_write(tmp, current.data)
                pending.append(PendingFile(
                    info=info, tmp=tmp, mode=info.mode_decoded))
                continue
            if result.match_data:
                # only the EOL differs, write the converted content
                # to the tmp file without decompressing
                file_write(tmp, result.match_data)
            elif not self._matches(info, self._read_current(tmp)).match:
                # decompress and write to the tmp file
                file_write(tmp, decoder.catfile(info))
            # the file is written by python with the default mode 666,
            # a 755 record is chmod-ed in replace()
            pending.append(PendingFile(
                info=info, tmp=tmp, mode=info.mode_decoded if info.mode == 1 else None))

        # the leftover files of the old version, recorded in the old
        # index but not in this pack
        pending += self._leftover_deletions(old_fileinfo, decoder.fileinfo)
        self.pending = pending
        # the new index pack is the commit record of the flow, not a
        # data file of pending: replace_index() commits it after every
        # data file, see the docstring
        self.pending_index = PendingFile(info=IdxInfo(path=self.index_rel), tmp=index_tmp)
