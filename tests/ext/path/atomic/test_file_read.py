"""
Tests for the read functions of alasio.ext.path.atomic.

Covered: file_read_text(), file_read_text_stream(), file_read_bytes(),
file_read_bytes_stream() and file_read_bytes_into(), the bare readers the
atomic_* wrappers are built on: one open, no retry, no tmp file.

The tests run on the in-memory fake filesystem (the fs fixture), the real disk
is never touched.
"""
import pytest

from alasio.ext.path.atomic import (
    file_read_bytes, file_read_bytes_into, file_read_bytes_stream, file_read_text, file_read_text_stream
)
from alasio.testing.filesystem import fs  # noqa: F401


def write_bytes(fs, path, content):
    """
    Write the content of a test file.

    Args:
        fs (FakeFilesystem): Fake filesystem
        path (str): Path of the file
        content (bytes): Content to write
    """
    fs.create_file(path, contents=content)


class TestFileReadText:
    """Test cases for file_read_text()."""

    def test_read(self, fs):
        """The whole content is returned as str."""
        write_bytes(fs, '/data/a.txt', 'hello\nworld'.encode('utf-8'))
        assert file_read_text('/data/a.txt') == 'hello\nworld'

    def test_read_encoding(self, fs):
        """The given encoding is used to decode the bytes."""
        write_bytes(fs, '/data/a.txt', '你好'.encode('utf-8'))
        assert file_read_text('/data/a.txt', encoding='utf-8') == '你好'

    def test_read_invalid_utf8(self, fs):
        """Bytes that are not valid utf-8 raise UnicodeDecodeError by default."""
        write_bytes(fs, '/data/a.txt', b'\xff\xfe')
        with pytest.raises(UnicodeDecodeError):
            file_read_text('/data/a.txt')

    def test_read_errors_replace(self, fs):
        """The errors mode of the caller is passed to the decoder."""
        write_bytes(fs, '/data/a.txt', b'a\xffb')
        assert file_read_text('/data/a.txt', errors='replace') == 'a\ufffdb'

    def test_read_missing(self, fs):
        """A missing file raises FileNotFoundError, nothing is created."""
        with pytest.raises(FileNotFoundError):
            file_read_text('/data/missing.txt')


class TestFileReadTextStream:
    """Test cases for file_read_text_stream()."""

    def test_read_chunks(self, fs):
        """The chunks concatenate to the whole content."""
        content = ''.join(f'line {index}\n' for index in range(20))
        write_bytes(fs, '/data/a.txt', content.encode('utf-8'))

        assert ''.join(file_read_text_stream('/data/a.txt', chunk_size=7)) == content

    def test_read_empty(self, fs):
        """An empty file yields no chunk."""
        write_bytes(fs, '/data/a.txt', b'')
        assert list(file_read_text_stream('/data/a.txt')) == []

    def test_read_missing(self, fs):
        """A missing file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            list(file_read_text_stream('/data/missing.txt'))


class TestFileReadBytes:
    """Test cases for file_read_bytes()."""

    def test_read(self, fs):
        """The whole content is returned as bytes."""
        write_bytes(fs, '/data/a.bin', b'\x00\x01\x02')
        assert file_read_bytes('/data/a.bin') == b'\x00\x01\x02'

    def test_read_empty(self, fs):
        """An empty file reads as b''."""
        write_bytes(fs, '/data/a.bin', b'')
        assert file_read_bytes('/data/a.bin') == b''

    def test_read_binary_is_not_decoded(self, fs):
        """Bytes that are not text are returned as they are."""
        write_bytes(fs, '/data/a.bin', b'\xff\xfe\x00')
        assert file_read_bytes('/data/a.bin') == b'\xff\xfe\x00'

    def test_read_missing(self, fs):
        """A missing file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            file_read_bytes('/data/missing.bin')


class TestFileReadBytesStream:
    """Test cases for file_read_bytes_stream()."""

    def test_read_chunks(self, fs):
        """The chunks concatenate to the whole content, at most chunk_size
        bytes each."""
        content = bytes(range(256))
        write_bytes(fs, '/data/a.bin', content)

        chunks = list(file_read_bytes_stream('/data/a.bin', chunk_size=16))
        assert b''.join(chunks) == content
        assert all(len(chunk) <= 16 for chunk in chunks)
        assert len(chunks) == 16

    def test_read_empty(self, fs):
        """An empty file yields no chunk."""
        write_bytes(fs, '/data/a.bin', b'')
        assert list(file_read_bytes_stream('/data/a.bin')) == []

    def test_read_missing(self, fs):
        """A missing file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            list(file_read_bytes_stream('/data/missing.bin'))


class TestFileReadBytesInto:
    """Test cases for file_read_bytes_into()."""

    def test_read_into_buffer(self, fs):
        """The content fills the buffer, the yielded lengths sum to the size."""
        content = b'0123456789'
        write_bytes(fs, '/data/a.bin', content)
        buffer = bytearray(4)

        total = 0
        data = bytearray()
        for length in file_read_bytes_into('/data/a.bin', memoryview(buffer)):
            data += bytes(buffer[:length])
            total += length

        assert bytes(data) == content
        assert total == len(content)

    def test_read_into_exact_buffer(self, fs):
        """A buffer of the size of the file reads it in one chunk."""
        write_bytes(fs, '/data/a.bin', b'abcd')
        buffer = bytearray(4)

        lengths = [length for length in file_read_bytes_into('/data/a.bin', memoryview(buffer))]

        assert lengths == [4]
        assert bytes(buffer) == b'abcd'

    def test_read_empty(self, fs):
        """An empty file yields no length."""
        write_bytes(fs, '/data/a.bin', b'')
        assert list(file_read_bytes_into('/data/a.bin', memoryview(bytearray(4)))) == []

    def test_read_missing(self, fs):
        """A missing file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            list(file_read_bytes_into('/data/missing.bin', memoryview(bytearray(4))))
