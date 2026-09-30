"""
Tests for the pack cache and for building packs from a git repo.

The pack server generates packs from the git repo instead of from stored full
packs: PackFull builds the records of a version, PackUpdate takes the new
version as a PackFull plus the commit and the recorded version of the old one,
and rebuilds the old index pack and the old extra files from the repo. The
tests pin the invariants of the change:

- the rebuilt index pack of a version is byte-identical to the index pack of the
  full pack that was published for it (the update pack patches the old index
  that the clients hold, a different rebuild would corrupt it). The round trip
  of the whole flow is covered by test_unpack_update.py
- the shared PackCache hits across the versions of a run: a content keeps its
  index encoding and its update encoding apart, both keyed by the git blob sha1
  (PackCache.content_index / content_update), and an M / RM patch is keyed by the
  content pair (PackCache.patch), see doc/2026-09-27_update-pack-from-repo.md
- the entries are computed once per key even when two builds need them at the
  same time: the computation runs under the per key lock of PackCache, see
  PackCache._compute_entry
"""
import threading

import pytest

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.pack_model import FileInfo
from alasio.deploy_dev.pack.encode_base import PackEncodeBase
from alasio.deploy_dev.pack.pack_cache import PackCache, PatchCache, PlainCache
from alasio.deploy_dev.pack.pack_repo import PackFull
from alasio.deploy_dev.pack.pack_update import PackUpdate
from alasio.deploy_dev.pack.repo_diff import RepoDiff
from alasio.ext.concurrent.threadpool import ThreadPool
from tests.deploy_dev.pack.conftest import FULL_SCENARIO_NEW, FULL_SCENARIO_OLD, make_repo


def make_pack(repo, commit, **kwargs):
    """
    Build the full pack of a version of the repo.

    Args:
        repo (MockGitRepo): Repo to read
        commit (str): Version to pack
        **kwargs: Arguments passed to PackFull

    Returns:
        bytes: Full pack data
    """
    return b''.join(PackFull(repo, commit=commit, **kwargs).iter_pack_data())


def make_update(new_pack, old_commit, old_pack_version=PackEncodeBase.PACK_VERSION, **kwargs):
    """
    Build the update pack from an old version to the new pack.

    Args:
        new_pack (PackFull): New version
        old_commit (str): Commit sha1 of the old version
        old_pack_version (bytes): Pack format version of the published old pack
        **kwargs: Arguments passed to PackUpdate

    Returns:
        bytes: Update pack data
    """
    return b''.join(PackUpdate(
        new_pack, old_commit, old_pack_version, **kwargs).iter_pack_data())


# ════════════════════════════════════════════════════════════════════════════
#  a repo with the full upgrade scenario
# ════════════════════════════════════════════════════════════════════════════

SCENARIO_REPO = make_repo({'old': FULL_SCENARIO_OLD, 'new': FULL_SCENARIO_NEW})
SCENARIO_OLD_PACK = make_pack(SCENARIO_REPO, 'old')
SCENARIO_NEW_PACK = make_pack(SCENARIO_REPO, 'new')

# ════════════════════════════════════════════════════════════════════════════
#  a repo with two lookback versions and one latest version
# ════════════════════════════════════════════════════════════════════════════

KEEP = b'keep\n'
EDIT_OLD = b'edit me\n' * 50
EDIT_NEW = b'edit me\n' * 49 + b'edited line\n'
SHARED = b'duplicate content\n' * 20
ADDED = b'added file\n' * 40

WINDOW_OLD = {
    'keep.txt': KEEP,
    'edit.txt': EDIT_OLD,
    # same content as dup.txt, the copy must compare as unchanged in the diff
    'copy.txt': SHARED,
    'dup.txt': SHARED,
}
WINDOW_NEW = {
    'keep.txt': KEEP,
    'edit.txt': EDIT_NEW,
    'copy.txt': SHARED,
    'dup.txt': SHARED,
    'added.txt': ADDED,
}
WINDOW_REPO = make_repo({'old1': WINDOW_OLD, 'old2': WINDOW_OLD, 'new': WINDOW_NEW})
WINDOW_NEW_PACK = make_pack(WINDOW_REPO, 'new')
WINDOW_UPDATE = {
    commit: make_update(PackFull(WINDOW_REPO, commit='new'), commit)
    for commit in ('old1', 'old2')
}


# ════════════════════════════════════════════════════════════════════════════
#  packs generated from the repo
# ════════════════════════════════════════════════════════════════════════════


class TestPackFromRepo:
    """A pack built from a git repo must match the published pack."""

    def test_index_pack_matches_published(self):
        """The rebuilt index pack is the index pack of the published full pack."""
        index = bytes(PackDecodeBase(SCENARIO_OLD_PACK).extract_index_pack())
        pack = PackFull(SCENARIO_REPO, commit='old')
        assert pack.index_pack == index

    def test_pack_version_selects_the_encoder(self):
        """A version is rebuilt with the pack format version it was encoded with."""
        index = bytes(PackDecodeBase(SCENARIO_NEW_PACK).extract_index_pack())
        assert PackFull(SCENARIO_REPO, commit='new').pack_version == PackEncodeBase.PACK_VERSION
        pack = PackFull(SCENARIO_REPO, commit='new', pack_version=PackEncodeBase.PACK_VERSION)
        assert pack.pack_version == PackEncodeBase.PACK_VERSION
        assert pack.index_pack == index

    def test_idx_info_matches_decoder(self):
        """The records of a version are the same with and without a stored pack."""
        pack = PackFull(WINDOW_REPO, commit='new')
        left = pack.idx_info
        right = PackDecodeBase(WINDOW_NEW_PACK).idx_info
        assert [info.path for info in left] == [info.path for info in right]
        for mine, decoded in zip(left, right):
            assert mine.edit == decoded.edit
            assert mine.eol == decoded.eol
            assert mine.mode == decoded.mode
            assert mine.size == decoded.size
            assert mine.sha1 == decoded.sha1
            assert mine.source_lookback == decoded.source_lookback
            if mine.edit == 0 and mine.source_lookback:
                # a copy carries no own data, the decoder restores the data
                # fields of the source record instead
                assert mine.data_size == 0
                assert mine.data == b''
            else:
                assert mine.algo == decoded.algo
                assert mine.data_size == decoded.data_size

    @pytest.mark.parametrize('old', ['old1', 'old2'])
    def test_update_pack_from_repo(self, old):
        """The update pack is generated from the repo, the old side included."""
        update = make_update(PackFull(WINDOW_REPO, commit='new'), old)
        assert update == WINDOW_UPDATE[old]

    def test_update_pack_old_version_defaults_to_commit(self):
        """The old pack version defaults to the commit sha1 of the old version."""
        pack = PackUpdate(PackFull(WINDOW_REPO, commit='new'), 'old1')
        assert pack.old_version == 'old1'
        assert pack.old.commit == 'old1'
        assert pack.current_version == 'new'

    def test_update_pack_builds_with_old_pack_version(self):
        """The old side is rebuilt with the pack format version of the old pack."""
        pack = PackUpdate(
            PackFull(WINDOW_REPO, commit='new'), 'old1', PackEncodeBase.PACK_VERSION)
        assert pack.old.pack_version == PackEncodeBase.PACK_VERSION
        assert pack.old.index_pack == bytes(PackDecodeBase(
            make_pack(make_repo({'old1': WINDOW_OLD}), 'old1')
        ).extract_index_pack())

    def test_update_pack_crosses_pack_version(self):
        """The old side is rebuilt with the format version of the old pack."""
        pack = PackUpdate(PackFull(WINDOW_REPO, commit='new'), 'old1', b'\x01')
        assert pack.old.pack_version == b'\x01'
        # the pack format version is the header byte behind b'PACK'
        assert pack.old.index_pack[4:5] == b'\x01'
        # the update pack itself is encoded in the format of the new pack
        assert pack.pack_version == PackEncodeBase.PACK_VERSION

    def test_copied_file_is_unchanged(self):
        """A copy that stays in the new version is not a diff record."""
        diff = RepoDiff(
            PackFull(WINDOW_REPO, commit='old1'), PackFull(WINDOW_REPO, commit='new'))
        assert 'copy.txt' not in diff.diff_info
        assert 'dup.txt' not in diff.diff_info
        assert 'keep.txt' not in diff.diff_info
        # .pack/history.pack is a synthetic record: the history of the latest
        # commits differs between the versions, so it is an M record like in
        # the decoder path
        assert set(diff.diff_info) == {'.pack/history.pack', 'edit.txt', 'added.txt'}

    def test_update_pack_rejects_decoder(self):
        """The new version must be a PackFull, not a decoder of a stored pack."""
        with pytest.raises(ValueError) as e:
            PackUpdate(PackDecodeBase(SCENARIO_NEW_PACK), 'old', b'old')
        assert 'requires a PackFull of the new version' in str(e.value)


# ════════════════════════════════════════════════════════════════════════════
#  cache hits
# ════════════════════════════════════════════════════════════════════════════

class TestPackCacheHit:
    """The tables of the cache must be hit when the same data comes back."""

    def test_content_index_hit(self, cache):
        """The index encoding of a content is reused by the next version."""
        PackFull(WINDOW_REPO, commit='new').fileinfo
        hit, miss = cache.stat['content']
        assert miss > 0
        assert hit == 0
        PackFull(WINDOW_REPO, commit='new').fileinfo
        assert cache.stat['content'][0] == miss
        assert cache.stat['content'][1] == miss

    def test_content_keeps_two_encodings(self, cache):
        """One content keeps the index encoding and the update encoding apart."""
        pack = PackFull(WINDOW_REPO, commit='new')
        pack.fileinfo
        key = pack.filelist['added.txt'].sha1
        index = cache.content_index[key]
        assert cache.content_update.get(key) is None
        make_update(PackFull(WINDOW_REPO, commit='new'), 'old1')
        update = cache.content_update[key]
        # both encode the same content, the rules may pick another algorithm
        assert index.info.sha1 == update.sha1
        assert index.info.size == update.size

    def test_update_pack_hit(self, cache):
        """The second update pack of a run reuses the encodings of the first."""
        for commit in ('old1', 'old2'):
            update = make_update(PackFull(WINDOW_REPO, commit='new'), commit)
            assert update == WINDOW_UPDATE[commit]
        # the added file of the latest version is an A record of both updates,
        # its update encoding is stored next to the index encoding of the file
        assert cache.stat['content'][0] > 0
        # the modified file has the same content pair in both updates
        assert cache.stat['patch'][0] > 0

    def test_a_warm_cache_does_not_change_the_update_pack(self, cache):
        """A build that takes the entries of the previous one gives the same bytes."""
        cold = make_update(PackFull(SCENARIO_REPO, commit='new'), 'old')
        warm = make_update(PackFull(SCENARIO_REPO, commit='new'), 'old')
        assert cold == warm

    def test_cache_does_not_change_index_pack(self, cache):
        """The index pack bytes do not depend on the cache."""
        pack = PackFull(SCENARIO_REPO, commit='new')
        assert pack.index_pack == bytes(PackDecodeBase(SCENARIO_NEW_PACK).extract_index_pack())

    def test_report(self, cache):
        """The cache renders its usage as one log line."""
        PackFull(WINDOW_REPO, commit='new').fileinfo
        report = cache.report()
        assert report.startswith('PackCache: content=0/')
        assert 'patch=0/0' in report
        assert 'data=' in report
        # every computation is over, the lock table is empty again
        assert 'locks=0' in report


# ════════════════════════════════════════════════════════════════════════════
#  the per key locks of the cache
# ════════════════════════════════════════════════════════════════════════════


def _count_lzma_calls(monkeypatch):
    """
    Count the lzma compressions of the pack encoder, from every thread

    Args:
        monkeypatch (MonkeyPatch): Pytest monkeypatch fixture

    Returns:
        list[str]: The contents compressed so far
    """
    import alasio.deploy_dev.pack.pack_repo as pack_repo

    calls = []
    original = pack_repo.lzma_compress

    def counting(data):
        calls.append(data)
        return original(data)

    monkeypatch.setattr(pack_repo, 'lzma_compress', counting)
    return calls


class TestPackCacheEntry:
    """
    The two entry methods of the cache: get reads an entry of a table without a
    lock (None when the table has none), submit computes it in a task of
    PACK_POOL under the lock of the key, once (see PackCache._compute_entry).
    """

    def test_get_reads_an_entry_and_counts_the_hit(self):
        """get reads the table and counts a hit, a miss counts nothing"""
        cache = PackCache()
        assert cache.get(cache.content_index, 'k') is None
        assert cache.stat['content'] == [0, 0]
        assert 'locks=0' in cache.report()
        value = PlainCache(info=FileInfo(path='a.txt', size=3))
        cache.content_index['k'] = value
        assert cache.get(cache.content_index, 'k') is value
        assert cache.stat['content'] == [1, 0]
        # the other encoding of the content is another table
        assert cache.get(cache.content_update, 'k') is None
        assert cache.stat['content'] == [1, 0]

    def test_the_entry_is_computed_once(self):
        """The threads that wait for the lock take the computed entry"""
        cache = PackCache()
        computed = []
        start = threading.Barrier(4)
        value = PlainCache(info=FileInfo(path='a.txt', size=3))
        entries = []

        def compute(entry):
            # the callback runs for every task (it fills the record), only the
            # task that found no stored entry computes
            if entry is None:
                computed.append(1)
            return value

        def worker(_):
            start.wait(timeout=10)
            entries.append(cache.submit(cache.content_index, 'k', compute).get())

        # the threads are the builders (they submit, a worker of PACK_POOL
        # computes), a task must not submit another one, see submit
        threads = [threading.Thread(target=worker, args=(index,)) for index in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive()
        assert len(computed) == 1
        assert all(entry is entries[0] for entry in entries)
        assert cache.stat['content'] == [3, 1]

    def test_the_callback_reuses_the_stored_entry(self):
        """A task that waited for the lock hands the stored entry to the callback"""
        cache = PackCache()
        value = PlainCache(info=FileInfo(path='a.txt', size=3))
        cache.content_index['k'] = value
        seen = []

        def compute(entry):
            seen.append(entry)
            return entry

        assert cache.submit(cache.content_index, 'k', compute).get() is value
        assert seen == [value]
        # the entry came from the cache, nothing was computed
        assert cache.stat['content'] == [1, 0]

    def test_a_failed_computation_leaves_no_lock(self):
        """A task that raised does not keep the lock of the key"""
        cache = PackCache()

        def broken(entry):
            raise RuntimeError('computation failed')

        with pytest.raises(RuntimeError, match='computation failed'):
            cache.submit(cache.content_index, 'k', broken).get()
        assert 'locks=0' in cache.report()
        value = PlainCache(info=FileInfo(path='a.txt', size=3))
        assert cache.submit(cache.content_index, 'k', lambda entry: value).get() is value
        assert 'locks=0' in cache.report()
        assert cache.stat['content'] == [0, 1]

    def test_the_counters_of_the_threads_are_not_lost(self):
        """Every lookup is counted, the counters are updated under a lock"""
        cache = PackCache()
        cache.content_index['k'] = PlainCache(info=FileInfo(path='a.txt', size=3))

        def count(_):
            for _ in range(250):
                cache.get(cache.content_index, 'k')

        ThreadPool(pool_size=4).thread_map(count, range(4))
        assert cache.stat['content'] == [1000, 0]

    def test_every_table_serves_its_entry(self):
        """Every table of the cache is read with get and computed with submit"""
        cache = PackCache()
        assert cache.get(cache.rename, 'pair') is None
        assert cache.submit(cache.rename, 'pair', lambda entry: 7).get() == 7
        assert cache.rename['pair'] == 7
        assert cache.get(cache.rename, 'pair') == 7
        assert cache.stat['rename'] == [1, 1]
        patch = PatchCache(FileInfo(path='a.txt'), True)
        assert cache.submit(cache.patch, 'pair', lambda entry: patch).get() is patch
        assert cache.get(cache.patch, 'pair') is patch
        assert cache.stat['patch'] == [1, 1]
        extra = cache.submit(cache.extra, ('v', 'f'), lambda entry: PlainCache(info=FileInfo(path='f'))).get()
        assert extra.info.path == 'f'
        assert cache.get(cache.extra, ('v', 'f')) is extra
        # the two encodings of a content are separate tables
        update = cache.submit(cache.content_update, 'c', lambda entry: FileInfo(path='a.txt')).get()
        assert cache.get(cache.content_update, 'c') is update
        assert cache.get(cache.content_index, 'c') is None
        index = cache.submit(
            cache.content_index, 'c', lambda entry: PlainCache(info=FileInfo(path='b.txt'))).get()
        assert index.info.path == 'b.txt'
        assert cache.stat['content'] == [1, 2]
        # the .gitattributes state tables are filled under the lock of the table
        with cache.eol.lock:
            cache.eol.setdefault('state', {})['a.txt'] = {'text': 'auto'}
        assert cache.get(cache.eol, 'state') == {'a.txt': {'text': 'auto'}}


class TestPackCacheConcurrency:
    """Two versions built at the same time share the computations of the cache"""

    @staticmethod
    def _make_repo(count=6):
        """
        Repo of distinct contents, every file compresses with lzma

        Args:
            count (int): Number of files. Defaults to 6.

        Returns:
            MockGitRepo:
        """
        return make_repo({'c1': {
            f'data/file_{index}.txt': b'line %d of the file\n' % index * 20
            for index in range(count)
        }})

    def test_two_builds_compress_a_content_once(self, monkeypatch, cache):
        """The two builds of a version compress every content once"""
        repo = self._make_repo()
        calls = _count_lzma_calls(monkeypatch)
        records = ThreadPool(pool_size=2).thread_map(
            lambda _: PackFull(repo, commit='c1').fileinfo, range(2))
        # one encoding of every content plus one of the generated history file:
        # the second build takes the entries the first one stored
        assert len(calls) == 7
        assert records[0] == records[1]
        assert 'locks=0' in cache.report()
