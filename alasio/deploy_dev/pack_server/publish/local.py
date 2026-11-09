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

`LocalGeneration.files` is an async cached property (see
`cached_property_async_trio_lock`): the first channel to await it digests the
generation, the other channels await the same result without doing any IO, and
concurrent first awaits are deduplicated by a trio lock.
"""

from typing import List

import msgspec
import trio

try:
    BaseExceptionGroup
except NameError:  # python 3.8 / 3.9
    from exceptiongroup import BaseExceptionGroup

from alasio.deploy.pack.server_file import LatestInfo
from alasio.deploy_dev.pack_server.manifest import MANIFEST_FILE, Manifest
from alasio.deploy_dev.pack_server.publish.common import (
    BARRIER_KEY, MANIFEST_KEY, MAX_CONCURRENCY, SkipChannel, find_skip, first_failure
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


class LocalFiles(msgspec.Struct):
    """
    What one generation holds on the disk.

    Attributes:
        manifest (Manifest): The published contract, after the guards
        items (List[UploadItem]): One item per published file, the barrier
            included, in the order of the contract
        manifest_item (UploadItem): The contract itself, which the manifest does
            not list
    """

    manifest: Manifest
    items: List[UploadItem]
    manifest_item: UploadItem


class LocalGeneration:
    """
    The workspace of one channel, read once for every mirror of it.

    A run builds one instance and hands it to every `ChannelUpload` of the
    channel: the first one to await `files` reads the record and digests the
    files, the others await the same result. An instance is a snapshot of a
    generation, not a live view: a generator that writes a new generation needs
    a new instance, and the lock of the channel keeps the two apart (see
    lock.py).

    Usage:
        local = LocalGeneration(workspace)
        files = await local.files

    Attributes:
        workspace (PathStr): Absolute path of the workspace
        concurrency (int): Files digested at once, 1 to MAX_CONCURRENCY
    """

    def __init__(self, workspace, concurrency=MAX_CONCURRENCY):
        """
        Args:
            workspace (str): Absolute path of the workspace
            concurrency (int): Files digested at once, 1 to MAX_CONCURRENCY
        """
        self.workspace = PathStr.new(workspace)
        self.concurrency = max(1, min(int(concurrency), MAX_CONCURRENCY))

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

    @cached_property_async_trio_lock
    async def files(self):
        """
        The record of the generation and the digest of every published file.

        The first await reads the record, digests the files in worker threads a
        few at a time, and reads the contract itself; the other awaits of the
        same instance return the same `LocalFiles` without touching the disk.

        Files of the workspace that the manifest does not list are not looked
        at: the manifest is the publication, and a leftover is the concern of
        the generator that owns the folder (repo.py), never of a mirror.

        Returns:
            LocalFiles: The contract, its items and the item of the contract

        Raises:
            SkipChannel: If the record is refused, or a published file is
                missing or cannot be read
        """
        manifest = await self.record
        items = await self._digest(manifest)
        return LocalFiles(
            manifest=manifest,
            items=items,
            manifest_item=await self._item(MANIFEST_KEY),
        )

    async def _item(self, relative):
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
            OSError: If the file cannot be read
        """
        file = self.workspace.joinpath(relative)
        size, md5 = await md5_local(file)
        return UploadItem(relative=relative, file=str(file), size=size, md5=md5)

    async def _digest(self, manifest):
        """
        Read every published file of the generation and compute its digest.

        The digest is the only thing the plan needs, and reading the file is
        what tells whether it is there. The reads run in worker threads, a few
        files at a time; a file that cannot be read cancels the siblings, so a
        broken generation is never half processed.

        Args:
            manifest (Manifest): The published contract

        Returns:
            list[UploadItem]: One item per published file, the barrier included

        Raises:
            SkipChannel: If a published file is missing or cannot be read
        """
        items = {}
        limit = trio.Semaphore(self.concurrency)

        async def one(relative):
            async with limit:
                try:
                    items[relative] = await self._item(relative)
                except FileNotFoundError:
                    raise SkipChannel(
                        f'the published file is missing on the disk: {relative}') from None
                except OSError as e:
                    raise SkipChannel(f'cannot read "{relative}": {e}') from None

        if self.concurrency == 1:
            # one at a time, in the order of the manifest: a serial read is a
            # loop, a nursery would only hand the order to the scheduler
            for relative in manifest.files:
                await one(relative)
            return [items[relative] for relative in manifest.files]

        try:
            async with trio.open_nursery() as nursery:
                for relative in manifest.files:
                    nursery.start_soon(one, relative)
        except BaseExceptionGroup as group:
            skip = find_skip(group)
            if skip is not None:
                raise skip
            raise first_failure(group) from None
        return [items[relative] for relative in manifest.files]


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
