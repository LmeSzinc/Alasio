import os

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job_base import JobBase, PendingFile
from alasio.ext import env
from alasio.ext.path.atomic import atomic_remove, atomic_replace, atomic_write
from alasio.ext.path.makedir import batch_makedirs
from alasio.logger import logger


class UnpackJob(JobBase):
    """
    A full pack unpack task, interruptible and resumable.

    The pack data is passed in __init__, the caller stores it to the job
    file .pack/workspace/job.pack with write() before unpacking, so an
    interrupted run can be resumed by the next run:

        job = DeployJob.get_unfinished_job()
        if job is not None:
            job.unpack()
            job.replace()
        job = UnpackJob(data)
        job.write()
        job.unpack()
        job.replace()

    All files unpack into env.PROJECT_ROOT, the pack structure (.pack/)
    lives inside it. The unpack flow follows the draft in PackEncodeBase:

    1. unpack() writes the index section to .pack/index.pack and
       decompresses all files to .pack/workspace/{size}_{sha1}_{index}.tmp,
       real files are untouched. Files that exist and pass the size +
       sha1 check are skipped, leftover tmp files that pass the check
       are reused.
    2. replace() moves every tmp file to the target path atomically and
       removes the deleted markers. Real file operations only start
       after every tmp file is ready, so an interruption never leaves a
       half-mixed set of old and new files.
    3. cleanup() cleans .pack/workspace atomically: the folder is
       renamed first, then removed slowly, so an interrupted cleanup
       never leaves a workspace that looks unfinished.

    On failure the workspace is kept, the next run resumes from it.

    Note: the exclusive lock on .pack/index.pack in the draft is shared
    by the whole update flow (full pack, update pack and file check),
    the caller is responsible for it.
    """

    def __init__(self, data, resume=False):
        """
        Args:
            data (bytes): Full pack data
            resume (bool): True if the data was read from the job file,
                run() does not write the job file again then
        """
        super().__init__(data)
        self._resume = resume
        self.pending: "list[PendingFile]" = []

    def run(self):
        """
        Execute the full unpack flow.

        Writes the job file first unless the job was resumed from it,
        then unpacks and replaces all files. On failure the workspace
        is cleaned up: errors during write() and unpack() are safe
        because no real file was written and are logged as warning,
        errors during replace() leave partially replaced files and are
        logged as error.
        """
        try:
            if not self._resume:
                self.write()
            logger.info(f'Unpacking data to "{env.PROJECT_ROOT}"')
            self.unpack()
        except Exception as e:
            # no real file was written, safe to clean up
            logger.warning(f'Failed to unpack: {e}')
            self.cleanup()
            return
        try:
            logger.info(f'Replacing files to "{env.PROJECT_ROOT}"')
            self.replace()
        except Exception as e:
            # real files may be partially replaced
            logger.error(f'Failed to replace file: {e}')
            self.cleanup()
            return
        # all changes applied, clean the workspace atomically
        self.cleanup()
        logger.info(f'Unpack done')

    def write(self):
        """
        Write the data to the job file, so that a future run can resume
        from it if this run gets interrupted.
        """
        atomic_write(env.PROJECT_ROOT.joinpath(self.JOB_FILE), self._data)

    def unpack(self):
        """
        Prepare all files in the workspace, real files are untouched.

        Writes the index section to .pack/index.pack and decompresses
        every file to .pack/workspace/{size}_{sha1}_{index}.tmp, filling
        self.pending with the changes to apply in replace().
        """
        decoder = PackDecodeBase(self._data)
        decoder.validate()
        # the front part of a full pack is an index pack
        atomic_write(env.PROJECT_ROOT.joinpath(self.INDEX_PACK), decoder.extract_index_pack())

        self.pending = []
        for index, (path, info) in enumerate(decoder.fileinfo.items()):
            target = env.PROJECT_ROOT.joinpath(path)
            if info.edit == 2:
                # deleted marker, its target is removed in replace()
                self.pending.append(PendingFile(info=info, tmp='', current_mode=0))
                continue
            current = self._read_current(target)
            if self._matches(info, current):
                # target file exists and passes the size + sha1 check
                continue
            tmp = self.workspace.joinpath(f'{info.size}_{info.sha1}_{index}.tmp')
            if not self._matches(info, self._read_current(tmp)):
                # decompress and write to the tmp file
                atomic_write(tmp, decoder.catfile(info))
            # the file is written by python with the default mode 666,
            # a 755 record is chmod-ed in replace()
            self.pending.append(PendingFile(info=info, tmp=tmp, current_mode=0o666))

    def replace(self):
        """
        Apply the pending changes to the real files.

        Every tmp file is moved to the target path atomically and the
        deleted markers are removed. The file mode is adjusted only
        when it differs from the record. The workspace is kept, the
        caller (run()) cleans it up after all changes are applied.
        """
        # create the parent folders of all targets in one batch
        batch_makedirs([
            env.PROJECT_ROOT.joinpath(pending.info.path)
            for pending in self.pending
            if pending.info.edit != 2
        ])

        for pending in self.pending:
            info = pending.info
            target = env.PROJECT_ROOT.joinpath(info.path)
            if info.edit == 2:
                # deleted marker, the file should not exist
                atomic_remove(target)
                continue
            atomic_replace(pending.tmp, target)
            self._adjust_mode(target, info, pending.current_mode)

    @staticmethod
    def _adjust_mode(target, info, current_mode):
        """
        Adjust the file mode if the execute bits differ from the record.

        A 644 record (mode=0) accepts any current mode without execute
        bits, e.g. 666/646/664, a 755 record (mode=1) accepts any with
        execute bits, e.g. 777/757/775. Otherwise the file is chmod-ed
        to the record mode, 644 or 755.

        Args:
            target (str): Target file path
            info (IdxInfo): File record
            current_mode (int): st_mode of the current file, or 0o666
                for a new file written with the python default mode
        """
        current_exec = current_mode & 0o111
        if info.mode == 1:
            if current_exec != 0o111:
                # 755 record, the file is not executable
                os.chmod(target, 0o755)
        else:
            if current_exec:
                # 644 record, the file is executable
                os.chmod(target, 0o644)
