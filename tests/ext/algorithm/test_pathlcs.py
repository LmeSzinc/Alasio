"""
Tests for alasio.ext.algorithm.pathlcs.PathLookbackLCS.

The implementation buckets paths by (suffix, last_char_of_stem) where:
  - suffix is the file extension including the leading dot (e.g. ".py")
  - last_char_of_stem is the last character of the part before the extension
    (the "stem").  If there is no dot in the path, both suffix and char are
    empty strings because ``str.rpartition('.')`` returns ``('', '', path)``
    in that case, leaving the ``path`` variable empty.

``get_lcs()`` returns ``(lookback, length)`` where lookback is the 1-based
distance to the matched item (``self.index - matched_index``), or 0 if no
match was found.  A lookback of 1 means the match is against the immediately
preceding ``add_path()`` call.

The method uses a three-level fallback strategy:
  1. Match within the exact (suffix, char) bucket.
  2. Fallback: match within the same suffix (any char).
  3. Fallback: match across all buckets (any suffix, any char).

The optional ``min_length``, ``max_length``, and ``max_lookback`` parameters
filter candidates at every level.  ``min_length`` defaults to 1, meaning
zero-length LCS matches (e.g. comparing two completely different strings)
are excluded by default.

At each level the innermost sub-bucket is iterated in **reverse insertion
order**.  Ties are kept in favour of the entry that appeared first in the
iteration (i.e. earliest sub-bucket, most recent within sub-bucket).
An exact match (length == len(path)) short-circuits immediately.
"""

import pytest

from alasio.ext.algorithm.pathlcs import PathLookbackLCS


# ---------------------------------------------------------------------------
# Tests for get_key()
# ---------------------------------------------------------------------------


class TestGetKey:
    """PathLookbackLCS.get_key() -> (suffix, last_char_of_stem)."""

    @pytest.mark.parametrize("path, suffix, char", [
        ("foo/bar/baz.py",         ".py",        "z"),
        ("foo/bar/baz",            "",           ""),
        ("foo/bar.baz.tar.gz",     ".gz",        "r"),
        ("foo/.gitignore",         ".gitignore", "/"),
        ("foo/.bashrc",            ".bashrc",    "/"),
        (".gitignore",             ".gitignore", ""),
        ("",                       "",           ""),
        ("/etc/config.yaml",       ".yaml",      "g"),
        ("some.dir/file.txt",      ".txt",       "e"),
        ("C:\\Users\\me\\file.py",  ".py",        "e"),
        ("目录/文件.py",             ".py",        "件"),
        (".",                      ".",          ""),
    ])
    def test_get_key(self, path, suffix, char):
        assert PathLookbackLCS().get_key(path) == (suffix, char)


# ---------------------------------------------------------------------------
# Tests for add_path()
# ---------------------------------------------------------------------------


class TestAddPath:
    """PathLookbackLCS.add_path() stores a path with a monotonically
    increasing index."""

    def test_add_first_path(self):
        lcs = PathLookbackLCS()
        lcs.add_path("foo.py")
        assert lcs.index == 1
        assert lcs.dict_suffix[".py"]["o"] == {"foo.py": 0}

    def test_add_multiple_paths(self):
        lcs = PathLookbackLCS()
        lcs.add_path("a.py")
        lcs.add_path("b.py")
        lcs.add_path("c.py")
        assert lcs.index == 3

    def test_add_paths_different_buckets(self):
        lcs = PathLookbackLCS()
        lcs.add_path("a.py")
        lcs.add_path("b.txt")
        assert ".py" in lcs.dict_suffix
        assert ".txt" in lcs.dict_suffix

    def test_add_paths_same_suffix_different_last_char(self):
        lcs = PathLookbackLCS()
        lcs.add_path("xa.py")
        lcs.add_path("xb.py")
        assert "a" in lcs.dict_suffix[".py"]
        assert "b" in lcs.dict_suffix[".py"]

    def test_add_duplicate_path(self):
        lcs = PathLookbackLCS()
        lcs.add_path("foo.py")
        lcs.add_path("foo.py")
        assert lcs.dict_suffix[".py"]["o"]["foo.py"] == 1
        assert lcs.index == 2


# ---------------------------------------------------------------------------
# Level 1: exact (suffix, char) bucket
# ---------------------------------------------------------------------------


class TestGetLcsLevel1:
    """Level 1 — match within the exact (suffix, char) bucket."""

    def test_exact_match_short_circuit(self):
        lcs = PathLookbackLCS()
        lcs.add_path("foo/bar.py")
        lcs.add_path("other.py")
        lookback, length = lcs.get_lcs("foo/bar.py")
        # self.index = 2, matched index 0 -> lookback = 2
        assert lookback == 2
        assert length == len("foo/bar.py")

    def test_exact_match_second_path(self):
        lcs = PathLookbackLCS()
        lcs.add_path("abc_def.py")
        lcs.add_path("xyz_def.py")
        lookback, length = lcs.get_lcs("xyz_def.py")
        # self.index = 2, matched index 1 -> lookback = 1
        assert lookback == 1
        assert length == len("xyz_def.py")

    @pytest.mark.parametrize("paths, query, expected_lookback, expected_length", [
        (["aaaaa.py", "bbaaa.py", "cccc.py"], "xxaaa.py", 2, 6),
        (["ab.py", "cb.py"],                  "xb.py",    1, 4),
    ])
    def test_best_match(self, paths, query, expected_lookback, expected_length):
        """Best LCS among candidates in the same sub-bucket.
        Reversed iteration -> most recent wins ties."""
        lcs = PathLookbackLCS()
        for p in paths:
            lcs.add_path(p)
        lookback, length = lcs.get_lcs(query)
        assert lookback == expected_lookback
        assert length == expected_length

    def test_empty_lookback(self):
        """No paths added -> (0, 0)."""
        lookback, length = PathLookbackLCS().get_lcs("foo.py")
        assert lookback == 0
        assert length == 0

    def test_empty_query_no_paths(self):
        lookback, length = PathLookbackLCS().get_lcs("")
        assert lookback == 0
        assert length == 0


# ---------------------------------------------------------------------------
# Level 2: same suffix, any char
# ---------------------------------------------------------------------------


class TestGetLcsLevel2:
    """Level 2 — query shares the suffix with stored paths but has a
    different last_char_of_stem, so Level 1 bucket is empty.
    The fallback iterates char sub-buckets within the same suffix in
    **insertion order** (the order paths of each char were added)."""

    def test_fallback_same_extension(self):
        lcs = PathLookbackLCS()
        lcs.add_path("aaaa.py")
        lcs.add_path("bbbb.py")
        # Query "xxxx.py" has char 'x', Level 1 empty
        # Level 2 finds index 0 -> lookback = 2
        lookback, length = lcs.get_lcs("xxxx.py")
        assert lookback == 2
        assert length == 3

    def test_fallback_extension_only(self):
        lcs = PathLookbackLCS()
        lcs.add_path("aaaa.txt")
        # Query "bbbb.txt" has char 'b', Level 2 finds index 0 -> lookback = 1
        lookback, length = lcs.get_lcs("bbbb.txt")
        assert lookback == 1
        assert length == 4

    def test_fallback_multiple_entries_same_char(self):
        lcs = PathLookbackLCS()
        lcs.add_path("xa.py")
        lcs.add_path("ya.py")
        lcs.add_path("zb.py")
        lookback, length = lcs.get_lcs("qz.py")
        # Level 1: '.py'/'z' empty.
        # Level 2 values(): 'a' then 'b'
        #   'a' reversed: ya.py(1), xa.py(0)
        #     ya.py LCS=3 -> best_index=1, best_length=3
        #     xa.py LCS=3, not > 3
        #   'b' reversed: zb.py(2) LCS=3, not > 3
        # best_index=1, self.index=3 -> lookback = 2
        assert lookback == 2
        assert length == 3


# ---------------------------------------------------------------------------
# Level 3: any suffix, any char (full fallback)
# ---------------------------------------------------------------------------


class TestGetLcsLevel3:
    """Level 3 — query has a suffix that doesn't exist in the stored
    data at all, so it falls through to iterating ALL buckets."""

    def test_fallback_other_extension(self):
        lcs = PathLookbackLCS()
        lcs.add_path("data.txt")
        lcs.add_path("info.txt")
        lookback, length = lcs.get_lcs("query.py")
        assert lookback == 0
        assert length == 0

    def test_fallback_best_across_extensions(self):
        lcs = PathLookbackLCS()
        lcs.add_path("prefix_data.bin")
        lcs.add_path("data.txt")
        # LCS("suffix_data.json", "prefix_data.bin") = "n" (len 1)
        # Matches index 0, self.index=2 -> lookback = 2
        lookback, length = lcs.get_lcs("suffix_data.json")
        assert lookback == 2
        assert length == 1


# ---------------------------------------------------------------------------
# Combined multi-level scenarios
# ---------------------------------------------------------------------------


class TestGetLcsFallbackOrder:
    """Verify fallback priority: Level 1 > Level 2 > Level 3."""

    def test_level1_wins_over_level2(self):
        lcs = PathLookbackLCS()
        lcs.add_path("data.txt")
        lcs.add_path("special_suffix.txt")
        # Query "some_data.txt" has char 'a', Level 1 finds data.txt (index 0)
        # self.index = 2 -> lookback = 2
        lookback, length = lcs.get_lcs("some_data.txt")
        assert lookback == 2
        assert length == 8

    def test_level2_wins_over_level3(self):
        lcs = PathLookbackLCS()
        lcs.add_path("prefix_common.py")
        lcs.add_path("data.txt")
        # Query "other_common.py" has suffix '.py', char 'y'
        # Level 1 finds prefix_common.py (index 0) -> lookback = 2
        lookback, length = lcs.get_lcs("other_common.py")
        assert lookback == 2
        assert length == 10

    def test_level3_when_earlier_empty(self):
        lcs = PathLookbackLCS()
        lcs.add_path("data.txt")
        lookback, length = lcs.get_lcs("data.json")
        assert lookback == 0
        assert length == 0

    def test_level1_empty_level2_finds(self):
        lcs = PathLookbackLCS()
        lcs.add_path("aaa.py")
        lcs.add_path("bbb.py")
        # Query "xxx.py" has char 'x', Level 1 empty
        # Level 2 finds index 0 -> lookback = 2
        lookback, length = lcs.get_lcs("xxx.py")
        assert lookback == 2
        assert length == 3

    def test_all_levels_empty(self):
        lookback, length = PathLookbackLCS().get_lcs("anything.py")
        assert lookback == 0
        assert length == 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestGetLcsEdgeCases:
    """Various edge-case paths and queries across all three levels."""

    def test_empty_query_with_stored_paths(self):
        lcs = PathLookbackLCS()
        lcs.add_path("noext")
        lookback, length = lcs.get_lcs("")
        # Empty query has LCS=0 with any stored path, so with default
        # min_length=1 it is filtered at every level -> (0, 0).
        assert lookback == 0
        assert length == 0

    def test_no_extension_stored_and_query(self):
        lcs = PathLookbackLCS()
        lcs.add_path("Makefile")
        lookback, length = lcs.get_lcs("Makefile")
        # self.index = 1, matched index 0 -> lookback = 1
        assert lookback == 1
        assert length == len("Makefile")

    def test_no_extension_query_with_extension(self):
        lcs = PathLookbackLCS()
        lcs.add_path("README")
        lookback, length = lcs.get_lcs("README.md")
        assert lookback == 0
        assert length == 0

    def test_unicode_paths(self):
        lcs = PathLookbackLCS()
        lcs.add_path("目录A/文件.py")
        lookback, length = lcs.get_lcs("目录B/文件.py")
        assert lookback == 1
        assert length == 6

    def test_identical_to_first_added(self):
        lcs = PathLookbackLCS()
        lcs.add_path("first.py")
        lcs.add_path("second.py")
        lookback, length = lcs.get_lcs("first.py")
        assert lookback == 2
        assert length == len("first.py")

    def test_identical_to_second_added(self):
        lcs = PathLookbackLCS()
        lcs.add_path("first.py")
        lcs.add_path("second.py")
        lookback, length = lcs.get_lcs("second.py")
        assert lookback == 1
        assert length == len("second.py")


# ---------------------------------------------------------------------------
# Regression tests
# ---------------------------------------------------------------------------


class TestGetLcsFilters:
    """Optional filter parameters on get_lcs(): min_length, max_length, max_lookback."""

    def test_no_filter_backward_compat(self):
        """Calling get_lcs without filters works identically to before."""
        lcs = PathLookbackLCS()
        lcs.add_path("aaaa.py")
        lcs.add_path("bbbb.py")
        lookback, length = lcs.get_lcs("xxxx.py")
        assert lookback == 2
        assert length == 3

    def test_min_length_skip_short(self):
        """min_length filters out candidates with LCS shorter than the cutoff."""
        lcs = PathLookbackLCS()
        lcs.add_path("aaaaa.py")        # index 0, LCS = ".py" (len 3)
        lcs.add_path("bbbbb.py")        # index 1, LCS = ".py" (len 3)
        # No candidate has LCS >= 5
        lookback, length = lcs.get_lcs("xxxxx.py", min_length=5)
        assert lookback == 0
        assert length == 0

    def test_min_length_accept_long_enough(self):
        """min_length keeps candidates that meet the threshold."""
        lcs = PathLookbackLCS()
        lcs.add_path("aaaaa.py")        # index 0
        lookback, length = lcs.get_lcs("xxxxx.py", min_length=3)
        assert lookback == 1
        assert length == 3

    def test_max_length_skip_long(self):
        """max_length filters out candidates with LCS longer than the cutoff."""
        lcs = PathLookbackLCS()
        lcs.add_path("abc_common.py")   # index 0, LCS = 10
        lcs.add_path("other.py")        # index 1, LCS = 3
        lookback, length = lcs.get_lcs("xyz_common.py", max_length=5)
        # "other.py" (index 1) has LCS=3 <= 5 -> found
        assert lookback == 1
        assert length == 3

    def test_max_length_allow_at_cutoff(self):
        """max_length keeps candidates at or below the cutoff."""
        lcs = PathLookbackLCS()
        lcs.add_path("abc_common.py")   # index 0, LCS = 10
        lookback, length = lcs.get_lcs("xyz_common.py", max_length=10)
        assert lookback == 1
        assert length == 10

    def test_max_lookback_filter_old(self):
        """max_lookback filters out candidates whose lookback > max."""
        lcs = PathLookbackLCS()
        lcs.add_path("aaaaa.py")        # index 0, lookback=3
        lcs.add_path("bbbbb.py")        # index 1, lookback=2
        lcs.add_path("ccccc.py")        # index 2, lookback=1
        # max_lookback=1: only ccccc.py qualifies -> lookback=1
        lookback, length = lcs.get_lcs("xxxxx.py", max_lookback=1)
        assert lookback == 1
        assert length == 3

    def test_max_lookback_exact_match_filtered(self):
        """Exact match is skipped when its lookback exceeds max_lookback."""
        lcs = PathLookbackLCS()
        lcs.add_path("exact.py")        # index 0, lookback=2
        lcs.add_path("other.py")        # index 1, lookback=1
        # max_lookback=1: exact.py filtered out (lookback 2 > 1)
        # Falls back to other.py -> LCS(".py")=3
        lookback, length = lcs.get_lcs("exact.py", max_lookback=1)
        assert lookback == 1
        assert length == 3

    def test_max_lookback_exact_match_allowed(self):
        """Exact match passes when its lookback is within max_lookback."""
        lcs = PathLookbackLCS()
        lcs.add_path("exact.py")        # index 0, lookback=2
        lcs.add_path("other.py")        # index 1, lookback=1
        lookback, length = lcs.get_lcs("exact.py", max_lookback=2)
        assert lookback == 2
        assert length == len("exact.py")

    def test_min_and_max_length(self):
        """Both min_length and max_length applied simultaneously."""
        lcs = PathLookbackLCS()
        lcs.add_path("aaaaa.py")            # index 0, char 'a', LCS = ".py" (len 3)
        lcs.add_path("abc_xcommon.py")      # index 1, char 'n', LCS = 11 (> 10)
        # Level 1: abc_xcommon.py filtered by max_length
        # Level 2: aaaaa.py filtered by min_length
        lookback, length = lcs.get_lcs("xyz_xcommon.py", min_length=5, max_length=10)
        assert lookback == 0
        assert length == 0

    def test_combined_min_and_max_lookback(self):
        """min_length and max_lookback combined."""
        lcs = PathLookbackLCS()
        lcs.add_path("abc_common.py")   # index 0, lookback=3, LCS=10
        lcs.add_path("def_common.py")   # index 1, lookback=2, LCS=10
        lcs.add_path("ghi.py")          # index 2, lookback=1, LCS=3
        lookback, length = lcs.get_lcs("xyz_common.py", min_length=5, max_lookback=1)
        # ghi.py has LCS=3 < 5 filtered; no other with lookback <= 1
        assert lookback == 0
        assert length == 0

    def test_all_filters_exclude_everything(self):
        """When all candidates are filtered out, returns (0, 0)."""
        lcs = PathLookbackLCS()
        lcs.add_path("a.py")
        lcs.add_path("b.py")
        lookback, length = lcs.get_lcs("c.py", min_length=10, max_length=1, max_lookback=0)
        assert lookback == 0
        assert length == 0


class TestRegression:
    """Edge cases found during development, testing new fallback behavior."""

    def test_very_short_paths_level2(self):
        lcs = PathLookbackLCS()
        lcs.add_path("a.py")
        lookback, length = lcs.get_lcs("b.py")
        assert lookback == 1
        assert length == 3

    def test_fallback_different_char_same_suffix(self):
        lcs = PathLookbackLCS()
        lcs.add_path("aaaa.py")
        lookback, length = lcs.get_lcs("xxxx.py")
        assert lookback == 1
        assert length == 3

    def test_fallback_level3_no_match(self):
        lcs = PathLookbackLCS()
        lcs.add_path("abc.xyz")
        lookback, length = lcs.get_lcs("def.txt")
        assert lookback == 0
        assert length == 0

    def test_single_char_no_extension(self):
        lcs = PathLookbackLCS()
        lcs.add_path("x")
        lookback, length = lcs.get_lcs("x")
        assert lookback == 1
        assert length == 1

    def test_single_char_different(self):
        lcs = PathLookbackLCS()
        lcs.add_path("x")
        lookback, length = lcs.get_lcs("y")
        assert lookback == 0
        assert length == 0

    def test_empty_path_as_key(self):
        """Adding an empty string as a path does not cause errors."""
        lcs = PathLookbackLCS()
        lcs.add_path("")                           # index 0, suffix='', char=''
        assert lcs.index == 1
        assert lcs.dict_suffix[""] == {"": {"": 0}}

    def test_query_after_empty_path_added(self):
        """Querying after an empty path was added works normally."""
        lcs = PathLookbackLCS()
        lcs.add_path("normal.py")                  # index 0
        lcs.add_path("")                           # index 1
        lcs.add_path("other.py")                   # index 2
        # Query matching first path -> should work
        lookback, length = lcs.get_lcs("normal.py")
        assert lookback == 3
        assert length == len("normal.py")

    def test_empty_path_as_only_entry(self):
        """Only an empty path is stored, query against it."""
        lcs = PathLookbackLCS()
        lcs.add_path("")
        lookback, length = lcs.get_lcs("something.py")
        # Level 1: dict_suffix['.py']['y'] empty (different suffix)
        # Level 2: dict_suffix['.py'] empty
        # Level 3: finds "" in dict_suffix['']['']
        # LCS("something.py", "") = 0, filtered by min_length=1
        assert lookback == 0
        assert length == 0

    def test_empty_path_many_empty_paths(self):
        """Multiple empty paths don't corrupt internal state."""
        lcs = PathLookbackLCS()
        lcs.add_path("")
        lcs.add_path("")
        assert lcs.index == 2
        assert lcs.dict_suffix[""][""] == {"": 1}

    def test_mixed_normal_and_empty_paths(self):
        """Normal operations interleaved with empty paths."""
        lcs = PathLookbackLCS()
        lcs.add_path("a.py")
        lcs.add_path("")
        lcs.add_path("b.py")
        lcs.add_path("")
        lcs.add_path("c.py")
        assert lcs.index == 5
        # Query finds most recent "c.py" exactly
        lookback, length = lcs.get_lcs("c.py")
        assert lookback == 1
        assert length == len("c.py")

    def test_path_with_dot_in_directory_name(self):
        lcs = PathLookbackLCS()
        lcs.add_path("dir.with.dots/file.py")
        lookback, length = lcs.get_lcs("dir.with.dots/other.py")
        assert lookback == 1
        assert length == 3

    def test_path_starting_with_dot(self):
        lcs = PathLookbackLCS()
        lcs.add_path(".hidden/config.py")
        lookback, length = lcs.get_lcs(".hidden/other.py")
        assert lookback == 1
        assert length == 3

    def test_get_key_consistency(self):
        lcs = PathLookbackLCS()
        assert lcs.get_key("foo/bar/baz.py") == (".py", "z")
        assert lcs.get_key("foo/bar/baz.py") == (".py", "z")


# ---------------------------------------------------------------------------
# Internal dict structure
# ---------------------------------------------------------------------------


class TestDictStructure:
    """Verification of the nested defaultdict structure."""

    def test_structure_after_adds(self):
        lcs = PathLookbackLCS()
        lcs.add_path("a.py")
        lcs.add_path("b.py")
        lcs.add_path("a.txt")

        assert set(lcs.dict_suffix.keys()) == {".py", ".txt"}
        assert set(lcs.dict_suffix[".py"].keys()) == {"a", "b"}
        assert lcs.dict_suffix[".py"]["a"] == {"a.py": 0}
        assert lcs.dict_suffix[".py"]["b"] == {"b.py": 1}
        assert set(lcs.dict_suffix[".txt"].keys()) == {"a"}
        assert lcs.dict_suffix[".txt"]["a"] == {"a.txt": 2}

    def test_multiple_char_buckets(self):
        lcs = PathLookbackLCS()
        lcs.add_path("xa.py")
        lcs.add_path("xb.py")
        lcs.add_path("xc.py")

        assert set(lcs.dict_suffix[".py"].keys()) == {"a", "b", "c"}
        assert lcs.dict_suffix[".py"]["a"] == {"xa.py": 0}
        assert lcs.dict_suffix[".py"]["b"] == {"xb.py": 1}
        assert lcs.dict_suffix[".py"]["c"] == {"xc.py": 2}
