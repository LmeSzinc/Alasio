import zlib

import pytest

from alasio.gitpython.file.exception import ObjectBroken
from alasio.gitpython.obj.objtree import parse_tree

obj_tree = (b'100644 assets.py\x00~\xa5A\x10\xe3_\xe9\x85\xba\xf0\xc2\x80\x9c*Ve\xbe\r\xba\x02'
            b'100644 camera.py\x00sY:\xb9\'\xcdD/\x9f\xc8s\x88K\xbe\x15\xb7\xec\x84\xc9\x1f'
            b'100644 exception.py\x00+\xe6\x87\x08\x89\xddp&\xa9F\xf2\xfa\x90\xee3\x19\xe7}\x80B'
            b'100644 fleet.py\x00:\xe0\x13\xa8s\x8e\x1c\x7fL\x84\xa1\xea4\xac\x15\xda\x1f\x8e\x9b\x85'
            b'100644 grid.py\x00A\x91\x9a1v\x14\x94\xe0\x985\xb0\x9b\x82e@\xf5\xb4\xace\x93'
            b'100644 grid_info.py\x00T=+\xb50)$\xe0r\xd4\x08\xbb\xfb\x94\x00T\xef\xc1\xe7\x88'
            b'100644 grid_predictor.py\x00G\x9fW&?\x17\xf48Q\xc9\xa0c\x08C\x00k\xcd=\x0b\x10'
            b'100644 grids.py\x00\x912\xbau\x15\xb7\xb2\x7f\xa2\xaf\xa3S\x14?\x0by$\xd8\x832'
            b'100644 map.py\x00\xa0\x13A6\xb1\xb0\x03R\xa6\xda\x9d08\xf3x\x8dB\xf8P\xb8'
            b'100644 map_base.py\x00Jt t \xa1dB\xd1\x18\xd7D\'d\x0e\xdd\xd2T\x02\x9f'
            b'100644 map_fleet_preparation.py\x00\xe9\x9a\x9e$\xe6\xfd=(\xe4\xa0\xd5\x99\xa6\xd3\xe1\xca\xa8\xd9\xde0'
            b'100644 map_grids.py\x00\xa0\x9c\x8e\x19\xc4\xaek\xbd,\xd8{\xb8\xb1Z\xd9\xc9\xd4\xfdh\xcf'
            b'100644 map_operation.py\x00\xfbu\'Ml\xc5\x99^,7\x1f]\x00\x01\x81" \xe3\x0f\xe7'
            b'100644 perspective.py\x00\x95d\xd0\xc0\xad"=zy\x14\xea\xabH\xf9\r\xd1\xe7\x97\xf6\x1b'
            b'100644 perspective_items.py\x00"Ri\xe6\xd1]^~\xc1a\x03\x0b\x00\xef\xfb\xcb3\x1aq\xec'
            b'100644 ui_mask.png\x0087\xc5\xa3\x02o\x98\x00Bg\xf8\x8c6\xd4\xd2\xd9{\x88^O')


def test_parse_tree_success():
    """Test successful parsing of a valid tree object."""
    # Compress the tree object to simulate git object storage
    compressed_data = zlib.compress(obj_tree)

    # Parse the tree
    entries = parse_tree(compressed_data)

    # Check that we got the expected number of entries
    assert len(entries) == 16

    # Check a few specific entries to ensure correct parsing
    assert entries[0].name == "assets.py"
    assert entries[0].mode == b"100644"
    assert len(entries[0].sha1) == 20

    assert entries[4].name == "grid.py"
    assert entries[4].mode == b"100644"

    # Check the last entry
    assert entries[-1].name == "ui_mask.png"
    assert entries[-1].mode == b"100644"


def test_different_valid_modes():
    """Test parsing tree entries with different valid modes."""
    # Create a sample tree with different valid modes
    sample_tree = (
            b'100644 regular_file.txt\x00' + b'a' * 20 +  # Regular file
            b'100755 executable.sh\x00' + b'b' * 20 +  # Executable file
            b'40000 directory\x00' + b'c' * 20 +  # Directory
            b'120000 symlink\x00' + b'd' * 20 +  # Symlink
            b'160000 submodule\x00' + b'e' * 20  # Gitlink/submodule
    )

    compressed_data = zlib.compress(sample_tree)
    entries = parse_tree(compressed_data)

    assert len(entries) == 5

    # Verify each mode type
    assert entries[0].mode == b'100644'
    assert entries[0].name == 'regular_file.txt'

    assert entries[1].mode == b'100755'
    assert entries[1].name == 'executable.sh'

    assert entries[2].mode == b'40000'
    assert entries[2].name == 'directory'

    assert entries[3].mode == b'120000'
    assert entries[3].name == 'symlink'

    assert entries[4].mode == b'160000'
    assert entries[4].name == 'submodule'


def test_invalid_file_mode():
    """Test parsing a tree with an invalid file mode."""
    # Create a tree object with an invalid mode
    invalid_mode_tree = b'123456 invalid.txt\x00' + b'a' * 20

    compressed_data = zlib.compress(invalid_mode_tree)

    # Parsing should raise ObjectBroken exception
    with pytest.raises(ObjectBroken) as excinfo:
        parse_tree(compressed_data)

    # Verify the exception message contains information about the invalid mode
    assert "Invalid filemode" in str(excinfo.value)
    assert b'123456' in str(excinfo.value).encode()


def test_unicode_decode_error():
    """Test parsing a tree with a filename that cannot be decoded as UTF-8."""
    # Create a tree object with a filename containing invalid UTF-8 bytes
    invalid_filename_tree = b'100644 \xff\xfe\x00invalid.txt\x00' + b'a' * 20

    compressed_data = zlib.compress(invalid_filename_tree)

    # Parsing should raise ObjectBroken exception
    with pytest.raises(ObjectBroken) as excinfo:
        parse_tree(compressed_data)

    # Verify the exception message contains information about the decode error
    assert "Failed to decode filename" in str(excinfo.value)


def test_invalid_sha1():
    """Test parsing a tree with an invalid (too short) SHA1."""
    # Create a tree object with a short SHA1
    short_sha1_tree = b'100644 file.txt\x00' + b'a' * 10  # SHA1 should be 20 bytes

    compressed_data = zlib.compress(short_sha1_tree)

    # Parsing should raise ObjectBroken exception
    with pytest.raises(ObjectBroken) as excinfo:
        parse_tree(compressed_data)

    # Verify the exception message contains information about the invalid SHA1
    assert "Invalid entry sha1" in str(excinfo.value)


def test_decompression_error():
    """Test handling of zlib decompression errors."""
    # Create invalid compressed data
    invalid_compressed_data = b'not a valid zlib compressed data'

    # Parsing should raise ObjectBroken exception
    with pytest.raises(ObjectBroken) as excinfo:
        parse_tree(invalid_compressed_data)

    # The exception should wrap the original zlib error
    assert "zlib" in str(excinfo.value).lower() or "error" in str(excinfo.value).lower()


def test_empty_tree():
    """Test parsing an empty tree."""
    # An empty tree is not valid in git, but let's test the handling
    empty_tree = b''

    compressed_data = zlib.compress(empty_tree)

    # This should raise an exception since a valid tree must have at least one entry
    with pytest.raises(ObjectBroken):
        parse_tree(compressed_data)


def test_filename_with_spaces():
    """Test parsing a tree with filenames containing spaces."""
    # Create a tree with a filename containing spaces
    spaced_filename_tree = b'100644 file with spaces.txt\x00' + b'a' * 20

    compressed_data = zlib.compress(spaced_filename_tree)

    entries = parse_tree(compressed_data)

    assert len(entries) == 1
    assert entries[0].name == "file with spaces.txt"
    assert entries[0].mode == b"100644"


def test_filename_with_null_bytes():
    """Test that filenames with embedded null bytes are handled correctly.

    This is an edge case that should not happen in valid git trees, but we should
    test how the parser handles it.
    """
    # The code actually doesn't handle this case specifically because it uses partition,
    # so the first null byte will be treated as the separator. Let's test this behavior.
    filename_with_null = b'100644 file\x00with\x00nulls.txt\x00' + b'a' * 20

    compressed_data = zlib.compress(filename_with_null)

    # The function will interpret the first \x00 as the separator between name and SHA1
    # This will result in a parse error because the SHA1 will be wrong
    with pytest.raises(ObjectBroken):
        parse_tree(compressed_data)


def test_sha1_with_null_bytes():
    """Test handling of SHA1 values that contain null bytes."""
    # Create a valid tree entry where the SHA1 contains null bytes
    # This is a valid case since SHA1 is binary and can contain any byte value
    sha1_with_null = b'\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x00\x0b\x0c\x0d\x0e\x0f\x10\x11\x12\x13'
    assert len(sha1_with_null) == 20  # Ensure it's the right length

    tree_with_null_sha1 = b'100644 test.txt\x00' + sha1_with_null

    compressed_data = zlib.compress(tree_with_null_sha1)

    entries = parse_tree(compressed_data)

    assert len(entries) == 1
    assert entries[0].name == "test.txt"
    assert entries[0].sha1 == sha1_with_null


def test_multiple_entries_same_name():
    """Test parsing a tree with multiple entries that have the same name.

    This shouldn't happen in a valid git tree, but the parser should handle it.
    """
    # Create a tree with duplicate filenames
    duplicate_names_tree = (
            b'100644 file.txt\x00' + b'a' * 20 +
            b'100644 file.txt\x00' + b'b' * 20
    )

    compressed_data = zlib.compress(duplicate_names_tree)

    entries = parse_tree(compressed_data)

    # Parser should return both entries without error
    assert len(entries) == 2
    assert entries[0].name == "file.txt"
    assert entries[1].name == "file.txt"
    # Check they have different SHA1s
    assert entries[0].sha1 != entries[1].sha1
