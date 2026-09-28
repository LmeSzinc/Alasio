"""
Tests for RepoDiff: compare two versions of a git repo, produce the diff records.

The tests build the versions as MockGitRepo commits and read the contents from
the repo like production does: no stored pack and no decoder is involved, the
diff logic (unchanged / modified / added / deleted / renamed / copied) runs on
the same records the pack server builds.
"""
import pytest

from alasio.deploy.pack.pack_model import RefInfo
from alasio.deploy_dev.pack.pack_cache import PackCache
from alasio.deploy_dev.pack.pack_repo import PackFull
from alasio.deploy_dev.pack.repo_diff import RepoDiff, UpdateInfo
from tests.deploy_dev.pack.conftest import (
    FULL_SCENARIO_NEW, FULL_SCENARIO_OLD, code_lines, damage, damage_lines, make_repo, random_bytes
)


def real_records(records):
    """
    Records of the files of the versions, without the generated extra files.

    Every version carries generated extras as normal records (e.g.
    .pack/history.pack, whose content includes the commit sha1 and therefore
    always differs between two commits), the tests below are about the files of
    the versions, so they filter the extras out.

    Args:
        records (dict): {path: record}

    Returns:
        dict: {path: record} without the extras
    """
    return {path: record for path, record in records.items() if not path.startswith('.pack/')}


def make_diff(old, new, **kwargs):
    """
    Build a RepoDiff of two versions from {path: content | (content, mode)} dicts.

    Args:
        old (dict[str, bytes | tuple[bytes, int]]): Old files
        new (dict[str, bytes | tuple[bytes, int]]): New files
        **kwargs: Arguments passed to RepoDiff

    Returns:
        RepoDiff:
    """
    repo = make_repo({'old': old, 'new': new}, message='')
    return RepoDiff(
        PackFull(repo, commit='old'),
        PackFull(repo, commit='new'),
        **kwargs,
    )


# ════════════════════════════════════════════════════════════════════════════
#  similarity
# ════════════════════════════════════════════════════════════════════════════


class TestSimilarity:
    """Tests for RepoDiff.similarity."""

    def test_identical_content(self):
        """Identical contents score ~1."""
        content = b'line one\nline two\n' * 100
        sim = RepoDiff.similarity(content, content)
        assert sim > 0.9

    def test_small_modification(self):
        """A small modification keeps a high score."""
        old = b'def func(x):\n    return x * 2\n' * 100
        new = damage(old, 0.05, seed=1)
        sim = RepoDiff.similarity(old, new)
        assert sim > 0.5

    def test_unrelated_content(self):
        """Unrelated contents score ~0."""
        old = random_bytes(4096, 'old')
        new = random_bytes(4096, 'new')
        sim = RepoDiff.similarity(old, new)
        assert sim < 0.5

    def test_levels_agree(self):
        """A fast level scores close to the slow level."""
        old = b'def func(x):\n    return x * 2\n' * 100
        new = damage(old, 0.1, seed=2)
        sim_fast = RepoDiff.similarity(old, new, level=3)
        sim_slow = RepoDiff.similarity(old, new, level=22)
        assert abs(sim_fast - sim_slow) < 0.05


# ════════════════════════════════════════════════════════════════════════════
#  basic diffs
# ════════════════════════════════════════════════════════════════════════════


class TestRepoDiffBasic:
    """Basic diff types: unchanged, added, deleted, modified."""

    def test_unchanged_absent(self):
        """Unchanged files are left out of the diff."""
        diff = make_diff({'a.txt': b'hello'}, {'a.txt': b'hello'})
        assert real_records(diff.diff_info) == {}
        assert real_records(diff.refinfo) == {}

    def test_added(self):
        """A new file becomes an A record with data."""
        diff = make_diff({'a.txt': b'old'}, {'a.txt': b'old', 'new.txt': b'new content\n'})
        info = diff.diff_info['new.txt']
        assert info.edit == 0
        assert info.source_path == ''
        assert info.size == len(b'new content\n')
        assert info.data_size > 0

    def test_added_empty(self):
        """An empty new file becomes an A record without data."""
        diff = make_diff({}, {'empty.txt': b''})
        info = diff.diff_info['empty.txt']
        assert info.edit == 0
        assert info.size == 0
        assert info.data_size == 0
        assert info.sha1 == b''

    def test_deleted(self):
        """A removed file becomes a D record keyed by the old path."""
        diff = make_diff({'gone.txt': b'delete me'}, {})
        info = diff.diff_info['gone.txt']
        assert info.edit == 2
        assert info.source_path == ''

    def test_modified(self):
        """A changed file becomes an M record with a zstd patch."""
        lines = code_lines(400)
        diff = make_diff(
            {'a.txt': b''.join(lines)},
            {'a.txt': damage_lines(lines, 0.05, seed=1)},
        )
        info = diff.diff_info['a.txt']
        assert info.edit == 1
        assert info.source_path == 'a.txt'
        assert info.algo == 2
        assert info.data_size < info.size

    def test_modified_small_plain(self):
        """A tiny modified file stores plain data, no source."""
        diff = make_diff({'a.txt': b'x'}, {'a.txt': b'y'})
        info = diff.diff_info['a.txt']
        assert info.edit == 1
        assert info.source_path == ''
        assert info.algo == 0

    def test_deleted_markers_ignored(self):
        """The synthetic D markers of a version are not diffed."""
        # pkg/a.py without pkg/__init__.py makes the pack add a D marker for
        # pkg/__init__.py, it is not a file of any version
        diff = make_diff({'pkg/a.py': b'hello'}, {'pkg/a.py': b'hello'})
        assert real_records(diff.diff_info) == {}

    def test_mode_change_only(self):
        """A mode change with identical content is an M record with the new mode."""
        diff = make_diff(
            {'run.sh': (b'#!/bin/sh\n', 755)},
            {'run.sh': (b'#!/bin/sh\n', 644)},
        )
        info = diff.diff_info['run.sh']
        assert info.edit == 1
        assert info.mode == 0


# ════════════════════════════════════════════════════════════════════════════
#  renames
# ════════════════════════════════════════════════════════════════════════════


class TestRepoDiffRename:
    """Rename detection: R (pure) and RM (renamed + modified)."""

    def test_pure_rename(self):
        """Same content at a new path becomes an R record, no data."""
        diff = make_diff(
            {'a.txt': b'hello world\n' * 20},
            {'moved.txt': b'hello world\n' * 20},
        )
        info = diff.diff_info['moved.txt']
        assert info.edit == 3
        assert info.source_path == 'a.txt'
        assert info.data_size == 0
        # the old path is moved, not deleted
        assert 'a.txt' not in diff.diff_info
        assert 'a.txt' in diff.refinfo

    def test_rename_modify(self):
        """A renamed file with changes becomes an RM record with a patch."""
        lines = code_lines(4000)
        diff = make_diff(
            {'a.txt': b''.join(lines)},
            {'moved.txt': damage_lines(lines, 0.05, seed=3)},
        )
        info = diff.diff_info['moved.txt']
        assert info.edit == 3
        assert info.source_path == 'a.txt'
        assert info.algo == 2
        assert info.data_size > 0
        assert 'a.txt' in diff.refinfo

    def test_unrelated_not_renamed(self):
        """Unrelated contents are not matched, the files become D and A."""
        diff = make_diff(
            {'a.txt': random_bytes(4096, 'old')},
            {'b.txt': random_bytes(4096, 'new')},
        )
        assert diff.diff_info['a.txt'].edit == 2
        assert diff.diff_info['b.txt'].edit == 0
        assert diff.refinfo == {}

    def test_min_similarity(self, monkeypatch):
        """MIN_SIMILARITY controls whether a pair is matched as a rename."""
        old = {'a.txt': b'def func(x):\n    return x * 2\n' * 60}
        new = {'b.txt': damage(b'def func(x):\n    return x * 2\n' * 60, 0.1, seed=4)}
        # default threshold: matched as RM
        diff = make_diff(old, new)
        assert diff.diff_info['b.txt'].edit == 3
        assert diff.diff_info['b.txt'].source_path == 'a.txt'
        # high threshold: not matched, D + A instead
        monkeypatch.setattr(RepoDiff, 'MIN_SIMILARITY', 0.9)
        diff = make_diff(old, new)
        assert diff.diff_info['a.txt'].edit == 2
        assert diff.diff_info['b.txt'].edit == 0

    def test_one_to_one_matching(self):
        """Every old file is the source of at most one rename."""
        old = {'a.txt': b'def func(x):\n    return x * 2\n' * 60}
        new = {
            'b1.txt': damage(b'def func(x):\n    return x * 2\n' * 60, 0.05, seed=5),
            'b2.txt': damage(b'def func(x):\n    return x * 2\n' * 60, 0.05, seed=6),
        }
        diff = make_diff(old, new)
        renamed = [path for path, info in diff.diff_info.items() if info.edit == 3]
        added = [path for path, info in diff.diff_info.items() if info.edit == 0]
        assert len(renamed) == 1
        assert len(added) == 1
        assert diff.diff_info[renamed[0]].source_path == 'a.txt'
        assert 'a.txt' not in diff.diff_info

    def test_size_filter(self):
        """Pairs with very different sizes are not rename candidates."""
        diff = make_diff({'a.txt': b'x' * 1000}, {'b.txt': (b'x' + b'y') * 5000})
        assert diff.diff_info['a.txt'].edit == 2
        assert diff.diff_info['b.txt'].edit == 0

    def test_empty_files_not_renamed(self):
        """Empty files are never rename candidates."""
        diff = make_diff({'a.txt': b''}, {'b.txt': b''})
        assert diff.diff_info['a.txt'].edit == 2
        assert diff.diff_info['b.txt'].edit == 0


# ════════════════════════════════════════════════════════════════════════════
#  copies
# ════════════════════════════════════════════════════════════════════════════


class TestRepoDiffCopied:
    """Content dedup: C records reference the source instead of carrying data."""

    def test_copy_from_unchanged_old_file(self):
        """A new file with the content of an unchanged old file is a C record."""
        diff = make_diff(
            {'keep.txt': b'copy me\n'},
            {'keep.txt': b'copy me\n', 'copy.txt': b'copy me\n'},
        )
        info = diff.diff_info['copy.txt']
        assert info.edit == 0
        assert info.source_path == 'keep.txt'
        # the copied record keeps its own info, the encoder ignores it
        assert info.data_size == len(b'copy me\n')
        assert 'keep.txt' in diff.refinfo

    def test_copy_chain_new_files(self):
        """Identical new files reference the nearest earlier record."""
        content = b'shared content\n'
        diff = make_diff({}, {'a1.txt': content, 'a2.txt': content, 'a3.txt': content})
        assert diff.diff_info['a1.txt'].source_path == ''
        assert diff.diff_info['a2.txt'].source_path == 'a1.txt'
        assert diff.diff_info['a3.txt'].source_path == 'a2.txt'
        assert diff.refinfo == {}

    def test_crlf_source_copied(self):
        """A CRLF old file can be a copy source, the copy keeps its own eol."""
        attributes = b'*.txt text eol=crlf\n'
        old = {'.gitattributes': attributes, 'keep.txt': b'copy me\n'}
        new = {'.gitattributes': attributes, 'keep.txt': b'copy me\n', 'copy.txt': b'copy me\n'}
        diff = make_diff(old, new)
        info = diff.diff_info['copy.txt']
        assert info.edit == 0
        assert info.source_path == 'keep.txt'
        # only the content matters, the copy keeps its own eol
        assert info.eol == 1
        assert 'keep.txt' in diff.refinfo

    def test_755_source_copied(self):
        """A 755 old file can be a copy source, the copy keeps its own mode."""
        diff = make_diff(
            {'keep.sh': (b'#!/bin/sh\n', 755)},
            {'keep.sh': (b'#!/bin/sh\n', 755), 'copy.sh': (b'#!/bin/sh\n', 755)},
        )
        info = diff.diff_info['copy.sh']
        assert info.edit == 0
        assert info.source_path == 'keep.sh'
        assert info.mode == 1

    def test_modified_to_existing_is_copied(self):
        """A file modified to match an unchanged old file becomes a C record."""
        diff = make_diff(
            {'a.txt': b'content x\n' * 5, 'keep.txt': b'content y\n' * 5},
            {'a.txt': b'content y\n' * 5, 'keep.txt': b'content y\n' * 5},
        )
        info = diff.diff_info['a.txt']
        assert info.edit == 0
        assert info.source_path == 'keep.txt'
        assert 'keep.txt' in diff.refinfo

    def test_modified_to_added_is_copied(self):
        """A modified file keeps the data, the new file with the same content copies from it."""
        diff = make_diff(
            {'a.txt': b'old content\n' * 10},
            {'a.txt': b'new content\n' * 10, 'copy.txt': b'new content\n' * 10},
        )
        diff_info = diff.diff_info
        # a.txt is modified first (new pack order), it keeps the patch data
        assert diff_info['a.txt'].edit == 1
        assert diff_info['a.txt'].source_path == 'a.txt'
        # copy.txt is added with the same content, it is copied from a.txt
        assert diff_info['copy.txt'].edit == 0
        assert diff_info['copy.txt'].source_path == 'a.txt'

    def test_modified_files_dedup(self):
        """Two files modified to the same content: the later one is copied from the earlier."""
        diff = make_diff(
            {'aa.txt': b'old aa\n' * 10, 'bb.txt': b'old bb\n' * 10},
            {'aa.txt': b'same new\n' * 10, 'bb.txt': b'same new\n' * 10},
        )
        diff_info = diff.diff_info
        # aa.txt sorts first, it keeps the M record, bb.txt copies from it
        assert diff_info['aa.txt'].edit == 1
        assert diff_info['bb.txt'].edit == 0
        assert diff_info['bb.txt'].source_path == 'aa.txt'

    def test_copy_chain_nearest(self):
        """Identical records reference the nearest source, the first is the unchanged old file."""
        content = b'shared content\n'
        diff = make_diff(
            {'orig.txt': content},
            {'orig.txt': content, 'a1.txt': content, 'a2.txt': content, 'a3.txt': content},
        )
        diff_info = diff.diff_info
        assert diff_info['a1.txt'].source_path == 'orig.txt'
        assert diff_info['a2.txt'].source_path == 'a1.txt'
        assert diff_info['a3.txt'].source_path == 'a2.txt'
        assert 'orig.txt' in diff.refinfo

    def test_copy_keeps_info(self):
        """A copied record keeps its own info, the encoder ignores it."""
        content = b'shared content\n' * 50
        diff = make_diff({}, {'first.txt': content, 'second.txt': content})
        first = diff.diff_info['first.txt']
        second = diff.diff_info['second.txt']
        assert second.edit == 0
        assert second.source_path == 'first.txt'
        # the copied record keeps its own size and data
        assert second.size == len(content)
        assert second.data_size > 0
        assert second.sha1 == first.sha1


# ════════════════════════════════════════════════════════════════════════════
#  ref paths
# ════════════════════════════════════════════════════════════════════════════


class TestRepoDiffRefinfo:
    """refinfo reports the old file records referenced by the diff."""

    def test_modified_patch_source(self):
        """An M record with patch data references the old file."""
        lines = code_lines(400)
        diff = make_diff(
            {'a.txt': b''.join(lines)},
            {'a.txt': damage_lines(lines, 0.05, seed=1)},
        )
        assert set(diff.refinfo) == {'a.txt'}

    def test_modified_plain_no_source(self):
        """An M record with plain data references no old file."""
        diff = make_diff({'a.txt': b'x'}, {'a.txt': b'y'})
        assert diff.refinfo == {}

    def test_rename_sources(self):
        """R and RM records reference their old files."""
        old = {
            'pure.txt': b'pure rename\n' * 10,
            'mod.txt': b'def old():\n    return 1\n' * 100,
        }
        new = {
            'pure_moved.txt': b'pure rename\n' * 10,
            'mod_moved.txt': b'def old():\n    return 2\n' * 100,
        }
        diff = make_diff(old, new)
        assert set(diff.refinfo) == {'pure.txt', 'mod.txt'}

    def test_copy_from_new_not_referenced(self):
        """A copy source that is a new file is not a ref path."""
        content = b'shared\n'
        diff = make_diff({}, {'first.txt': content, 'second.txt': content})
        assert diff.diff_info['second.txt'].source_path == 'first.txt'
        assert diff.refinfo == {}

    def test_copy_modified_releases_old_source(self):
        """A modified file keeps the data, the old file is not referenced."""
        diff = make_diff({'a.txt': b'x'}, {'a.txt': b'y', 'b.txt': b'y'})
        # a.txt is modified first (new pack order), it keeps the data
        assert diff.diff_info['a.txt'].edit == 1
        assert diff.diff_info['a.txt'].source_path == ''
        # b.txt is added with the same content, it is copied from a.txt
        assert diff.diff_info['b.txt'].edit == 0
        assert diff.diff_info['b.txt'].source_path == 'a.txt'
        # the old a.txt is not referenced by any record
        assert diff.refinfo == {}


# ════════════════════════════════════════════════════════════════════════════
#  input validation
# ════════════════════════════════════════════════════════════════════════════


class TestRenameScoreCache:
    """
    The rename score of a content pair is cached by the git blob sha1 pair, see
    doc/2026-09-27_update-pack-from-repo.md section 7.21
    """

    OLD = {'a.txt': b'def func(x):\n    return x * 2\n' * 60}

    def _new(self):
        """
        A 10% damaged copy of OLD, a rename candidate

        Returns:
            dict[str, bytes]: Files of the new version
        """
        return {'b.txt': damage(b'def func(x):\n    return x * 2\n' * 60, 0.1, seed=4)}

    def test_the_second_run_takes_the_cached_score(self, monkeypatch):
        """The same pair is scored once, the next diff takes the entry"""
        import alasio.deploy_dev.pack.repo_diff as repo_diff

        cache = PackCache()
        calls = []
        original = repo_diff.zstd_compress

        def counting(*args, **kwargs):
            calls.append(kwargs.get('source') is not None)
            return original(*args, **kwargs)

        monkeypatch.setattr(repo_diff, 'zstd_compress', counting)
        first = make_diff(self.OLD, self._new(), cache=cache).diff_info
        assert calls == [True]
        second = make_diff(self.OLD, self._new(), cache=cache).diff_info
        # no compression at all, the score comes from the cache
        assert calls == [True]
        assert real_records(first) == real_records(second)
        assert cache.stat['rename'] == [1, 1]

    def test_the_score_is_the_uncached_one(self, monkeypatch):
        """The cached run produces the same records as the uncached run"""
        cache = PackCache()
        cached = real_records(make_diff(self.OLD, self._new(), cache=cache).diff_info)
        plain = real_records(make_diff(self.OLD, self._new()).diff_info)
        assert cached.keys() == plain.keys()
        for path in cached:
            assert cached[path].edit == plain[path].edit
            assert cached[path].source_path == plain[path].source_path
            assert cached[path].data == plain[path].data


class TestRepoDiffValidation:
    """Input validation of RepoDiff."""

    def test_the_diff_policy_is_not_an_input(self):
        """The rename policy follows the pack format, the call can not change it"""
        assert (RepoDiff.MIN_SIMILARITY, RepoDiff.MAX_SIZE_RATIO, RepoDiff.SIMILARITY_LEVEL) == (
            0.5, 4.0, 3)
        repo = make_repo({'v': {'a.txt': b'a'}}, message='')
        old = PackFull(repo, commit='v')
        new = PackFull(repo, commit='v')
        with pytest.raises(TypeError):
            RepoDiff(old, new, min_similarity=0.9)
        with pytest.raises(TypeError):
            RepoDiff(old, new, max_size_ratio=2.0)
        with pytest.raises(TypeError):
            RepoDiff(old, new, similarity_level=1)


# ════════════════════════════════════════════════════════════════════════════
#  full scenario
# ════════════════════════════════════════════════════════════════════════════


def _no_data(info):
    """
    A copy of a diff record without the compressed data.

    The data bytes are compression output: hard-coding them would be
    unreadable and fragile to compression library upgrades, the
    expectations check the data by algo / data_size instead.

    Args:
        info (UpdateInfo): Record to copy

    Returns:
        UpdateInfo: Record with data = b''
    """
    return UpdateInfo(
        path=info.path, edit=info.edit, eol=info.eol, mode=info.mode,
        algo=info.algo, size=info.size, data_size=info.data_size,
        sha1=info.sha1, source_path=info.source_path,
    )


class TestRepoDiffFullScenario:
    """A real upgrade between two full packs, every diff type at once.

    The versions are the shared FULL_SCENARIO_OLD / FULL_SCENARIO_NEW
    of conftest, also used by test_unpack_update on the update job
    side. The versions are built with MockGitRepo and PackFull like
    the server pipeline, the diff output is hard-coded per record like
    test_full_decode_all_data on the decode side. The scenario covers:
    M (patch / plain / eol-only / mode-only), A, C (from an unchanged
    old file, from an earlier new file, cross eol / mode, copy
    chains), D, R, RM, empty files, binary files and CRLF content
    changes.
    """

    OLD = FULL_SCENARIO_OLD
    NEW = FULL_SCENARIO_NEW

    def _diff(self):
        """
        Build the RepoDiff of the scenario, like the server pipeline.

        Returns:
            RepoDiff:
        """
        # one message for both versions: the generated history (.pack/history.pack)
        # is a record of the update pack too, identical here so that the records
        # below are the files of the versions only
        repo = make_repo({'old': self.OLD, 'new': self.NEW}, message='')
        return RepoDiff(
            PackFull(repo, commit='old'),
            PackFull(repo, commit='new'),
        )

    def test_diff_info_records(self):
        """Every diff record is exact: path order, edit, meta, data and source."""
        diff = self._diff()
        diff_info = diff.diff_info
        # every record type is exercised
        assert {info.edit for info in diff_info.values()} == {0, 1, 2, 3}
        # the records follow the DFS path order of the new pack,
        # the deleted records come last
        assert list(diff_info) == [
            '.gitattributes',
            '.pack/history.pack',
            'backend/a1.py', 'backend/a2.py', 'backend/a3.py',
            'backend/copy.py', 'backend/empty.txt', 'backend/main.py',
            'backend/tiny.py', 'data/new_blob.bin',
            'docs/guide2.txt', 'docs/notes.txt',
            'docs/readme_copy.txt', 'docs/readme_copy2.txt',
            'frontend/App.svelte', 'frontend/App2.svelte',
            'scripts/new_tool.py', 'scripts/run.bat', 'scripts/runner.sh',
            'tools/run.sh', 'tools/tool.sh',
            'backend/legacy.py', 'data/cache.pkl',
        ]
        # per-record hard-coded expectations, data is compressed and
        # checked by algo / data_size
        # M (patch): modified, the zstd patch-from wins
        assert _no_data(diff_info['.gitattributes']) == UpdateInfo(
            path='.gitattributes', edit=1, eol=0, mode=0, algo=2,
            size=85, data_size=14,
            sha1=bytes.fromhex('4864d5ef0b398e6c74051b4612982e1a5f818f29'), source_path='.gitattributes')
        # M (plain): the commit history changed, too small to compress
        assert _no_data(diff_info['.pack/history.pack']) == UpdateInfo(
            path='.pack/history.pack', edit=1, eol=2, mode=0, algo=0,
            size=16, data_size=16,
            sha1=bytes.fromhex('b24684c28a51fa5809522373b392b988a67de0fc'), source_path='')
        # A: added, carries the data, the first of the copy chain
        assert _no_data(diff_info['backend/a1.py']) == UpdateInfo(
            path='backend/a1.py', edit=0, eol=0, mode=0, algo=2,
            size=540, data_size=40,
            sha1=bytes.fromhex('bcfd6ec09f6db21da66c3e3e67d0c474dda5b5e5'), source_path='')
        # C: copied from the earlier new file (copy chain)
        assert _no_data(diff_info['backend/a2.py']) == UpdateInfo(
            path='backend/a2.py', edit=0, eol=0, mode=0, algo=2,
            size=540, data_size=40,
            sha1=bytes.fromhex('bcfd6ec09f6db21da66c3e3e67d0c474dda5b5e5'), source_path='backend/a1.py')
        # C: copied from the earlier new file (copy chain)
        assert _no_data(diff_info['backend/a3.py']) == UpdateInfo(
            path='backend/a3.py', edit=0, eol=0, mode=0, algo=2,
            size=540, data_size=40,
            sha1=bytes.fromhex('bcfd6ec09f6db21da66c3e3e67d0c474dda5b5e5'), source_path='backend/a2.py')
        # C: copied from the unchanged old file (refinfo)
        assert _no_data(diff_info['backend/copy.py']) == UpdateInfo(
            path='backend/copy.py', edit=0, eol=0, mode=0, algo=0,
            size=43, data_size=43,
            sha1=bytes.fromhex('80c4a3c2cc87ffa168e205743b3b883ad3e08eb5'), source_path='backend/config.py')
        # A: added, empty file
        assert _no_data(diff_info['backend/empty.txt']) == UpdateInfo(
            path='backend/empty.txt', edit=0, eol=1, mode=0, algo=0,
            size=0, data_size=0,
            sha1=b'', source_path='')
        # M (patch): modified, the zstd patch-from wins
        assert _no_data(diff_info['backend/main.py']) == UpdateInfo(
            path='backend/main.py', edit=1, eol=0, mode=0, algo=2,
            size=94, data_size=21,
            sha1=bytes.fromhex('3216f7cf6a0d9caef5c769f38c1dd0ee69fac744'), source_path='backend/main.py')
        # M (plain): modified, too small to compress
        assert _no_data(diff_info['backend/tiny.py']) == UpdateInfo(
            path='backend/tiny.py', edit=1, eol=0, mode=0, algo=0,
            size=1, data_size=1,
            sha1=bytes.fromhex('95cb0bfd2977c761298d9624e4b4d4c72a39974a'), source_path='')
        # A: added, incompressible binary, stored raw
        assert _no_data(diff_info['data/new_blob.bin']) == UpdateInfo(
            path='data/new_blob.bin', edit=0, eol=2, mode=0, algo=0,
            size=12800, data_size=12800,
            sha1=bytes.fromhex('554c3af44eba0c91d80abd712f1a01fc84097af1'), source_path='')
        # C: copied from the unchanged old file, CRLF on both sides
        assert _no_data(diff_info['docs/guide2.txt']) == UpdateInfo(
            path='docs/guide2.txt', edit=0, eol=1, mode=0, algo=0,
            size=30, data_size=30,
            sha1=bytes.fromhex('fe8170a5c33baa1a71d1913fea45de3734c4fdfa'), source_path='docs/guide.txt')
        # M (plain): modified CRLF content, too small to compress
        assert _no_data(diff_info['docs/notes.txt']) == UpdateInfo(
            path='docs/notes.txt', edit=1, eol=1, mode=0, algo=0,
            size=13, data_size=13,
            sha1=bytes.fromhex('04cfda732a5e72c4f024e19fb65c4bd9e33a1d44'), source_path='')
        # C: copied from the unchanged LF old file, the copy keeps its
        # own eol (crlf), a cross eol copy
        assert _no_data(diff_info['docs/readme_copy.txt']) == UpdateInfo(
            path='docs/readme_copy.txt', edit=0, eol=1, mode=0, algo=0,
            size=10, data_size=10,
            sha1=bytes.fromhex('2cb9d0884150f87ef58e08e6517c854ee00b90c6'), source_path='docs/readme.md')
        # C: copied from the earlier new file (copy chain)
        assert _no_data(diff_info['docs/readme_copy2.txt']) == UpdateInfo(
            path='docs/readme_copy2.txt', edit=0, eol=1, mode=0, algo=0,
            size=10, data_size=10,
            sha1=bytes.fromhex('2cb9d0884150f87ef58e08e6517c854ee00b90c6'), source_path='docs/readme_copy.txt')
        # M (patch): modified, the new content of the copy that follows
        assert _no_data(diff_info['frontend/App.svelte']) == UpdateInfo(
            path='frontend/App.svelte', edit=1, eol=0, mode=0, algo=2,
            size=52, data_size=20,
            sha1=bytes.fromhex('c797b6c4c27f268e5e6c2181ba7ce52f0a7327d0'), source_path='frontend/App.svelte')
        # C: copied from the modified new file
        assert _no_data(diff_info['frontend/App2.svelte']) == UpdateInfo(
            path='frontend/App2.svelte', edit=0, eol=0, mode=0, algo=0,
            size=52, data_size=52,
            sha1=bytes.fromhex('c797b6c4c27f268e5e6c2181ba7ce52f0a7327d0'), source_path='frontend/App.svelte')
        # RM: renamed + modified, patched from the old file
        # (the best of the level and the small input level 3 wins, here 18 < 22)
        assert _no_data(diff_info['scripts/new_tool.py']) == UpdateInfo(
            path='scripts/new_tool.py', edit=3, eol=0, mode=0, algo=2,
            size=750, data_size=18,
            sha1=bytes.fromhex('74d902ad26e3c957239cf22ab92efab8f67c95f5'), source_path='scripts/old_tool.py')
        # M: eol-only change, CRLF (v1) to LF (v2), same content
        assert _no_data(diff_info['scripts/run.bat']) == UpdateInfo(
            path='scripts/run.bat', edit=1, eol=0, mode=0, algo=2,
            size=28, data_size=11,
            sha1=bytes.fromhex('30c7e458805ec8f7c2335f2d26b01f2ba8d66c16'), source_path='scripts/run.bat')
        # R: pure rename, no data
        assert _no_data(diff_info['scripts/runner.sh']) == UpdateInfo(
            path='scripts/runner.sh', edit=3, eol=0, mode=0, algo=0,
            size=28, data_size=0,
            sha1=bytes.fromhex('e0cb6eaf13a42970a3d71a53fe36ac851fa09e95'), source_path='scripts/run.sh')
        # C: copied from the unchanged 755 old file (refinfo)
        assert _no_data(diff_info['tools/run.sh']) == UpdateInfo(
            path='tools/run.sh', edit=0, eol=0, mode=1, algo=0,
            size=31, data_size=31,
            sha1=bytes.fromhex('2690963383249907ec8304c2f09e5d0a5d86f24d'), source_path='tools/deploy.sh')
        # M: mode-only change, 755 to 644, same content
        assert _no_data(diff_info['tools/tool.sh']) == UpdateInfo(
            path='tools/tool.sh', edit=1, eol=0, mode=0, algo=2,
            size=29, data_size=11,
            sha1=bytes.fromhex('c3e8889ba5dbcf9068862da9336f09491c2ab027'), source_path='tools/tool.sh')
        # D: deleted
        assert _no_data(diff_info['backend/legacy.py']) == UpdateInfo(
            path='backend/legacy.py', edit=2, eol=0, mode=0, algo=0,
            size=0, data_size=0,
            sha1=b'', source_path='')
        # D: deleted, binary
        assert _no_data(diff_info['data/cache.pkl']) == UpdateInfo(
            path='data/cache.pkl', edit=2, eol=0, mode=0, algo=0,
            size=0, data_size=0,
            sha1=b'', source_path='')
        # R / D / empty records carry no data
        for path in ('scripts/runner.sh', 'backend/legacy.py', 'data/cache.pkl', 'backend/empty.txt'):
            assert diff_info[path].data == b''

    def test_refinfo_records(self):
        """Every refinfo record is exact, in the DFS path order."""
        diff = self._diff()
        assert diff.refinfo == {
            # M (patch) source
            '.gitattributes': RefInfo(
                path='.gitattributes', size=87,
                sha1=bytes.fromhex('6d71afb94811acaa6f1021f95718d19aeefee5de')),
            # C source, copied by backend/copy.py
            'backend/config.py': RefInfo(
                path='backend/config.py', size=43,
                sha1=bytes.fromhex('80c4a3c2cc87ffa168e205743b3b883ad3e08eb5')),
            # M (patch) source
            'backend/main.py': RefInfo(
                path='backend/main.py', size=94,
                sha1=bytes.fromhex('01408d658d83912219e67c2d1141640cb4eb643b')),
            # C source, copied by docs/guide2.txt
            'docs/guide.txt': RefInfo(
                path='docs/guide.txt', size=30,
                sha1=bytes.fromhex('fe8170a5c33baa1a71d1913fea45de3734c4fdfa')),
            # C source, copied by docs/readme_copy.txt (cross eol)
            'docs/readme.md': RefInfo(
                path='docs/readme.md', size=10,
                sha1=bytes.fromhex('2cb9d0884150f87ef58e08e6517c854ee00b90c6')),
            # M (patch) source
            'frontend/App.svelte': RefInfo(
                path='frontend/App.svelte', size=56,
                sha1=bytes.fromhex('b9bdefd8b362e86cf6af8b127f5d2d6855b7d631')),
            # RM source, renamed + modified to scripts/new_tool.py
            'scripts/old_tool.py': RefInfo(
                path='scripts/old_tool.py', size=750,
                sha1=bytes.fromhex('4c9e1b37edb31e5a5f7879d7fedd777281ca0f68')),
            # M (patch) source
            'scripts/run.bat': RefInfo(
                path='scripts/run.bat', size=28,
                sha1=bytes.fromhex('30c7e458805ec8f7c2335f2d26b01f2ba8d66c16')),
            # R source, renamed to scripts/runner.sh
            'scripts/run.sh': RefInfo(
                path='scripts/run.sh', size=28,
                sha1=bytes.fromhex('e0cb6eaf13a42970a3d71a53fe36ac851fa09e95')),
            # C source, copied by tools/run.sh
            'tools/deploy.sh': RefInfo(
                path='tools/deploy.sh', size=31,
                sha1=bytes.fromhex('2690963383249907ec8304c2f09e5d0a5d86f24d')),
            # M (patch) source
            'tools/tool.sh': RefInfo(
                path='tools/tool.sh', size=29,
                sha1=bytes.fromhex('c3e8889ba5dbcf9068862da9336f09491c2ab027')),
        }
