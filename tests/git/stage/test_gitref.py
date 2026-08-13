import pytest

from alasio.git.stage.gitref import GitRef, LooseRef, parse_loose_ref, parse_packed_refs
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401

SHA_MASTER = "da39a3ee5e6b4b0d3255bfef95601890afd80709"
SHA_ORIG = "ac7d554f78e6529b706617cb7b601cd4cdc65f7d"
SHA_DEV = "17eb307dd538860f62ac32808706f1cebc34149b"


# --- Tests for parse_loose_ref ---

@pytest.mark.parametrize(
    "content, expected_result",
    [
        # --- Test valid SHA-1 hashes ---
        # Standard 40-character hexadecimal string
        (
                b"da39a3ee5e6b4b0d3255bfef95601890afd80709",
                LooseRef(sha1="da39a3ee5e6b4b0d3255bfef95601890afd80709", ref=""),
        ),
        # SHA-1 with leading/trailing whitespace (should be stripped)
        (
                b"  \n da39a3ee5e6b4b0d3255bfef95601890afd80709 \t ",
                LooseRef(sha1="da39a3ee5e6b4b0d3255bfef95601890afd80709", ref=""),
        ),
        # --- Test valid symbolic refs ---
        # Standard symbolic ref
        (
                b"ref: refs/heads/master",
                LooseRef(sha1="", ref="refs/heads/master"),
        ),
        # Symbolic ref with leading/trailing whitespace
        (
                b" \t ref: refs/heads/dev\n",
                LooseRef(sha1="", ref="refs/heads/dev"),
        ),
        # Symbolic ref with non-ASCII characters (UTF-8 encoded)
        (
                b"ref: refs/heads/\xe4\xb8\xad\xe6\x96\x87",  # "中文" (Chinese)
                LooseRef(sha1="", ref="refs/heads/中文"),
        ),
    ],
)
def test_parse_loose_ref_valid_cases(content, expected_result):
    """
    Tests that parse_loose_ref correctly handles various valid inputs.
    """
    assert parse_loose_ref(content) == expected_result


@pytest.mark.parametrize(
    "invalid_content",
    [
        # --- Test invalid inputs ---
        # Empty byte string
        b"",
        # Whitespace only
        b"   \n\t ",
        # Invalid format
        b"hello world",
        # SHA-1 too short (39 chars)
        b"da39a3ee5e6b4b0d3255bfef95601890afd8070",
        # SHA-1 too long (41 chars)
        b"da39a3ee5e6b4b0d3255bfef95601890afd80709a",
        # 40-byte string containing non-ASCII characters (invalid for a SHA)
        b"da39a3ee5e6b4b0d3255bfef95601890afd807\xe4",
        # Looks like a ref path, but missing the "ref: " prefix
        b"refs/heads/master",
        # Similar to a symbolic ref, but with an incorrect prefix
        b"reference: refs/heads/master",
        # Symbolic ref with invalid UTF-8 sequence in the path
        b"ref: refs/heads/\xff\xfe",
    ],
)
def test_parse_loose_ref_invalid_cases_raise_value_error(invalid_content):
    """
    Tests that parse_loose_ref raises a ValueError for invalid inputs.
    """
    with pytest.raises(ValueError):
        parse_loose_ref(invalid_content)


# --- Tests for parse_packed_refs ---

def test_parse_packed_refs_with_real_data():
    """
    Tests parsing a real packed-refs file content provided by the user.
    This test verifies that comments, valid refs, and peeled tags are handled correctly.
    """
    content = b"""# pack-refs with: peeled fully-peeled sorted
3d53d4f3600df8496a36f5b38bac8d06033dac31 refs/heads/bug_fix
ac7d554f78e6529b706617cb7b601cd4cdc65f7d refs/heads/cloud
17eb307dd538860f62ac32808706f1cebc34149b refs/heads/dev
bec222bb05cc21c8304f6b489a3ae279d101afdd refs/heads/feature
947e89356c560f82d94a17d5dfe39025bfa5fde6 refs/heads/v2020.07.15
8b63ab79e956b90805dff2167448a72262fe50eb refs/heads/v2021.10.24
3a743f091b0a98d42a8dc04c3d16be9552a9c7d6 refs/remotes/azurstats/master
3537a7bb479c8b0a3c7d2cb34c961d43d950c78b refs/remotes/mirror/master
7d271660efde6364e5ba4f0cc9f9e04c040e18a5 refs/stash
a32019d9e0dd8680d7f2a4ec987d92ed09c8ee7f refs/tags/v0.2.1
ab3fdd0ca73edadf29c2df88da4480b6359e9612 refs/tags/v0.3.1
50f49a6350aa584d96dc4efe162cec8ce09a212b refs/tags/v0.5.1
8b955975df6f7af8b8411f9b753ff84c26adf110 refs/tags/v0.5.2
b408a075f13681edd44fcbb17d452bdd8aaf67aa refs/tags/v2020.04.08
^cae9762b6561a0cf87603d9e900a00718da4106a
b94baf883168351f6342ee685cfbfbc057c8c998 refs/tags/v2020.04.15
8f1d8fb3638feb5e8be8bc128f0fc67f62bd3cfb refs/tags/v2020.04.21
d43ff8a3f79baf6e00eb0a06b670a017d31a16dc refs/tags/v2020.04.25
"""
    expected = {
        "refs/heads/bug_fix": "3d53d4f3600df8496a36f5b38bac8d06033dac31",
        "refs/heads/cloud": "ac7d554f78e6529b706617cb7b601cd4cdc65f7d",
        "refs/heads/dev": "17eb307dd538860f62ac32808706f1cebc34149b",
        "refs/heads/feature": "bec222bb05cc21c8304f6b489a3ae279d101afdd",
        "refs/heads/v2020.07.15": "947e89356c560f82d94a17d5dfe39025bfa5fde6",
        "refs/heads/v2021.10.24": "8b63ab79e956b90805dff2167448a72262fe50eb",
        "refs/remotes/azurstats/master": "3a743f091b0a98d42a8dc04c3d16be9552a9c7d6",
        "refs/remotes/mirror/master": "3537a7bb479c8b0a3c7d2cb34c961d43d950c78b",
        "refs/stash": "7d271660efde6364e5ba4f0cc9f9e04c040e18a5",
        "refs/tags/v0.2.1": "a32019d9e0dd8680d7f2a4ec987d92ed09c8ee7f",
        "refs/tags/v0.3.1": "ab3fdd0ca73edadf29c2df88da4480b6359e9612",
        "refs/tags/v0.5.1": "50f49a6350aa584d96dc4efe162cec8ce09a212b",
        "refs/tags/v0.5.2": "8b955975df6f7af8b8411f9b753ff84c26adf110",
        "refs/tags/v2020.04.08": "b408a075f13681edd44fcbb17d452bdd8aaf67aa",
        "refs/tags/v2020.04.15": "b94baf883168351f6342ee685cfbfbc057c8c998",
        "refs/tags/v2020.04.21": "8f1d8fb3638feb5e8be8bc128f0fc67f62bd3cfb",
        "refs/tags/v2020.04.25": "d43ff8a3f79baf6e00eb0a06b670a017d31a16dc",
    }
    assert parse_packed_refs(content) == expected


def test_parse_packed_refs_with_malformed_lines():
    """
    Tests parsing content with various empty, commented, and malformed lines.
    The function should gracefully ignore these lines and only parse valid ones.
    """
    content = b"""# This is a header comment
d4e3e4085f1877669527b1a942b02a9442a86981 refs/heads/main

# This is another comment
^a9b8c7d6e5f4a3b2c1d0e9f8a7b6c5d4e3f2a1b0
a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0 refs/remotes/origin/feature/new
this_is_a_malformed_line_with_no_space
badsha_not_ascii_in_sha\xe4 refs/heads/badsha
1234567890abcdef1234567890abcdef12345678 refs/heads/badref\xff
"""
    expected = {
        "refs/heads/main": "d4e3e4085f1877669527b1a942b02a9442a86981",
        "refs/remotes/origin/feature/new": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
    }
    assert parse_packed_refs(content) == expected


@pytest.mark.parametrize(
    "content, expected",
    [
        # Empty content
        (b"", {}),
        # Content with only comments
        (b"# pack-refs with: peeled\n# another comment", {}),
        # Content with only peeled tags
        (b"^a9b8c7d6e5f4a3b2c1d0e9f8a7b6c5d4e3f2a1b0", {}),
        # Content with only newlines and whitespace
        (b"\n  \n\t\n", {}),
        # A mix of comments, newlines, and peeled tags
        (b"# header\n\n^peeled_tag\n", {}),
    ],
)
def test_parse_packed_refs_empty_and_comment_only_cases(content, expected):
    """
    Tests that `parse_packed_refs` returns an empty dictionary for content
    that is empty or contains no valid ref lines.
    """
    assert parse_packed_refs(content) == expected


@pytest.fixture
def git_repo(fs):
    """
    Create a repository with .git structure in the in-memory filesystem.

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture

    Returns:
        str: Path of the repository root
    """
    root = fs.root_dir.path.rstrip('/\\')
    repo = f'{root}/repo'
    fs.create_dir(f'{repo}/.git/refs/heads')
    return repo


class TestHeadGet:
    def test_detached(self, git_repo, fs):
        """HEAD stores a commit sha1 directly."""
        fs.create_file(f'{git_repo}/.git/HEAD', contents=SHA_MASTER)

        gitref = GitRef(git_repo)
        assert gitref.head_get() == SHA_MASTER

    def test_symbolic_loose(self, git_repo, fs):
        """HEAD points to a loose ref."""
        fs.create_file(f'{git_repo}/.git/HEAD', contents='ref: refs/heads/master')
        fs.create_file(f'{git_repo}/.git/refs/heads/master', contents=SHA_MASTER)

        gitref = GitRef(git_repo)
        assert gitref.head_get() == SHA_MASTER

    def test_symbolic_packed(self, git_repo, fs):
        """HEAD points to a ref that only exists in packed-refs."""
        fs.create_file(f'{git_repo}/.git/HEAD', contents='ref: refs/heads/master')
        fs.create_file(f'{git_repo}/.git/packed-refs', contents=f'{SHA_MASTER} refs/heads/master\n')

        gitref = GitRef(git_repo)
        assert gitref.head_get() == SHA_MASTER

    def test_orig_head_fallback(self, git_repo, fs):
        """Fall back to ORIG_HEAD when HEAD is missing."""
        fs.create_file(f'{git_repo}/.git/ORIG_HEAD', contents=SHA_ORIG)

        gitref = GitRef(git_repo)
        assert gitref.head_get() == SHA_ORIG

    def test_head_priority(self, git_repo, fs):
        """HEAD takes priority over ORIG_HEAD."""
        fs.create_file(f'{git_repo}/.git/HEAD', contents=SHA_MASTER)
        fs.create_file(f'{git_repo}/.git/ORIG_HEAD', contents=SHA_ORIG)

        gitref = GitRef(git_repo)
        assert gitref.head_get() == SHA_MASTER

    def test_missing(self, git_repo):
        """No HEAD and no ORIG_HEAD, return empty string."""
        gitref = GitRef(git_repo)
        assert gitref.head_get() == ''

    def test_invalid_head(self, git_repo, fs):
        """Invalid HEAD content, log error and return empty string."""
        fs.create_file(f'{git_repo}/.git/HEAD', contents='not a ref')

        gitref = GitRef(git_repo)
        with logger.mock_capture_writer() as capture:
            assert gitref.head_get() == ''
            assert capture.fd.any_contains('GitRef.head_get error')
            capture.clear()

    def test_invalid_head_no_fallback(self, git_repo, fs):
        """Invalid HEAD does not fall back to ORIG_HEAD."""
        fs.create_file(f'{git_repo}/.git/HEAD', contents='not a ref')
        fs.create_file(f'{git_repo}/.git/ORIG_HEAD', contents=SHA_ORIG)

        gitref = GitRef(git_repo)
        with logger.mock_capture_writer() as capture:
            assert gitref.head_get() == ''
            assert capture.fd.any_contains('GitRef.head_get error')
            capture.clear()

    def test_specific_head(self, git_repo, fs):
        """head_get with a specific head, no fallback between the two."""
        fs.create_file(f'{git_repo}/.git/HEAD', contents=SHA_MASTER)
        fs.create_file(f'{git_repo}/.git/ORIG_HEAD', contents=SHA_ORIG)

        gitref = GitRef(git_repo)
        assert gitref.head_get('HEAD') == SHA_MASTER
        assert gitref.head_get('ORIG_HEAD') == SHA_ORIG

    def test_specific_head_missing(self, git_repo):
        """head_get with a specific head that does not exist."""
        gitref = GitRef(git_repo)
        assert gitref.head_get('ORIG_HEAD') == ''


class TestRefGet:
    def test_loose_sha1(self, git_repo, fs):
        """Get a loose ref with a commit sha1."""
        fs.create_file(f'{git_repo}/.git/refs/heads/master', contents=SHA_MASTER)

        gitref = GitRef(git_repo)
        assert gitref.ref_get('refs/heads/master') == SHA_MASTER

    def test_symbolic_recursive(self, git_repo, fs):
        """A ref pointing to another ref is resolved recursively."""
        fs.create_file(f'{git_repo}/.git/refs/heads/dev', contents='ref: refs/heads/master')
        fs.create_file(f'{git_repo}/.git/refs/heads/master', contents=SHA_MASTER)

        gitref = GitRef(git_repo)
        assert gitref.ref_get('refs/heads/dev') == SHA_MASTER

    def test_symbolic_chain_broken(self, git_repo, fs):
        """A symbolic ref pointing to a missing ref returns empty string."""
        fs.create_file(f'{git_repo}/.git/refs/heads/dev', contents='ref: refs/heads/nonexist')

        gitref = GitRef(git_repo)
        assert gitref.ref_get('refs/heads/dev') == ''

    def test_packed_fallback(self, git_repo, fs):
        """No loose ref, lookup in packed-refs."""
        fs.create_file(f'{git_repo}/.git/packed-refs', contents=f'{SHA_MASTER} refs/heads/master\n')

        gitref = GitRef(git_repo)
        assert gitref.ref_get('refs/heads/master') == SHA_MASTER

    def test_missing(self, git_repo):
        """No loose ref and no packed ref, return empty string."""
        gitref = GitRef(git_repo)
        assert gitref.ref_get('refs/heads/master') == ''

    def test_invalid(self, git_repo, fs):
        """Invalid loose ref content, log error and return empty string."""
        fs.create_file(f'{git_repo}/.git/refs/heads/master', contents='not a ref')

        gitref = GitRef(git_repo)
        with logger.mock_capture_writer() as capture:
            assert gitref.ref_get('refs/heads/master') == ''
            assert capture.fd.any_contains('GitRef error')
            capture.clear()

    def test_cyclic(self, git_repo, fs):
        """A cyclic symbolic ref chain returns empty string with an error log."""
        fs.create_file(f'{git_repo}/.git/refs/heads/a', contents='ref: refs/heads/b')
        fs.create_file(f'{git_repo}/.git/refs/heads/b', contents='ref: refs/heads/a')

        gitref = GitRef(git_repo)
        with logger.mock_capture_writer() as capture:
            assert gitref.ref_get('refs/heads/a') == ''
            assert gitref.ref_get('refs/heads/b') == ''
            assert capture.fd.any_contains('cyclic ref')
            capture.clear()

    def test_self_cyclic(self, git_repo, fs):
        """A ref pointing to itself returns empty string with an error log."""
        fs.create_file(f'{git_repo}/.git/refs/heads/a', contents='ref: refs/heads/a')

        gitref = GitRef(git_repo)
        with logger.mock_capture_writer() as capture:
            assert gitref.ref_get('refs/heads/a') == ''
            assert capture.fd.any_contains('cyclic ref')
            capture.clear()


class TestRefSet:
    def test_set_sha1(self, git_repo, fs):
        """Write a commit sha1 to a ref."""
        gitref = GitRef(git_repo)
        gitref.ref_set('refs/heads/master', target_sha1=SHA_MASTER)

        file = fs.get_file(f'{git_repo}/.git/refs/heads/master')
        assert file.content == f'{SHA_MASTER}\n'.encode()

    def test_set_ref(self, git_repo, fs):
        """Write a symbolic ref."""
        gitref = GitRef(git_repo)
        gitref.ref_set('HEAD', target_ref='refs/heads/master')

        file = fs.get_file(f'{git_repo}/.git/HEAD')
        assert file.content == b'ref: refs/heads/master\n'

    def test_set_empty_noop(self, git_repo, fs):
        """No target, no file is written."""
        gitref = GitRef(git_repo)
        assert gitref.ref_set('refs/heads/master') is None
        assert not fs.exists(f'{git_repo}/.git/refs/heads/master')

    def test_set_sha1_priority(self, git_repo, fs):
        """target_sha1 takes priority when both targets are given."""
        gitref = GitRef(git_repo)
        gitref.ref_set('refs/heads/master', target_sha1=SHA_MASTER, target_ref='refs/heads/dev')

        file = fs.get_file(f'{git_repo}/.git/refs/heads/master')
        assert file.content == f'{SHA_MASTER}\n'.encode()

    def test_set_deep_path(self, git_repo, fs):
        """Parent directories are created automatically for a deep ref path."""
        gitref = GitRef(git_repo)
        gitref.ref_set('refs/remotes/origin/master', target_sha1=SHA_MASTER)

        file = fs.get_file(f'{git_repo}/.git/refs/remotes/origin/master')
        assert file.content == f'{SHA_MASTER}\n'.encode()


class TestRefDel:
    def test_del_existing(self, git_repo, fs):
        """Delete an existing ref."""
        fs.create_file(f'{git_repo}/.git/refs/heads/master', contents=SHA_MASTER)

        gitref = GitRef(git_repo)
        assert gitref.ref_del('refs/heads/master')
        assert not fs.exists(f'{git_repo}/.git/refs/heads/master')

    def test_del_missing(self, git_repo):
        """Delete a missing ref, return False."""
        gitref = GitRef(git_repo)
        assert not gitref.ref_del('refs/heads/master')


class TestRefExist:
    def test_exist_true(self, git_repo, fs):
        """An existing ref returns True."""
        fs.create_file(f'{git_repo}/.git/refs/heads/master', contents=SHA_MASTER)

        gitref = GitRef(git_repo)
        assert gitref.ref_exist('refs/heads/master')

    def test_exist_false(self, git_repo):
        """A missing ref returns False."""
        gitref = GitRef(git_repo)
        assert not gitref.ref_exist('refs/heads/master')


class TestRefAll:
    def test_packed_and_loose(self, git_repo, fs):
        """packed-refs and loose refs are merged."""
        fs.create_file(
            f'{git_repo}/.git/packed-refs',
            contents=f'{SHA_MASTER} refs/heads/master\n{SHA_ORIG} refs/tags/v1.0\n',
        )
        fs.create_file(f'{git_repo}/.git/refs/heads/dev', contents=SHA_DEV)

        gitref = GitRef(git_repo)
        assert gitref.ref_all == {
            'refs/heads/master': SHA_MASTER,
            'refs/tags/v1.0': SHA_ORIG,
            'refs/heads/dev': SHA_DEV,
        }

    def test_loose_overrides_packed(self, git_repo, fs):
        """A loose ref overrides the same ref in packed-refs."""
        fs.create_file(f'{git_repo}/.git/packed-refs', contents=f'{SHA_MASTER} refs/heads/master\n')
        fs.create_file(f'{git_repo}/.git/refs/heads/master', contents=SHA_DEV)

        gitref = GitRef(git_repo)
        assert gitref.ref_all['refs/heads/master'] == SHA_DEV

    def test_symbolic_solved(self, git_repo, fs):
        """A symbolic ref is solved to the sha1 of the target ref."""
        fs.create_file(f'{git_repo}/.git/refs/heads/master', contents=SHA_MASTER)
        fs.create_file(f'{git_repo}/.git/refs/remotes/origin/master', contents='ref: refs/heads/master')

        gitref = GitRef(git_repo)
        assert gitref.ref_all['refs/remotes/origin/master'] == SHA_MASTER

    def test_unsolved_symbolic(self, git_repo, fs):
        """A symbolic ref to a missing ref is dropped with an error log."""
        fs.create_file(f'{git_repo}/.git/refs/heads/dev', contents='ref: refs/heads/nonexist')

        gitref = GitRef(git_repo)
        with logger.mock_capture_writer() as capture:
            assert 'refs/heads/dev' not in gitref.ref_all
            assert capture.fd.any_contains('failed to solve ref')
            capture.clear()

    def test_invalid_loose_ref(self, git_repo, fs):
        """An invalid loose ref file is skipped with an error log."""
        fs.create_file(f'{git_repo}/.git/refs/heads/master', contents='not a ref')

        gitref = GitRef(git_repo)
        with logger.mock_capture_writer() as capture:
            assert gitref.ref_all == {}
            assert capture.fd.any_contains('GitRef error')
            capture.clear()

    def test_cyclic_symbolic(self, git_repo, fs):
        """A cyclic symbolic ref chain is dropped with an error log."""
        fs.create_file(f'{git_repo}/.git/refs/heads/a', contents='ref: refs/heads/b')
        fs.create_file(f'{git_repo}/.git/refs/heads/b', contents='ref: refs/heads/a')

        gitref = GitRef(git_repo)
        with logger.mock_capture_writer() as capture:
            assert gitref.ref_all == {}
            assert capture.fd.any_contains('failed to solve ref')
            capture.clear()


class TestPackedRefs:
    def test_missing_file(self, git_repo):
        """No packed-refs file, return empty dict."""
        gitref = GitRef(git_repo)
        assert gitref._packed_refs == {}

    def test_read(self, git_repo, fs):
        """Read the packed-refs file."""
        fs.create_file(f'{git_repo}/.git/packed-refs', contents=f'{SHA_MASTER} refs/heads/master\n')

        gitref = GitRef(git_repo)
        assert gitref._packed_refs == {'refs/heads/master': SHA_MASTER}
