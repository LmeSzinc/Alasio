import re
from threading import Lock

from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_remove, is_tmp_file
from alasio.logger import logger


class TmpManager:
    """
    The tmp file names of a deploy job workspace, thread safe.

    Every tmp file of a job is named {size}_{sha1hex}_{rank}.tmp: size
    and sha1hex describe the content the file holds (the LF blob of the
    record, an empty sha1 for an empty file) and rank is the occurrence
    index of that content among the tmp files of the run.

    The names are applied for in real time, when a caller needs the tmp
    file -- not upfront, one per record of the run: a record whose target
    already matches its content needs no tmp file and consumes no name.
    The leftovers of an interrupted run come first: get() hands the name
    of a leftover of the content out while one is left (the file is
    already there, the caller verifies the content and reuses it without
    a download), and only then a new name, counted from rank 0. The new
    names always stay behind every name that was handed out, so no two
    records ever get the same name (every tmp file is moved to its own
    target by replace_data()).

    The name is a pure function of (content, occurrence index), so a tmp
    file left by an interrupted run is found again by the next run.

    The workspace is scanned by the caller (scan()): the constructor only
    stores the workspace, so the io of the scan runs wherever the caller
    runs it (JobBase.tmp_manager_init runs it in a worker thread). The tmp
    files found by the scan are the leftover pool of each content, see
    scan(). The tmp files a run
    writes are registered per content, see register(): a reader that
    needs a content (the source lookup of a copied record, see
    candidates()) finds the leftovers and the files of the run without
    walking the records. The ranks of a
    content may be non-continuous -- a tmp file may already have been
    moved to its target before the interruption, and a stale file may
    hold any rank -- so every file is indexed on its own and nothing
    assumes the ranks are dense. Entries that do not follow the naming
    rule are not tmp files of a job (the job file job.pack, the new index
    pack new_index.tmp, the tmp files of other mechanisms) and are
    ignored.

    The manager is shared by the threads of a run (the phases of a job
    and the nested job of UpdateJob._validate_remaining() run in worker
    threads): get(), claim(), register(), candidates() and scan() are
    atomic under the lock.
    """

    def __init__(self, workspace):
        """
        Args:
            workspace (str): Workspace folder of the job, it may not
                exist yet (a fresh ledger)
        """
        self.workspace = PathStr.new(workspace)
        # the lock of the mutable state: the leftover pool, the rank
        # counters and the registry, see get() / register()
        self._lock = Lock()
        # {(size, sha1hex): {rank}} of the leftovers of an interrupted
        # run not handed out yet, the pool get() takes from
        self.leftover: "dict[tuple[int, str], set[int]]" = {}
        # {(size, sha1hex): next fresh rank} of get()
        self.next: "dict[tuple[int, str], int]" = {}
        # {(size, sha1hex): {path}} of the tmp files this run wrote (or
        # reused), see register()
        self.written: "dict[tuple[int, str], set[str]]" = {}
        # the leftover pool stays empty until scan() runs, the caller scans
        # (see the class docstring)

    def scan(self):
        """
        Fill the leftover pool with the tmp files currently in the
        workspace.

        Every file of the workspace is parsed on its own, so the ranks of
        a content may be non-continuous, see the class docstring. A file
        that does not follow the naming rule is not a tmp file of a job:
        a leftover of an interrupted atomic write (filename.RID.tmp, see
        is_tmp_file) is removed, anything else is ignored. A rank below
        the counter of its content was handed out already (a claimed
        leftover that is not moved to its target yet, or a fresh name):
        it never returns to the pool, a name is handed out once.
        """
        # {size}_{sha1hex}_{rank}.tmp: sha1hex is the 40 hex chars of the
        # content sha1, empty for an empty file (size 0, sha1 b'')
        pattern = re.compile(r'^(\d+)_([0-9a-f]{40}|)_(\d+)\.tmp$')
        names = list(self.workspace.iter_filenames(ext='.tmp'))
        leftover: "dict[tuple[int, str], set[int]]" = {}
        stale = []
        with self._lock:
            for name in names:
                matched = pattern.match(name)
                if matched is None:
                    # not a tmp file of a job: an interrupted atomic write
                    # is removed, anything else (e.g. new_index.tmp) is
                    # left alone
                    if is_tmp_file(name):
                        stale.append(name)
                    continue
                size, sha1hex, rank = matched.groups()
                key = (int(size), sha1hex)
                if int(rank) < self.next.get(key, 0):
                    # handed out by an earlier get(), it stays claimed
                    continue
                if key not in leftover:
                    leftover[key] = set()
                leftover[key].add(int(rank))
            self.leftover = leftover
        for name in stale:
            file = self.workspace.joinpath(name)
            try:
                atomic_remove(file)
            except OSError as e:
                logger.warning(f'Failed to remove the leftover of an interrupted atomic write '
                               f'"{file}": {e}')
            else:
                logger.info(f'Removed the leftover of an interrupted atomic write: {file}')
        if leftover:
            count = sum(len(ranks) for ranks in leftover.values())
            logger.debug(
                f'Found {count} tmp files left by an interrupted run '
                f'in "{self.workspace}"'
            )

    def get(self, size, sha1):
        """
        Apply for the tmp file name of a record.

        The leftovers of the content come first, the lowest rank first:
        those files are already there and the caller can reuse them
        without a download (it verifies the content, a leftover may be
        partial or stale). When every leftover of the content is claimed,
        the manager hands out a fresh name, counted from rank 0 and kept
        behind every name handed out before, so no two records get the
        same name. The names of a run are the tmp files it writes: a
        record that needed no tmp file (its target already matched, its
        content is read from the working tree) applied for none.

        A reader that picked a leftover itself (the source lookup of a
        copied record, see candidates()) claims it with claim() and uses
        the path it read, it does not come back here.

        Args:
            size (int): Size of the record content, the LF blob
            sha1 (bytes): Sha1 digest of the record content, b'' for an
                empty file

        Returns:
            PathStr: Path of the tmp file in the workspace
        """
        sha1hex = sha1.hex()
        key = (size, sha1hex)
        with self._lock:
            ranks = self.leftover.get(key)
            if ranks:
                # a leftover comes first, the lowest rank first
                rank = min(ranks)
                self._take(key, rank)
            else:
                rank = self.next.get(key, 0)
                self.next[key] = rank + 1
        return self.workspace.joinpath(f'{size}_{sha1hex}_{rank}.tmp')

    def claim(self, size, sha1, rank):
        """
        Claim a leftover a reader picked (see candidates()).

        The file the reader read becomes the one it writes: it is
        removed from the pool so no other record gets the name, and the
        fresh names of the content continue behind it. The caller uses
        the path it read itself, no name is built here. Only a leftover
        of an interrupted run can be claimed: a tmp file of this run
        (registered for its content) is never in the pool, it belongs to
        the record that wrote it and is moved to the target of that
        record by replace_data().

        Args:
            size (int): Size of the record content, the LF blob
            sha1 (bytes): Sha1 digest of the record content
            rank (int): Rank of the leftover to claim

        Returns:
            bool: True when the leftover was still in the pool and is
                claimed now, False when it was handed out already (the
                caller falls back to get())
        """
        key = (size, sha1.hex())
        with self._lock:
            return self._take(key, rank)

    def _take(self, key, rank):
        """
        Remove a leftover rank from the pool, the caller holds the lock.

        The fresh names of the content are kept behind every claimed
        leftover, so a fresh name never repeats the name of a leftover
        that was handed out before.

        Args:
            key (tuple[int, str]): Content key
            rank (int): Rank of the leftover to claim

        Returns:
            bool: True when the rank was in the pool
        """
        ranks = self.leftover.get(key)
        if ranks is None or rank not in ranks:
            return False
        ranks.discard(rank)
        if not ranks:
            del self.leftover[key]
        if self.next.get(key, 0) <= rank:
            self.next[key] = rank + 1
        return True

    def register(self, size, sha1, tmp):
        """
        Register a tmp file this run wrote (or reused) for its content.

        A reader that needs that content (the source lookup of a copied
        record, see candidates()) finds the file in the registry instead
        of walking the records of the run, and the name is never handed
        out by get() again: it belongs to the record that registered it
        (the reader only reads it). A file that holds no content of a
        record (the fixed new_index.tmp of an update pack) is not a tmp
        file of the manager and is never registered.

        Args:
            size (int): Size of the record content, the LF blob
            sha1 (bytes): Sha1 digest of the record content
            tmp (str): Path of the tmp file holding the content
        """
        key = (size, sha1.hex())
        with self._lock:
            if key not in self.written:
                self.written[key] = set()
            self.written[key].add(str(tmp))

    def candidates(self, size, sha1):
        """
        The tmp files that may already hold a content, to read.

        The leftovers of an interrupted run not handed out yet first, in
        rank order: a reader that uses one claims it (see claim()) and
        the file it read becomes the tmp file it writes. Then the tmp
        files this run registered for the content (see register()), in
        path order: they are read only, every one of them is the tmp
        file of the record that wrote it and replace_data() moves it to
        the target of that record. The caller probes the files and uses
        the first one that passes the content check.

        Args:
            size (int): Size of the record content, the LF blob
            sha1 (bytes): Sha1 digest of the record content

        Returns:
            list[tuple[int, PathStr]]: (rank, path) of the candidates,
                in the order above; rank is -1 for a registered tmp
                file, which no record can claim: claim() only takes
                from the leftovers of an interrupted run
        """
        sha1hex = sha1.hex()
        key = (size, sha1hex)
        with self._lock:
            candidates = [
                (rank, self.workspace.joinpath(f'{size}_{sha1hex}_{rank}.tmp'))
                for rank in sorted(self.leftover.get(key, ()))
            ]
            candidates += [
                (-1, PathStr.new(tmp)) for tmp in sorted(self.written.get(key, ()))
            ]
        return candidates
