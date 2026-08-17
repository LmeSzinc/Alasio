"""
Tests for alasio/ext/path/validate.py.

validate_filename() is pure string validation, validate_resolve_filepath()
runs on the in-memory fake filesystem.
"""
import os

import pytest

from alasio.ext.path.validate import validate_filename, validate_resolve_filepath
from alasio.testing.filesystem import fs  # noqa: F401


class TestValidateFilename:
    """Tests for validate_filename()."""

    # We use parametrize to test a wide range of invalid inputs with a single function.
    @pytest.mark.parametrize("invalid_name", [
        # Check 1: Type and emptiness
        None,
        123,
        [],
        {},
        "",

        # Check 2: Character length too long
        "a" * 256,

        # Check 3: Illegal characters
        "my/file.txt",
        "my\\file.txt",
        "my:file.txt",
        "my*file.txt",
        'my"file.txt',
        "my?file.txt",
        "my<file.txt",
        "my>file.txt",
        "my|file.txt",

        # Check 3: Control characters
        "file-with-newline\n.txt",
        "file-with-tab\t.txt",
        "file-with-null\0.txt",

        # Check 4: Reserved names
        ".",
        "..",
        "$MFT",
        "con",
        "PRN.txt",
        "lpt1.doc",
        "COM5.zip",
        "NUL",
        "aux.json",
        # windows will ignore <space> prefix, <space> <dot> suffix
        "CON ",
        " CON",
        "CON.",
        "CON. ",
        "CON .",
        "LPT1.txt",
        "LPT1 .txt",
        " LPT1.txt",
        "LPT1..txt",
        "LPT1.txt.",
        "LPT1.abc.txt",
        "LPT1 .abc.txt",
        " LPT1.abc.txt",

        # Check 5: Invalid start/end characters
        " starts-with-space.txt",
        "ends-with-space.txt ",
        "ends-with-dot.txt.",

        # Check 6: Byte length too long (aggressive test)
        "a" * 253 + "€",  # Char len is 254, but byte len is 256

        # Check 6: Invalid encoding (aggressive test)
        "malformed-\ud800-string.txt",
    ])
    def test_invalid_inputs_raise_value_error(self, invalid_name):
        """
        Verifies that validate_filename raises a ValueError for any invalid input.
        This test does NOT check the content of the error message, only that an
        exception of the correct type is raised.
        """
        with pytest.raises(ValueError):
            validate_filename(invalid_name)

    @pytest.mark.parametrize("valid_name", [
        "file.txt",
        "document-1.docx",
        "image.jpg",
        "a" * 255,  # Max length
    ])
    def test_valid_inputs_do_not_raise_exception(self, valid_name):
        """
        Verifies that valid filenames do not cause any exception to be raised.
        This is the counterpart to the exception test, ensuring the function
        doesn't fail on good data.
        """
        try:
            validate_filename(valid_name)
        except ValueError:
            pytest.fail(f"validate_filename('{valid_name}') raised an unexpected ValueError.")


class TestValidateResolveFilepath:
    """Tests for validate_resolve_filepath() on the in-memory fake filesystem."""

    @pytest.fixture
    def temp_fs(self, fs):
        """
        Create a controlled in-memory filesystem environment for testing
        path resolution and traversal.

        Structure created:
        {root}/
        ├── safe_dir/
        │   ├── existing_file.txt
        │   └── link_to_secret -> ../outside_dir/secret.txt
        └── outside_dir/
            └── secret.txt

        Yields:
            dict[str, str]: safe_dir, outside_dir and symlink_path
        """
        root = 'C:/test_root' if os.name == 'nt' else '/test_root'
        safe_dir = f'{root}/safe_dir'
        outside_dir = f'{root}/outside_dir'
        fs.create_file(f'{safe_dir}/existing_file.txt', contents='safe content')
        fs.create_file(f'{outside_dir}/secret.txt', contents='secret content')
        os.symlink(f'{outside_dir}/secret.txt', f'{safe_dir}/link_to_secret')

        yield {
            'safe_dir': safe_dir,
            'outside_dir': outside_dir,
            'symlink_path': f'{safe_dir}/link_to_secret',
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
    def test_invalid_paths_raise_value_error(self, temp_fs, invalid_path):
        """A comprehensive test for a wide range of invalid and malicious paths."""
        with pytest.raises(ValueError):
            validate_resolve_filepath(temp_fs["safe_dir"], invalid_path)

    def test_symlink_traversal_raises_value_error(self, temp_fs):
        """A symlink pointing outside the safe directory should be rejected."""
        with pytest.raises(ValueError, match="Path traversal detected"):
            validate_resolve_filepath(temp_fs["safe_dir"], "link_to_secret")

    @pytest.mark.parametrize("valid_path, expected_suffix", [
        ("file.txt", "file.txt"),
        ("new_dir/new_file.txt", "new_dir/new_file.txt"),
        # ("a/b/../c/file.txt", "a/c/file.txt"),
        # ("./a/./b/file.txt", "a/b/file.txt"),
    ])
    def test_valid_paths_return_correct_absolute_path(self, temp_fs, valid_path, expected_suffix):
        """Valid paths should resolve to the absolute path inside the safe dir."""
        safe_dir = temp_fs["safe_dir"]
        try:
            resolved_path = validate_resolve_filepath(safe_dir, valid_path)
            assert os.path.isabs(resolved_path)
            assert resolved_path.startswith(safe_dir)
            expected_path = f'{safe_dir}/{expected_suffix}'
            assert resolved_path == expected_path
        except ValueError as e:
            pytest.fail(f"validate_and_resolve_path('{valid_path}') raised an unexpected ValueError: {e}")
