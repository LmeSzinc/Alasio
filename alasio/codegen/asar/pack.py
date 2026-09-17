"""
Write side of the asar format.

Packing is a two pass streaming process, because the header has to be written
before the contents but describes them:

1. pass 1 streams every source to get its **real byte count** and content hash,
   the content is not kept in memory (except small files, see ``CACHE_FILE_SIZE``)
2. pass 2 streams the contents into the archive and recomputes size and hash,
   a source that changed between the two passes fails the pack instead of
   silently producing an archive that does not match its own header

Memory usage is ``O(chunk size) + O(entry count)``, independent of the archive
size, and no temporary file is used to spool the content.
"""
import hashlib
import os

import msgspec

from alasio.ext.path.atomic import CHUNK_SIZE, atomic_open, file_read_bytes_stream, file_write, replace_tmp, to_tmp_file

from .errors import AsarError, AsarUnsupportedError
from .format import BLOCK_SIZE, UINT32_MAX, calc_header_size, pack_header_pickle, pack_size_pickle
from .model import (
    KIND_FILE, build_header, decode_header, encode_header, new_integrity, propagate_unpacked, validate_header
)

# Content of files not larger than this is kept in memory after pass 1, so that
# an archive of many small files (the common case) is read only once
CACHE_FILE_SIZE = 2 * 1024 * 1024
# Total memory budget of that cache, small files beyond it are read twice
CACHE_BUDGET = 64 * 1024 * 1024


class FileRange:
    """
    A byte range of a local file.

    It is the content of an entry that lives in an archive which is not loaded
    in memory anymore, typically the archive a previous ``write_asar()`` wrote.
    """

    __slots__ = ('path', 'offset', 'size')

    def __init__(self, path, offset, size):
        """
        Args:
            path (str): Path of the file holding the range
            offset (int): Offset of the range in the file
            size (int): Byte length of the range
        """
        self.path = path
        self.offset = offset
        self.size = size

    def __repr__(self):
        return f'FileRange({self.path!r}, {self.offset}, {self.size})'


class PackResult(msgspec.Struct):
    """
    Result of ``AsarArchive.write_asar()``.

    Attributes:
        dest (str): Path of the written archive
        file_count (int): Number of file entries stored in the archive body
        unpacked_count (int): Number of file entries stored next to the archive
        archive_size (int): Total byte length of the archive
        header_size (int): Header pickle length, the data area starts at 8 + it
        data_size (int): Byte length of the data area
        sha256 (str): SHA256 of the whole archive, hex digest
    """
    dest: str
    file_count: int
    unpacked_count: int
    archive_size: int
    header_size: int
    data_size: int
    sha256: str


class AtomicChunkWriter:
    """
    Write one file in chunks, atomically.

    Content goes to ``<file>.<rid>.tmp`` and is moved over the target with
    ``os.replace()`` only once the file is complete, so an interrupted or failed
    extraction never leaves a half written file behind. The Windows retry of the
    project's atomic helpers is reused, an archive may be read by another process.
    """

    def __init__(self, file):
        """
        Args:
            file (str): Target file path
        """
        self.file = file
        self.tmp = to_tmp_file(file)
        self.size = 0
        self._f = None

    def write(self, data):
        """
        Append a chunk to the target file.

        Args:
            data (bytes | memoryview): Content chunk
        """
        if self._f is None:
            self._open()
        self._f.write(data)
        self.size += len(data)

    def _open(self):
        """
        Open the temporary file, creating the parent directory if needed.
        """
        try:
            self._f = open(self.tmp, 'wb')
        except FileNotFoundError:
            directory = os.path.dirname(self.tmp)
            if directory:
                os.makedirs(directory, exist_ok=True)
            self._f = open(self.tmp, 'wb')

    def close(self):
        """
        Finish the file and move it over the target path.
        """
        if self._f is None:
            # Nothing was written, an empty file is still a file
            file_write(self.tmp, b'')
        else:
            self._f.flush()
            os.fsync(self._f.fileno())
            self._f.close()
            self._f = None
        replace_tmp(self.tmp, self.file)

    def abort(self):
        """
        Drop the temporary file, the target path is left untouched.
        """
        if self._f is not None:
            try:
                self._f.close()
            except OSError:
                pass
            self._f = None
        try:
            os.unlink(self.tmp)
        except OSError:
            pass


class ContentVerifier:
    """
    Check that what pass 2 writes is what pass 1 read.

    The header must describe the archive exactly, so a source that changed, was
    truncated or was replaced between the two passes has to fail the pack.
    """

    def __init__(self, path, expected_size, expected_hash=None, error=AsarError):
        """
        Args:
            path (str): Archive path of the entry, only used in error messages
            expected_size (int): Byte count read by pass 1
            expected_hash (str): SHA256 read by pass 1, None to check the size only
            error (type): Exception to raise on a mismatch, the extraction of an
                archive reports a format error while packing reports its source
        """
        self.path = path
        self.expected_size = expected_size
        self.expected_hash = expected_hash
        self.error = error
        self.size = 0
        self._hasher = hashlib.sha256() if expected_hash is not None else None

    def update(self, data):
        """
        Count a written chunk.

        Args:
            data (bytes | memoryview): Written chunk
        """
        self.size += len(data)
        if self._hasher is not None:
            self._hasher.update(data)

    def check(self):
        """
        Compare with pass 1.

        Raises:
            AsarError: If the content is not the one that was expected
        """
        if self.size != self.expected_size:
            raise self.error(
                f'Content of "{self.path}" does not match, '
                f'{self.expected_size} bytes were expected but {self.size} bytes were written'
            )
        if self._hasher is None:
            return
        digest = self._hasher.hexdigest()
        if digest != self.expected_hash:
            raise self.error(
                f'Content hash of "{self.path}" does not match, '
                f'it is {digest} instead of {self.expected_hash}'
            )


def hash_content(chunks, integrity=True):
    """
    Stream content chunks to get the byte count and the integrity.

    Args:
        chunks (Iterable): Content chunks, bytes or memoryview
        integrity (bool): Calculate the SHA256 hashes

    Returns:
        (int, dict, bytearray): ``(size, integrity or None, cached content)``, the
            cache is None once the content grows over ``CACHE_FILE_SIZE``

    Raises:
        AsarUnsupportedError: If the content is larger than the format allows
    """
    size = 0
    hasher = hashlib.sha256() if integrity else None
    block_hasher = hashlib.sha256() if integrity else None
    block_size = 0
    block_hashes = []
    cache = bytearray()
    for chunk in chunks:
        size += len(chunk)
        if size > UINT32_MAX:
            raise AsarUnsupportedError(
                f'Content is larger than the {UINT32_MAX} bytes an asar entry can store'
            )
        if integrity:
            hasher.update(chunk)
            offset = 0
            while offset < len(chunk):
                take = min(BLOCK_SIZE - block_size, len(chunk) - offset)
                block_hasher.update(memoryview(chunk)[offset:offset + take])
                block_size += take
                offset += take
                if block_size == BLOCK_SIZE:
                    block_hashes.append(block_hasher.hexdigest())
                    block_hasher = hashlib.sha256()
                    block_size = 0
        if cache is not None:
            cache += chunk
            if len(cache) > CACHE_FILE_SIZE:
                cache = None
    if not integrity:
        return size, None, cache
    # The reference implementation (4.3.0) pushes a block hash when the last
    # block is partial, and always when there is no block at all, so a file of
    # exactly one block size has one block and an empty file has one empty block
    if block_size > 0 or not block_hashes:
        block_hashes.append(block_hasher.hexdigest())
    return size, new_integrity(hasher.hexdigest(), block_hashes), cache


def iter_file_chunks(file, chunk_size=CHUNK_SIZE):
    """
    Stream a local file.

    Args:
        file (str): Source file path
        chunk_size (int): Read chunk size

    Yields:
        bytes: Content chunks
    """
    for chunk in file_read_bytes_stream(file, chunk_size=chunk_size):
        yield chunk


def iter_file_range(file, offset, size, chunk_size=CHUNK_SIZE):
    """
    Stream a byte range of a local file.

    Args:
        file (str): Source file path
        offset (int): Offset of the range
        size (int): Byte length of the range
        chunk_size (int): Read chunk size

    Yields:
        bytes: Content chunks

    Raises:
        AsarError: If the file ends before the end of the range
    """
    if size <= 0:
        return
    with atomic_open(file, 'rb', buffering=0) as f:
        f.seek(offset)
        remaining = size
        while remaining > 0:
            chunk = f.read(min(chunk_size, remaining))
            if not chunk:
                raise AsarError(
                    f'Content of "{file}" is truncated, {remaining} bytes are missing'
                )
            remaining -= len(chunk)
            yield chunk


def iter_entry_chunks(path, info, source, data_area, unpacked_root=None, chunk_size=CHUNK_SIZE):
    """
    Stream the content of one entry.

    Args:
        path (str): Archive path of the entry
        info (AsarFileInfo): Entry, gives the size and offset of archive content
        source (str | bytes | FileRange): Local file path, memory content or byte
            range, None when the content comes from an archive that was read before
        data_area (memoryview): Data area of the archive the entries were read
            from, it starts at the first content byte
        unpacked_root (str): Directory of the unpacked content of the archive
        chunk_size (int): Read chunk size of a local file

    Yields:
        bytes | memoryview: Content chunks

    Raises:
        AsarError: If an entry has neither a source nor archive content
    """
    if isinstance(source, FileRange):
        for chunk in iter_file_range(source.path, source.offset, source.size, chunk_size=chunk_size):
            yield chunk
        return
    if source is not None:
        if isinstance(source, str):
            for chunk in iter_file_chunks(source, chunk_size=chunk_size):
                yield chunk
        else:
            yield memoryview(source)
        return
    if info.unpacked:
        if unpacked_root is None:
            raise AsarError(f'Entry "{path}" is unpacked but there is no unpacked directory')
        file = os.path.join(unpacked_root, *path.split('/'))
        for chunk in iter_file_chunks(file, chunk_size=chunk_size):
            yield chunk
        return
    if data_area is None:
        raise AsarError(f'Entry "{path}" has no source and the archive data was not loaded')
    start = info.offset
    yield data_area[start:start + info.size]


def check_written_header(json_bytes):
    """
    Check the encoded header against the reference ``validateHeader`` rules.

    The check runs on the decoded bytes, so it sees exactly what a reader sees:
    an archive that we would refuse to read is a bug, not a valid pack.

    Args:
        json_bytes (bytes): Encoded header JSON

    Raises:
        AsarFormatError: If the encoded header is not a valid asar header
    """
    validate_header(decode_header(json_bytes))


def pack_archive(files, sources, dest, data_area=None, integrity=True, unpacked_root=None):
    """
    Write the flat entry table to an archive file.

    Sizes and integrity of the entries are recalculated from the content, and
    the offsets are allocated in entry order, so ``files`` is updated in place
    and becomes an exact description of the written archive.

    Args:
        files (dict): ``{path: AsarFileInfo}``, see ``AsarArchive.files``
        sources (dict): ``{path: local file path or content}``, an entry without a
            source is read from ``data_area``
        dest (str): Target archive path
        data_area (memoryview): Data area of the archive the entries were read
            from, it starts at the first content byte
        integrity (bool): Write the per file SHA256 integrity
        unpacked_root (str): Directory holding the unpacked content

    Returns:
        PackResult: Statistics of the written archive

    Raises:
        AsarError: If an entry has no content source, or if a source changes
            between the two passes
        AsarUnsupportedError: If an entry is larger than the format allows
    """
    propagate_unpacked(files)

    # Pass 1: real size and content hash of every entry
    cache = {}
    cache_budget = CACHE_BUDGET
    for path, info in files.items():
        if info.kind != KIND_FILE:
            continue
        chunks = iter_entry_chunks(path, info, sources.get(path), data_area, unpacked_root)
        size, content_integrity, buffer = hash_content(chunks, integrity=integrity)
        info.size = size
        info.integrity = content_integrity
        if buffer is not None and len(buffer) <= cache_budget:
            cache[path] = buffer
            cache_budget -= len(buffer)

    # Offsets are relative to the data area and follow the entry order
    offset = 0
    file_count = 0
    unpacked_count = 0
    for path, info in files.items():
        if info.kind != KIND_FILE:
            continue
        if info.unpacked:
            info.offset = None
            unpacked_count += 1
            continue
        info.offset = offset
        offset += info.size
        file_count += 1
    data_size = offset

    # Pass 2: the header first, it is written before the contents
    json_bytes = encode_header(build_header(files))
    check_written_header(json_bytes)
    header_size = calc_header_size(len(json_bytes))

    # The unpacked content is written before the archive, so that a new header
    # never points at an unpacked file that does not exist yet
    if unpacked_count:
        unpacked_dir = f'{dest}.unpacked'
        for path, info in files.items():
            if info.kind != KIND_FILE or not info.unpacked:
                continue
            write_entry(
                os.path.join(unpacked_dir, *path.split('/')), path, info,
                sources.get(path), data_area, unpacked_root,
            )

    writer = AtomicChunkWriter(dest)
    hasher = hashlib.sha256()
    try:
        for chunk in (pack_size_pickle(header_size), pack_header_pickle(json_bytes)):
            hasher.update(chunk)
            writer.write(chunk)
        for path, info in files.items():
            if info.kind != KIND_FILE or info.unpacked:
                continue
            verifier = ContentVerifier(
                path, info.size, info.integrity['hash'] if info.integrity else None,
            )
            for chunk in iter_content_with_cache(path, info, sources, data_area, cache, unpacked_root):
                verifier.update(chunk)
                hasher.update(chunk)
                writer.write(chunk)
            verifier.check()
        writer.close()
    except BaseException:
        writer.abort()
        raise
    return PackResult(
        dest=dest,
        file_count=file_count,
        unpacked_count=unpacked_count,
        archive_size=writer.size,
        header_size=header_size,
        data_size=data_size,
        sha256=hasher.hexdigest(),
    )


def iter_content_with_cache(path, info, sources, data_area, cache, unpacked_root=None):
    """
    Stream the content of one entry, reusing the pass 1 cache when possible.

    Args:
        path (str): Archive path of the entry
        info (AsarFileInfo): Entry
        sources (dict): ``{path: source}``
        data_area (memoryview): Data area of the archive the entries were read from
        cache (dict): ``{path: content}`` of the entries read in pass 1
        unpacked_root (str): Directory holding the unpacked content

    Yields:
        bytes | memoryview: Content chunks
    """
    buffer = cache.get(path)
    if buffer is not None:
        del cache[path]
        yield buffer
        return
    for chunk in iter_entry_chunks(path, info, sources.get(path), data_area, unpacked_root):
        yield chunk


def write_entry(dest, path, info, source, data_area=None, unpacked_root=None, verify=True,
                error=AsarError, chunk_size=CHUNK_SIZE):
    """
    Write one entry to its own file, atomically and verified.

    Args:
        dest (str): Target file path
        path (str): Archive path of the entry, only used in error messages
        info (AsarFileInfo): Entry
        source (str | bytes | memoryview): Content source
        data_area (memoryview): Data area of the archive the entries were read from
        unpacked_root (str): Directory holding the unpacked content
        verify (bool): Check the content against the size and hash of the entry
        error (type): Exception to raise on a mismatch
        chunk_size (int): Read chunk size when the source is a file

    Raises:
        AsarError: If the content does not match the entry
    """
    verifier = None
    if verify:
        verifier = ContentVerifier(
            path, info.size, info.integrity['hash'] if info.integrity else None, error=error,
        )
    writer = AtomicChunkWriter(dest)
    try:
        for chunk in iter_entry_chunks(path, info, source, data_area, unpacked_root, chunk_size=chunk_size):
            if verifier is not None:
                verifier.update(chunk)
            writer.write(chunk)
        if verifier is not None:
            verifier.check()
        writer.close()
    except BaseException:
        writer.abort()
        raise


def hash_file(file, chunk_size=CHUNK_SIZE):
    """
    Calculate the SHA256 of a file, streaming it.

    Args:
        file (str): Source file path
        chunk_size (int): Read chunk size

    Returns:
        str: SHA256 hex digest
    """
    hasher = hashlib.sha256()
    for chunk in iter_file_chunks(file, chunk_size=chunk_size):
        hasher.update(chunk)
    return hasher.hexdigest()
