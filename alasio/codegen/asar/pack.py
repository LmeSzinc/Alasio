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
import itertools
import os

import msgspec

from alasio.ext.concurrent.threadpool import THREAD_POOL
from alasio.ext.path.atomic import CHUNK_SIZE, file_read_bytes_stream, file_write, replace_tmp, to_tmp_file

from .errors import AsarError, AsarUnsupportedError
from .format import BLOCK_SIZE, UINT32_MAX, pack_header
from .model import KIND_DIR, KIND_FILE, Integrity, build_header, canonical_entries, keys_path

# Content of files not larger than this is kept in memory after pass 1, so that
# an archive of many small files (the common case) is read only once
CACHE_FILE_SIZE = 2 * 1024 * 1024
# Total memory budget of that cache, small files beyond it are read twice
CACHE_BUDGET = 64 * 1024 * 1024
# Content of an entry larger than this is written in the calling thread instead
# of being handed to the thread pool: the pool is for the many small files whose
# flush latency dominates an extraction, one flush of a large file is cheap
POOL_FILE_SIZE = 2 * 1024 * 1024


class AtomicChunkWriter:
    """
    Write one file in chunks, atomically.

    Content goes to ``<file>.<rid>.tmp`` and is moved over the target with
    ``os.replace()`` only once the file is complete, so an interrupted or failed
    extraction never leaves a half written file behind. The Windows retry of the
    project's atomic helpers is reused, an archive may be read by another process.
    """

    def __init__(self, file, mode=None):
        """
        Args:
            file (str): Target file path
            mode (int): POSIX mode of the target file, set on the temporary file
                before it replaces the target, so the mode of the target is
                never observable in between
        """
        self.file = file
        self.mode = mode
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
        if self.mode is not None:
            os.chmod(self.tmp, self.mode)
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
        tuple[int, Integrity, bytearray]: The size, the integrity of the content
            (None when it was not calculated) and the cached content, the cache
            is None once the content grows over ``CACHE_FILE_SIZE``

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
    return size, Integrity.new(hasher.hexdigest(), block_hashes), cache


def iter_entry_content(keys, info, fd=None, chunk_size=CHUNK_SIZE):
    """
    Stream the content of one entry from its source.

    Args:
        keys (tuple): Path segments of the entry, only used to build error messages
        info (AsarFileInfo): Entry to read
        fd (io.IOBase): Open handle of the archive, for the entries that read
            their content from it
        chunk_size (int): Read chunk size

    Yields:
        bytes | memoryview: Content chunks

    Raises:
        AsarError: If the entry has no content source
    """
    source = info.source
    if source is None:
        raise AsarError(
            f'Entry "{keys_path(keys)}" has no content source, it can not be written to an archive'
        )
    yield from source.iter_chunks(fd=fd, chunk_size=chunk_size)


def iter_write_content(keys, info, fd, cache, chunk_size=CHUNK_SIZE):
    """
    Stream the content of one entry for pass 2, reusing the pass 1 cache.

    Args:
        keys (tuple): Path segments of the entry, only used to build error messages
        info (AsarFileInfo): Entry to read
        fd (io.IOBase): Open handle of the archive
        cache (dict): ``{id(entry): content}`` of the entries read in pass 1
        chunk_size (int): Read chunk size

    Yields:
        bytes | memoryview: Content chunks
    """
    buffer = cache.pop(id(info), None)
    if buffer is not None:
        yield buffer
        return
    yield from iter_entry_content(keys, info, fd, chunk_size=chunk_size)


def write_content(dest, chunks, verifier=None, mode=None):
    """
    Write the content of one entry to its own file, atomically and checked.

    Args:
        dest (str): Target file path, the parent directory is created
        chunks (Iterable): Content chunks
        verifier (ContentVerifier): Checker of the content, None to write as is
        mode (int): POSIX mode of the target file, None to keep the default

    Raises:
        AsarError: If the content does not match the entry it belongs to
    """
    writer = AtomicChunkWriter(dest, mode=mode)
    try:
        for chunk in chunks:
            if verifier is not None:
                verifier.update(chunk)
            writer.write(chunk)
        if verifier is not None:
            verifier.check()
        writer.close()
    except BaseException:
        writer.abort()
        raise


class ContentWriter:
    """
    Write the content of an extraction, one thread pool task per file

    The content of every entry is read in the calling thread -- the handle of
    the archive is shared, it is never read by two threads at once -- and the
    write of the file (temporary file, flush to the disk, move over the target
    path) is one task on the thread pool: the flush of a file overlaps with the
    reads of the entries that follow it and with the flushes of the files around
    it, instead of every flush waiting for the device on its own. wait() joins
    the tasks and raises the first error: when it returns, every file is
    complete, durable and at its target path.

    An entry larger than POOL_FILE_SIZE is written in the calling thread, with
    its chunks streaming: it costs a single flush (the pool is for the many
    small files whose flush latency dominates an extraction) and a large entry
    is never held in memory.

    The pool blocks when every worker of it is busy, so a writer must not be
    used from a task of that same pool: the task would wait for a free worker
    that can not come free while it waits.

    Args:
        pool (ThreadPool): Pool the files are written on, defaults to THREAD_POOL
    """

    def __init__(self, pool=None):
        self.pool = THREAD_POOL if pool is None else pool
        self._jobs = []

    def write(self, dest, chunks, verifier=None, mode=None):
        """
        Read the content of one entry and write its file on the pool

        Args:
            dest (str): Target file path, the parent directory is created
            chunks (Iterable): Content chunks, all read by this call
            verifier (ContentVerifier): Checker of the content, None to write as is
            mode (int): POSIX mode of the target file, None to keep the default
        """
        buffer = bytearray()
        for chunk in chunks:
            if verifier is not None:
                verifier.update(chunk)
            buffer += chunk
            if len(buffer) > POOL_FILE_SIZE:
                # Too large to hand over: write it here, streaming the chunks
                # that are left. The content is complete and checked already
                if verifier is not None:
                    verifier.check()
                write_content(dest, itertools.chain((buffer,), chunks), None, mode)
                return
        if verifier is not None:
            verifier.check()
        # The buffer is handed over as it is: the task only writes it and the
        # caller never touches it again
        self._jobs.append(self.pool.start_thread_soon(write_content, dest, (buffer,), mode=mode))

    def wait(self):
        """
        Wait for the write tasks of this writer

        Raises:
            Exception: The first error of a task, every task is waited for
                before it is raised (no write is left running)
        """
        jobs, self._jobs = self._jobs, []
        error = None
        for job in jobs:
            try:
                job.get()
            except Exception as e:
                if error is None:
                    error = e
        if error is not None:
            raise error

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.wait()
        return False


def mark_unpacked(files):
    """
    Mark every entry that lives inside an unpacked directory as unpacked.

    An entry that is added below a directory that is unpacked belongs to the
    '.unpacked' content of the archive whatever the call that added it said: a
    caller may add a single file of its own into such a directory (a patch, a
    build output) without thinking about the flag. Unpacked files are copied
    next to the archive instead of being stored in it, so they have no offset.

    The entries are walked from the shortest path to the longest one: the path
    of a directory is a proper prefix of the path of every entry it holds, so a
    directory is always settled before the entries below it and an entry only
    has to look at the directory right above it. One pass over the table, and
    the order the entries are stored in does not matter.

    Args:
        files (dict): Flat entry table, modified in place
    """
    for keys in sorted(files, key=len):
        info = files[keys]
        if info.unpacked:
            continue
        # A directory that is inside an unpacked subtree carries the flag as
        # well, so a directory is enough to tell whether the entries below it
        # are unpacked: only a directory carries the flag down
        parent = files.get(keys[:-1])
        if parent is None or parent.kind != KIND_DIR or not parent.unpacked:
            continue
        info.unpacked = True
        if info.kind == KIND_FILE:
            info.offset = None


def pack_archive(files, dest, integrity=True, fd=None, release=None):
    """
    Write the flat entry table to an archive file.

    Sizes and integrity are recalculated from the content of the entries and the
    offsets are allocated in the canonical order, so the table describes the
    written archive exactly when this returns.

    Args:
        files (dict): Flat entry table, see ``AsarArchive.files``
        dest (str): Target archive path
        integrity (bool): Write the per file SHA256 integrity of the reference
            implementation, disable it to save the hashing time
        fd (io.IOBase): Open handle of the archive the entries were read from,
            the sources that live in an archive read through it
        release (Callable): Called after the last byte was read and before the
            temporary file replaces `dest`. The caller closes the handle it holds
            on `dest` there: Windows refuses to replace an open file, and the
            handle would point at the replaced file afterwards. It is never
            called when the pack fails, so a failed pack leaves the archive it
            reads from untouched

    Raises:
        AsarError: If an entry has no content source, or if a source changes
            between the two passes
        AsarUnsupportedError: If an entry is larger than the format allows
    """
    mark_unpacked(files)
    entries = canonical_entries(files)

    # Pass 1: real size and content hash of every entry
    cache = {}
    cache_budget = CACHE_BUDGET
    for keys, info in entries:
        if info.kind != KIND_FILE:
            continue
        source = info.source
        size, content_integrity, buffer = hash_content(
            iter_entry_content(keys, info, fd), integrity=integrity,
        )
        info.size = size
        info.integrity = content_integrity
        if source.mode is not None and os.name != 'nt':
            # Only POSIX has an executable bit, and only a file of its own
            # knows it, an entry read from an archive keeps what the header says
            info.executable = bool(source.mode & 0o100)
        if buffer is not None and len(buffer) <= cache_budget:
            cache[id(info)] = buffer
            cache_budget -= len(buffer)

    # Offsets are relative to the data area and follow the canonical order
    offset = 0
    unpacked_count = 0
    for keys, info in entries:
        if info.kind != KIND_FILE:
            continue
        if info.unpacked:
            info.offset = None
            unpacked_count += 1
            continue
        info.offset = offset
        offset += info.size

    # The header is written before the contents, but it describes them, so it is
    # encoded first (msgspec.json emits the same bytes as JSON.stringify)
    json_bytes = msgspec.json.encode(build_header(entries))

    # The unpacked content is written before the archive, so that a new header
    # never points at an unpacked file that does not exist yet. The writer waits
    # for its tasks here: the files are on the disk before the archive goes
    if unpacked_count:
        unpacked_dir = f'{dest}.unpacked'
        with ContentWriter() as writer:
            for keys, info in entries:
                if info.kind != KIND_FILE or not info.unpacked:
                    continue
                verifier = ContentVerifier(
                    keys_path(keys), info.size, info.integrity.hash if info.integrity else None,
                )
                writer.write(
                    os.path.join(unpacked_dir, *keys),
                    iter_entry_content(keys, info, fd),
                    verifier=verifier,
                    mode=0o755 if info.executable and os.name != 'nt' else None,
                )

    # Pass 2: the content, recomputed and checked against pass 1
    writer = AtomicChunkWriter(dest)
    try:
        writer.write(pack_header(json_bytes))
        for keys, info in entries:
            if info.kind != KIND_FILE or info.unpacked:
                continue
            verifier = ContentVerifier(
                keys_path(keys), info.size, info.integrity.hash if info.integrity else None,
            )
            for chunk in iter_write_content(keys, info, fd, cache):
                verifier.update(chunk)
                writer.write(chunk)
            verifier.check()
        if release is not None:
            release()
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
    for chunk in file_read_bytes_stream(file, chunk_size=chunk_size):
        hasher.update(chunk)
    return hasher.hexdigest()
