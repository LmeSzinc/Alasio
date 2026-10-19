"""
Local presence probe: what a local tree supplies for an index pack.

The rebuild flow can apply a version to a target in two ways: verify
every record against the working tree and fetch the records that fail
with range requests of the full pack (per file), or download the whole
full pack once and unpack it locally (full pack). The per-file way saves
every byte the tree already holds, the full pack way saves every request
(one request transfers everything). LocalProbe measures the only input
that decides between them: how much of the stored bytes of the pack the
local tree supplies, and how many blocks would still need a range
request.

The probe groups the records of the pack into the distinct stored blocks
of its data section first (stored_blocks()): the records of a block - a
C record and its source for example - share the exact stored bytes, so
the per-file way fetches a block at most once. Every record path is then
checked with os.stat only: a regular file at the path counts as present,
whatever its content. That is the cheapest reading of the tree (no file
is opened) and the optimistic one (the per-file validation covers the
content later, an existing file of wrong content only costs the fetch
the probe assumed to save; a false full-pack choice on the other hand
only costs bandwidth, never correctness).

The blocks are checked from the largest one down, in waves on the thread
pool (PROBE_CHUNK blocks per job, PROBE_WAVE jobs at once): the decision
is evaluated when a wave is done, so the scan still stops before the many
small records behind the large ones, at most one wave behind the point a
serial scan would have stopped at. PresenceResult carries the decision
(prefer_full_pack()) and the accounting at the point the scan stopped:
complete is False when blocks were skipped, the byte counters are then
lower bounds of the full accounting. The verdict stays exact either way:
an early stop only fires when the outcome cannot change any more.

The probe is driven by an index pack (a full pack decodes the same way,
but the rebuild flow has the index pack decoded already): the caller
passes the decoder, localized to the local namespace of the target (see
JobBase.localize()), and the root its record paths are relative to. One
os.stat per record path is the io of the estimate - thousands of stats
on a large pack - so the caller runs LocalProbe.presence() in a worker
thread, like every other local phase of a deploy job. The scan must not
run on a worker of THREAD_POOL itself: its waves wait for that pool.
"""
import os
import stat

from msgspec import Struct

from alasio.ext.cache import cached_property
from alasio.ext.concurrent.threadpool import THREAD_POOL
from alasio.ext.path import PathStr

# Share of the stored bytes of a pack a local tree must supply for the
# per-file way (validate the tree, fetch the failed records) to beat
# downloading the full pack: below it the tree reuses too little of the
# pack to justify the per-record range requests, the whole full pack is
# downloaded once instead. The policy default proposed on the measured
# pack (1/5 of 106 MB is ~21 MB).
PRESENT_RATIO = 1 / 5

# Highest number of blocks the per-file way may still have to fetch (one
# range request per missing block, the fetches are serial): above the
# budget the round trips of a large pack cost more wall clock than one
# full pack download, however few bytes the tree reuses. The accounting
# is per block, a block shared by several records counts once.
REQUEST_BUDGET = 100

# Blocks checked by one job of the presence scan. The scan is io bound (one
# os.stat per record path) and a pool dispatch costs ~15us per job, so one
# job per block is pure dispatch overhead: the chunk amortizes it over the
# stats of the blocks it carries.
#
# Measured on the Alas2026 checkout (7638 files, 7635 blocks, 108.9 MiB,
# warm cache, pool_size=8):
#
#   chunk     full scan     early stop     extra blocks
#   serial      467 ms         2.31 ms              0
#   8           120 ms         2.28 ms             27
#   16          123 ms         1.87 ms             27
#   32          108 ms         3.35 ms            155
#   64          112 ms         7.08 ms            411
#   128         115 ms        13.64 ms            923
#   256         103 ms        28.55 ms           1947
#   512         108 ms        57.23 ms           3995
#
# The absolute times move with the machine and the file system cache, the
# shape of the table is what the choice is based on.
# The full scan (the tree supplies every block, the per-file way is chosen
# and every block is checked anyway) is flat from 32 blocks on, the dispatch
# is amortized there. The early stop (a tree that supplies too little, the
# full pack is chosen) is where the chunk costs: every block of the wave is
# checked even when the decision is certain inside it, and a large chunk
# turns the 2ms shortcut into 13-57ms. 8 keeps the shortcut within one wave
# (64 blocks, the extra stats of the table) while the full scan stays close
# to its floor.
PROBE_CHUNK = 8

# Jobs sent to the thread pool at once, one per worker of the default pool.
# The decision is evaluated between waves, so a wave is the granularity of
# the early stop: at most PROBE_WAVE * PROBE_CHUNK blocks behind the point a
# serial scan would have stopped at are checked.
PROBE_WAVE = 8


class StoredBlock(Struct):
    """
    One stored block of the pack data section and the records referencing it.

    The block is the unit of transfer: the records referencing it share
    the exact stored bytes, the per-file way fetches it at most once
    whatever the number of references (a C record reads the content of
    its source instead of fetching the same range again).
    """
    # offset of the stored bytes in the full pack file, the start of the
    # range request of the block
    data_start: int
    # length of the stored bytes, the stored size of every referencing
    # record
    data_size: int
    # paths of the referencing records, in index order
    paths: "list[str]"


class PresenceResult(Struct):
    """
    The presence accounting of a local tree against the stored blocks of a
    pack.

    Every field is accounted per distinct stored block, the byte fields
    sum the data_size of the blocks (the byte counts are of the stored,
    i.e. compressed, bytes, the unit of a range request - not of the
    unpacked files). The counters are complete when every block of the
    pack was checked, the state at the point the scan stopped otherwise
    (see LocalProbe.presence()): they are then lower bounds of the full
    accounting, while prefer_full_pack() stays the exact decision.
    """
    # stored bytes of the blocks whose every referencing record is present
    # locally
    present_bytes: int
    # stored bytes of every block of the pack, the size of its data section
    total_bytes: int
    # blocks with at least one missing referencing record: at most one
    # range request each when the records are fetched per file
    missing_blocks: int
    # False when the scan stopped before every block was checked: the
    # decision was already certain, the counters are the state at the stop
    complete: bool = True

    @property
    def present_ratio(self):
        """
        Share of the stored bytes the local tree supplies.

        Returns:
            float: present_bytes / total_bytes, 1.0 when the pack has no
                data section (nothing to fetch either way)
        """
        if not self.total_bytes:
            return 1.0
        return self.present_bytes / self.total_bytes

    def prefer_full_pack(self):
        """
        Decide the way to apply the pack: full pack download or per-file.

        The per-file way (validate every record, fetch the failed ones) is
        cheaper when the tree supplies at least PRESENT_RATIO of the
        stored bytes and at most REQUEST_BUDGET blocks are missing: it
        then saves the present bytes at a bounded number of range
        requests. Every other case prefers the full pack: one request
        transfers the data section once, however few bytes the tree
        reuses. The decision holds for a partial accounting too: the scan
        only stops early when the missing counters already decide it (see
        LocalProbe.presence()).

        Returns:
            bool: True when the full pack download is the cheaper way
        """
        return self.present_ratio < PRESENT_RATIO or self.missing_blocks > REQUEST_BUDGET


class LocalProbe:
    """
    The presence probe of an index pack against a local tree.

    The probe is built for the decoder of a pack (the index pack of the
    rebuild flow) and run against the root of a target:

        probe = LocalProbe(decoder)
        result = probe.presence(root)
        if result.prefer_full_pack():
            ...  # the full pack download is the cheaper way

    The blocks are checked from the largest stored block down (see
    blocks): the stored bytes, the currency of the decision, are
    dominated by the large blocks, so the scan stops as soon as the
    decision cannot change any more (see presence()) and the many small
    records behind the large ones are never checked. The scan runs in
    waves on the thread pool (see PROBE_CHUNK and PROBE_WAVE).
    """

    def __init__(self, decoder):
        """
        Args:
            decoder (PackDecodeBase): Decoder of the pack (the index pack
                of the rebuild flow), localized to the local namespace of
                the target, see JobBase.localize()
        """
        self.decoder = decoder

    @cached_property
    def blocks(self):
        """
        The stored blocks of the pack, largest first.

        Returns:
            list[StoredBlock]: The blocks in scan order: by stored size
                descending, equal sizes in data offset order
        """
        return sorted(stored_blocks(self.decoder), key=lambda block: -block.data_size)

    @cached_property
    def total_bytes(self):
        """
        Returns:
            int: Stored bytes of every block of the pack, the size of
                its data section
        """
        return sum(block.data_size for block in self.blocks)

    def presence(self, root):
        """
        Scan a local tree for the blocks of the pack.

        Every record path of every checked block is checked with os.stat
        only, no content is read: a regular file at the path counts as
        present, a missing path, a folder or a dangling symbolic link
        counts as missing. A block is present when every record
        referencing it is present, the per-file way then fetches nothing
        for it; a block with at least one missing record needs at most
        one range request, whatever the number of referencing records.

        The blocks are checked from the largest one down, in waves on the
        thread pool: PROBE_WAVE jobs of PROBE_CHUNK blocks are checked at
        once and the decision is evaluated when the wave is done, so the
        scan stops as soon as the decision of the result is certain:

        - the missing blocks are over REQUEST_BUDGET (one range request
          each, the rest of the scan cannot bring them back under it);
        - the present bytes plus every unchecked byte stay below
          PRESENT_RATIO of the pack (even a tree that held every
          unchecked record could not keep the per-file way).

        Both stops only skip the blocks behind the large ones the
        decision is made of; the returned accounting is flagged partial
        (complete is False) and its counters are lower bounds of the
        full accounting. The blocks of the wave that made the decision
        are checked anyway, at most PROBE_WAVE * PROBE_CHUNK of them (see
        PROBE_CHUNK for the choice). A decision that only becomes certain
        on the last block skips nothing: the accounting is complete then.

        The caller runs this in a worker thread: one os.stat per record
        path is the io of the estimate, thousands of stats on a large
        pack. It must not run on a worker of THREAD_POOL: the waves wait
        for that pool.

        Args:
            root (str): Root folder of the target, the record paths are
                relative to it

        Returns:
            PresenceResult: The accounting of the tree, complete False
                when the scan stopped before every block was checked
        """
        root = PathStr.new(root)
        total_bytes = self.total_bytes
        # the share of the stored bytes the scan can still reach at best:
        # the bytes found present so far plus every unchecked byte. Below
        # the ratio bound, even a tree that held every unchecked record
        # could not keep the per-file way
        bound = PRESENT_RATIO * total_bytes
        present_bytes = 0
        missing_blocks = 0
        remaining = total_bytes
        complete = True
        blocks = self.blocks
        index = 0
        while index < len(blocks):
            # one wave: PROBE_WAVE jobs of PROBE_CHUNK blocks, the blocks
            # of a wave are checked in parallel and their results are
            # merged here when the wave is done
            jobs = []
            wave_bytes = 0
            for _ in range(PROBE_WAVE):
                piece = blocks[index:index + PROBE_CHUNK]
                if not piece:
                    break
                index += len(piece)
                wave_bytes += sum(block.data_size for block in piece)
                jobs.append(THREAD_POOL.start_thread_soon(_check_blocks, root, piece))
            for job in jobs:
                got_present, got_missing = job.get()
                present_bytes += got_present
                missing_blocks += got_missing
            remaining -= wave_bytes
            if remaining <= 0:
                # the last block was checked: nothing is left to skip, the
                # accounting is complete whatever the bounds below say
                continue
            if missing_blocks > REQUEST_BUDGET:
                # one range request per missing block: over the budget
                # the full pack is certain, the unchecked blocks cannot
                # bring the requests back
                complete = False
                break
            if present_bytes + remaining < bound:
                # the best case of the unchecked blocks stays below the
                # ratio bound: the full pack is certain
                complete = False
                break
        return PresenceResult(
            present_bytes=present_bytes,
            total_bytes=total_bytes,
            missing_blocks=missing_blocks,
            complete=complete,
        )


def stored_blocks(decoder):
    """
    Group the records of a pack into the distinct stored blocks of its data
    section.

    The records of a block share the exact stored bytes: a C (copied)
    record references the block of its source, and the encoder stores one
    block for the records of identical content. Deleted markers (edit ==
    2) and records without data (empty files, R records) have no block:
    nothing is fetched for them either way, they are left out of the
    accounting. An empty file still matches, its absence from the blocks
    is the whole accounting of it.

    Args:
        decoder (PackDecodeBase): Decoder of the pack, localized to the
            local namespace of the target

    Returns:
        list[StoredBlock]: The blocks in data offset order
    """
    blocks: "dict[tuple[int, int], StoredBlock]" = {}
    for path, info in decoder.fileinfo.items():
        if info.edit == 2 or not info.data_size:
            continue
        key = (info.data_start, info.data_size)
        block = blocks.get(key)
        if block is None:
            blocks[key] = StoredBlock(
                data_start=key[0], data_size=key[1], paths=[path])
        else:
            block.paths.append(path)
    return [blocks[key] for key in sorted(blocks)]


def _check_blocks(root, blocks):
    """
    Check a chunk of blocks, the task of presence() on the thread pool.

    The task only stats the record paths of its blocks, no state is shared
    with the other tasks: the caller merges the results of a wave.

    Args:
        root (PathStr): Root folder of the target
        blocks (list[StoredBlock]): Blocks of the chunk

    Returns:
        tuple[int, int]: (stored bytes of the present blocks, missing blocks)
    """
    present_bytes = 0
    missing_blocks = 0
    for block in blocks:
        if _block_present(root, block.paths):
            present_bytes += block.data_size
        else:
            missing_blocks += 1
    return present_bytes, missing_blocks


def _block_present(root, paths):
    """
    Check whether every record of a block is present in the local tree.

    Args:
        root (PathStr): Root folder of the target
        paths (list[str]): Paths of the referencing records

    Returns:
        bool: True when a regular file exists at every path
    """
    for path in paths:
        try:
            st = os.stat(root.joinpath(path))
        except OSError:
            # missing, a parent that is not a folder, an unreadable path
            return False
        if not stat.S_ISREG(st.st_mode):
            # a folder or a special file holds no content of the record
            return False
    return True
