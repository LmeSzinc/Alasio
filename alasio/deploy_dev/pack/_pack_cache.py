"""
In-process cache of the pack server pipeline and the thread pool its entries are
computed on.

The pack server builds one full pack of the latest version and one update pack
from every lookback version to the latest one. Both sides encode the same
content again and again, the cache removes the repeated work:

- a content is encoded once per rule set, keyed by the **git blob sha1** (the
  hex str the git tree carries) of the file: rebuilding any version that shares
  the content skips the blob read and the compression. Two encodings of one
  content are kept apart because both must keep their exact bytes: the index
  encoding (raw / lzma, what the index pack of a version stores, it must stay
  byte-identical to the index pack the clients hold) and the update encoding
  (raw / lzma / zstd, what an A record of an update pack carries)
- an encoded content pair (old content sha1, new content sha1) is an M / RM
  patch: a patch depends on the old content as the zstd dictionary and on
  nothing else, so versions that share the pair share the patch, renames
  included
- the plain zstd candidate of a content (what _load_data compares a patch
  against) lives on the entry of the content (see PlainCache): a content is the
  new side of a record in every version of a run, so the first record that
  really tries the candidate compresses it and every other record takes the
  bytes from the entry. The candidate is expensive (56ms for the 230KB index
  pack) and it is compressed on demand, the skip of _load_data (a patch far
  smaller than the plain best) keeps a version that does not need it from
  paying for it

The cache is the module level PACK_CACHE: every PackFull / PackUpdate of the
process shares it, so the tables live across the versions of a run. It is not
persisted, every entry is a pure function of the repo content, a later run
recomputes what it needs.

- the resolved .gitattributes attributes of a path (what the eol of its record
  is decided from) are kept per .gitattributes state, and the eol of a
  text="auto" path is kept per (path, content): the .gitattributes files of a
  repo rarely change and a content rarely changes twice, so the versions of a
  run share the resolutions and a version resolves only the paths and the
  contents its predecessors did not see, see PackFull._populate_eol

Measured on AzurLaneAutoScript (see doc/2026-09-27_update-pack-from-repo.md):
200 lookback versions need 1,522 unique A record encodings and 1,842 unique
patches against 222,218 and 32.9 times that many record occurrences, the cache
is worth about 29 minutes per run for some 26MB.

Every table is read and written by every thread that builds a version of the
run, through get (a lookup, no lock) and submit (the computation of one entry as
a task of PACK_POOL, see _compute_entry): an entry is computed once per key even
when two builds need it at the same time, the task takes the per key lock of the
entry and reuses the entry another build stored.

The tasks run on the thread pool of the module, PACK_POOL (see
PackCache.submit). Building a pack compresses thousands of contents: the data
section of a full pack holds every file of the version, and an update pack
compresses the records its diff produced. The compressors (lzma, zstd, the C
accelerators called through ctypes) release the GIL while they run, so their
batch runs on a thread pool instead of the single thread that walks the files,
see doc/2026-09-27_update-pack-from-repo.md section 7.29.

The pool is dedicated to this module. The shared THREAD_POOL runs the blocking
calls of the whole process (device IO, http, cmd): a pack build queues thousands
of compressions on it and would starve them. Its size is the physical core
count of the machine, see get_max_worker: the physical cores are the width that
pays off for a compression batch, the hyperthreads only add contention
(measured 4.06x on 6 physical cores against 4.52x on the 12 logical ones of the
same machine, see the same document section 7.29.2).

The pool blocks a caller while every worker of it is busy -- that is the
backpressure that bounds the jobs in flight, see PackFull._populate_data -- so
the tasks submitted to it must not submit tasks of it themselves: such a task
would wait for a worker that can not come free while it waits.
"""
from threading import Lock
from typing import Optional

from msgspec import Struct

from alasio.deploy.pack.pack_model import FileInfo
from alasio.ext.concurrent.processpool import get_max_worker
from alasio.ext.concurrent.threadpool import ThreadPool

# Thread pool of the pack encoders, one worker per physical core of the machine
# (a single worker when the count can not be detected: a narrow pool only costs
# time, while a blind wider one would oversubscribe the machine).
PACK_POOL = ThreadPool(pool_size=get_max_worker() or 1)


class Table(dict):
    """
    A table of the cache: a dict of entries that carries its name, the counters
    of its lookups (see PackCache.report) and its lock.

    The lock of a table is for the table that is resolved in one go instead of
    one entry at a time: the .gitattributes state tables hold a dict of resolved
    paths per state and are filled under the lock of the table, see
    PackFull._populate_eol. The entries of the other tables are computed one by
    one, their lock is per key and lives in the cache (see
    PackCache._compute_entry).

    Attributes:
        name (str): Name of the table, the row of report
        hit (int): Lookups that found an entry
        miss (int): Entries the cache computed
        lock (Lock): Lock of the table
    """

    def __init__(self, name):
        super().__init__()
        self.name = name
        self.hit = 0
        self.miss = 0
        self.lock = Lock()


class PatchCache(Struct):
    """
    The M / RM encoding of one content pair.

    Attributes:
        info (FileInfo): Encoding with the update pack rules
            (RepoDiff._load_modified: raw / lzma / zstd patch-from / zstd)
        patch_used (bool): True when info.data is the zstd patch from the old
            content, the record then references the old file and the client
            decompresses with it
    """

    info: FileInfo
    patch_used: bool


class PlainCache(Struct):
    """
    The plain encodings of a content: the cache entry of a version file, of a
    generated extra file, and the cache_info of _load_data.

    _load_data builds the stored data of a record out of the plain encodings of
    the content (raw / lzma / zstd) and, when the record has an old file, out of
    the zstd patch from it. Both plain inputs of that comparison live on the
    entry of the content (see PackCache.content_index and PackCache.extra), so a
    version that shares the content with an earlier one compresses nothing:

        cache_info = entry                       # the cache entry of the content
        PackFull._load_data(info, data, cache_info=cache_info, zstd_source=old)

    The info slot is filled by the lookup that builds the entry, the zstd slot
    by _load_data: a record that needs the candidate compresses it and leaves it
    here, every next record of the content takes it from the entry, no lookup.
    The candidate is compressed in the thread of that record, and only when the
    comparison needs it (see PackFull._load_data): the update side builds the
    lookback versions of a run concurrently, a build that submitted the
    compression to PACK_POOL would wait for a worker that is busy with another
    build of the same run, see doc/2026-09-27_update-pack-from-repo.md 7.29.1
    and 7.35.

    Attributes:
        info (FileInfo | None): Cached raw / lzma encoding of the content, the
            bar the patch is measured against, None when the cache has none
        zstd (FileInfo | None): Plain zstd candidate of the content, None until
            a record compares it (and while the cache has none). An entry of
            another size is not the content and is replaced
    """

    info: Optional[FileInfo] = None
    zstd: Optional[FileInfo] = None


class PackCache:
    """
    Shared cache of the encoded content and the encoded patches.

    The API is two methods: get reads an entry of a table (None when the table
    has none) and submit computes it in a task of PACK_POOL (see
    _compute_entry). The caller passes the table itself, one of the attributes
    below.

    Attributes:
        content_index (Table): {git blob sha1 hex: PlainCache} encoding of a
            content with the rules of the index of the full pack (raw / lzma),
            see PackFull._populate_data
        content_update (Table): {git blob sha1 hex: FileInfo} encoding of a
            content with the rules of an A record of an update pack
            (raw / lzma / zstd), see RepoDiff._load_added
        patch (Table): {(old content sha1, new content sha1): PatchCache}, see
            RepoDiff._load_modified
        extra (Table): {(version, filepath): PlainCache} of the generated extra
            files (the index pack, the commit history), they are not files of
            the repo and have no git blob sha1, see PackFull._extra_cache_info
        rename (Table): {(deleted git blob sha1 hex, added git blob sha1 hex):
            int} zstd patch length of the rename scores, see RepoDiff._similarity
        eol (Table): {gitattributes fingerprint: {path or (path, content):
            attrs or eol}} of the version files, one table per .gitattributes
            state of the repo, see PackFull._populate_eol
        stat (dict[str, list[int]]): [hit, miss] of every table, by name (the
            two encodings of a content share the row of 'content'), see report

    Usage:
        cache = PACK_CACHE
        pack = PackFull(repo, commit)
        update = PackUpdate(pack, old_commit)
        logger.info(cache.report())
    """

    # names of the tables, the rows of report
    TABLES = ('content', 'patch', 'extra', 'rename', 'eol')

    def __init__(self):
        self.content_index = Table('content')
        self.content_update = Table('content')
        self.patch = Table('patch')
        self.extra = Table('extra')
        self.rename = Table('rename')
        self.eol = Table('eol')
        # per key locks of the entries, {(table name, key): Lock}, see _compute_entry
        self._lock_guard = Lock()
        self._locks: "dict[tuple, Lock]" = {}
        # the counters of the tables are updated by every thread
        self._stat_lock = Lock()

    def get(self, table, key):
        """
        Read an entry of a table, None when the table has none

        The lookup takes no lock and computes nothing, see submit for the
        computation: a stored entry is a pure function of the repo and never
        changes, so a version that shares it with the versions built before it
        reads it directly. The hit is counted, a lookup that finds nothing is
        not (the miss is counted when the entry is computed).

        Args:
            table (Table): Table to read, one of the attributes of the cache
            key: Key of the entry

        Returns:
            The entry, None when the table has none
        """
        entry = table.get(key)
        if entry is not None:
            with self._stat_lock:
                table.hit += 1
        return entry

    def submit(self, table, key, compute):
        """
        Submit the computation of an entry to PACK_POOL, it is computed once

        A caller that needs the entry now calls _compute_entry directly in its
        own thread, a round trip through the pool would only add latency.

        The task takes the lock of the key and computes the entry, see
        _compute_entry. A task must not call submit: the pool blocks the caller
        while every worker of it is busy, a task that waits for a free worker
        would wait for itself.

        Args:
            table (Table): Table of the entry, one of the attributes of the cache
            key: Key of the entry, the lock is per key
            compute (Callable): The computation, see _compute_entry

        Returns:
            Job: The task, get() returns the entry
        """
        return PACK_POOL.start_thread_soon(self._compute_entry, table, key, compute)

    def _compute_entry(self, table, key, compute):
        """
        Take the lock of a key, compute its entry when the table has none

        The task of submit, it runs in a worker of PACK_POOL. It takes the per
        key lock of the entry, reads the table again and hands the stored entry
        to compute -- the task that waited for another build reuses the entry
        that build stored, so an entry is computed once even when two builds
        need it at the same time. The lock is removed once the entry is done
        (also on an exception, a table that kept a lock per key that failed once
        would cost memory for nothing). Callers submit from the thread that
        builds the version, see PackFull._populate_data.

        Args:
            table (Table): Table of the entry
            key: Key of the entry, the lock is per key
            compute (Callable): compute(cache) -- cache is the stored entry of
                the key, None when the table has none -- fills the record of the
                caller and returns the entry to keep: the stored one to reuse
                it, or the computed one to store

        Returns:
            The entry
        """
        entry_key = (table.name, key)
        with self._lock_guard:
            try:
                lock = self._locks[entry_key]
            except KeyError:
                lock = self._locks[entry_key] = Lock()
        try:
            with lock:
                stored = table.get(key)
                entry = compute(stored)
                with self._stat_lock:
                    if entry is stored:
                        table.hit += 1
                    else:
                        table[key] = entry
                        table.miss += 1
                return entry
        finally:
            # remove the lock to reduce memory, unless it was replaced while
            # this call held it (another thread that failed and started over)
            with self._lock_guard:
                if self._locks.get(entry_key) is lock:
                    del self._locks[entry_key]

    @property
    def stat(self):
        """
        [hit, miss] of every table, by name, the counters that report renders

        Returns:
            dict[str, list[int]]: {'content': [hit, miss], ...}, the two
                encodings of a content share the row of 'content'
        """
        stat = {}
        for table in (
                self.content_index, self.content_update, self.patch,
                self.extra, self.rename, self.eol):
            row = stat.setdefault(table.name, [0, 0])
            row[0] += table.hit
            row[1] += table.miss
        return stat

    def file_size(self):
        """
        Total size of the encoded data held by the cache

        Returns:
            int: Bytes of the data, without the overhead of the records
        """
        size = 0
        size += sum(len(entry.data) for entry in self.content_update.values())
        for table in (self.content_index, self.extra):
            for entry in table.values():
                if entry.info is not None:
                    size += len(entry.info.data)
                if entry.zstd is not None:
                    size += len(entry.zstd.data)
        size += sum(len(entry.info.data) for entry in self.patch.values())
        return size

    def report(self):
        """
        Render the cache usage as one log line

        Every table is rendered as ``name=hit/lookup``, the lookup count is
        hits plus misses. ``locks`` is the number of entries that are being
        computed right now, it is 0 between the versions of a run (the locks
        are removed once an entry is computed, see _compute_entry).

        Returns:
            str: Hit rate and size of every table
        """
        rows = [
            f'{name}={hit}/{hit + miss}' for name, (hit, miss) in self.stat.items()
        ]
        entries = len(self.content_index.keys() | self.content_update.keys())
        return (
            f'PackCache: {", ".join(rows)}, '
            f'entries={entries}/{len(self.patch)}/{len(self.extra)}, '
            f'locks={len(self._locks)}, '
            f'data={self.file_size() / 1048576:.1f}MB'
        )


# The cache of the process: the pack server builds the full pack and every update
# pack of a run with it, and the pack modules always read it through this module
# (_pack_cache.PACK_CACHE), so a test or a benchmark can swap the whole cache for a
# fresh one. See the module docstring for what it holds.
PACK_CACHE = PackCache()
