import os
from hashlib import sha1

from msgspec import Struct

from alasio.deploy.pack.pack_model import IdxInfo
from alasio.ext import env
from alasio.ext.path.atomic import atomic_open, atomic_remove, atomic_replace, atomic_rmtree
from alasio.ext.path.makedir import batch_makedirs


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


class PendingFile(Struct):
    """
    A file change to apply in replace().

    The tmp file is moved to the target path, deleted records
    (edit == 2) have empty tmp, their targets are removed instead.
    current_mode is the file mode after replace(): the file is written
    by python with the default mode 666, a 644 record needs no further
    operation, a 755 record is chmod-ed in replace().
    """
    # record of the file to apply
    info: IdxInfo
    # tmp file path in the workspace, empty for deleted markers
    tmp: str
    # file mode after replace(), python writes 666 by default
    current_mode: int


class MatchResult(Struct):
    """
    Result of a file match check.

    match is True when the file matches the record as-is. When the
    file does not match only because its EOL differs from the record,
    match_data carries the content converted to the record EOL: the
    caller writes it to a tmp file and replaces the target without a
    download. match_data is empty when the file is missing, has the
    wrong content or cannot be fixed by converting the EOL.
    """
    # whether the file matches the record as-is
    match: bool
    # content converted to the record EOL, empty when not fixable
    match_data: bytes = b''

    def __bool__(self):
        """
        Returns:
            bool: The match flag, so a result can be used as the old
                boolean return of _matches()
        """
        return self.match


class JobBase:
    """
    Base class of deploy jobs.

    The pack data is passed in __init__, the workspace is the folder
    where the job stores its temporary files, both are relative to
    env.PROJECT_ROOT.

    _read_current() and _matches() are shared by every job that
    compares working tree files against the records of a pack.
    """

    # pack structure, relative to the app root folder (env.PROJECT_ROOT)
    INDEX_PACK = '.pack/index.pack'
    WORKSPACE = '.pack/workspace'
    JOB_FILE = '.pack/workspace/job.pack'

    def __init__(self, data):
        """
        Args:
            data (bytes): Pack data
        """
        self._data = data
        self.workspace = env.PROJECT_ROOT.joinpath(self.WORKSPACE)
        self.pending: "list[PendingFile]" = []

    def run(self):
        """
        Execute the job, each subclass implements its own run().

        Raises:
            NotImplementedError: Subclasses must implement run()
        """
        raise NotImplementedError

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

        The record size and sha1 are of the LF blob, text is compared
        in the LF form like unpack: eol=1 (CRLF) and eol=0 (LF)
        normalize the working tree CRLF to LF before hashing, eol=2
        (binary) is compared as-is. The EOL of the working tree file
        must match the record: eol=1 expects CRLF, eol=0 expects LF.
        The content is compared with the EOL assumed correct first,
        the EOL conversion is only attempted when the size or sha1
        fails. When the EOL differs but the LF content matches the
        record, the content is converted to the record EOL and
        returned in match_data: the caller writes it to a tmp file
        and replaces the target without a download. Mixed line
        endings count as an EOL mismatch and are fixed the same way,
        a lone CR that cannot be converted cleanly is not fixable.
        Records with empty sha1 (empty files) match on size only.

        Args:
            info (IdxInfo): Record to check against
            current (CurrentFile): Current file read from the path

        Returns:
            MatchResult: match=True when the file exists and matches
                the record as-is; match=False with non-empty
                match_data when the content matches only after
                converting the EOL to the record EOL
        """
        if not current.exist:
            return MatchResult(match=False)
        data = current.data
        if info.eol == 1:
            # the record expects CRLF, the record size and sha1 are of
            # the LF blob: normalize the working tree CRLF to LF first
            blob = data.replace(b'\r\n', b'\n')
            if len(blob) != info.size:
                return MatchResult(match=False)
            if info.sha1 and sha1(blob).hexdigest() != info.sha1:
                return MatchResult(match=False)
            # the content is the record blob, only the EOL decides:
            # every \n of a clean CRLF file is part of a \r\n, and the
            # normalization removed exactly one byte per \r\n
            if data.count(b'\n') == len(data) - len(blob):
                # clean CRLF (or empty), the file matches as-is
                return MatchResult(match=True)
            # the file is LF or mixed, convert the LF blob to CRLF
            return MatchResult(match=False, match_data=blob.replace(b'\n', b'\r\n'))
        if info.eol == 0:
            # the record expects LF, compare the file as-is first:
            # a CRLF file is longer than the LF blob and falls through
            if len(data) == info.size and (
                    not info.sha1 or sha1(data).hexdigest() == info.sha1):
                return MatchResult(match=True)
            if b'\r' not in data:
                # no CR, the content itself differs from the record
                return MatchResult(match=False)
            # the file has CR: the EOL differs, try converting CRLF to LF
            converted = data.replace(b'\r\n', b'\n')
            if b'\r' in converted:
                # a lone CR, the EOL cannot be converted cleanly
                return MatchResult(match=False)
            if len(converted) != info.size:
                return MatchResult(match=False)
            if info.sha1 and sha1(converted).hexdigest() != info.sha1:
                return MatchResult(match=False)
            return MatchResult(match=False, match_data=converted)
        # binary (eol == 2), compared as-is
        if len(data) != info.size:
            return MatchResult(match=False)
        if info.sha1 and sha1(data).hexdigest() != info.sha1:
            return MatchResult(match=False)
        return MatchResult(match=True)

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
