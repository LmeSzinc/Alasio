"""
Where the content of the entries of an archive comes from.

An entry does not hold its content, it holds the way to read it: a byte range of
an archive (``RangeSource``), a file on disk (``LocalFileSource``) or content
that was handed over in memory (``MemorySource``). A source is a plain value (a
msgspec struct): two sources that point at the same bytes are equal, and a
source holds no state of its own besides the mode a file backed one reads. Both
the write side and the extraction side only know this interface, so "where do
the bytes come from" has a single implementation, and a source that is added to
the module works for every caller with no other change.
"""
import os
from typing import Any, Optional

import msgspec

from alasio.ext.path.atomic import CHUNK_SIZE

from .errors import AsarError


class ContentSource(msgspec.Struct):
    """
    The content of one entry of an archive, as a stream of chunks.

    A source of the base kind holds nothing of its own: the classes below say
    where their content comes from, and this is the interface they implement.

    Attributes:
        mode (int): ``st_mode`` of the file the content is read from, None when
            the content does not come from a file of its own. Only a source that
            reads a file fills it, a reader of the source uses it for the
            executable flag, which saves a stat call for every entry.
    """

    # Not a field: a source that reads no file of its own never has a mode, so
    # the classes below answer with this constant while the one that reads a
    # file declares the field itself
    mode = None

    def __repr__(self):
        return f'{type(self).__name__}()'

    def iter_chunks(self, fd=None, chunk_size=CHUNK_SIZE):
        """
        Stream the content.

        Args:
            fd (io.IOBase): Open handle of the archive, only used by the sources
                that read their content from the archive
            chunk_size (int): Read chunk size of a file backed source

        Yields:
            bytes | memoryview: Content chunks

        Raises:
            AsarError: If the content can not be read
        """
        raise NotImplementedError


class MemorySource(ContentSource):
    """
    Content that only lives in memory, added by ``AsarArchive.add_file()``.

    Attributes:
        data (bytes | bytearray | memoryview): Content
    """

    data: Any

    def __repr__(self):
        return f'MemorySource({len(self.data)} bytes)'

    def iter_chunks(self, fd=None, chunk_size=CHUNK_SIZE):
        """
        Yield the content, in one chunk.

        Args:
            fd (io.IOBase): Unused, the content is already in memory
            chunk_size (int): Unused, the content is not read from a file

        Yields:
            memoryview: Whole content, a read only view
        """
        yield memoryview(self.data)


class LocalFileSource(ContentSource):
    """
    Content of a file on disk, read when the archive is written or extracted.

    Attributes:
        file (str): Path of the file
        mode (int): ``st_mode`` of the file, filled while it is read
    """

    file: str
    # The source that reads a file is the one that carries its mode, the field
    # with a default comes after the required one, as msgspec wants it
    mode: Optional[int] = None

    def __repr__(self):
        return f'LocalFileSource({self.file!r})'

    def iter_chunks(self, fd=None, chunk_size=CHUNK_SIZE):
        """
        Read the file, in chunks of `chunk_size` bytes.

        The mode of the file is recorded while reading it, so that the entry
        knows whether it is executable without a stat call of its own.

        Args:
            fd (io.IOBase): Unused, the content is read from its own file
            chunk_size (int): Read chunk size

        Yields:
            bytes: Content chunks

        Raises:
            OSError: If the file can not be read, the caller decides how a
                missing source is reported
        """
        with open(self.file, mode='rb') as f:
            if self.mode is None:
                self.mode = os.fstat(f.fileno()).st_mode
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    return
                yield chunk


class RangeSource(ContentSource):
    """
    Content that is stored in the data area of the archive.

    The offset is absolute, it points into the archive file itself, and the
    source reads through a handle the caller owns: one open archive serves a
    whole extraction, and a sequential scan keeps reading without a seek.

    Attributes:
        offset (int): Offset of the content in the archive file
        size (int): Byte length of the content
    """

    offset: int
    size: int

    def __repr__(self):
        return f'RangeSource({self.offset}, {self.size})'

    def iter_chunks(self, fd=None, chunk_size=CHUNK_SIZE):
        """
        Read the byte range of the archive.

        Args:
            fd (io.IOBase): Open handle of the archive, the source never opens
                the archive itself
            chunk_size (int): Read chunk size of a large content

        Yields:
            bytes: Content chunks

        Raises:
            AsarError: If there is no open archive, or if the archive is
                truncated
        """
        if self.size <= 0:
            # An empty entry holds no byte, it does not touch the archive
            return
        if fd is None:
            raise AsarError(
                'The content of this entry is stored in an archive, '
                'an open archive handle is needed to read it'
            )
        fd.seek(self.offset)
        remaining = self.size
        while remaining > 0:
            chunk = fd.read(min(chunk_size, remaining))
            if not chunk:
                raise AsarError(
                    f'Archive is truncated, {remaining} bytes of this entry are missing'
                )
            remaining -= len(chunk)
            yield chunk
