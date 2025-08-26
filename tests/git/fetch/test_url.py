import pytest

from alasio.git.fetch.url import GitUrl

# ==============================================================================
# 1. Positive Test Cases (Happy Path)
# ==============================================================================

# A comprehensive list of valid URLs and their expected parsed results.
# We use a list of tuples: (test_id, url_input, expected_output)
# The test_id helps identify which specific case failed in pytest's output.
POSITIVE_TEST_CASES = [
    # --- Standard Protocols ---
    ("httpss_simple", "https://github.com/user/repo.git",
     GitUrl(scheme='https', host='github.com', port=None, path='/user/repo')),
    ("http_with_port", "http://gitserver:8080/group/project",
     GitUrl(scheme='http', host='gitserver', port=8080, path='/group/project')),
    ("ssh_uri_with_port", "ssh://git@gitlab.com:2222/team/repo.git",
     GitUrl(scheme='ssh', host='gitlab.com', port=2222, path='/team/repo')),
    ("git_protocol", "git://host.com/path/to/repo",
     GitUrl(scheme='git', host='host.com', port=None, path='/path/to/repo')),

    # --- SCP-like SSH ---
    ("scp_like_simple", "git@github.com:user/repo.git",
     GitUrl(scheme='ssh', host='github.com', port=None, path='/user/repo')),
    ("scp_like_subdomain", "user@dev.gitserver.com:api/v2/project",
     GitUrl(scheme='ssh', host='dev.gitserver.com', port=None, path='/api/v2/project')),

    # --- File Paths ---
    ("file_uri", "file:///etc/nix/my-repo.git", GitUrl(scheme='file', host='', port=None, path='/etc/nix/my-repo')),
    ("unix_abs_path", "/var/git/project.git", GitUrl(scheme='file', host=None, port=None, path='/var/git/project')),
    ("unix_rel_path", "../relative/path", GitUrl(scheme='file', host=None, port=None, path='../relative/path')),
    ("windows_path", "C:\\Users\\MyUser\\Project.git",
     GitUrl(scheme='file', host=None, port=None, path='C:\\Users\\MyUser\\Project')),

    # --- Host Variations ---
    ("ipv4_host", "https://192.168.1.100/repo.git",
     GitUrl(scheme='https', host='192.168.1.100', port=None, path='/repo')),
    ("ipv6_host", "http://[2001:db8::1]/repo", GitUrl(scheme='http', host='2001:db8::1', port=None, path='/repo')),
    ("ipv6_host_with_port", "ssh://git@[::1]:2222/path/repo.git",
     GitUrl(scheme='ssh', host='::1', port=2222, path='/path/repo')),

    # --- Path Variations and Edge Cases ---
    ("no_git_suffix", "https://github.com/user/repo",
     GitUrl(scheme='https', host='github.com', port=None, path='/user/repo')),
    ("with_query_params", "https://host.com/repo.git?ref=main",
     GitUrl(scheme='https', host='host.com', port=None, path='/repo')),
    ("with_fragment", "https://host.com/repo.git#readme",
     GitUrl(scheme='https', host='host.com', port=None, path='/repo')),
    ("bare_domain", "https://github.com", GitUrl(scheme='https', host='github.com', port=None, path='/')),
    ("scp_empty_path", "git@host:", GitUrl(scheme='ssh', host='host', port=None, path='/')),

    # --- Real-world Cases ---
    ("alas_repo_http", "https://github.com/LmeSzinc/AzurLaneAutoScript.git",
     GitUrl(scheme='https', host='github.com', port=None, path='/LmeSzinc/AzurLaneAutoScript')),
    ("alas_repo_ssh", "git@github.com:LmeSzinc/AzurLaneAutoScript.git",
     GitUrl(scheme='ssh', host='github.com', port=None, path='/LmeSzinc/AzurLaneAutoScript')),
    ("alas_mirror", "git://git.lyoko.io/AzurLaneAutoScript",
     GitUrl(scheme='git', host='git.lyoko.io', port=None, path='/AzurLaneAutoScript')),
]


@pytest.mark.parametrize("test_id, url_input, expected_output", POSITIVE_TEST_CASES,
                         ids=[t[0] for t in POSITIVE_TEST_CASES])
def test_successful_parsing(test_id, url_input, expected_output):
    """
    Tests that various forms of valid Git URLs are parsed correctly.
    """
    result = GitUrl.from_string(url_input)
    assert result == expected_output


# ==============================================================================
# 2. Aggressive and Malformed Input Tests (Attack Path)
# ==============================================================================

# This section tests inputs that are intentionally malformed, ambiguous,
# or designed to break simple string splitting logic.
AGGRESSIVE_TEST_CASES = [
    # --- Ambiguous Inputs ---
    ("ambiguous_windows_path", "C:repo", GitUrl(scheme='file', host=None, port=None, path='C:repo')),
    ("colon_in_path_not_scp", "project:feature/branch",
     GitUrl(scheme='file', host=None, port=None, path='project:feature/branch')),
    ("at_symbol_in_path_not_scp", "some/path@version",
     GitUrl(scheme='file', host=None, port=None, path='some/path@version')),

    # --- Malformed URI components ---
    # ("uri_with_non_numeric_port", "http://host:abc/repo",
    #  GitUrl(scheme='http', host='host:abc', port=None, path='/repo')),
    ("uri_with_multiple_colons_no_brackets", "http://2001:db8::1:8000/repo",
     GitUrl(scheme='http', host='2001:db8::1:8000', port=None, path='/repo')),
    ("uri_with_multiple_at_symbols", "ssh://user1@user2@host/repo",
     GitUrl(scheme='ssh', host='host', port=None, path='/repo')),
    ("uri_with_no_authority", "https://", GitUrl(scheme='https', host='', port=None, path='/')),
    ("uri_just_scheme", "http://", GitUrl(scheme='http', host='', port=None, path='/')),

    # --- Empty and Weird Inputs ---
    ("empty_string", "", GitUrl(scheme='file', host=None, port=None, path='.')),
    ("multiple_protocol_separators", "a://b://c", GitUrl(scheme='a', host='b', port=None, path='//c')),
    ("strange_chars_in_host", "https://!@#$%^/path", GitUrl(scheme='https', host='', port=None, path='/')),
    # userinfo is '!', host is '', '#$%^/path' is consider as #xxxxx
]


@pytest.mark.parametrize("test_id, url_input, expected_output", AGGRESSIVE_TEST_CASES,
                         ids=[t[0] for t in AGGRESSIVE_TEST_CASES])
def test_aggressive_and_malformed_urls(test_id, url_input, expected_output):
    """
    Tests that malformed, ambiguous, and aggressive inputs are handled gracefully
    and produce a predictable (if not strictly "correct") output without crashing.
    """
    result = GitUrl.from_string(url_input)
    assert result == expected_output


# ==============================================================================
# 3. Exception Tests
# ==============================================================================

def test_raises_error():
    """
    Tests that passing None as input correctly raises an exception,
    as string methods cannot be called on None.
    """
    with pytest.raises((TypeError, AttributeError)):
        GitUrl.from_string(None)
    # Malformed port
    with pytest.raises(ValueError):
        GitUrl.from_string('http://host:abc/repo')


# ==============================================================================
# 4. URL Reconstruction Tests
# ==============================================================================

@pytest.fixture
def network_url_model() -> GitUrl:
    """Provides a standard network GitUrl instance for reconstruction tests."""
    return GitUrl(scheme='ssh', host='gitlab.com', port=2222, path='/group/project')


@pytest.fixture
def ipv6_url_model() -> GitUrl:
    """Provides an IPv6-based GitUrl instance."""
    return GitUrl(scheme='https', host='2001:db8::1', port=8000, path='/repo')


@pytest.fixture
def local_path_model() -> GitUrl:
    """Provides a local file path GitUrl instance."""
    return GitUrl(scheme='file', host=None, port=None, path='/var/git/project')


def test_to_http_reconstruction(network_url_model, ipv6_url_model):
    """Tests the to_http() method with all its options."""
    # Standard case
    assert network_url_model.to_http() == "https://gitlab.com:2222/group/project.git"
    # Toggling https
    assert network_url_model.to_http(https=False) == "http://gitlab.com:2222/group/project.git"
    # Toggling git_suffix
    assert network_url_model.to_http(git_suffix=False) == "https://gitlab.com:2222/group/project"
    # IPv6 case
    assert ipv6_url_model.to_http() == "https://[2001:db8::1]:8000/repo.git"


def test_to_ssh_reconstruction(network_url_model, ipv6_url_model):
    """Tests the to_ssh() method."""
    assert network_url_model.to_ssh() == "ssh://git@gitlab.com:2222/group/project.git"
    assert network_url_model.to_ssh(git_suffix=False) == "ssh://git@gitlab.com:2222/group/project"
    assert ipv6_url_model.to_ssh() == "ssh://git@[2001:db8::1]:8000/repo.git"


def test_to_git_reconstruction(network_url_model):
    """Tests the to_git() method."""
    # The Git protocol rarely uses ports, but the formatter should handle it
    assert network_url_model.to_git() == "git://gitlab.com:2222/group/project.git"
    assert network_url_model.to_git(git_suffix=False) == "git://gitlab.com:2222/group/project"


def test_to_git_scp_reconstruction(network_url_model, ipv6_url_model):
    """Tests the to_git_scp() method, ensuring it ignores the port."""
    # Port number (2222) should be ignored in the output
    assert network_url_model.to_git_scp() == "git@gitlab.com:group/project.git"
    assert network_url_model.to_git_scp(git_suffix=False) == "git@gitlab.com:group/project"
    # IPv6 host should not be bracketed in SCP-like format
    assert ipv6_url_model.to_git_scp() == "git@2001:db8::1:repo.git"


def test_reconstruction_fails_on_local_path(local_path_model):
    """
    Tests that all network URL reconstruction methods raise a ValueError
    when called on a model parsed from a local path (host is None).
    """
    with pytest.raises(ValueError, match="Cannot generate a network URL"):
        local_path_model.to_http()

    with pytest.raises(ValueError, match="Cannot generate a network URL"):
        local_path_model.to_ssh()

    with pytest.raises(ValueError, match="Cannot generate a network URL"):
        local_path_model.to_git()

    with pytest.raises(ValueError, match="Cannot generate an SCP-like URL"):
        local_path_model.to_git_scp()
