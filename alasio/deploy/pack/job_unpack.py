from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job_base import JobBase, PendingFile
from alasio.ext import env
from alasio.ext.path.atomic import atomic_write
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
        every file to .pack/workspace/{size}_{sha1}_{index}.tmp,
        filling self.pending with the changes to apply in replace().
        A target file whose content matches the record only after
        converting its EOL is written to the tmp file with the
        converted content, no decompression is needed.
        """
        decoder = PackDecodeBase(self._data)
        decoder.validate()
        # the front part of a full pack is an index pack
        atomic_write(env.PROJECT_ROOT.joinpath(self.INDEX_PACK), decoder.extract_index_pack())

        pending = []
        for index, (path, info) in enumerate(decoder.fileinfo.items()):
            target = env.PROJECT_ROOT.joinpath(path)
            if info.edit == 2:
                # deleted marker, its target is removed in replace()
                pending.append(PendingFile(info=info, tmp='', current_mode=0))
                continue
            current = self._read_current(target)
            result = self._matches(info, current)
            tmp = self.workspace.joinpath(f'{info.size}_{info.sha1}_{index}.tmp')
            if result.match:
                # the target file exists and passes the size + sha1 check
                if self._mode_matches(info, current):
                    continue
                # only the mode differs, the content is verified:
                # write the current content to the tmp file, replace()
                # fixes the mode
                if not self._matches(info, self._read_current(tmp)).match:
                    atomic_write(tmp, current.data)
                pending.append(PendingFile(info=info, tmp=tmp, current_mode=0o666))
                continue
            if result.match_data:
                # only the EOL differs, write the converted content
                # to the tmp file without decompressing
                atomic_write(tmp, result.match_data)
            elif not self._matches(info, self._read_current(tmp)).match:
                # decompress and write to the tmp file
                atomic_write(tmp, decoder.catfile(info))
            # the file is written by python with the default mode 666,
            # a 755 record is chmod-ed in replace()
            pending.append(PendingFile(info=info, tmp=tmp, current_mode=0o666))

        self.pending = pending
