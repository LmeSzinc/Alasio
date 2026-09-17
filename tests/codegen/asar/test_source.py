"""
Tests of the content sources: what an entry reads its content from.

The sources are the only place that knows where the bytes of an entry live, so
they are tested on their own: the chunks they yield, the handle they read
through and the errors they report.
"""
import os

import pytest

from alasio.codegen.asar.errors import AsarError
from alasio.codegen.asar.source import ContentSource, LocalFileSource, MemorySource, RangeSource
from alasio.testing.filesystem import fs  # noqa: F401


class FakeHandle:
    """
    An open file that serves a byte string and records how it was read.
    """

    def __init__(self, data):
        self.data = data
        self.position = 0
        self.seeks = []
        self.reads = []
        self.closed = False

    def seek(self, offset, whence=0):
        assert whence == 0
        self.seeks.append(offset)
        self.position = offset
        return offset

    def read(self, size=-1):
        self.reads.append(size)
        if size is None or size < 0:
            end = len(self.data)
        else:
            end = self.position + size
        chunk = self.data[self.position:end]
        self.position += len(chunk)
        return chunk


class TestContentSource:
    def test_interface(self):
        """The base class is an interface, it has no content of its own."""
        source = ContentSource()
        assert source.mode is None
        with pytest.raises(NotImplementedError):
            list(source.iter_chunks())


class TestMemorySource:
    def test_content(self):
        """The whole content is yielded in one chunk."""
        source = MemorySource(b'hello world')
        chunks = list(source.iter_chunks())
        assert len(chunks) == 1
        assert bytes(chunks[0]) == b'hello world'

    def test_does_not_copy(self):
        """The chunk is a read only view of the content that was handed over."""
        data = b'hello world'
        source = MemorySource(data)
        chunk = next(iter(source.iter_chunks()))
        assert chunk.readonly is True
        assert chunk.obj is data

    def test_empty(self):
        """An empty content is still one chunk, the writer needs no special case."""
        assert [bytes(chunk) for chunk in MemorySource(b'').iter_chunks()] == [b'']

    def test_mode(self):
        """Content in memory has no file, so it has no mode."""
        assert MemorySource(b'x').mode is None

    def test_repr(self):
        assert repr(MemorySource(b'abc')) == 'MemorySource(3 bytes)'


class TestLocalFileSource:
    def test_content(self, fs):
        """A file is read in chunks of the requested size."""
        fs.create_file('/data.bin', contents=b'0123456789')
        source = LocalFileSource('/data.bin')
        assert list(source.iter_chunks(chunk_size=4)) == [b'0123', b'4567', b'89']

    def test_chunks_are_lazy(self, fs):
        """The file is read while the chunks are consumed, not when asked."""
        fs.create_file('/data.bin', contents=b'a' * 100)
        source = LocalFileSource('/data.bin')
        chunks = source.iter_chunks(chunk_size=10)
        assert next(chunks) == b'a' * 10
        chunks.close()

    def test_empty(self, fs):
        """An empty file yields no chunk at all."""
        fs.create_file('/data.bin', contents=b'')
        assert list(LocalFileSource('/data.bin').iter_chunks()) == []

    def test_missing_file(self, fs):
        """A source that can not be read keeps the error of the file system."""
        with pytest.raises(FileNotFoundError):
            list(LocalFileSource('/nope.bin').iter_chunks())

    def test_mode_is_recorded(self, fs):
        """The mode of the file is taken while reading it, not by a stat of its own."""
        fs.create_file('/data.bin', contents=b'x')
        source = LocalFileSource('/data.bin')
        assert source.mode is None
        list(source.iter_chunks())
        assert source.mode is not None
        assert source.mode & 0o100 == os.stat('/data.bin').st_mode & 0o100

    def test_iterated_twice(self, fs):
        """Pass 1 and pass 2 both read the whole content."""
        fs.create_file('/data.bin', contents=b'0123456789')
        source = LocalFileSource('/data.bin')
        assert b''.join(source.iter_chunks(chunk_size=3)) == b'0123456789'
        assert b''.join(source.iter_chunks(chunk_size=3)) == b'0123456789'

    def test_repr(self):
        assert repr(LocalFileSource('/data.bin')) == "LocalFileSource('/data.bin')"


class TestRangeSource:
    def test_content(self):
        """The byte range of the archive is read through the handle."""
        handle = FakeHandle(b'__hello world__')
        chunks = list(RangeSource(2, 11).iter_chunks(handle, chunk_size=4))
        assert chunks == [b'hell', b'o wo', b'rld']
        assert handle.seeks == [2]
        assert handle.reads == [4, 4, 3]

    def test_one_read_without_chunk_size(self):
        """A range smaller than the chunk size is read in one piece."""
        handle = FakeHandle(b'__hello__')
        assert list(RangeSource(2, 5).iter_chunks(handle)) == [b'hello']
        assert handle.reads == [len(b'hello')]

    def test_empty_range(self):
        """An empty entry holds no byte, it does not touch the archive."""
        handle = FakeHandle(b'hello')
        assert list(RangeSource(2, 0).iter_chunks(handle)) == []
        assert handle.seeks == []
        assert handle.reads == []

    def test_without_handle(self):
        """The source never opens the archive itself."""
        with pytest.raises(AsarError) as e:
            list(RangeSource(0, 5).iter_chunks())
        assert str(e.value) == (
            'The content of this entry is stored in an archive, '
            'an open archive handle is needed to read it'
        )

    def test_truncated(self):
        """A range that is longer than the file is a format error."""
        handle = FakeHandle(b'hell')
        chunks = RangeSource(0, 5).iter_chunks(handle, chunk_size=2)
        assert next(chunks) == b'he'
        assert next(chunks) == b'll'
        with pytest.raises(AsarError) as e:
            next(chunks)
        assert str(e.value) == 'Archive is truncated, 1 bytes of this entry are missing'

    def test_mode(self):
        """The content of an archive is read from the archive, not from a file of its own."""
        assert RangeSource(0, 1).mode is None

    def test_repr(self):
        assert repr(RangeSource(10, 5)) == 'RangeSource(10, 5)'
