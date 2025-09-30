import os

import pytest

from alasio.ext.path.validate import validate_and_resolve_path


@pytest.fixture(scope='module')
def temp_fs(tmp_path_factory):
    """
    Pytest fixture to create a temporary, controlled filesystem environment
    for testing path resolution and traversal.

    This fixture now uses pytest's built-in `tmp_path` for automatic
    creation and cleanup, returning a pathlib.Path object.

    Structure created:
    {tmp_path}/
    ├── safe_dir/
    │   └── existing_file.txt
    └── outside_dir/
        └── secret.txt
    """
    tmp_path = tmp_path_factory.mktemp("shared_test_root")

    safe_dir = tmp_path / "safe_dir"
    outside_dir = tmp_path / "outside_dir"
    secret_file = outside_dir / "secret.txt"

    safe_dir.mkdir()
    outside_dir.mkdir()
    (safe_dir / "existing_file.txt").write_text("safe content")
    secret_file.write_text("secret content")

    symlink_path = safe_dir / "link_to_secret"
    symlink_created = False
    try:
        symlink_path.symlink_to(secret_file)
        symlink_created = True
    except (OSError, AttributeError, NotImplementedError):
        # Symlink creation might fail on Windows without admin rights
        # or on certain filesystems.
        pass

    yield {
        "safe_dir": str(safe_dir),
        "outside_dir": str(outside_dir),
        "symlink_path": str(symlink_path) if symlink_created else None
    }


@pytest.mark.parametrize("invalid_path", [
    # --- Advanced Traversal & Obfuscation ---
    "a/b/c/../../../..",  # Resolves to one level above safe_dir root
    "a/./b/../c/../../..",  # Mixed '.' and '..'
    "a\\b/..\\../..",  # Mixed path separators (Windows/Linux)

    # --- URL Encoding (Simulated) ---
    # In a real web app, these would be decoded before hitting the function,
    # so we test the decoded form.
    # "..%2f..%2fetc%2fpasswd",  # Simulating decoded URL-encoded slash
    # "..%5c..%5cboot.ini",  # Simulating decoded URL-encoded backslash

    # --- Null Byte Injection ---
    # Our string-level validation should catch this.
    "a/b/c\0/real_file.txt",
    "a/b/c.txt\0.log",

    # --- Filename & Extension Obfuscation (Windows-specific behavior) ---
    "CON.txt",  # Reserved name with extension
    "LPT1.anything",  # Reserved name with extension
    "file.txt.",  # Trailing dot
    "file.txt ",  # Trailing space
    " file.txt",  # Leading space

    # --- Deeply Nested Paths (within character limits) ---
    # This tests for potential performance issues or recursion limits,
    # though our function is iterative.
    "/".join(["d"] * 50) + "/../../" + "../outside_dir/secret.txt",

    # --- Path Normalization Edge Cases ---
    "safe_dir/../safe_dir/../outside_dir/secret.txt",  # Weaving in and out

    # --- Non-standard but potentially problematic ---
    # "a/b~1.txt",  # Short filename notation (should be valid but good check)
    "a::$DATA",  # NTFS Alternate Data Streams (colon is blocked)

    # --- Unicode Homoglyph/Lookalike Attacks ---
    # Simulating a user trying to create a file that looks like another.
    # Our function allows unicode, but this is a reminder of this attack class.
    # The validation should still pass if the characters are valid.
    # e.g., "ｓcript.js" (full-width) vs "script.js" (half-width)
    # No direct test here as our validator correctly allows valid Unicode,
    # but it's an important attack vector to be aware of at a higher level.
])
def test_all_invalid_paths_raise_value_error(temp_fs, invalid_path):
    """A comprehensive test for a wide range of invalid and malicious paths."""
    with pytest.raises(ValueError):
        validate_and_resolve_path(temp_fs["safe_dir"], invalid_path)


def test_symlink_traversal_raises_value_error(temp_fs):
    if not temp_fs["symlink_path"]:
        pytest.skip("Symlink could not be created, skipping this test.")

    with pytest.raises(ValueError, match="Path traversal detected"):
        validate_and_resolve_path(temp_fs["safe_dir"], "link_to_secret")


@pytest.mark.parametrize("valid_path, expected_suffix", [
    ("file.txt", "file.txt"),
    ("new_dir/new_file.txt", "new_dir/new_file.txt"),
    # ("a/b/../c/file.txt", "a/c/file.txt"),
    # ("./a/./b/file.txt", "a/b/file.txt"),
])
def test_valid_paths_return_correct_absolute_path(temp_fs, valid_path, expected_suffix):
    safe_dir = temp_fs["safe_dir"]
    try:
        resolved_path = validate_and_resolve_path(safe_dir, valid_path)
        assert os.path.isabs(resolved_path)
        assert resolved_path.startswith(safe_dir)
        expected_path = os.path.abspath(os.path.join(safe_dir, expected_suffix))
        assert resolved_path == expected_path
    except ValueError as e:
        pytest.fail(f"validate_and_resolve_path('{valid_path}') raised an unexpected ValueError: {e}")
