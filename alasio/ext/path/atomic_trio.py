"""
Trio variants of the streaming write functions of alasio.ext.path.atomic.

The file IO runs in the worker threads of trio, so an async caller never
blocks the event loop on opening, writing, flushing, fsyncing or replacing a
file. The async iterator is still consumed in the calling task: the chunks
are collected there and written in batches, because a thread hop per chunk
costs more than the write of the chunk itself.

The sync functions stay in alasio.ext.path.atomic, which does not import
trio: the sync scripts must not pay for the async stack.
"""
import os
from contextlib import suppress
from functools import partial

import trio

from alasio.ext.path.atomic import (
    CHUNK_SIZE, _remove_tmp_on_failure, file_open, replace_tmp, stream_open_args, to_tmp_file
)


def _write_batch(f, chunks):
    """
    Write a batch of chunks, runs in a worker thread

    Args:
        f (file object): Open file, text or binary mode
        chunks (list[str | bytes]): Chunks to write, in order
    """
    f.writelines(chunks)


def _flush_file(f):
    """
    Flush the written data to the disk, runs in a worker thread

    Args:
        f (file object): Open file
    """
    f.flush()
    os.fsync(f.fileno())


async def file_write_stream_async(file, data_iter):
    """
    Creates the file even if the iterator yields no data chunk,
    an empty iterator writes an empty file, replacing the content of the target.
    Auto determines write mode based on the type of first chunk.

    All the file IO runs in a worker thread, awaiting this function never
    blocks the event loop on the disk.

    Args:
        file (str): Target file path
        data_iter (AsyncIterator): An async iterator that yields data chunks
            (str or bytes)

    Raises:
        TypeError: If a chunk is neither str nor bytes, such a chunk has no
            length to batch and no write() accepting it
        trio.Cancelled: If the caller is cancelled, the file is closed before
    """
    # The first chunk decides the mode of the file, await it in the caller
    try:
        first_chunk = await data_iter.__anext__()
    except StopAsyncIteration:
        # An empty iterator still writes an empty file: no chunk to tell text
        # from bytes, the empty bytes chunk goes through the binary mode
        first_chunk = b''

    mode, encoding, newline = stream_open_args(first_chunk)
    # The parent directory is created when it is missing
    f = await trio.to_thread.run_sync(
        partial(file_open, file, mode=mode, encoding=encoding, newline=newline)
    )

    # Collect the chunks in the caller, a batch is written by one thread call.
    # The batch moves out before the write, so a failed write is never repeated
    batch = [first_chunk]
    size = len(first_chunk)
    try:
        async for chunk in data_iter:
            batch.append(chunk)
            size += len(chunk)
            if size >= CHUNK_SIZE:
                to_write, batch, size = batch, [], 0
                await trio.to_thread.run_sync(_write_batch, f, to_write)
        if batch:
            to_write, batch = batch, []
            await trio.to_thread.run_sync(_write_batch, f, to_write)
        await trio.to_thread.run_sync(_flush_file, f)
    except BaseException:
        # The iterator failed or the caller was cancelled: write the chunks
        # collected but not written yet, close the file, the original error wins
        with trio.CancelScope(shield=True):
            if batch:
                with suppress(Exception):
                    await trio.to_thread.run_sync(_write_batch, f, batch)
            with suppress(Exception):
                await trio.to_thread.run_sync(f.close)
        raise
    await trio.to_thread.run_sync(f.close)


async def atomic_write_stream_async(file, data_iter, mode=None):
    """
    Atomic file write with streaming data support.
    Handles cases where file might be read by another process.
    A failed write leaves the target untouched and no tmp file behind.

    os.replace() is an atomic operation among all OS,
    we write to temp file then do os.replace()

    All the file IO runs in a worker thread, awaiting this function never
    blocks the event loop on the disk.

    Args:
        file (str): Target file path
        data_iter (AsyncIterator): An async iterator that yields data chunks
            (str or bytes)
        mode (int): POSIX mode of the target file, set on the tmp file before it
            replaces the target, so the mode of the target is never observable
            in between. None to keep the default mode

    Raises:
        TypeError: If a chunk is neither str nor bytes, such a chunk has no
            length to batch and no write() accepting it
        trio.Cancelled: If the caller is cancelled, the target is left
            untouched and the tmp file is removed before
    """
    tmp = to_tmp_file(file)
    try:
        await file_write_stream_async(tmp, data_iter)
        if mode is not None:
            # The mode is set on the tmp file, not on the target, so a reader
            # never sees the target between two modes
            await trio.to_thread.run_sync(partial(os.chmod, tmp, mode))
        await trio.to_thread.run_sync(replace_tmp, tmp, file)
    except BaseException:
        # The stream failed or the caller was cancelled: the target is not
        # touched yet, drop the tmp file. Shielded, a cancelled caller still
        # has to clean up, and the error of the cleanup never masks the write
        with trio.CancelScope(shield=True):
            with suppress(Exception):
                await trio.to_thread.run_sync(_remove_tmp_on_failure, tmp)
        raise
