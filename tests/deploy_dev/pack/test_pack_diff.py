"""
Tests for PackDiff: compare the decoders of two versions, produce the diff records.

The tests build the versions with MockDecodeBase.from_data, without the
pack machinery, so the diff logic (unchanged / modified / added /
deleted / renamed / copied) can be exercised in isolation.
"""
import pytest
from conftest import MockDecodeBase, code_lines, damage, damage_lines, random_bytes

from alasio.deploy_dev.pack.pack_diff import PackDiff


def make_diff(old, new, **kwargs):
    """
    Build a PackDiff from {path: content} dicts with mock decoders.

    Args:
        old (dict[str, bytes]): Old files
        new (dict[str, bytes]): New files
        **kwargs: Arguments passed to PackDiff

    Returns:
        PackDiff:
    """
    return PackDiff(
        MockDecodeBase.from_data(old),
        MockDecodeBase.from_data(new),
        **kwargs,
    )


# ════════════════════════════════════════════════════════════════════════════
#  similarity
# ════════════════════════════════════════════════════════════════════════════


class TestSimilarity:
    """Tests for PackDiff.similarity."""

    def test_identical_content(self):
        """Identical contents score ~1."""
        content = b'line one\nline two\n' * 100
        sim = PackDiff.similarity(content, content)
        assert sim > 0.9

    def test_small_modification(self):
        """A small modification keeps a high score."""
        old = b'def func(x):\n    return x * 2\n' * 100
        new = damage(old, 0.05, seed=1)
        sim = PackDiff.similarity(old, new)
        assert sim > 0.5

    def test_unrelated_content(self):
        """Unrelated contents score ~0."""
        old = random_bytes(4096, 'old')
        new = random_bytes(4096, 'new')
        sim = PackDiff.similarity(old, new)
        assert sim < 0.5

    def test_levels_agree(self):
        """A fast level scores close to the slow level."""
        old = b'def func(x):\n    return x * 2\n' * 100
        new = damage(old, 0.1, seed=2)
        sim_fast = PackDiff.similarity(old, new, level=3)
        sim_slow = PackDiff.similarity(old, new, level=22)
        assert abs(sim_fast - sim_slow) < 0.05


# ════════════════════════════════════════════════════════════════════════════
#  basic diffs
# ════════════════════════════════════════════════════════════════════════════


class TestPackDiffBasic:
    """Basic diff types: unchanged, added, deleted, modified."""

    def test_unchanged_absent(self):
        """Unchanged files are left out of the diff."""
        diff = make_diff({'a.txt': b'hello'}, {'a.txt': b'hello'})
        assert diff.diff_info == {}
        assert diff.ref_paths == set()

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
        assert info.sha1 == ''

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
        """D records in the input are not real files."""
        files = {'a.txt': b'hello', 'pkg/__init__.py': b''}
        edits = {'pkg/__init__.py': 2}
        diff = PackDiff(
            MockDecodeBase.from_data(files, edits=edits),
            MockDecodeBase.from_data(files, edits=edits),
        )
        assert diff.diff_info == {}

    def test_mode_change_only(self):
        """A mode change with identical content is an M record with the new mode."""
        diff = PackDiff(
            MockDecodeBase.from_data({'run.sh': b'#!/bin/sh\n'}, modes={'run.sh': 1}),
            MockDecodeBase.from_data({'run.sh': b'#!/bin/sh\n'}, modes={'run.sh': 0}),
        )
        info = diff.diff_info['run.sh']
        assert info.edit == 1
        assert info.mode == 0


# ════════════════════════════════════════════════════════════════════════════
#  renames
# ════════════════════════════════════════════════════════════════════════════


class TestPackDiffRename:
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
        assert 'a.txt' in diff.ref_paths

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
        assert 'a.txt' in diff.ref_paths

    def test_unrelated_not_renamed(self):
        """Unrelated contents are not matched, the files become D and A."""
        diff = make_diff(
            {'a.txt': random_bytes(4096, 'old')},
            {'b.txt': random_bytes(4096, 'new')},
        )
        assert diff.diff_info['a.txt'].edit == 2
        assert diff.diff_info['b.txt'].edit == 0
        assert diff.ref_paths == set()

    def test_min_similarity(self):
        """min_similarity controls whether a pair is matched as a rename."""
        old = {'a.txt': b'def func(x):\n    return x * 2\n' * 60}
        new = {'b.txt': damage(b'def func(x):\n    return x * 2\n' * 60, 0.1, seed=4)}
        # default threshold: matched as RM
        diff = make_diff(old, new)
        assert diff.diff_info['b.txt'].edit == 3
        assert diff.diff_info['b.txt'].source_path == 'a.txt'
        # high threshold: not matched, D + A instead
        diff = make_diff(old, new, min_similarity=0.9)
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


class TestPackDiffCopied:
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
        assert 'keep.txt' in diff.ref_paths

    def test_copy_chain_new_files(self):
        """Identical new files reference the nearest earlier record."""
        content = b'shared content\n'
        diff = make_diff({}, {'a1.txt': content, 'a2.txt': content, 'a3.txt': content})
        assert diff.diff_info['a1.txt'].source_path == ''
        assert diff.diff_info['a2.txt'].source_path == 'a1.txt'
        assert diff.diff_info['a3.txt'].source_path == 'a2.txt'
        assert diff.ref_paths == set()

    def test_crlf_source_copied(self):
        """A CRLF old file can be a copy source, the copy keeps its own eol."""
        files = {'keep.txt': b'copy me\n', 'copy.txt': b'copy me\n'}
        eols = {'keep.txt': 1, 'copy.txt': 1}
        diff = PackDiff(
            MockDecodeBase.from_data({'keep.txt': b'copy me\n'}, eols=eols),
            MockDecodeBase.from_data(files, eols=eols),
        )
        info = diff.diff_info['copy.txt']
        assert info.edit == 0
        assert info.source_path == 'keep.txt'
        # only the content matters, the copy keeps its own eol
        assert info.eol == 1
        assert 'keep.txt' in diff.ref_paths

    def test_755_source_copied(self):
        """A 755 old file can be a copy source, the copy keeps its own mode."""
        files = {'keep.sh': b'#!/bin/sh\n', 'copy.sh': b'#!/bin/sh\n'}
        modes = {'keep.sh': 1, 'copy.sh': 1}
        diff = PackDiff(
            MockDecodeBase.from_data({'keep.sh': b'#!/bin/sh\n'}, modes=modes),
            MockDecodeBase.from_data(files, modes=modes),
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
        assert 'keep.txt' in diff.ref_paths

    def test_modified_to_added_is_copied(self):
        """A file modified to match a new file is copied from it."""
        diff = make_diff(
            {'a.txt': b'old content\n' * 10},
            {'a.txt': b'new content\n' * 10, 'copy.txt': b'new content\n' * 10},
        )
        diff_info = diff.diff_info
        # copy.txt is added in the copied step, it keeps the data
        assert diff_info['copy.txt'].edit == 0
        assert diff_info['copy.txt'].source_path == ''
        # a.txt is modified to the same content, it is copied from copy.txt
        assert diff_info['a.txt'].edit == 0
        assert diff_info['a.txt'].source_path == 'copy.txt'

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
        assert 'orig.txt' in diff.ref_paths

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


class TestPackDiffRefPaths:
    """ref_paths reports the old files referenced by the diff."""

    def test_modified_patch_source(self):
        """An M record with patch data references the old file."""
        lines = code_lines(400)
        diff = make_diff(
            {'a.txt': b''.join(lines)},
            {'a.txt': damage_lines(lines, 0.05, seed=1)},
        )
        assert diff.ref_paths == {'a.txt'}

    def test_modified_plain_no_source(self):
        """An M record with plain data references no old file."""
        diff = make_diff({'a.txt': b'x'}, {'a.txt': b'y'})
        assert diff.ref_paths == set()

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
        assert diff.ref_paths == {'pure.txt', 'mod.txt'}

    def test_copy_from_new_not_referenced(self):
        """A copy source that is a new file is not a ref path."""
        content = b'shared\n'
        diff = make_diff({}, {'first.txt': content, 'second.txt': content})
        assert diff.diff_info['second.txt'].source_path == 'first.txt'
        assert diff.ref_paths == set()

    def test_copy_modified_releases_old_source(self):
        """A modified file converted to a copy releases its old file reference."""
        diff = make_diff({'a.txt': b'x'}, {'a.txt': b'y', 'b.txt': b'y'})
        # a.txt is modified to the content of the new b.txt, it is copied from it
        assert diff.diff_info['a.txt'].edit == 0
        assert diff.diff_info['a.txt'].source_path == 'b.txt'
        # the old a.txt is not referenced by any record
        assert diff.ref_paths == set()


# ════════════════════════════════════════════════════════════════════════════
#  input validation
# ════════════════════════════════════════════════════════════════════════════


class TestPackDiffValidation:
    """Input validation of PackDiff."""

    def test_invalid_parameters(self):
        """Out of range parameters are rejected."""
        old = MockDecodeBase.from_data({})
        new = MockDecodeBase.from_data({})
        with pytest.raises(ValueError, match='min_similarity'):
            PackDiff(old, new, min_similarity=1.0)
        with pytest.raises(ValueError, match='min_similarity'):
            PackDiff(old, new, min_similarity=-0.1)
        with pytest.raises(ValueError, match='max_size_ratio'):
            PackDiff(old, new, max_size_ratio=0.5)
