"""
The object stores a workspace is mirrored to.

An uploader mirrors the files of a manifest to one or more static hosts and a
cleaner removes what a later generation no longer serves. Neither of them knows
what the host is: both work on the methods of ObjectStore, and the store of a
host implements them. The differences between the hosts that do matter to the
core flow are values of ObjectInfo / Capabilities, not branches of the core
flow:

- whether the ETag of an object is the MD5 of its content (etag_md5), which
  decides whether a written object is verified from the answer of its PUT or
  from the checksum of a HEAD;
- whether the store has collections that are removed when they are empty
  (dirs).

A capability is a fact the core flow reads. A fact nobody reads does not belong
here: it would be a second description that drifts from the code (a store that
cannot serve a workspace is refused by the driver that builds it, not by a flag).

A store instance is bound to a prefix, the keys it sees are relative to it. The
core flow therefore never builds a key of a physical layout (a bucket prefix, a
base url): only the store joins the two, and a wrong slash cannot move a file
into the workspace next to it.

The list / stat split is the one the core flow needs: the cleaner needs every
object of the workspace (list), the uploader needs the state of a known set of
keys (stat), and a store whose listing is expensive answers both with one
request inside its own implementation.

Every method is async: a mirror run has dozens of objects in flight, and the
uploader lets trio schedule them instead of holding a thread per transfer. A
store never blocks the event loop on IO of its own: the file of an upload is
read in a worker thread, the requests are awaited.
"""

from typing import Dict

import msgspec


class StoreError(Exception):
    """
    Raised when an operation of a store fails.

    The message names the object (a workspace-relative key) and what the server
    answered, never a credential. A failure a caller may retry (a transport
    error) is retried inside the store first, so a StoreError is the answer of
    the server after the retries or a configuration the server refuses.
    """


class ObjectNotFound(StoreError):
    """
    Raised when an object a caller reads does not exist.

    A delete of a missing object is not an error: the store returns normally,
    so a cleanup run is idempotent.
    """


class ObjectInfo(msgspec.Struct):
    """
    What a store reports about one object.

    Attributes:
        size (int): Size of the object in bytes, as the server reports it
        etag (str): Version token of the object, as the server reports it and
            normalized by the store: the quotes of an HTTP ETag are dropped and
            the value is lowercased when the server uses uppercase hex. The
            core flow compares it, it never parses it
        last_modified (float): Unix time the object was last written, from the
            clock of the server
        checksum (str): Digest of the content the server reports besides the
            ETag, in the form the store chose (COS: the decimal CRC64 of
            x-cos-hash-crc64ecma). '' when the server reports none, and for a
            value a listing carries: only an answer about one object (a PUT or
            a HEAD) reports it, see Capabilities.head_checksum
    """

    size: int
    etag: str
    last_modified: float
    checksum: str = ''


class ObjectListing(msgspec.Struct):
    """
    Every object of a store prefix, plus the clock the server answered with.

    A cleaner takes its `now` from server_date and the age of an object from
    last_modified, so both sides of a grace window come from the same clock.
    The clock of the machine that runs the cleanup is not used: a machine whose
    time is a few minutes fast must not delete an object of a generation that is
    still being published.

    Attributes:
        objects (Dict[str, ObjectInfo]): Object of the prefix by relative key
        server_date (float): Unix time of the Date header of the listing
            response, the clock of the server
    """

    objects: Dict[str, ObjectInfo]
    server_date: float


class Capabilities(msgspec.Struct):
    """
    What a store can promise to the core flow.

    A field here is read by the flow, not merely declared: the value changes a
    decision, and every one of them is pinned by a test against the real service
    (tests/deploy_dev/store/cos/test_real.py). A fact the flow never reads does
    not belong here - a store that cannot serve a workspace is refused by the
    driver that builds it, not by a flag nobody checks.

    Attributes:
        etag_md5 (bool): The ETag of an object written by a single PUT is the
            MD5 of its content. False for a store whose ETag is opaque or is
            computed over something else, and then a write is verified by the
            checksum of a HEAD or by reading the object back
        head_checksum (bool): A HEAD of an object reports ObjectInfo.checksum,
            a digest of the content of the stored object, in the same form a
            PUT reports it. The two answers prove the object a reader sees is
            the one that was written, without downloading it
        dirs (bool): The store has collections that outlive their objects, and
            remove_dirs() removes the empty ones
    """

    etag_md5: bool = False
    head_checksum: bool = False
    dirs: bool = False


class ObjectStore:
    """
    The operations a mirror of a workspace needs.

    An instance is bound to one prefix (a workspace of a bucket, a collection of
    a WebDAV server), and every key it takes or returns is relative to it. A
    store owns the client it sends the requests with when it created it:
    aclose() releases it, and an injected client belongs to its caller.

    Every method is a coroutine: the caller runs several transfers at once in
    trio tasks, and the store awaits its requests instead of blocking the event
    loop. A store reads the file of an upload in a worker thread, so a slow disk
    does not stall the other transfers.

    The methods raise StoreError (ObjectNotFound for a read of a missing object)
    and never a transport error of the http library: the retries are the concern
    of the store, the core flow only decides what to do with a failed operation.
    """

    # what this store can promise, a class constant of the driver, see
    # Capabilities. The base class promises nothing
    CAPABILITIES = Capabilities()

    __slots__ = ()

    async def list(self):
        """
        Every object under the prefix of this store.

        One request for a store that lists a prefix in a page, more for one
        that walks a tree. The keys of the result are relative to the prefix.

        Returns:
            ObjectListing: The objects and the clock of the server

        Raises:
            StoreError: If the listing fails
        """
        raise NotImplementedError

    async def stat(self, relatives):
        """
        The info of exactly these objects.

        The default implementation lists the prefix and filters the result: a
        store whose listing is expensive overrides it with the cheaper request
        of its protocol (one HEAD per key, one PROPFIND per parent collection).

        Args:
            relatives (Iterable[str]): Relative keys to look up

        Returns:
            Dict[str, ObjectInfo]: The info of the keys that exist, a missing
                key is absent from the result

        Raises:
            StoreError: If the lookup fails
        """
        objects = (await self.list()).objects
        return {relative: objects[relative] for relative in relatives if relative in objects}

    async def get(self, relative):
        """
        Read one object.

        Args:
            relative (str): Relative key of the object

        Returns:
            bytes: Content of the object

        Raises:
            ObjectNotFound: If the object does not exist
            StoreError: If the read fails
        """
        raise NotImplementedError

    async def head(self, relative):
        """
        The state of one object, without its content.

        The verification of a write reads it when the store declares
        head_checksum: the checksum of the stored object is compared with the
        one the PUT answered, so the flow proves the object a reader sees is
        the one that was written without downloading it. A store that reports
        the checksum in a listing may answer this from stat().

        The default implementation asks stat() for the key, so a store whose
        listing carries every value of ObjectInfo needs nothing of its own.

        Args:
            relative (str): Relative key of the object

        Returns:
            ObjectInfo: What the store reports about the object

        Raises:
            ObjectNotFound: If the object does not exist
            StoreError: If the lookup fails
        """
        found = await self.stat([relative])
        try:
            return found[relative]
        except KeyError:
            raise ObjectNotFound(f'Object "{relative}" does not exist') from None

    async def put(self, relative, file, size, md5, content_type=None, cache_control=None):
        """
        Write one object with a single request.

        The object is written once, never in parts: a multipart upload of an
        object storage does not produce the MD5 of the content as its ETag (it
        produces a digest of the part digests), and a listing that can no longer
        recognize the MD5 of a file would silently resend every large object of
        every run.

        Args:
            relative (str): Relative key of the object
            file (str): Path of the local file to send. The store opens it once
                per attempt, so a retried transfer starts at the beginning
            size (int): Size of the file in bytes, sent as Content-Length
            md5 (str): Lowercase hex MD5 of the content, sent as Content-MD5
            content_type (str): Content-Type of the object, the store default
                when None. Defaults to None
            cache_control (str): Cache-Control of the object, unset when None.
                Defaults to None

        Returns:
            ObjectInfo: What the server reports about the written object

        Raises:
            StoreError: If the write fails
        """
        raise NotImplementedError

    async def delete(self, relative):
        """
        Delete one object, idempotently.

        A key that does not exist is a success: a cleanup run that deletes the
        same key twice, or deletes a key another run already removed, must not
        fail.

        Args:
            relative (str): Relative key of the object

        Raises:
            StoreError: If the delete fails for another reason
        """
        raise NotImplementedError

    async def remove_dirs(self, relatives):
        """
        Remove the collections of the given keys that are now empty.

        A store whose keys are flat has no collections and does nothing. A store
        with collections removes only an empty one and never recurses: a
        collection that still holds an object is left as it is.

        Args:
            relatives (Iterable[str]): Relative keys whose parent collections
                are candidates for removal, deepest first
        """
        return

    async def aclose(self):
        """
        Release the client of this store when the store created it.

        An injected client belongs to the caller and is not closed here. The
        method is idempotent, and a store must not send a request after it.
        """
        return


async def read_chunks(file, chunk_size=1 << 20):
    """
    Yield the rest of a binary file in chunks, without blocking the loop.

    The body of an upload is an async iterable in httpx2: a sync file object
    sent through an AsyncClient raises, and reading it inline would stall every
    other transfer of the run while the disk works. The read runs in a worker
    thread, one call per chunk.

    Args:
        file (IO[bytes]): Open binary file, read from its current position
        chunk_size (int): Bytes per read, 1 MiB by default. Defaults to 1 << 20

    Yields:
        bytes: The next chunk of the file, empty at the end never yielded
    """
    # local import: the base package is importable without the async stack, the
    # module that reads a file is the one that needs it
    import trio

    while True:
        chunk = await trio.to_thread.run_sync(file.read, chunk_size)
        if not chunk:
            return
        yield chunk


async def open_local(path, mode='rb'):
    """
    Open a local file without blocking the loop.

    `open()` is IO as well: it resolves the path and reads the inode of the
    file, which on a network share or a slow disk can take milliseconds. An
    async function that calls it inline stalls every other transfer of the run,
    so the call goes to a worker thread like the reads of read_chunks().

    Args:
        path (str): Path of the file
        mode (str): Mode of the open, 'rb' by default. Defaults to 'rb'

    Returns:
        IO: The open file, closed by close_local()

    Raises:
        OSError: If the file cannot be opened
    """
    import trio

    return await trio.to_thread.run_sync(open, path, mode)


async def close_local(file):
    """
    Close a file opened by open_local(), without blocking the loop.

    Args:
        file (IO): The open file
    """
    import trio

    await trio.to_thread.run_sync(file.close)


async def stat_local(path):
    """
    The os.stat of a local path, taken in a worker thread.

    Args:
        path (str): Path to look at

    Returns:
        os.stat_result: The stat of the path

    Raises:
        OSError: If the path cannot be stat'ed
    """
    import os

    import trio

    return await trio.to_thread.run_sync(os.stat, path)


async def md5_local(path, chunk_size=1 << 20):
    """
    The size and the MD5 of a local file, computed in a worker thread.

    The digest is taken by a single worker call rather than one call per chunk:
    hashing costs CPU, and doing it on the event loop would stall the other
    transfers of the run for as long as the disk can read. The file is never
    held in memory.

    Args:
        path (str): Path of the file
        chunk_size (int): Bytes per read inside the worker. Defaults to 1 << 20

    Returns:
        tuple[int, str]: (size in bytes, lowercase hex MD5)

    Raises:
        OSError: If the file cannot be read
    """
    import hashlib

    import trio

    def digest():
        hasher = hashlib.md5()
        size = 0
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(chunk_size), b''):
                hasher.update(chunk)
                size += len(chunk)
        return size, hasher.hexdigest()

    return await trio.to_thread.run_sync(digest)
