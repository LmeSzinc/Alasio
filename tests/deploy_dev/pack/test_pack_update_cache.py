"""
Tests for the update pack cache across the versions of a lookback window.

The pack server packs one lookback version after another, from the newest to the
oldest, all through the shared PACK_CACHE: a version is packed against the latest
one, and the cache holds the latest version plus every newer version that was
packed before it. The scenarios below are written by hand (small git repos built
in this file, no pack of a real repository and no external library is involved)
and pin the cases of the caching behavior:

1. a file added in the latest version is an A record of every lookback version:
   the newest lookback version encodes it once, the older versions reuse it
2. a file whose content a version shares with the newer version (the next commit
   did not change it) reuses the patch the newer version encoded
3. a file the next commit changed holds a content only the older versions have,
   every version that still has it encodes it again
4. both files the next commit changed miss together
5. the misses of a version do not grow with the cumulative difference to the
   latest version: they stay the size of a single commit

The content table of the cache is keyed by the **git blob sha1** of a file of a
version and holds up to two encodings of it (the index rules and the update
rules), the patch table is keyed by the **content sha1** pair (old content, new
content). A miss is one encoding that had to be done, so the tests count the
freshly filled slots and name them by their keys.
"""
from hashlib import sha1

from alasio.deploy_dev.pack.pack_repo import PackFull
from alasio.deploy_dev.pack.pack_update import PackUpdate
from tests.deploy_dev.pack.conftest import make_repo


def content_sha1(content):
    """
    Content sha1 of a blob, the key of the patch table

    Args:
        content (bytes): File content

    Returns:
        bytes: 20 bytes digest
    """
    return sha1(content).digest()


def blob_sha1(repo, commit, path):
    """
    Git blob sha1 hex of a file of a version, the key of the content table

    Args:
        repo (MockGitRepo): Repo of the versions
        commit (str): Commit sha1
        path (str): File path

    Returns:
        str: 40 chars hex digest
    """
    return repo.list_files(commit)[path].sha1


def cache_slots(cache):
    """
    {git blob sha1 hex: (index encoded, update encoded)} of the content tables

    Args:
        cache (PackCache): Cache of the run

    Returns:
        dict[str, tuple[bool, bool]]:
    """
    return {
        key: (key in cache.content_index, key in cache.content_update)
        for key in sorted(set(cache.content_index) | set(cache.content_update))
    }


def pack_window(versions, cache):
    """
    Pack every lookback version of the repo to the latest one

    The versions are packed from the newest to the oldest, the order of the pack
    server, they all read and fill the process wide PACK_CACHE (the cache
    fixture of the test, fresh for it). The latest version is packed first, it
    fills the cache the way the published full pack does.

    Args:
        versions (dict[str, dict[str, bytes]]): {commit: {path: content}} in
            time order, the oldest commit first, the last commit is the latest
            version
        cache (PackCache): Cache of the run, the one the pack modules read

    Returns:
        tuple: (repo, list of row), the rows are in packing order (the newest
            lookback version first), every row has the commit, the misses of the
            version (the encodings it had to do) and the records of its update
            pack
    """
    commits = list(versions)
    repo = make_repo(versions)
    new_pack = PackFull(repo, commits[-1])
    new_pack.fileinfo
    new_pack.index_pack
    rows = []
    for commit in reversed(commits[:-1]):
        slots = cache_slots(cache)
        patch_keys = set(cache.patch)
        update = PackUpdate(new_pack, commit)
        update.diff_info
        update.refinfo
        data = b''.join(update.iter_pack_data())
        now = cache_slots(cache)
        content_index = {
            key for key, slot in now.items() if slot[0] and not slots.get(key, (False, False))[0]
        }
        content_update = {
            key for key, slot in now.items() if slot[1] and not slots.get(key, (False, False))[1]
        }
        rows.append({
            'commit': commit,
            'content_miss': len(content_index) + len(content_update),
            'patch_miss': len(set(cache.patch) - patch_keys),
            'content_index': content_index,
            'content_update': content_update,
            'patch': set(cache.patch) - patch_keys,
            'records': len(update.fileinfo),
            'data': data,
        })
    return repo, rows


# ════════════════════════════════════════════════════════════════════════════
#  1. a file added in the latest version
# ════════════════════════════════════════════════════════════════════════════

KEEP = b'keep\n'
ADDED = b'added content\n' * 20
ADDED_VERSIONS = {
    'older': {'keep.txt': KEEP},
    'newest': {'keep.txt': KEEP},
    'latest': {'keep.txt': KEEP, 'added.txt': ADDED},
}


class TestPackUpdateCacheAddedFile:
    """A file of the latest version is an A record of every lookback version."""

    def test_added_file_encoded_once(self, cache):
        """The newest lookback version encodes the file, the older one reuses it."""
        repo, rows = pack_window(ADDED_VERSIONS, cache)
        newest, older = rows
        assert newest['commit'] == 'newest'
        # the A record of added.txt needs the update encoding, the files of the
        # version itself are all shared with the latest one
        assert newest['content_index'] == set()
        assert newest['content_update'] == {blob_sha1(repo, 'latest', 'added.txt')}
        assert newest['patch_miss'] == 1
        # the older version shares added.txt with the newer one: the encoding
        # is served by the cache, only the generated history pack is new
        assert older['content_miss'] == 0
        assert older['patch_miss'] == 1

    def test_added_file_data_is_identical(self, cache):
        """Both update packs carry the same encoded data of the added file."""
        repo, _ = pack_window(ADDED_VERSIONS, cache)
        new_pack = PackFull(repo, 'latest')
        new_pack.fileinfo
        records = []
        for commit in ('newest', 'older'):
            update = PackUpdate(new_pack, commit)
            records.append(update.fileinfo['added.txt'])
        first, second = records
        assert first.edit == 0
        assert first.data
        assert (first.algo, first.size, first.data_size, first.data) == (
            second.algo, second.size, second.data_size, second.data)


# ════════════════════════════════════════════════════════════════════════════
#  2. one file shared with the newer version, one file changed by it
# ════════════════════════════════════════════════════════════════════════════

A1 = b'A version 1\n' * 40
A2 = b'A version 2\n' * 40
B1 = b'B version 1\n' * 40
B2 = b'B version 2\n' * 40

# A was changed after the newest lookback version: the latest holds A2 while
# both lookback versions still hold A1, so the newest version encodes the patch
# of A. B was changed by the newest lookback version: it holds B2 and the older
# version still holds B1, so only the older version needs B.
AB_VERSIONS = {
    'older': {'A.txt': A1, 'B.txt': B1},
    'newest': {'A.txt': A1, 'B.txt': B2},
    'latest': {'A.txt': A2, 'B.txt': B2},
}


class TestPackUpdateCacheSharedRevision:
    """A content shared with the newer version reuses its encoding."""

    def test_newest_version_encodes_a(self, cache):
        """The newest version holds A's old revision only, it encodes it."""
        repo, rows = pack_window(AB_VERSIONS, cache)
        newest = rows[0]
        # the index pack of the version needs A1 (the latest holds A2) and B2 is
        # the content of the latest version itself
        assert newest['content_index'] == {blob_sha1(repo, 'newest', 'A.txt')}
        assert newest['content_update'] == set()
        assert (content_sha1(A1), content_sha1(A2)) in cache.patch

    def test_older_version_reuses_a_and_encodes_b(self, cache):
        """A's patch is served by the cache, only B needs a new one."""
        repo, rows = pack_window(AB_VERSIONS, cache)
        older = rows[1]
        pair_a = (content_sha1(A1), content_sha1(A2))
        pair_b = (content_sha1(B1), content_sha1(B2))
        # the older version holds B1, the revision the newer version replaced,
        # while its A is the same revision the newer version already encoded
        assert older['content_index'] == {blob_sha1(repo, 'older', 'B.txt')}
        assert pair_a not in older['patch']
        assert pair_b in older['patch']
        # the patch of B plus the generated history pack
        assert older['patch_miss'] == 2
        assert older['records'] > rows[0]['records']


# ════════════════════════════════════════════════════════════════════════════
#  3. both files changed by the newer version
# ════════════════════════════════════════════════════════════════════════════

C1 = b'C version 1\n' * 40
AB_TOGETHER_VERSIONS = {
    'older': {'A.txt': A1, 'B.txt': B1},
    'newest': {'A.txt': A2, 'B.txt': B2},
    'latest': {'A.txt': A2, 'B.txt': B2, 'C.txt': C1},
}


class TestPackUpdateCacheChangedTogether:
    """Files changed by the newer version are encoded again."""

    def test_both_files_encoded_again(self, cache):
        """A and B both differ from the newer version, both miss the cache."""
        repo, rows = pack_window(AB_TOGETHER_VERSIONS, cache)
        newest, older = rows
        pair_a = (content_sha1(A1), content_sha1(A2))
        pair_b = (content_sha1(B1), content_sha1(B2))
        # the newest version has A2 / B2 and the latest adds C.txt: only the A
        # record of C.txt needs the update encoding, plus the history patch
        assert newest['content_update'] == {blob_sha1(repo, 'latest', 'C.txt')}
        assert newest['patch_miss'] == 1
        # the older version still holds the revisions the newest one replaced
        assert older['content_index'] == {
            blob_sha1(repo, 'older', 'A.txt'), blob_sha1(repo, 'older', 'B.txt')}
        assert {pair_a, pair_b} <= older['patch']
        assert older['patch_miss'] == 3
        # C.txt is shared with the newer version, it is served by the cache
        assert blob_sha1(repo, 'latest', 'C.txt') not in older['content_index']
        assert blob_sha1(repo, 'latest', 'C.txt') not in older['content_update']


# ════════════════════════════════════════════════════════════════════════════
#  4. the misses do not grow with the cumulative difference
# ════════════════════════════════════════════════════════════════════════════

WINDOW_COUNT = 8
# the file of a commit keeps the content that commit gave it: every commit
# changes exactly one file (the one of its own number) and keeps an old revision
# of every file of the commits before it, so the difference to the latest
# version grows with the distance while the work of a version stays one commit
WINDOW_VERSIONS = {
    f'c{number}': {
        f'f{other}.txt': (
            f'file {other} at commit {other}\n' if number >= other else f'file {other} initial\n'
        ).encode() * 20
        for other in range(1, WINDOW_COUNT + 1)
    }
    for number in range(1, WINDOW_COUNT + 1)
}


class TestPackUpdateCacheMissSize:
    """The misses of a version are the size of a single commit."""

    def test_miss_stays_constant(self, cache):
        """Every version misses one content and two patches, whatever the distance."""
        repo, rows = pack_window(WINDOW_VERSIONS, cache)
        # every commit changed one file: the revision the version kept of it
        # (the index pack of the version needs it) and the patch to the latest
        # one, plus the generated history patch
        assert [row['content_miss'] for row in rows] == [1] * len(rows)
        assert [row['patch_miss'] for row in rows] == [2] * len(rows)
        # the missed content is the revision of the file the next commit changed
        newest = rows[0]
        assert newest['commit'] == f'c{WINDOW_COUNT - 1}'
        assert newest['content_index'] == {
            blob_sha1(repo, newest['commit'], f'f{WINDOW_COUNT}.txt')}
        assert newest['content_update'] == set()

    def test_records_grow_with_distance(self, cache):
        """The update pack records do grow, the misses do not follow them."""
        repo, rows = pack_window(WINDOW_VERSIONS, cache)
        records = [row['records'] for row in rows]
        # the newest lookback version differs from the latest by one file, the
        # oldest one by every file
        assert records == sorted(records)
        assert records[0] < records[-1]
        assert rows[0]['content_miss'] + rows[0]['patch_miss'] == (
            rows[-1]['content_miss'] + rows[-1]['patch_miss'])


# ════════════════════════════════════════════════════════════════════════════
#  5. the plain zstd candidate of a content is shared by the versions
# ════════════════════════════════════════════════════════════════════════════


class TestPackUpdateZstdCandidate:
    """
    _load_data compares the patch of a record against the plain zstd candidate
    of its content, and the content is the new side of a record in every version
    of a run: the candidate is compressed once per content and shared, see
    PackFull._load_data and doc/2026-09-27_update-pack-from-repo.md 7.34
    """

    def test_the_history_candidate_is_compressed_once(self, monkeypatch, cache):
        """Every update pack carries the history of the latest version, one candidate"""
        import alasio.deploy_dev.pack.pack_repo as pack_repo

        repo = make_repo(ADDED_VERSIONS)
        new_pack = PackFull(repo, 'latest')
        new_pack.fileinfo
        history = new_pack.extra_content['.pack/history.pack']
        calls = []
        original = pack_repo.zstd_compress

        def counting(data, *args, **kwargs):
            if kwargs.get('source') is None and data == history:
                calls.append(1)
            return original(data, *args, **kwargs)

        monkeypatch.setattr(pack_repo, 'zstd_compress', counting)
        for commit in ('newest', 'older'):
            update = PackUpdate(new_pack, commit)
            update.diff_info
            b''.join(update.iter_pack_data())
        # one candidate for both of the versions
        assert len(calls) == 1

    def test_the_candidate_does_not_change_the_pack(self, monkeypatch):
        """The pack of a run that shares the cache equals the pack of a cold run"""
        from alasio.deploy_dev.pack import pack_cache

        repo = make_repo(ADDED_VERSIONS)
        monkeypatch.setattr(pack_cache, 'PACK_CACHE', pack_cache.PackCache())
        cold_pack = PackFull(repo, 'latest')
        cold_pack.fileinfo
        cold = b''.join(PackUpdate(cold_pack, 'newest').iter_pack_data())
        # the second run finds every encoding of the first one, the candidate
        # of the extra files included
        warm_pack = PackFull(repo, 'latest')
        warm_pack.fileinfo
        warm = b''.join(PackUpdate(warm_pack, 'newest').iter_pack_data())
        assert cold == warm
