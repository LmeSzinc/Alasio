import pytest

from alasio.git.stage.gitref import LooseRef, parse_loose_ref, parse_packed_refs


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
