"""
Tests for file_write_stream_async() and atomic_write_stream_async() of
alasio.ext.path.atomic_trio.

Covered: text and binary streams, the empty stream, the chunk types, the
parent folder creation, the batching, the atomic replace (target untouched
and no tmp file left on failure or cancel, the mode set on the tmp file), and
the thread boundary: every file operation runs in a worker thread of trio
while the chunks are still pulled in the task that awaits, so the event loop
never waits for the disk.

The tests run on the in-memory fake filesystem (the fs fixture), the real
disk is never touched.
"""
import os
import stat
import threading

import pytest
import trio

from alasio.ext.path import atomic, atomic_trio
from alasio.ext.path.atomic import file_read_bytes, file_read_text, windows_attempt_delay
from alasio.ext.path.atomic_trio import atomic_write_stream_async, file_write_stream_async
from alasio.testing.filesystem import fs  # noqa: F401
from tests.ext.path.atomic.helpers import break_function, record_sleeps


class SpyFile:
    """
    Proxy of a file object, records the thread of every write and flush.

    Args:
        file (file object): Wrapped file
        threads (list): Records the thread id of every write and flush
    """
    def __init__(self, file, threads):
        self._file = file
        self._threads = threads

    def writelines(self, lines):
        """
        Args:
            lines (Iterable[str] | Iterable[bytes]): Chunks to write
        """
        for line in lines:
            self._threads.append(threading.get_ident())
            self._file.write(line)

    def flush(self):
        """
        Returns:
            None
        """
        self._threads.append(threading.get_ident())
        return self._file.flush()

    def fileno(self):
        """
        Returns:
            int: File descriptor
        """
        return self._file.fileno()

    def close(self):
        """
        Returns:
            None
        """
        return self._file.close()


def record_open(monkeypatch, opened):
    """
    Patch file_open() of the module to record the opened file objects

    Args:
        monkeypatch (pytest.MonkeyPatch): Monkeypatch fixture of the test
        opened (list): Records the returned file objects
    """
    original = atomic_trio.file_open

    def spying_open(*args, **kwargs):
        file = original(*args, **kwargs)
        opened.append(file)
        return file

    monkeypatch.setattr(atomic_trio, 'file_open', spying_open)


class TestFileWriteStreamAsync:
    """Test cases for file_write_stream_async()."""

    @pytest.mark.trio
    async def test_write_text(self, fs):
        """Text chunks should be written with no newline translation."""
        async def chunks():
            yield 'a\n'
            yield 'b\n'

        await file_write_stream_async('/data/a.txt', chunks())
        assert file_read_bytes('/data/a.txt') == b'a\nb\n'

    @pytest.mark.trio
    async def test_write_bytes(self, fs):
        """Bytes chunks should be written as-is."""
        async def chunks():
            yield b'ab'
            yield b'cd'

        await file_write_stream_async('/data/a.bin', chunks())
        assert file_read_bytes('/data/a.bin') == b'abcd'

    @pytest.mark.trio
    async def test_write_bytes_like(self, fs):
        """The mode is taken from the first chunk, the later chunks follow it."""
        async def chunks():
            yield bytearray(b'ab')
            yield memoryview(b'cd')

        await file_write_stream_async('/data/a.bin', chunks())
        assert file_read_bytes('/data/a.bin') == b'abcd'

    @pytest.mark.trio
    async def test_write_empty(self, fs):
        """An iterator that yields nothing should create an empty file."""
        async def chunks():
            for chunk in []:
                yield chunk

        await file_write_stream_async('/data/a/b.txt', chunks())
        assert file_read_bytes('/data/a/b.txt') == b''
        assert os.listdir('/data/a') == ['b.txt']

    @pytest.mark.trio
    async def test_write_empty_over_existing(self, fs):
        """An empty iterator should empty an existing file."""
        fs.create_file('/data/a.txt', contents='old content')

        async def chunks():
            for chunk in []:
                yield chunk

        await file_write_stream_async('/data/a.txt', chunks())
        assert file_read_bytes('/data/a.txt') == b''

    @pytest.mark.trio
    async def test_write_creates_parents(self, fs, monkeypatch):
        """All the chunks should be written after the parent folders are created."""
        calls = break_function(monkeypatch, atomic, 'open', [FileNotFoundError(2, 'No such file or directory')])
        makedirs = break_function(monkeypatch, os, 'makedirs', [])

        async def chunks():
            yield 'x'
            yield 'y'
            yield 'z'

        await file_write_stream_async('/data/a/b/c.txt', chunks())
        assert len(calls) == 2
        assert makedirs == [(('/data/a/b',), {'exist_ok': True})]
        assert file_read_text('/data/a/b/c.txt') == 'xyz'

    @pytest.mark.trio
    async def test_write_unsupported_type(self, fs):
        """A chunk that is neither str nor bytes fails with TypeError."""
        async def chunks():
            yield 123

        with pytest.raises(TypeError):
            await file_write_stream_async('/data/a.txt', chunks())

    @pytest.mark.trio
    async def test_write_many_chunks(self, fs, monkeypatch):
        """Chunks are written in batches, the order of the stream is kept."""
        monkeypatch.setattr(atomic_trio, 'CHUNK_SIZE', 4)
        batches = []
        original = atomic_trio._write_batch

        def counting_batch(f, chunks):
            batches.append(list(chunks))
            original(f, chunks)

        monkeypatch.setattr(atomic_trio, '_write_batch', counting_batch)

        async def chunks():
            yield b'ab'
            yield b'cd'
            yield b'e'
            yield b'f'
            yield b'ghij'

        await file_write_stream_async('/data/a.bin', chunks())
        assert batches == [[b'ab', b'cd'], [b'e', b'f', b'ghij']]
        assert file_read_bytes('/data/a.bin') == b'abcdefghij'


class TestAtomicWriteStreamAsync:
    """Test cases for atomic_write_stream_async()."""

    @pytest.mark.trio
    async def test_write_stream(self, fs):
        """The chunks should be written and no tmp file left behind."""
        async def chunks():
            yield 'a'
            yield 'b'

        await atomic_write_stream_async('/data/a/b.txt', chunks())
        assert file_read_text('/data/a/b.txt') == 'ab'
        assert os.listdir('/data/a') == ['b.txt']

    @pytest.mark.trio
    async def test_write_stream_bytes(self, fs):
        """Bytes chunks should be written as-is."""
        async def chunks():
            yield b'ab'
            yield b'cd'

        await atomic_write_stream_async('/data/a.bin', chunks())
        assert file_read_bytes('/data/a.bin') == b'abcd'

    @pytest.mark.trio
    async def test_write_stream_empty(self, fs):
        """An empty iterator should write an empty file."""
        async def chunks():
            for chunk in []:
                yield chunk

        await atomic_write_stream_async('/data/a/b.txt', chunks())
        assert file_read_bytes('/data/a/b.txt') == b''
        assert os.listdir('/data/a') == ['b.txt']

    @pytest.mark.trio
    async def test_write_stream_overwrites(self, fs):
        """The target should be replaced by the new content."""
        fs.create_file('/data/a.txt', contents='old')

        async def chunks():
            yield 'new'

        await atomic_write_stream_async('/data/a.txt', chunks())
        assert file_read_text('/data/a.txt') == 'new'
        assert os.listdir('/data') == ['a.txt']

    @pytest.mark.trio
    async def test_write_stream_mode(self, fs, monkeypatch):
        """The mode should be set on the tmp file, before it replaces the target."""
        original = os.replace
        seen = []

        def checking_replace(src, dst):
            seen.append(stat.S_IMODE(os.stat(src).st_mode))
            return original(src, dst)

        monkeypatch.setattr(os, 'replace', checking_replace)

        async def chunks():
            yield 'x'

        await atomic_write_stream_async('/data/a.sh', chunks(), mode=0o755)
        assert seen == [0o755]
        assert stat.S_IMODE(os.stat('/data/a.sh').st_mode) == 0o755

    @pytest.mark.trio
    async def test_write_stream_chmod_error_keeps_target(self, fs, monkeypatch):
        """A failed chmod should remove the tmp file and keep the target."""
        fs.create_file('/data/a.sh', contents='old')
        error = OSError(1, 'Operation not permitted')
        calls = break_function(monkeypatch, os, 'chmod', [error])

        async def chunks():
            yield 'new'

        with pytest.raises(OSError) as e:
            await atomic_write_stream_async('/data/a.sh', chunks(), mode=0o755)
        assert e.value is error
        assert len(calls) == 1
        assert file_read_text('/data/a.sh') == 'old'
        assert os.listdir('/data') == ['a.sh']

    @pytest.mark.trio
    async def test_write_stream_error_keeps_target(self, fs):
        """A stream failing midway should keep the target and leave no tmp file."""
        fs.create_file('/data/a.txt', contents='old content')

        async def chunks():
            yield 'new'
            raise RuntimeError('source failed midway')

        with pytest.raises(RuntimeError, match='source failed midway'):
            await atomic_write_stream_async('/data/a.txt', chunks())
        assert file_read_text('/data/a.txt') == 'old content'
        assert os.listdir('/data') == ['a.txt']

    @pytest.mark.trio
    async def test_write_stream_error_before_chunk(self, fs):
        """A stream failing before yielding should leave no tmp file."""
        async def chunks():
            raise RuntimeError('source failed at once')
            yield 'never'

        with pytest.raises(RuntimeError, match='source failed at once'):
            await atomic_write_stream_async('/data/a.txt', chunks())
        assert not os.path.exists('/data')

    @pytest.mark.trio
    async def test_write_stream_cancel_keeps_target(self, fs):
        """A cancelled stream should keep the target and leave no tmp file."""
        fs.create_file('/data/a.txt', contents='old content')

        async def chunks():
            yield 'new'
            await trio.sleep_forever()
            yield 'never'

        with trio.move_on_after(1):
            await atomic_write_stream_async('/data/a.txt', chunks())
        assert file_read_text('/data/a.txt') == 'old content'
        assert os.listdir('/data') == ['a.txt']

    @pytest.mark.trio
    async def test_write_stream_replace_failure_keeps_target(self, fs, monkeypatch):
        """A failed replace should keep the target and leave no tmp file."""
        monkeypatch.setattr(atomic, 'IS_WINDOWS', False)
        fs.create_file('/data/a.txt', contents='old')
        error = PermissionError(13, 'Permission denied')
        calls = break_function(monkeypatch, os, 'replace', [error])

        async def chunks():
            yield 'new'

        with pytest.raises(PermissionError) as e:
            await atomic_write_stream_async('/data/a.txt', chunks())
        assert e.value is error
        assert len(calls) == 1
        assert file_read_text('/data/a.txt') == 'old'
        assert os.listdir('/data') == ['a.txt']

    @pytest.mark.trio
    async def test_write_stream_retries_on_windows(self, fs, monkeypatch):
        """The Windows retry of the replace should run in the worker thread too."""
        monkeypatch.setattr(atomic, 'IS_WINDOWS', True)
        sleeps = record_sleeps(monkeypatch)
        error = PermissionError(13, 'Permission denied')
        break_function(monkeypatch, os, 'replace', [error])

        async def chunks():
            yield 'data'

        await atomic_write_stream_async('/data/a.txt', chunks())
        assert file_read_text('/data/a.txt') == 'data'
        assert os.listdir('/data') == ['a.txt']
        assert sleeps.sleeps == [windows_attempt_delay(0)]


class TestThreadBoundary:
    """The file IO runs in a worker thread, the chunks come from the task."""

    @pytest.mark.trio
    async def test_file_io_runs_in_worker_thread(self, fs, monkeypatch):
        """open / write / flush should run outside of the event loop thread."""
        loop_thread = threading.get_ident()
        threads = {'open': [], 'write': [], 'chunks': []}
        original = atomic_trio.file_open

        def spying_open(*args, **kwargs):
            threads['open'].append(threading.get_ident())
            return SpyFile(original(*args, **kwargs), threads['write'])

        monkeypatch.setattr(atomic_trio, 'file_open', spying_open)

        async def chunks():
            threads['chunks'].append(threading.get_ident())
            yield 'a'
            threads['chunks'].append(threading.get_ident())
            yield 'b'

        await file_write_stream_async('/data/a.txt', chunks())
        assert file_read_text('/data/a.txt') == 'ab'
        # the file operations are in another thread
        assert threads['open'] != [loop_thread]
        assert len(threads['write']) == 3
        assert loop_thread not in threads['write']
        # the chunks are still produced in the calling task
        assert threads['chunks'] == [loop_thread, loop_thread]

    @pytest.mark.trio
    async def test_event_loop_not_blocked(self, fs, monkeypatch):
        """The loop keeps running while the writer thread is inside fsync."""
        entered = threading.Event()
        release = threading.Event()

        def blocking_fsync(fd):
            entered.set()
            release.wait(5)

        monkeypatch.setattr(os, 'fsync', blocking_fsync)

        async def chunks():
            yield b'data'

        done = []

        async def write():
            await file_write_stream_async('/data/a.bin', chunks())
            done.append(True)

        async with trio.open_nursery() as nursery:
            nursery.start_soon(write)
            # the loop is alive while the writer thread is blocked in fsync
            assert await trio.to_thread.run_sync(entered.wait, 5)
            assert done == []
            release.set()
        assert done == [True]
        assert file_read_bytes('/data/a.bin') == b'data'

    @pytest.mark.trio
    async def test_replace_runs_in_worker_thread(self, fs, monkeypatch):
        """The atomic replace should run outside of the event loop thread."""
        loop_thread = threading.get_ident()
        threads = []
        original = os.replace

        def spying_replace(src, dst):
            threads.append(threading.get_ident())
            return original(src, dst)

        monkeypatch.setattr(os, 'replace', spying_replace)

        async def chunks():
            yield 'data'

        await atomic_write_stream_async('/data/a.txt', chunks())
        assert len(threads) == 1
        assert threads[0] != loop_thread
        assert file_read_text('/data/a.txt') == 'data'

    @pytest.mark.trio
    async def test_cancel_closes_file(self, fs, monkeypatch):
        """A cancelled stream stops and closes the file."""
        opened = []
        record_open(monkeypatch, opened)

        async def chunks():
            yield 'a'
            await trio.sleep_forever()
            yield 'b'

        with trio.move_on_after(1):
            await file_write_stream_async('/data/a.txt', chunks())
        assert len(opened) == 1
        assert opened[0].closed
        # what was produced before the cancel is kept
        assert file_read_text('/data/a.txt') == 'a'

    @pytest.mark.trio
    async def test_iterator_error_closes_file(self, fs, monkeypatch):
        """An error of the iterator should propagate and close the file."""
        opened = []
        record_open(monkeypatch, opened)

        async def chunks():
            yield 'a'
            raise ValueError('broken source')

        with pytest.raises(ValueError) as e:
            await file_write_stream_async('/data/a.txt', chunks())
        assert str(e.value) == 'broken source'
        assert len(opened) == 1
        assert opened[0].closed
        assert file_read_text('/data/a.txt') == 'a'

    @pytest.mark.trio
    async def test_failed_batch_not_repeated(self, fs, monkeypatch):
        """A batch that already reached the file is not written again."""
        monkeypatch.setattr(atomic_trio, 'CHUNK_SIZE', 4)
        calls = []

        def failing_batch(f, chunks):
            calls.append(list(chunks))
            # the first chunk lands, the second one fails the write
            f.write(chunks[0])
            raise OSError('disk full')

        monkeypatch.setattr(atomic_trio, '_write_batch', failing_batch)

        async def chunks():
            yield b'ab'
            yield b'cd'

        with pytest.raises(OSError) as e:
            await file_write_stream_async('/data/a.bin', chunks())
        assert str(e.value) == 'disk full'
        assert calls == [[b'ab', b'cd']]
        assert file_read_bytes('/data/a.bin') == b'ab'
