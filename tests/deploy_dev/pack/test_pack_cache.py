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
  (PackCache.content), and an M / RM patch is keyed by the content pair
  (PackCache.patch), see doc/2026-09-27_update-pack-from-repo.md
"""
import pytest

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy_dev.pack.encode_base import PackEncodeBase
from alasio.deploy_dev.pack.pack_cache import PackCache
from alasio.deploy_dev.pack.pack_repo import PackFull
from alasio.deploy_dev.pack.pack_update import PackUpdate
from alasio.deploy_dev.pack.repo_diff import RepoDiff
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

def make_cached_scenario_update():
    """
    Build the scenario update pack with a shared cache.

    Returns:
        bytes: Update pack data
    """
    cache = PackCache()
    return make_update(
        PackFull(SCENARIO_REPO, commit='new', cache=cache), 'old', cache=cache)


class TestPackCacheHit:
    """The tables of the cache must be hit when the same data comes back."""

    def test_content_index_hit(self):
        """The index encoding of a content is reused by the next version."""
        cache = PackCache()
        PackFull(WINDOW_REPO, commit='new', cache=cache).fileinfo
        hit, miss = cache.stat['content']
        assert miss > 0
        assert hit == 0
        PackFull(WINDOW_REPO, commit='new', cache=cache).fileinfo
        assert cache.stat['content'][0] == miss
        assert cache.stat['content'][1] == miss

    def test_content_keeps_two_encodings(self):
        """One content keeps the index encoding and the update encoding apart."""
        cache = PackCache()
        pack = PackFull(WINDOW_REPO, commit='new', cache=cache)
        pack.fileinfo
        entry = cache.content[bytes.fromhex(pack.filelist['added.txt'].sha1)]
        assert entry.index is not None
        assert entry.update is None
        make_update(PackFull(WINDOW_REPO, commit='new', cache=cache), 'old1', cache=cache)
        assert entry.update is not None
        # both encode the same content, the rules may pick another algorithm
        assert entry.index.sha1 == entry.update.sha1
        assert entry.index.size == entry.update.size

    def test_update_pack_hit(self):
        """The second update pack of a run reuses the encodings of the first."""
        cache = PackCache()
        for commit in ('old1', 'old2'):
            update = make_update(
                PackFull(WINDOW_REPO, commit='new', cache=cache), commit, cache=cache)
            assert update == WINDOW_UPDATE[commit]
        # the added file of the latest version is an A record of both updates,
        # its update encoding is stored next to the index encoding of the file
        assert cache.stat['content'][0] > 0
        # the modified file has the same content pair in both updates
        assert cache.stat['patch'][0] > 0

    def test_cache_does_not_change_update_pack(self):
        """A cached run produces the same bytes as an uncached one."""
        assert make_update(
            PackFull(SCENARIO_REPO, commit='new'), 'old'
        ) == make_cached_scenario_update()

    def test_cache_does_not_change_index_pack(self):
        """The index pack bytes do not depend on the cache."""
        cache = PackCache()
        pack = PackFull(SCENARIO_REPO, commit='new', cache=cache)
        assert pack.index_pack == bytes(PackDecodeBase(SCENARIO_NEW_PACK).extract_index_pack())

    def test_report(self):
        """The cache renders its usage as one log line."""
        cache = PackCache()
        PackFull(WINDOW_REPO, commit='new', cache=cache).fileinfo
        report = cache.report()
        assert report.startswith('PackCache: content=0/')
        assert 'patch=0/0' in report
        assert 'data=' in report
