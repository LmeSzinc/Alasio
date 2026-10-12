import trio

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job_base import JobBase, PendingFile
from alasio.deploy.pack.pack_model import IdxInfo
from alasio.ext.path.atomic import file_write
from alasio.logger import logger


class UnpackJob(JobBase):
    """
    A full pack unpack task.

    Unpacking is a local rebuild: the working tree is rebuilt from the
    full pack data passed in __init__, the leftover files of the old
    version (recorded in the old local index pack and in the job file of
    a previous run, see residue_fileinfo(), not in this pack) are
    removed, the new index pack (index.pack in the ledger folder of the
    target) is replaced last. No server is involved. The pack data is
    passed in __init__; the caller stores it to the job file
    ({ledger}/workspace/job.pack) with write() before unpacking, so the
    flow leaves a record of what it applies. A run never works from an
    old job file: it re-derives every file and cleans the workspace up
    when it fully succeeded (the tmp files of a failed run are reused by
    content, see TmpManager).

    All files unpack into the root of the target; the pack area paths
    of the pack (.pack/**) resolve inside the ledger folder of the
    target, see JobBase.localize(). The unpack flow follows the draft
    in PackEncodeBase:

    1. unpack() reads the old local index pack, writes the index
       section to {ledger}/workspace/new_index.tmp and decompresses
       all files to {ledger}/workspace/{size}_{sha1}_{rank}.tmp,
       real files are untouched. The tmp files are named by the tmp
       manager of the workspace (the rank is the occurrence index of
       the content among the records that need a tmp file, see
       TmpManager), so a leftover
       tmp file of an interrupted run is found again and reused. The
       leftover files of the old version, recorded in the old index but
       not in this pack, are appended as deleted markers, and the new
       index pack is committed last by replace_index() when every data
       file landed: an interruption during replace() leaves the old
       index pack in place, so a resumed run still computes the
       leftover deletion list from it. Files that exist and pass the
       size + sha1 check are skipped, leftover tmp files that pass the
       check are reused.
    2. replace() moves every tmp file to the target path atomically,
       removes the deleted markers and commits the new index pack last,
       see JobBase.replace(). Real file operations only start
       after every tmp file is ready, so an interruption never leaves a
       half-mixed set of old and new files.
    3. cleanup() cleans {ledger}/workspace atomically: the folder is
       renamed first, then removed slowly, so an interrupted cleanup
       never leaves a workspace that looks unfinished.

    On failure the workspace is kept for the next run to resume from
    (the job file and the tmp files written so far): errors during
    write() and unpack() are safe, no real file was written, errors
    during replace() leave partially replaced files that the resumed
    run repairs. The workspace is cleaned up only when every file
    landed and the new index pack was committed, see run().

    Note: the exclusive lock of the target ledger (the lock file, see
    DeployJob.locked()) is held by DeployJob around the whole update
    flow (full pack, update pack and file check); a direct user of
    this job must take it itself.
    """

    def __init__(self, data, root=None, name=''):
        """
        Args:
            data (bytes): Full pack data
            root (str, optional): Folder to update. Defaults to None,
                env.PROJECT_ROOT
            name (str, optional): Ledger key of the target. Defaults to
                '', the project tree target
        """
        super().__init__(data, root=root, name=name)

    async def run(self):
        """
        Execute the full unpack flow.

        Writes the job file first, then unpacks and replaces all files. The job is async like
        every job (see JobBase.run()): its local phases run in a
        worker thread phase by phase, so the event loop stays
        responsive. On failure the workspace is kept so the next run
        resumes the job: errors during write() and unpack() are safe
        because no real file was written and are logged as warning,
        errors during replace() leave partially replaced files and are
        logged as error. The workspace is cleaned up only when every
        file landed and the new index pack was committed.

        Returns:
            bool: True if every file is unpacked, False otherwise
        """
        try:
            # the records of the job file of a previous run join the
            # deletion base (R19): read them before write() replaces it
            await trio.to_thread.run_sync(self._warm_residue)
            await trio.to_thread.run_sync(self.write)
            logger.info(f'Unpacking data to "{self.root}", name="{self.name}"')
            await trio.to_thread.run_sync(self.unpack)
        except Exception as e:
            # no real file was written: the workspace (the job file
            # and the tmp files written so far) is kept as the resume
            # base of the next run
            logger.warning(f'Failed to unpack: {e}, keeping the workspace for the next run')
            return False
        try:
            logger.info(f'Replacing files to "{self.root}", name="{self.name}"')
            await trio.to_thread.run_sync(self.replace)
        except Exception as e:
            # real files may be partially replaced, the workspace is
            # kept for the next run to resume and repair the targets
            logger.error(f'Failed to replace file: {e}, keeping the workspace for the next run')
            return False
        # every file landed and the new index pack is committed: the
        # workspace is done, clean it up atomically
        await trio.to_thread.run_sync(self.cleanup)
        logger.info(f'Unpack done')
        return True

    def write(self):
        """
        Write the data to the job file, so that the flow leaves a record
        of what it applies: the next run takes the records of a failed
        flow as an extra deletion base (see residue_fileinfo()) and may
        reuse the data instead of computing or downloading it again.

        The job file lives in the workspace, an undecodable one is
        ignored by the next run, so a plain write is enough.
        """
        file_write(self.job_file, self._data)

    def unpack(self):
        """
        Prepare all files in the workspace, real files are untouched.

        Writes the index section to {ledger}/workspace/new_index.tmp and
        decompresses every file to
        {ledger}/workspace/{size}_{sha1}_{rank}.tmp, filling self.pending
        with the changes to apply in replace_data(). The old local index
        pack is read first: files recorded in it but not in this pack
        are the leftover files of the old version, removed by
        replace_data() like the deleted markers; the records of the job
        file of a previous run are taken as a second base (R19, see
        residue_fileinfo()), so a file that only an intermediate version
        has - it is in neither the index nor this pack - is removed too.
        A target file whose
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
        # the old index is read before the new index pack replaces it,
        # and it joins the records of the job file of a previous run
        # (R19, read before this run replaced it): a file that only an
        # intermediate version has is in neither base alone
        old_fileinfo = {**self._old_fileinfo_from_index(), **self.residue_fileinfo}

        # unpack index
        index_tmp = self.workspace.joinpath(self.NEW_INDEX)
        index_pack = decoder.extract_index_pack()
        current = self._read_current(index_tmp)
        if not current.exist or current.data != index_pack:
            file_write(index_tmp, index_pack)

        # unpack files, the loop body is unchanged
        pending = []
        for path, info in decoder.fileinfo.items():
            target = self.root.joinpath(path)
            if info.edit == 2:
                # deleted marker, its target is removed in replace()
                pending.append(PendingFile(info=info, tmp=''))
                continue
            current = self._read_current(target)
            result = self._matches(info, current)
            if result.match and result.mode_matched:
                # the target file exists and passes the size + sha1 check
                # with the record mode: the record needs no tmp file and
                # applies for no tmp name
                continue
            # the record needs a tmp file, it applies for its name now
            tmp = self.tmp_file(path, info)
            if result.match:
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
