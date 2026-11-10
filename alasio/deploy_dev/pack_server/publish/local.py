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
the uploader should not wait for either. `prepare()` creates one slot per file
of the generation, `warmup()` reads them in the order of the manifest beside the
caller (the caller starts it in a nursery of its own), and `item()` waits for
the file it is about to publish and only for that one. An `UploadItem` is
therefore never read twice, and a mirror waits for the file it needs, not for
the generation.

There is exactly one reader per file. `warmup()` starts it; `item()` never does
IO of its own. A read that is cancelled with the run that queued it leaves its
slot unread, so the next `warmup()` of the same generation queues it again
instead of handing a caller a file that was never read.
"""

from collections import deque

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

    A slot is created by `prepare()`, once per key of its queue, and that is the
    only place a key is added: a caller that asks for a key the queue did not
    create gets a KeyError, because both lists come from the manifest.

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
        local.prepare(await local.record)
        async with trio.open_nursery() as nursery:
            local.start(nursery)
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
        self._slots = {}
        # the files the running warmup has to read, in queue order, see
        # prepare() and warmup()
        self._pending = deque()

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
        except OSError as e:
            # _read_pair answers None for a file that is not there, so this is a
            # file that exists and cannot be read: a permission, a folder where
            # a file belongs
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

    async def prepare(self):
        """
        Validate the record, then create one slot per file of the generation.

        The slots exist before `warmup()` runs, so a caller may ask `item()` for
        a file the warmup has not reached yet: it waits for that file instead of
        failing on a key the queue has not created.

        A file that is already read is skipped, which is what makes the second
        mirror of a channel read nothing; a file whose read was cancelled has
        neither an item nor an error and is queued again. The state of the disk
        outlives the mirror that happened to fail.

        The record is read first: a workspace whose two records do not name
        one generation is refused before a single file of it is read.
        """
        manifest = await self.record
        self._pending = deque(self._queue(list(manifest.files) + [MANIFEST_KEY]))

    def start(self, nursery):
        """
        Start the reads of the prepared generation in this nursery.

        One `warmup()` task per reader: each pulls the queue on its own, so the
        head of the queue is the first file read and several files are read at
        once. The nursery belongs to the caller, which keeps its own work in
        the same block: the reads of the disk overlap the first requests of the
        run, and a failure of the block cancels the reads that have not
        finished.

        Args:
            nursery (trio.Nursery): The nursery of the calling block
        """
        for _ in range(min(self.concurrency, len(self._pending))):
            nursery.start_soon(self.warmup)

    async def warmup(self):
        """
        Read the files of the generation, claiming them in queue order.

        One task of this method is one reader: it takes the next file of the
        queue, reads it, and takes the next one until the queue is empty. The
        claim is one synchronous popleft(), so two readers never take the same
        file, and the file at the head of the queue is always the first one
        taken.

        The order matters because the slots exist from prepare(): the first
        `item()` of the run waits for the head of the queue, not for the whole
        disk, and the reads its caller needs first are the ones that start
        first.

        A queue position is not a file number: the tasks of a nursery are
        scheduled by trio, so which reader takes which file is the scheduler's
        business. What is fixed is the order the files are handed out in.
        """
        while self._pending:
            relative, slot = self._pending.popleft()
            # the failure is stored on the slot instead of raised: a missing
            # file is the concern of the mirror that publishes it, and the
            # other files of the generation are still readable
            try:
                slot.item = await self._read(relative)
            except SkipChannel as e:
                slot.error = e
            finally:
                slot.done.set()

    def _queue(self, order):
        """
        The slots to read now, in the order the caller read them from.

        A slot is created here and nowhere else: the uploader derives its keys
        from the same lists, so a key without a slot is a bug of the flow, not a
        state of the disk. A key whose read settled is skipped, which is what
        makes the second mirror of a channel read nothing; a key whose read was
        cancelled has neither an item nor an error, and is queued again.

        The caller runs the reads of one generation one after another
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
