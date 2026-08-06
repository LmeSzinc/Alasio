import os
from hashlib import sha1

from msgspec import Struct

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.pack_model import IdxInfo
from alasio.ext import env
from alasio.ext.path.atomic import (
    atomic_open, atomic_read_bytes, atomic_remove, atomic_replace, atomic_rmtree, atomic_write
)


class PendingFile(Struct):
    """
    A file change to apply in replace().

    The tmp file is moved to the target path, deleted records
    (edit == 2) have empty tmp, their targets are removed instead.
    current_mode is the mode of the current target file, -1 if the
    target does not exist.
    """
    # record of the file to apply
    info: IdxInfo
    # tmp file path in the workspace, empty for deleted markers
    tmp: str
    # file mode of the current target file, -1 if it does not exist
    current_mode: int


class CurrentFile(Struct):
    """
    Data and st_mode of a current file, read in one file open.

    exist is False if the file does not exist, data and mode are empty
    then.
    """
    # whether the file exists
    exist: bool
    # file content, empty if the file does not exist
    data: bytes
    # st_mode of the file, 0 if the file does not exist
    mode: int


class UnpackJob:
    """
    A full pack unpack task, interruptible and resumable.

    The pack data is passed in __init__, the caller stores it to the job
    file .pack/workspace/job.pack with write() before unpacking, so an
    interrupted run can be resumed by the next run:

        job = UnpackJob.get_unfinished_job()
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

    # pack structure, relative to the app root folder (env.PROJECT_ROOT)
    INDEX_PACK = '.pack/index.pack'
    WORKSPACE = '.pack/workspace'
    JOB_FILE = '.pack/workspace/job.pack'

    def __init__(self, data):
        """
        Args:
            data (bytes): Full pack data
        """
        self._data = data
        self.workspace = env.PROJECT_ROOT.joinpath(self.WORKSPACE)
        self.pending: "list[PendingFile]" = []

    @classmethod
    def get_unfinished_job(cls):
        """
        Check if there is an unfinished job, read it and create an
        UnpackJob object.

        Returns:
            UnpackJob: The unfinished job, or None if there is no
                unfinished job
        """
        try:
            data = atomic_read_bytes(env.PROJECT_ROOT.joinpath(cls.JOB_FILE))
        except FileNotFoundError:
            return None
        return cls(data)

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
                self.pending.append(PendingFile(info=info, tmp='', current_mode=-1))
                continue
            current = self._read_current(target)
            if self._matches(info, current):
                # target file exists and passes the size + sha1 check
                continue
            tmp = self.workspace.joinpath(f'{info.size}_{info.sha1}_{index}.tmp')
            if not self._matches(info, self._read_current(tmp)):
                # decompress and write to the tmp file
                atomic_write(tmp, decoder.catfile(info))
            current_mode = current.mode if current.exist else -1
            self.pending.append(PendingFile(info=info, tmp=tmp, current_mode=current_mode))

    def replace(self):
        """
        Apply the pending changes, then clean the workspace.

        Every tmp file is moved to the target path atomically and the
        deleted markers are removed. The file mode is adjusted only
        when it differs from the record. The workspace is cleaned up at
        the end.
        """
        for pending in self.pending:
            info = pending.info
            target = env.PROJECT_ROOT.joinpath(info.path)
            if info.edit == 2:
                # deleted marker, the file should not exist
                atomic_remove(target)
                continue
            os.makedirs(target.uppath(), exist_ok=True)
            atomic_replace(pending.tmp, target)
            if info.mode == 1 and pending.current_mode & 0o777 != 0o755:
                # executable, adjust the mode if it differs
                os.chmod(target, 0o755)

        # all changes applied, clean the workspace atomically
        self.cleanup()

    def cleanup(self):
        """
        Clean the workspace folder atomically.

        The folder is renamed to a tmp name first (atomic), then removed
        slowly, so an interrupted cleanup never leaves a workspace that
        looks unfinished.
        """
        atomic_rmtree(self.workspace)

    @staticmethod
    def _read_current(file):
        """
        Read a current file in the project, data and mode in one open.

        Args:
            file (str): File path to read

        Returns:
            CurrentFile: Data and st_mode of the file, exist is False
                if the file does not exist
        """
        try:
            with atomic_open(file, 'rb') as f:
                data = f.read()
                mode = os.fstat(f.fileno()).st_mode
        except FileNotFoundError:
            return CurrentFile(exist=False, data=b'', mode=0)
        return CurrentFile(exist=True, data=data, mode=mode)

    @staticmethod
    def _matches(info, current):
        """
        Check if a current file matches a record: exists, same size,
        same sha1.

        Working tree files of eol=1 records are CRLF, the blob sha1 is
        LF, so CRLF is normalized before hashing. Records with empty
        sha1 (empty files) match on size only.

        Args:
            info (IdxInfo): Record to check against
            current (CurrentFile): Current file read from the path

        Returns:
            bool: True if the file exists and matches the record
        """
        if not current.exist:
            return False
        data = current.data
        if info.eol == 1:
            # working tree files are CRLF, blob content is LF
            data = data.replace(b'\r\n', b'\n')
        if len(data) != info.size:
            return False
        if info.sha1:
            return sha1(data).hexdigest() == info.sha1
        return True
