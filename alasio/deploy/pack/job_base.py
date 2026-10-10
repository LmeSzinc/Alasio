import os
from hashlib import sha1
from typing import Optional

from msgspec import Struct

from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.deploy.pack.pack_model import IdxInfo
from alasio.deploy.simple_pip.cleanup_folder import CleanupFolder
from alasio.ext import env
from alasio.ext.cache import InstanceCacheOperation
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import (
    atomic_open, atomic_read_bytes, atomic_remove, atomic_replace, atomic_rmtree, folder_rmtree_empty
)
from alasio.ext.path.makedir import batch_makedirs
from alasio.logger import logger


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
    A file change to apply in replace_data().

    The tmp file is moved to the target path, deleted records
    (edit == 2) have empty tmp, their targets are removed instead.
    mode is the mode to chmod the target to after the move, None when
    the mode is already correct: a file written by python with the
    default mode 666 is accepted by a 644 record as-is, a 755 record
    sets 0o755; a file whose mode differs from the record sets the
    record mode (644 or 755).
    """
    # record of the file to apply
    info: IdxInfo
    # tmp file path in the workspace, empty for deleted markers
    tmp: str
    # mode to chmod the target to, None when the mode is already correct
    mode: Optional[int] = None


class MatchResult(Struct):
    """
    Result of a file match check.

    match is True when the file matches the record as-is. When the
    file does not match only because its EOL differs from the record,
    match_data carries the content converted to the record EOL: the
    caller writes it to a tmp file and replaces the target without a
    download. match_data is empty when the file is missing, has the
    wrong content or cannot be fixed by converting the EOL.
    mode_matched is True when the file mode matches the record, it is
    only meaningful when match is True.
    """
    # whether the file matches the record as-is
    match: bool
    # content converted to the record EOL, empty when not fixable
    match_data: bytes = b''
    # whether the file mode matches the record, only meaningful when
    # match is True
    mode_matched: bool = True

    def __bool__(self):
        """
        Returns:
            bool: The match flag, so a result can be used as the old
                boolean return of _matches()
        """
        return self.match


class DeployTarget:
    """
    The paths of one deploy target.

    A target is a folder being updated and a ledger key (name):

        {root}/.pack              name='', the project tree target
        {root}/.pack/{name}       a named target, e.g. a python dist
                                  sharing site-packages with others

    The ledger folder holds the index pack (index.pack), the workspace
    of the jobs (workspace/, job.pack inside) and the lock file of the
    target (lock), taken by DeployJob for a whole update flow, see
    DeployJob.locked(). index_rel is the ledger path relative to the
    root: it is the local form of the canonical .pack/index.pack path
    of the packs, see JobBase.local_path().
    """

    # The pack area of the pack format, relative to the target root. The
    # constant keeps the trailing separator so local_path() maps the area
    # with a plain startswith(), without building a prefix on every path;
    # PACK_AREA_DIR is the folder itself (the ledger folder when name is
    # empty), it must be a folder, never a file of a pack.
    PACK_AREA = '.pack/'
    PACK_AREA_DIR = PACK_AREA[:-1]

    def __init__(self, root=None, name=''):
        """
        Args:
            root (str, optional): Folder to update. Defaults to None,
                env.PROJECT_ROOT
            name (str, optional): Ledger key of the target. Defaults to
                '', the ledger folder is {root}/.pack itself
        """
        self.root = env.PROJECT_ROOT if root is None else PathStr.new(root)
        self.name = name or ''
        self.ledger_rel = self.PACK_AREA_DIR if not self.name else f'{self.PACK_AREA}{self.name}'
        self.ledger = self.root.joinpath(self.ledger_rel)
        # path of the index pack relative to the root: the local form
        # of the canonical .pack/index.pack path of the packs
        self.index_rel = f'{self.ledger_rel}/index.pack'
        self.index_file = self.ledger.joinpath('index.pack')
        self.workspace = self.ledger.joinpath('workspace')
        self.job_file = self.workspace.joinpath('job.pack')
        # lock file of the target ledger: a file of its own, never the
        # index pack (every update replaces the index pack atomically,
        # a lock on it would not survive the flow), see
        # DeployJob.locked()
        self.lock_file = self.ledger.joinpath('lock')


class JobBase(DeployTarget):
    """
    Base class of deploy jobs.

    The pack data is passed in __init__, the target (root, name, see
    DeployTarget) is passed together: root defaults to
    env.PROJECT_ROOT and name to '', the project tree target. The
    workspace is the folder where the job stores its temporary files,
    inside the ledger folder of the target.

    The pack format speaks the canonical pack area namespace .pack/**:
    local_path() maps such a path into the ledger folder of the target
    and localize() rewrites a decoded pack once, right after decoding,
    so every later comparison and file operation of the job works on
    the local paths of the target.

    _read_current() and _matches() are shared by every job that
    compares working tree files against the records of a pack,
    replace() / replace_data() / replace_index() and
    cleanup_empty_folders() by every job that applies the changes to
    the real files.
    """

    # fixed name of the new index pack in the workspace: the index
    # pack is always prepared to this file, replace_index() commits
    # it to the local index pack as the last change of the flow
    NEW_INDEX = 'new_index.tmp'

    def __init__(self, data, root=None, name=''):
        """
        Args:
            data (bytes): Pack data
            root (str, optional): Folder to update. Defaults to None,
                env.PROJECT_ROOT
            name (str, optional): Ledger key of the target. Defaults to
                '', the project tree target
        """
        super().__init__(root=root, name=name)
        self._data = data
        self.pending: "list[PendingFile]" = []
        # the new index pack of the flow, the commit record of the
        # local version: the jobs prepare it here, it is not a data
        # file of pending. replace_index() commits it after every data
        # file of replace_data() landed, so a flow that did not fully
        # succeed keeps the old index pack and the next check still
        # sees the update as available. None when the flow has no
        # index pack change to commit
        self.pending_index: "Optional[PendingFile]" = None
        # {path: IdxInfo} of the files the new version records, the
        # emptiness base of cleanup_empty_folders(). Every job sets it
        # to its best knowledge (UnpackJob: the full pack, UpdateJob:
        # the update pack upgraded to the new index, ResetJob /
        # RebuildJob: the new index), {} when unknown: a folder of no
        # record is only removed when os.rmdir() confirms it is empty,
        # so an unknown record set never removes a folder that is not
        # empty
        self.new_fileinfo: "dict[str, IdxInfo]" = {}

    def local_path(self, path):
        """
        Map a path of the pack format to the local path of the target.

        The canonical pack area .pack/... of the packs is mapped into
        the ledger folder of the target: .pack/index.pack becomes
        .pack/{name}/index.pack, .pack/history.pack becomes
        .pack/{name}/history.pack, and so on. Every other path is
        returned unchanged. The mapping is the identity when name is
        empty: the ledger folder is .pack itself then.

        The pack area folder itself is never a file of a pack, the pack
        builder rejects it (it must be a folder): a path equal to it is
        rejected here too, instead of being mapped to the ledger folder.

        Args:
            path (str): Path of the pack format, relative to the root

        Returns:
            str: Local path of the target

        Raises:
            ValueError: If the path is the pack area folder itself
        """
        if path == self.PACK_AREA_DIR:
            raise ValueError(
                f'Pack path is the pack area itself, it must be a folder: {path!r}'
            )
        if not self.name or not path.startswith(self.PACK_AREA):
            return path
        # replace the pack area prefix with the ledger folder
        return self.ledger_rel + path[len(self.PACK_AREA_DIR):]

    def localize(self, decoder):
        """
        Rewrite the pack area paths of a decoded pack to the local
        namespace of the target, see local_path().

        The rewrite covers the three views of the decoder: idx_info
        (the record list, rewritten in place), fileinfo and refinfo
        (cached dicts of the same records, keyed by the path at build
        time and rebuilt here so the keys follow the rewritten paths).
        It must run right after decoding and before any comparison or
        file operation: the paths of the pack (e.g. the index pack
        record .pack/index.pack) and the local paths of the target
        must never be mixed. Nothing to do when name is empty, the
        mapping is the identity then.

        The bytes of the index pack itself always stay canonical (it
        is written to the ledger folder from the pack bytes): the
        rewrite only affects the decoded views, so every reader of the
        ledger gets the same canonical records and localizes them the
        same way.

        Args:
            decoder (PackDecodeBase): Decoder of the pack to rewrite

        Returns:
            PackDecodeBase: The decoder, for call chaining
        """
        if not self.name:
            return decoder
        for info in decoder.idx_info:
            info.path = self.local_path(info.path)
            if info.source_path:
                info.source_path = self.local_path(info.source_path)
        # fileinfo / refinfo share the records with idx_info: rebuild
        # the cached dicts so their keys follow the rewritten paths.
        # The access builds the cache first when it was not built yet,
        # then the set() replaces it with the re-keyed dict.
        InstanceCacheOperation.set(decoder, 'refinfo', {
            info.path: info for info in decoder.refinfo.values()
        })
        InstanceCacheOperation.set(decoder, 'fileinfo', {
            info.path: info for info in decoder.fileinfo.values()
        })
        return decoder

    async def run(self):
        """
        Execute the job, each subclass implements its own run().

        Raises:
            NotImplementedError: Subclasses must implement run()
        """
        raise NotImplementedError

    def replace(self, commit=True):
        """
        Apply the pending changes to the real files: the data files
        first, the new index pack last.

        The index pack is the commit record of the local version, the
        version DeployJob.check() reads: a committed index on a
        partially updated tree would tell the caller the update
        succeeded when it did not. It is therefore committed only
        after every data file landed and only when commit is True, so
        every failure path (an exception during replace_data(),
        records left in error) keeps the old index pack in place. The
        two steps are separate methods (replace_data() /
        replace_index()) for the jobs that need to interleave their
        own changes, a caller must never commit the index first.

        Args:
            commit (bool): True to commit the new index pack after the
                data files. False replaces the data files only and
                keeps the local index pack untouched (the flow did not
                fully succeed). Defaults to True
        """
        self.replace_data()
        if commit:
            self.replace_index()

    def replace_data(self):
        """
        Apply the pending data files to the real files: the writes
        first, the deletions after every write.

        Every tmp file is moved to the target path atomically and the
        deleted markers are removed. The two groups of paths are
        disjoint (a deletion removes a file the new version does not
        have, a write targets a file it has), so applying every write
        before every deletion is safe, whatever the order of the
        pending list is: an interruption leaves the old files in place
        instead of half-deleted. The target is chmod-ed when
        pending.mode is set, the mode decision is made by the job that
        prepared the pending list. The folders left empty by the
        deletions are removed, see cleanup_empty_folders(). The
        workspace is kept, the caller (run()) cleans it up after all
        changes are applied. The index pack is not a data file, see
        pending_index and replace_index().
        """
        applies = [pending for pending in self.pending if pending.info.edit != 2]
        deletes = [pending for pending in self.pending if pending.info.edit == 2]

        # create the parent folders of all targets in one batch
        batch_makedirs([
            self.root.joinpath(pending.info.path)
            for pending in applies
        ])

        for pending in applies:
            target = self.root.joinpath(pending.info.path)
            atomic_replace(pending.tmp, target)
            if pending.mode is not None:
                os.chmod(target, pending.mode)

        for pending in deletes:
            # deleted marker, the file should not exist
            atomic_remove(self.root.joinpath(pending.info.path))

        self.cleanup_empty_folders()

    def replace_index(self):
        """
        Commit the new index pack of the flow, the last change applied.

        The new index pack prepared to pending_index (new_index.tmp in
        the workspace, see NEW_INDEX) is moved to the local index pack
        (index.pack in the ledger folder of the target). A missing
        pending_index is a no-op: the flow has no index pack change to
        commit, e.g. the local index is already the latest one.
        """
        pending = self.pending_index
        if pending is None:
            return
        target = self.root.joinpath(pending.info.path)
        # the ledger folder of the target holds the workspace of the
        # job, it exists by now; the batch keeps the replace below
        # working when it does not (a target updated without a
        # workspace, e.g. a direct job call)
        batch_makedirs([target])
        atomic_replace(pending.tmp, target)
        if pending.mode is not None:
            os.chmod(target, pending.mode)
        self.pending_index = None

    def cleanup_empty_folders(self):
        """
        Remove the folders left empty by the deleted files.

        The deletion candidates are the parent folders of the deletions
        (the deleted markers, the renamed sources and the leftover files
        of the old version, all edit == 2 in pending) and the parent
        folders a removal leaves empty, see CleanupFolder. A candidate
        is removed only when the new version records no file at or below
        it (self.new_fileinfo, the deleted markers are not files) and
        os.rmdir() confirms the folder is empty: a folder that still
        holds a file the update does not manage, e.g. a file the user
        placed by hand, fails the removal and is kept. The index pack
        keeps its folder alive, and the root of the target is never
        removed.
        """
        cleaner = CleanupFolder()
        cleaner.register_deleted({
            pending.info.path
            for pending in self.pending
            if pending.info.edit == 2
        })
        # the files of the new version occupy their folder, e.g.
        # "a/b/c.py" occupies "a/b" and "a". The deleted markers are
        # skipped: they describe files that should not exist
        cleaner.register_file({
            path
            for path, info in self.new_fileinfo.items()
            if info.edit != 2
        })
        # the index pack is never deleted: its folder (the ledger
        # folder) is kept even when every other record of the folder
        # is gone
        cleaner.register_file(self.index_rel)
        for folder in cleaner.get_cleanup_folders():
            # os.rmdir() only removes an empty folder: a folder that
            # still holds a file of no record is kept
            folder_rmtree_empty(self.root.joinpath(folder))

    def cleanup(self):
        """
        Clean the workspace folder atomically.

        The folder is renamed to a tmp name first (atomic), then removed
        slowly, so an interrupted cleanup never leaves a workspace that
        looks unfinished.
        """
        atomic_rmtree(self.workspace)

    def _old_fileinfo_from_index(self):
        """
        Read the fileinfo of the local index pack, the leftover
        deletion base of a rebuild.

        The index is read from the ledger folder of the target and
        rewritten to the local namespace like every decoded pack, see
        localize(). Deleted markers (edit == 2) are excluded: they
        describe files that should not exist, not files that exist. A
        missing, malformed or checksum-failed index pack degrades to
        {}: the leftover cleanup is skipped, the rebuild still
        converges for every file of the new index.

        Returns:
            dict[str, IdxInfo]: Fileinfo of the old local index pack,
                {} when it is missing or malformed
        """
        try:
            decoder = PackDecodeBase(atomic_read_bytes(self.index_file))
            decoder.validate_index()
        except (FileNotFoundError, PackDecodeError) as e:
            logger.warning(f'Failed to read the old index pack: {e}')
            return {}
        self.localize(decoder)
        return {
            path: info for path, info in decoder.fileinfo.items()
            if info.edit != 2
        }

    @staticmethod
    def _leftover_deletions(old_fileinfo, new_fileinfo):
        """
        The leftover files of the old version: recorded in the old
        index but not in the new one, removed by replace_data().

        Args:
            old_fileinfo (dict[str, IdxInfo]): Fileinfo of the old
                index pack, deleted markers excluded
            new_fileinfo (dict[str, IdxInfo]): Fileinfo of the new
                index pack, all records

        Returns:
            list[PendingFile]: Deleted markers (edit == 2) of the
                leftover paths, removed by replace_data()
        """
        return [
            PendingFile(info=IdxInfo(path=path, edit=2), tmp='')
            for path in old_fileinfo
            if path not in new_fileinfo
        ]

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
            if info.sha1 and sha1(blob).digest() != info.sha1:
                return MatchResult(match=False)
            # the content is the record blob, only the EOL decides:
            # every \n of a clean CRLF file is part of a \r\n, and the
            # normalization removed exactly one byte per \r\n
            if data.count(b'\n') == len(data) - len(blob):
                # clean CRLF (or empty), the file matches as-is
                return MatchResult(match=True, mode_matched=JobBase._mode_matches(info, current))
            # the file is LF or mixed, convert the LF blob to CRLF
            return MatchResult(match=False, match_data=blob.replace(b'\n', b'\r\n'))
        if info.eol == 0:
            # the record expects LF, compare the file as-is first:
            # a CRLF file is longer than the LF blob and falls through
            if len(data) == info.size and (
                    not info.sha1 or sha1(data).digest() == info.sha1):
                return MatchResult(match=True, mode_matched=JobBase._mode_matches(info, current))
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
            if info.sha1 and sha1(converted).digest() != info.sha1:
                return MatchResult(match=False)
            return MatchResult(match=False, match_data=converted)
        # binary (eol == 2), compared as-is
        if len(data) != info.size:
            return MatchResult(match=False)
        if info.sha1 and sha1(data).digest() != info.sha1:
            return MatchResult(match=False)
        return MatchResult(match=True, mode_matched=JobBase._mode_matches(info, current))

    @staticmethod
    def _mode_matches(info, current):
        """
        Check if the file mode of a current file matches a record.

        A 644 record (mode == 0) accepts any current mode without
        execute bits, e.g. 666/646/664, a 755 record (mode == 1)
        accepts any with execute bits, e.g. 777/757/775. Any other
        mode is a mismatch. On Windows the mode always matches:
        executability is determined by the file extension, the exec
        bits cannot be set.

        Args:
            info (IdxInfo): Record to check against
            current (CurrentFile): Current file read from the path

        Returns:
            bool: True if the file mode matches the record
        """
        if not env.POSIX:
            # Windows cannot set the exec bits, the mode always matches
            return True
        current_exec = current.mode & 0o111
        if info.mode == 1:
            return current_exec == 0o111
        return current_exec == 0
