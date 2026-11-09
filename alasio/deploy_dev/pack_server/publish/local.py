"""
The local side of a publication: the record of one generation and the digests of
its files.

The facts here belong to the **channel**, not to a mirror. The records of a
generation are two files on the disk, and the digests of the files they name do
not change while a run publishes them: the lock of the channel keeps every
writer but this run out of the workspace for the whole of it (see lock.py). A
run that mirrors one generation to three hosts must therefore read and digest
it **once**, not once per host: a generation of 300 files and 1.5 GB would
otherwise re-read 4.5 GB from the disk for the third mirror.

Reading is split from deciding, because the two have very different latencies:
the disk reads a hundred files while the mirror answers its first request, and
the uploader should not wait for either. Entering `warmup()` reads the two files
of the record and starts one task per content file; `item()` waits for the file
it is about to publish, and only for that one. An `UploadItem` is therefore
never read twice, and a mirror is never held back by a file it does not need
yet.

There is exactly one reader per file. `warmup()` starts it; `item()` never does
IO of its own. A read that is cancelled with the run that queued it leaves its
slot unread, so the next `warmup()` of the same generation queues it again
instead of handing a caller a file that was never read.
"""

from contextlib import asynccontextmanager

import msgspec
import trio

from alasio.deploy.pack.server_file import LatestInfo
from alasio.deploy_dev.pack_server.manifest import MANIFEST_FILE, Manifest
from alasio.deploy_dev.pack_server.publish.common import (
    BARRIER_KEY, MANIFEST_KEY, MAX_CONCURRENCY, PublishError, SkipChannel
)
from alasio.deploy_dev.store import md5_local
from alasio.ext.cache import cached_property_async_trio_lock
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_bytes
from alasio.ext.path.validate import validate_filepath


class UploadItem(msgspec.Struct):
    """
    One local file to mirror, and what the mirror needs to know about it.

    Attributes:
        relative (str): Key relative to the workspace, at the same time the
            relative key of the store
        file (str): Absolute path of the local file
        size (int): Size in bytes
        md5 (str): Lowercase hex MD5 of the content
    """

    relative: str
    file: str
    size: int
    md5: str


class _Slot:
    """
    One file of the generation, read once.

    A slot is created by `warmup()`, once per key of its queue, and that is the
    only place a key is added: a caller that asks for a key the warmup did not
    queue gets a KeyError, because both lists come from the manifest.

    There are two states, and one field tells them apart: a settled slot holds
    an item or an error, an unread one holds neither. A slot of a run whose
    reads were cancelled is replaced, never reused, so a partially filled slot
    cannot outlive the run that left it.

    Attributes:
        done (trio.Event): Set last by the reader, after the item or the error
        item (UploadItem): The file, None until a read succeeds
        error (SkipChannel): Why the read failed, None when it succeeded
    """

    __slots__ = ('done', 'item', 'error')

    def __init__(self):
        self.done = trio.Event()
        self.item = None
        self.error = None


class LocalGeneration:
    """
    The workspace of one channel, read once for every mirror of it.

    A run builds one instance and hands it to every `ChannelUpload` of the
    channel: `warmup()` starts the reads, `item()` awaits the ones each mirror
    needs, and a second mirror finds the slots of the first already filled. An
    instance is a snapshot of a generation, not a live view: a generator that
    writes a new generation needs a new instance, and the lock of the channel
    keeps the two apart (see lock.py).

    Usage:
        async with local.warmup():
            item = await local.item('packrepo/latest.pack')

    Attributes:
        workspace (PathStr): Absolute path of the workspace
        concurrency (int): Files read at once, 1 to MAX_CONCURRENCY
    """

    def __init__(self, workspace, concurrency=MAX_CONCURRENCY):
        """
        Args:
            workspace (str): Absolute path of the workspace
            concurrency (int): Files read at once, 1 to MAX_CONCURRENCY
        """
        self.workspace = PathStr.new(workspace)
        self.concurrency = max(1, min(int(concurrency), MAX_CONCURRENCY))
        # the width of the read queue, held by the readers and by nobody else
        self._limit = trio.Semaphore(self.concurrency)
        self._slots = {}

    @cached_property_async_trio_lock
    async def record(self):
        """
        The published contract of the generation, after the guards.

        The guards refuse a workspace whose two records do not name one complete
        generation, because mirroring half of one would leave a mirror that
        serves a manifest over missing files. The read is cached: every mirror
        of the channel waits for the same answer.

        Returns:
            Manifest: The published contract

        Raises:
            SkipChannel: If the record is missing, broken, of another
                generation, or names a file that is not usable
        """
        try:
            manifest_data, barrier_data = await trio.to_thread.run_sync(
                _read_pair, self.workspace.joinpath(MANIFEST_FILE),
                self.workspace.joinpath(BARRIER_KEY))
        except FileNotFoundError as e:
            raise SkipChannel(f'{e.filename} is not there yet') from None
        except OSError as e:
            raise SkipChannel(f'cannot read the record: {e}') from None

        if manifest_data is None:
            raise SkipChannel(f'{MANIFEST_FILE} is not there yet')
        if barrier_data is None:
            raise SkipChannel(f'{BARRIER_KEY} is not there yet')

        try:
            manifest = Manifest.decode(manifest_data)
        except msgspec.DecodeError as e:
            raise SkipChannel(f'{MANIFEST_FILE} is not a manifest: {e}') from None
        try:
            barrier = LatestInfo.parse(barrier_data)
        except Exception as e:  # noqa: BLE001 - PackDecodeError and friends
            raise SkipChannel(f'{BARRIER_KEY} is not readable: {e}') from None

        if manifest.channel != self.workspace.name:
            raise SkipChannel(
                f'{MANIFEST_FILE} names channel "{manifest.channel}", '
                f'the folder is "{self.workspace.name}"')
        if (manifest.version, manifest.checksum) != (barrier.version, barrier.checksum):
            raise SkipChannel(
                f'{MANIFEST_FILE} is version {manifest.version}/{manifest.checksum[:12]}, '
                f'{BARRIER_KEY} is {barrier.version}/{barrier.checksum[:12]}')
        if BARRIER_KEY not in manifest.files:
            raise SkipChannel(f'{BARRIER_KEY} is not one of the published files')
        if str(MANIFEST_FILE) in manifest.files:
            raise SkipChannel(f'{MANIFEST_FILE} lists itself as a published file')
        seen = set()
        for relative in manifest.files:
            if relative in seen:
                raise SkipChannel(f'{MANIFEST_FILE} lists "{relative}" twice')
            seen.add(relative)
            try:
                validate_filepath(relative)
            except ValueError as e:
                raise SkipChannel(f'{MANIFEST_FILE} lists an unusable path: {e}') from None
        return manifest

    @asynccontextmanager
    async def warmup(self):
        """
        Hold the generation open while the caller publishes it.

        Entering the block validates the record of the generation: the two files
        that name it are read and checked, so a workspace that is broken is
        refused before the caller sends its first request. Nothing else is
        awaited: the block is entered while the disk has read only those two
        small files.

        The content files are then read in tasks of a nursery of this call,
        including the barrier and the contract, which the uploader publishes
        last but reads like any other file: nothing in this class treats them
        specially, and the queue is the manifest itself. The reads run while the
        caller sends its first requests; a failure in the block cancels the ones
        that have not finished, so a run that will not publish does not read the
        rest of the disk, while a clean exit waits for them.

        With a concurrency of 1 the reads are a loop instead of tasks of a
        nursery: one file at a time, in the order of the queue, all of them
        before the block is entered. The caller that asks for one at a time
        gets a run whose requests and reads have one order, which is what a
        test pins.

        A read that was cancelled with an earlier mirror left its slot unread
        and is queued again here, while a file that is already read is skipped:
        the state of the disk outlives the mirror that happened to fail.

        Yields:
            None: The block in which `item()` may be awaited

        Raises:
            SkipChannel: If the record of the generation is not usable
        """
        manifest = await self.record
        # the keys of the queue are exactly the keys a caller may ask for: the
        # published files, and the contract the manifest does not list
        order = list(manifest.files) + [MANIFEST_KEY]
        if self.concurrency == 1:
            # a serial read is a loop, a nursery would only hand the order to
            # the scheduler
            for relative, slot in self._queue(order):
                await self._fill(relative, slot)
            yield
            return
        async with trio.open_nursery() as nursery:
            for relative, slot in self._queue(order):
                nursery.start_soon(self._fill, relative, slot)
            yield

    def _queue(self, order):
        """
        The slots to read now, in the order the caller read them from.

        A slot is created here and nowhere else: the uploader derives its keys
        from the same lists, so a key without a slot is a bug of the flow, not a
        state of the disk. A key whose read settled is skipped, which is what
        makes the second mirror of a channel read nothing; a key whose read was
        cancelled has neither an item nor an error, and is queued again.

        The caller runs the warmup blocks of one generation one after another
        (the mirrors of a channel are published in turn), so nothing has to
        guard against two readers of the same key.

        Args:
            order (Iterable[str]): Keys of the generation, in queue order

        Yields:
            tuple[str, _Slot]: (key, slot) of every file that still needs a read
        """
        for relative in order:
            slot = self._slots.get(relative)
            if slot is not None and (slot.item is not None or slot.error is not None):
                continue
            # created here, from the manifest: the uploader derives its keys
            # from the same lists and never invents one of its own
            self._slots[relative] = slot = _Slot()
            yield relative, slot

    async def item(self, relative):
        """
        The file of the generation, read by the time this returns.

        The call waits for the file it asks about and no other: a mirror that
        publishes ten of three hundred files waits for those ten.

        Args:
            relative (str): '/'-separated key relative to the workspace

        Returns:
            UploadItem: The file, its size and its MD5

        Raises:
            KeyError: If the key is not one warmup() queued. Both the queue and
                the caller take their keys from the manifest, so a key that is
                missing here is a bug of the flow, not a state of the disk
            SkipChannel: If the file is missing or cannot be read
            PublishError: If the read was cancelled before it settled
        """
        slot = self._slots[relative]
        await slot.done.wait()
        if slot.error is not None:
            raise slot.error
        if slot.item is None:
            # the event is set last in _fill, so a slot without an item was not
            # read: its attempt was cancelled with the run that queued it
            raise PublishError(
                f'"{relative}" was not read: the attempt was cancelled')
        return slot.item

    async def _fill(self, relative, slot):
        """
        Read one file and settle its slot, a few readers at a time.

        The failure is stored on the slot instead of raised: a missing file is
        the concern of the mirror that publishes it, and the other files of the
        generation are still readable.

        Args:
            relative (str): '/'-separated key relative to the workspace
            slot (_Slot): The slot of that key, queued by warmup()
        """
        try:
            async with self._limit:
                slot.item = await self._read(relative)
        except SkipChannel as e:
            slot.error = e
        finally:
            slot.done.set()

    async def _read(self, relative):
        """
        Read one file of the workspace and compute its size and MD5.

        The digest runs in a worker thread: a caller awaits this from the event
        loop, and a hundred compiled wheels of a generation would otherwise
        stall every other transfer of the run. Reading the file is also what
        tells whether it is there, so no stat() pass is needed first.

        Args:
            relative (str): '/'-separated key relative to the workspace

        Returns:
            UploadItem: The item

        Raises:
            SkipChannel: If the file is missing or cannot be read
        """
        file = self.workspace.joinpath(relative)
        try:
            size, md5 = await md5_local(file)
        except FileNotFoundError:
            raise SkipChannel(
                f'the published file is missing on the disk: {relative}') from None
        except OSError as e:
            raise SkipChannel(f'cannot read "{relative}": {e}') from None
        return UploadItem(relative=relative, file=str(file), size=size, md5=md5)


def _read_pair(manifest_path, barrier_path):
    """
    Read the two files of a generation, in one worker call.

    Args:
        manifest_path (str): Path of manifest.json
        barrier_path (str): Path of packrepo/latest.pack

    Returns:
        tuple[bytes, bytes]: (manifest, barrier), None for a file that is absent

    Raises:
        OSError: If a file exists but cannot be read
    """
    pair = []
    for path in (manifest_path, barrier_path):
        try:
            pair.append(atomic_read_bytes(path))
        except FileNotFoundError:
            pair.append(None)
    return pair[0], pair[1]
