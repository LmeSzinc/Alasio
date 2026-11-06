"""
Tests for the write functions of alasio.ext.path.atomic.

Covered: file_write() and file_write_stream(), the bare writers the atomic_*
wrappers are built on: no tmp file, no replace, no retry.

The tests run on the in-memory fake filesystem (the fs fixture), the real disk
is never touched.
"""
import os

import pytest

from alasio.ext.path.atomic import file_read_bytes, file_write, file_write_stream
from alasio.testing.filesystem import fs  # noqa: F401


class TestFileWrite:
    """Test cases for file_write()."""

    def test_write_bytes(self, fs):
        """Bytes are written in binary mode."""
        file_write('/data/a.bin', b'\x00\x01\x02')
        assert file_read_bytes('/data/a.bin') == b'\x00\x01\x02'

    def test_write_str(self, fs):
        """A str is written as utf-8, the newline characters are kept as they
        are (no newline translation)."""
        file_write('/data/a.txt', 'hello\nworld\n')
        assert file_read_bytes('/data/a.txt') == 'hello\nworld\n'.encode('utf-8')

    def test_write_bytearray(self, fs):
        """A bytearray is written in binary mode."""
        file_write('/data/a.bin', bytearray(b'data'))
        assert file_read_bytes('/data/a.bin') == b'data'

    def test_create_parent(self, fs):
        """The parent folders are created, the file is the only entry at the
        leaf."""
        file_write('/data/a/b/c.txt', 'data')
        assert os.path.isdir('/data/a/b')
        assert file_read_bytes('/data/a/b/c.txt') == b'data'

    def test_overwrite(self, fs):
        """An existing file is replaced by the new content."""
        file_write('/data/a.txt', 'old content')
        file_write('/data/a.txt', 'new')
        assert file_read_bytes('/data/a.txt') == b'new'

    def test_write_empty(self, fs):
        """An empty content is a file of zero bytes, not a missing file."""
        file_write('/data/a.txt', '')
        assert os.path.isfile('/data/a.txt')
        assert file_read_bytes('/data/a.txt') == b''

    def test_unicode(self, fs):
        """Non ascii text is encoded as utf-8."""
        file_write('/data/a.txt', '你好')
        assert file_read_bytes('/data/a.txt') == '你好'.encode('utf-8')


class TestFileWriteStream:
    """Test cases for file_write_stream()."""

    def test_write_chunks(self, fs):
        """Every chunk lands in the file, in the order of the generator."""
        file_write_stream('/data/a.txt', iter(['a', 'b', 'c']))
        assert file_read_bytes('/data/a.txt') == b'abc'

    def test_write_binary_chunks(self, fs):
        """Bytes chunks are written in binary mode."""
        file_write_stream('/data/a.bin', iter([b'\x00', b'\x01']))
        assert file_read_bytes('/data/a.bin') == b'\x00\x01'

    def test_create_parent(self, fs):
        """The parent folders are created like file_write() does."""
        file_write_stream('/data/a/b/c.txt', iter(['data']))
        assert file_read_bytes('/data/a/b/c.txt') == b'data'

    def test_overwrite(self, fs):
        """The target is replaced, not appended to."""
        file_write('/data/a.txt', 'old content')
        file_write_stream('/data/a.txt', iter(['new']))
        assert file_read_bytes('/data/a.txt') == b'new'

    def test_generator_error_leaves_the_partial_file(self, fs):
        """The bare writer has no tmp file: a generator that fails half way
        leaves the chunks it already wrote. The atomic_* wrapper cleans that up,
        file_write_stream() itself does not."""

        def chunks():
            yield 'first'
            raise ValueError('the source failed')

        with pytest.raises(ValueError):
            file_write_stream('/data/a.txt', chunks())
        assert file_read_bytes('/data/a.txt') == b'first'
