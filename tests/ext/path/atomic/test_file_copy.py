"""
Tests for the copy and the presence functions of alasio.ext.path.atomic.

Covered: file_copy(), file_ensure_exist() and file_touch(), the bare layer the
atomic_* wrappers are built on: no tmp file, no replace, no retry.

The tests run on the in-memory fake filesystem (the fs fixture), the real disk
is never touched.
"""
import os

import pytest

from alasio.ext.path.atomic import file_copy, file_ensure_exist, file_read_bytes, file_touch, file_write
from alasio.testing.filesystem import fs  # noqa: F401


class TestFileCopy:
    """Test cases for file_copy()."""

    def test_copy(self, fs):
        """The content is copied, the source stays."""
        file_write('/data/source.bin', b'\x00\x01\x02')
        file_copy('/data/source.bin', '/data/target.bin')

        assert file_read_bytes('/data/target.bin') == b'\x00\x01\x02'
        assert file_read_bytes('/data/source.bin') == b'\x00\x01\x02'

    def test_copy_chunks(self, fs):
        """A content larger than chunk_size is copied whole."""
        content = bytes(range(256)) * 4
        file_write('/data/source.bin', content)
        file_copy('/data/source.bin', '/data/target.bin', chunk_size=16)

        assert file_read_bytes('/data/target.bin') == content

    def test_copy_exact_chunk(self, fs):
        """A content of exactly chunk_size is copied whole."""
        content = b'a' * 16
        file_write('/data/source.bin', content)
        file_copy('/data/source.bin', '/data/target.bin', chunk_size=16)

        assert file_read_bytes('/data/target.bin') == content

    def test_copy_empty(self, fs):
        """An empty source produces an empty target: the target exists, it is
        not missing."""
        file_write('/data/source.bin', b'')
        file_copy('/data/source.bin', '/data/target.bin')

        assert os.path.isfile('/data/target.bin')
        assert file_read_bytes('/data/target.bin') == b''

    def test_copy_create_parent(self, fs):
        """The parent folders of the target are created."""
        file_write('/data/source.bin', b'data')
        file_copy('/data/source.bin', '/data/a/b/target.bin')

        assert file_read_bytes('/data/a/b/target.bin') == b'data'

    def test_copy_overwrite(self, fs):
        """An existing target is replaced."""
        file_write('/data/source.bin', b'new')
        file_write('/data/target.bin', b'old content')
        file_copy('/data/source.bin', '/data/target.bin')

        assert file_read_bytes('/data/target.bin') == b'new'

    def test_copy_missing_source(self, fs):
        """A missing source raises FileNotFoundError, no target is created."""
        with pytest.raises(FileNotFoundError):
            file_copy('/data/missing.bin', '/data/target.bin')

        assert not os.path.exists('/data/target.bin')


class TestFileEnsureExist:
    """Test cases for file_ensure_exist()."""

    def test_create(self, fs):
        """A missing file is created, the creation is reported."""
        assert file_ensure_exist('/data/a.txt') is True
        assert os.path.isfile('/data/a.txt')
        assert file_read_bytes('/data/a.txt') == b''

    def test_create_with_default(self, fs):
        """The default content is written into the new file."""
        assert file_ensure_exist('/data/a.txt', default=b'default') is True
        assert file_read_bytes('/data/a.txt') == b'default'

    def test_existing_is_not_touched(self, fs):
        """An existing file keeps its content, the creation is not reported."""
        file_write('/data/a.txt', b'the content')
        assert file_ensure_exist('/data/a.txt', default=b'default') is False
        assert file_read_bytes('/data/a.txt') == b'the content'

    def test_missing_parent_is_created(self, fs):
        """The parent folders are created: the caller does not prepare the
        tree of a file it only ensures."""
        assert file_ensure_exist('/data/a/b.txt', default=b'default') is True
        assert file_read_bytes('/data/a/b.txt') == b'default'

    def test_mode(self, fs):
        """The mode of the caller is applied to the new file."""
        file_ensure_exist('/data/a.txt', mode=0o600)
        assert os.stat('/data/a.txt').st_mode & 0o777 == 0o600


class TestFileTouch:
    """Test cases for file_touch()."""

    def test_create(self, fs):
        """A missing file is created empty."""
        file_touch('/data/a.txt')
        assert os.path.isfile('/data/a.txt')
        assert file_read_bytes('/data/a.txt') == b''

    def test_create_missing_parent(self, fs):
        """The parent folders are created with the file."""
        file_touch('/data/a/b.txt')
        assert os.path.isfile('/data/a/b.txt')

    def test_existing_keeps_content(self, fs):
        """An existing file keeps its content, only the time is bumped."""
        file_write('/data/a.txt', b'the content')
        file_touch('/data/a.txt')
        assert file_read_bytes('/data/a.txt') == b'the content'

    def test_existing_raises_when_not_allowed(self, fs):
        """exist_ok=False refuses an existing file."""
        file_write('/data/a.txt', b'the content')
        with pytest.raises(FileExistsError):
            file_touch('/data/a.txt', exist_ok=False)
        assert file_read_bytes('/data/a.txt') == b'the content'
