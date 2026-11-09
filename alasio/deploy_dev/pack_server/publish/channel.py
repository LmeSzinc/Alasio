"""
The publication of one generation to one remote: plan, write, verify, retire.

The input is the record of the generator, not a directory listing: the published
contract (`manifest.json`) names the files a mirror serves, and the barrier
(`packrepo/latest.pack`) names the version a client switches to. The local side
of that record is read once per channel (`LocalGeneration`, see local.py); this
module only reads the remote.

The order is the contract of a mirror:

1. the content files of the manifest, a few at a time;
2. the barrier, `packrepo/latest.pack`;
3. the contract, `manifest.json`;
4. the keys the new generation no longer serves.

A client that reads the manifest therefore finds a complete generation, and the
retire step runs only once the whole generation landed: a mirror that fails
before step 3 keeps serving the previous generation, which is complete.

The retire step reuses the listing the plan already needed, so publishing costs
one LIST plus the writes, and the keys of the previous generation go without a
second pass over the mirror.

Every request carries a numbered line, `PUT 3/12 wheel/x.whl (1.18MB)`, so a long
run reads as a progress report instead of a silence that ends in a summary.
"""

from typing import List

import msgspec
import trio

try:
    BaseExceptionGroup
except NameError:  # python 3.8 / 3.9
    from exceptiongroup import BaseExceptionGroup

from alasio.base.pretty import pretty_size
from alasio.deploy_dev.pack_server.publish.common import (
    BARRIER_KEY, MANIFEST_KEY, MAX_CONCURRENCY, MD5_HEX, PublishError, SkipChannel, cache_control, content_type,
    find_skip, first_failure
)
from alasio.deploy_dev.pack_server.publish.local import UploadItem
from alasio.deploy_dev.pack_server.publish.retire import DeletePlan, SkipRetire, build_delete_plan
from alasio.logger import logger

# the status of one channel/remote pair, the value of UploadResult.status
STATUS_OK = 'ok'
STATUS_DRY_RUN = 'dry-run'
STATUS_SKIPPED = 'skipped'
STATUS_FAILED = 'failed'

# the kind of a planned write, for the log and the summary
KIND_CONTENT = 'content'
KIND_BARRIER = 'barrier'
KIND_MANIFEST = 'manifest'


class UploadResult(msgspec.Struct):
    """
    What happened to one channel on one remote.

    Attributes:
        channel (str): Name of the workspace
        remote (str): Name of the remote
        status (str): STATUS_OK / STATUS_DRY_RUN / STATUS_SKIPPED / STATUS_FAILED
        uploaded (int): Files written in this run
        skipped (int): Files that were already on the mirror
        unverified (int): Of the skipped ones, the files the mirror held with the
            same size but no digest that could prove it
        size (int): Bytes written in this run
        deleted (int): Keys of an older generation removed in this run
        deleted_size (int): Bytes those keys held
        reason (str): Why the pair was skipped or failed, '' otherwise
    """

    channel: str
    remote: str
    status: str
    uploaded: int = 0
    skipped: int = 0
    unverified: int = 0
    size: int = 0
    deleted: int = 0
    deleted_size: int = 0
    reason: str = ''

    @property
    def ok(self):
        """
        Returns:
            bool: True when nothing failed
        """
        return self.status != STATUS_FAILED


# the outcome of comparing one local file with what the mirror reports
COMPARE_UPLOAD = 'upload'
COMPARE_SAME = 'same'
COMPARE_UNVERIFIED = 'unverified'


def compare(info, item, capabilities):
    """
    What the mirror reports about one local file.

    The only evidence the flow accepts is what the mirror reports right now:
    there is no local record of a previous upload, so a machine that never
    published this generation decides exactly like the one that did.

    - UPLOAD: the key is absent, its size differs, or the ETag is an MD5 and
      differs;
    - SAME: the ETag is an MD5 and equals the local MD5;
    - UNVERIFIED: the object has the same size but the mirror reports no digest
      that can be compared. The file is skipped, and the run counts it, so an
      unproven skip never looks like a proven one.

    Args:
        info (ObjectInfo): What the mirror reports, None when the key is absent
        item (UploadItem): The local file
        capabilities (Capabilities): What the store promises

    Returns:
        str: COMPARE_UPLOAD / COMPARE_SAME / COMPARE_UNVERIFIED
    """
    if info is None or info.size != item.size:
        return COMPARE_UPLOAD
    # the shape is checked as well: a bucket switched to a KMS key keeps
    # reporting an ETag the store promised was an md5, and it is not one anymore
    if capabilities.etag_md5 and MD5_HEX.match(info.etag):
        return COMPARE_SAME if info.etag == item.md5 else COMPARE_UPLOAD
    return COMPARE_UNVERIFIED


def needs_upload(info, item, capabilities):
    """
    Whether the file has to be written again.

    Args:
        info (ObjectInfo): What the mirror reports, None when the key is absent
        item (UploadItem): The local file
        capabilities (Capabilities): What the store promises

    Returns:
        bool: True when the local file has to be written
    """
    return compare(info, item, capabilities) == COMPARE_UPLOAD


class UploadPlan(msgspec.Struct):
    """
    The split of a generation into the files to write and the files to keep.

    `unverified` is not a failure and not a proof: the mirror holds an object of
    the same size, and neither the store nor the ETag can say whether it is the
    same content. The run keeps it and reports the count.

    Attributes:
        uploads (List[UploadItem]): Files to write
        same (List[UploadItem]): Files the mirror proved it holds
        unverified (List[UploadItem]): Files skipped on the size alone
    """

    uploads: List[UploadItem] = msgspec.field(default_factory=list)
    same: List[UploadItem] = msgspec.field(default_factory=list)
    unverified: List[UploadItem] = msgspec.field(default_factory=list)


def plan(items, snapshot, capabilities):
    """
    Split the files of a generation into the ones to write and the ones to skip.

    The function is the whole decision of the uploader: it takes the local files,
    what the mirror reports and the capabilities, and returns three lists.
    Nothing about a driver is decided here, only the values of its Capabilities
    are read. It calls no store: the snapshot is the one request the run already
    made.

    Args:
        items (Iterable[UploadItem]): The files of the manifest
        snapshot (Dict[str, ObjectInfo]): What the mirror reports by key
        capabilities (Capabilities): What the store promises

    Returns:
        UploadPlan: (to upload, proved same, skipped on the size alone)
    """
    result = UploadPlan()
    for item in items:
        outcome = compare(snapshot.get(item.relative), item, capabilities)
        if outcome == COMPARE_SAME:
            result.same.append(item)
        elif outcome == COMPARE_UNVERIFIED:
            result.unverified.append(item)
        else:
            result.uploads.append(item)
    return result


class ChannelUpload:
    """
    One workspace on one remote: plan, write, verify, retire.

    The instance owns the store bound to the channel and the local generation
    shared by every mirror of it: it does no local IO of its own, it awaits
    `LocalGeneration.files`, so three mirrors of one channel read and digest the
    generation once.

    Usage:
        local = LocalGeneration(workspace)
        upload = ChannelUpload(local, store, remote_name='cos-main', grace=3600)
        result = await upload.run()
    """

    def __init__(self, local, store, remote_name='', grace=3600, concurrency=MAX_CONCURRENCY):
        """
        Args:
            local (LocalGeneration): The generation to publish, shared by every
                mirror of the channel
            store (ObjectStore): The store bound to the workspace
            remote_name (str): Name of the remote, for the log and the result
            grace (int): Seconds the barrier of this remote must have been in
                place before a key of an older generation is removed. Defaults
                to 3600
            concurrency (int): Transfers in flight at once, 1 to MAX_CONCURRENCY
        """
        self.local = local
        self.store = store
        self.remote_name = remote_name
        self.grace = max(0, int(grace))
        # the cap is a property of the tool, not of the config: more transfers in
        # flight than this only move the queue into the connection pool
        self.concurrency = max(1, min(int(concurrency), MAX_CONCURRENCY))
        # the progress lines: requests done of the requests the plan announced
        self.done = 0
        self.total = 0

    @property
    def channel(self):
        """
        Returns:
            str: Name of the workspace
        """
        return self.local.workspace.name

    def _step(self, action, relative, detail='', index=True):
        """
        Log one request of the run, as a line of a progress report.

        The line is written before the request is sent: a transfer of hundreds
        of megabytes shows up while it is in flight instead of after it, and a
        run that fails still shows what it was doing. The numbering is against
        the count the plan announced, so the last line of a run reads `n/n`.

        Args:
            action (str): 'PUT', 'GET' or 'DELETE'
            relative (str): Key of the object
            detail (str): What the line says after the key, e.g. the size.
                Defaults to ''
            index (bool): True to number the line against the plan, False for a
                request the plan did not announce. Defaults to True
        """
        if index:
            self.done += 1
            head = f'{action} {self.done}/{self.total}'
        else:
            head = action
        suffix = f'  {detail}' if detail else ''
        logger.info(f'  {head} {relative:<44}{suffix}')

    async def _verify(self, info, item):
        """
        Check that the object the mirror holds is the local file.

        The evidence, cheapest first, and each kind is a statement about the
        stored object rather than about the request that wrote it:

        1. the ETag of a single PUT is the MD5 of the content, when the store
           declares it (etag_md5). Comparing it costs nothing;
        2. the store reports a digest of the stored object on a HEAD
           (head_checksum), in the same form its PUT reported it. The value of
           the PUT and the value of the HEAD are compared, so a key overwritten
           after the write is caught without downloading the object and without
           computing the digest here;
        3. neither: the object is read back and its MD5 compared (see
           ObjectStore.md5). This is the only path of a store that reports no
           digest at all.

        The size alone is never accepted: an object of the right length can
        hold other bytes.

        The HEAD and the GET are requests of their own, which is why this is a
        method of the uploader and not a free function: they go on the same
        progress log as the transfers.

        Args:
            info (ObjectInfo): What the mirror reports about the written object
            item (UploadItem): The local file

        Returns:
            bool: True when the object is the local file

        Raises:
            StoreError: If the HEAD or the read-back fails
        """
        capabilities = self.store.CAPABILITIES
        if capabilities.etag_md5 and MD5_HEX.match(info.etag):
            return info.etag == item.md5
        if capabilities.head_checksum and info.checksum:
            self._step(
                'HEAD', item.relative, 'the checksum the mirror reports', index=False)
            head = await self.store.head(item.relative)
            return head.checksum == info.checksum
        self._step(
            'GET', item.relative, pretty_size(item.size) + ', read back to verify',
            index=False)
        return await self.store.md5(item.relative) == item.md5

    async def run(self, dry_run=False):
        """
        Publish the generation of the workspace to the remote, then retire.

        Args:
            dry_run (bool): Print the plan and write nothing. Defaults to False

        Returns:
            UploadResult: What was written, retired, or why the run was refused
        """
        try:
            manifest = await self.local.record
        except SkipChannel as e:
            return self._skipped(e)
        try:
            files, listing = await self._gather(manifest)
        except SkipChannel as e:
            return self._skipped(e)

        items = files.items
        snapshot = listing.objects
        manifest_item = files.manifest_item

        barrier_item = next(item for item in items if item.relative == BARRIER_KEY)
        content = [item for item in items if item.relative != BARRIER_KEY]
        split = plan(content, snapshot, self.store.CAPABILITIES)
        barrier_outcome = compare(
            snapshot.get(BARRIER_KEY), barrier_item, self.store.CAPABILITIES)
        manifest_outcome = compare(
            snapshot.get(MANIFEST_KEY), manifest_item, self.store.CAPABILITIES)
        tail = [(barrier_item, barrier_outcome), (manifest_item, manifest_outcome)]
        write_barrier = barrier_outcome == COMPARE_UPLOAD
        write_manifest = manifest_outcome == COMPARE_UPLOAD
        same = split.same + [item for item, outcome in tail if outcome == COMPARE_SAME]
        unverified = split.unverified + [
            item for item, outcome in tail if outcome == COMPARE_UNVERIFIED]
        to_write = len(split.uploads) + int(write_barrier) + int(write_manifest)
        upload_bytes = sum(item.size for item in split.uploads)
        upload_bytes += barrier_item.size if write_barrier else 0
        upload_bytes += manifest_item.size if write_manifest else 0
        retire = self._retire_plan(files, listing, write_barrier)
        self.total = to_write + len(retire.keys)
        self.done = 0

        logger.info(
            f'[{self.remote_name}] {self.channel}: version={manifest.version} '
            f'{to_write} to upload ({pretty_size(upload_bytes)}), '
            f'{len(same)} unchanged, {len(unverified)} unverified, '
            f'{len(retire.keys)} to retire ({pretty_size(retire.size)})')

        if dry_run:
            self._log_plan(split, barrier_item, barrier_outcome, manifest_item, manifest_outcome)
            for relative in retire.keys:
                logger.info(
                    f'  WOULD DELETE {relative:<44} '
                    f'{pretty_size(snapshot[relative].size)}')
            return UploadResult(
                channel=self.channel, remote=self.remote_name, status=STATUS_DRY_RUN,
                uploaded=to_write, skipped=len(same) + len(unverified),
                unverified=len(unverified), size=upload_bytes,
                deleted=len(retire.keys), deleted_size=retire.size)

        await self._upload_content(split.uploads)
        if write_barrier:
            await self._put(barrier_item, KIND_BARRIER)
        if write_manifest:
            await self._put(manifest_item, KIND_MANIFEST)

        deleted = await self._retire(retire, listing)
        return UploadResult(
            channel=self.channel, remote=self.remote_name, status=STATUS_OK,
            uploaded=to_write, skipped=len(same) + len(unverified),
            unverified=len(unverified), size=upload_bytes,
            deleted=deleted, deleted_size=retire.size if deleted else 0)

    def _retire_plan(self, files, listing, write_barrier):
        """
        The keys of the mirror the generation that was just published no longer serves.

        The plan is built from the listing taken before the writes, so a key
        written by this run is never a candidate. The age of the generation is
        the age of the barrier in that listing:

        - the run writes the barrier (a version switch) → the age is zero for
          this round, nothing is retired, the next run does it;
        - the run skips the barrier (the content is already there) → the listing
          holds the barrier of the live generation, and the grace applies to it.

        Args:
            files (LocalFiles): The generation that was published
            listing (ObjectListing): The mirror as it was before the writes
            write_barrier (bool): True when this run switched the version

        Returns:
            DeletePlan: The keys to delete, empty when the grace is not over
        """
        if write_barrier:
            logger.debug(
                f'[{self.remote_name}] {self.channel}: the barrier was written by this '
                f'run, the previous generation is retired by the next one')
            return DeletePlan()
        try:
            return build_delete_plan(
                files.manifest.files, listing, self.grace, listing.server_date,
                self.store.CAPABILITIES.dirs)
        except SkipRetire as e:
            # a mirror without the barrier of its manifest is not one this run
            # may delete from, and it is not a failure of the publication
            logger.warning(f'[{self.remote_name}] {self.channel}: no retire, {e}')
            return DeletePlan()

    async def _retire(self, retire, listing):
        """
        Delete the keys of the plan, a few at a time, keeping the run alive.

        A failed delete is logged and counted out: the key stays, the next run
        finds it again, and the other keys are still removed. The publication
        itself already succeeded, so a delete never fails the pair.

        Args:
            retire (DeletePlan): The keys to delete
            listing (ObjectListing): The listing the plan was built on, for the
                size of each key

        Returns:
            int: Number of keys that were deleted
        """
        if not retire.keys:
            return 0

        async def one(relative):
            self._step(
                'DELETE', relative, pretty_size(listing.objects[relative].size))
            try:
                await self.store.delete(relative)
            except Exception as e:  # noqa: BLE001 - one key must not stop the rest
                logger.warning(
                    f'[{self.remote_name}] {self.channel}: cannot delete "{relative}": {e}')
                return 0
            return 1

        results = await self._run_all(retire.keys, one)
        if retire.folders:
            logger.debug(
                f'[{self.remote_name}] {self.channel}: '
                f'remove {len(retire.folders)} empty folder(s)')
            try:
                await self.store.remove_dirs(retire.folders)
            except Exception as e:  # noqa: BLE001 - an empty folder is cosmetic
                logger.warning(
                    f'[{self.remote_name}] {self.channel}: '
                    f'cannot remove {len(retire.folders)} empty folder(s): {e}')
        return sum(results)

    async def _run_all(self, items, call):
        """
        Run one coroutine per item, a few at a time, and collect their results.

        Args:
            items (Iterable): The items, in the order the run reports them
            call (Callable): Coroutine function (item) -> result; a failure it
                raises is a failure of the run

        Returns:
            list: The results, in the order of the items
        """
        if self.concurrency == 1:
            # one at a time, in the order of the plan: the progress lines of a
            # serial run follow the plan, not the scheduler
            return [await call(item) for item in items]
        limit = trio.Semaphore(self.concurrency)
        found = {}

        async def one(item):
            async with limit:
                found[item] = await call(item)

        try:
            async with trio.open_nursery() as nursery:
                for item in items:
                    nursery.start_soon(one, item)
        except BaseExceptionGroup as group:
            raise first_failure(group) from None
        return [found[item] for item in items]

    def _skipped(self, error):
        """
        Log a refused generation and build its result.

        Args:
            error (SkipChannel): Why the workspace was refused

        Returns:
            UploadResult: The skipped result
        """
        logger.info(f'[{self.remote_name}] {self.channel}: skipped, {error}')
        return UploadResult(
            channel=self.channel, remote=self.remote_name, status=STATUS_SKIPPED,
            reason=str(error))

    async def _gather(self, manifest):
        """
        Read the local generation and ask the mirror at the same time.

        The two gatherers never wait for each other: one awaits the digests of
        the generation (worker threads, shared by every mirror of the channel),
        the other sends the one request that reports the whole workspace (a LIST
        for COS). They only meet at the plan, so a generation of several hundred
        megabytes of wheels is digested while the mirror is still answering the
        listing.

        A local file that is missing cancels the request in flight: the result
        is the skip a serial run would report, not a failed upload.

        Args:
            manifest (Manifest): The published contract, for the size of the
                query in the log

        Returns:
            tuple[LocalFiles, ObjectListing]: (the generation, the mirror)

        Raises:
            SkipChannel: If a published file is missing on the disk
            StoreError: If the mirror cannot be asked
        """
        found = {}

        async def gather_local():
            found['files'] = await self.local.files

        async def gather_remote():
            logger.debug(
                f'[{self.remote_name}] {self.channel}: LIST the workspace '
                f'({len(manifest.files)} published file(s))')
            found['listing'] = await self.store.list()

        try:
            async with trio.open_nursery() as nursery:
                nursery.start_soon(gather_local)
                nursery.start_soon(gather_remote)
        except BaseExceptionGroup as group:
            skip = find_skip(group)
            if skip is not None:
                raise skip
            raise first_failure(group) from None
        return found['files'], found['listing']

    async def _upload_content(self, items):
        """
        Write the content files, a few at a time.

        The barrier and the manifest are not here: they are written once every
        content file has landed, so a mirror that is interrupted before that
        keeps advertising the previous generation.

        Args:
            items (List[UploadItem]): Files to write

        Raises:
            PublishError: If a file cannot be written or does not verify
        """
        if not items:
            return
        if self.concurrency == 1:
            # one at a time, in the order of the manifest
            for item in items:
                await self._put(item, KIND_CONTENT)
            return
        limit = trio.Semaphore(self.concurrency)

        async def one(item):
            async with limit:
                await self._put(item, KIND_CONTENT)

        # a failing put cancels its siblings; the ones already in flight finish,
        # which is harmless: every object of the generation is written even if
        # the generation never becomes current
        try:
            async with trio.open_nursery() as nursery:
                for item in items:
                    nursery.start_soon(one, item)
        except BaseExceptionGroup as group:
            # a nursery wraps the failures of its children: the caller of the
            # uploader expects the failure of one file, not a group of them
            raise first_failure(group) from None

    async def _put(self, item, kind):
        """
        Write one file and verify the answer of the mirror.

        Args:
            item (UploadItem): The file to write
            kind (str): KIND_CONTENT / KIND_BARRIER / KIND_MANIFEST

        Raises:
            PublishError: If the write fails or the object does not verify
        """
        detail = pretty_size(item.size)
        if kind != KIND_CONTENT:
            detail += f', {kind}'
        self._step('PUT', item.relative, detail)
        info = await self.store.put(
            item.relative,
            item.file,
            item.size,
            item.md5,
            content_type=content_type(item.relative),
            cache_control=cache_control(item.relative),
        )
        if not await self._verify(info, item):
            raise PublishError(
                f'[{self.remote_name}] {self.channel}: {item.relative} was written but '
                f'the mirror reports size={info.size} etag={info.etag}, '
                f'expected size={item.size} md5={item.md5}'
            )

    def _log_plan(self, split, barrier_item, barrier_outcome, manifest_item, manifest_outcome):
        """
        Print the plan of a dry run, one line per file.

        Args:
            split (UploadPlan): What the plan decided on the content files
            barrier_item (UploadItem): The barrier
            barrier_outcome (str): COMPARE_* of the barrier
            manifest_item (UploadItem): The contract
            manifest_outcome (str): COMPARE_* of the contract
        """
        for item in split.uploads:
            logger.info(f'  PUT  {item.relative:<44} {pretty_size(item.size)}')
        for item in split.same:
            logger.info(f'  SKIP {item.relative:<44} (unchanged)')
        for item in split.unverified:
            logger.info(f'  SKIP {item.relative:<44} (unverified, same size)')
        for item, outcome in ((barrier_item, barrier_outcome),
                              (manifest_item, manifest_outcome)):
            if outcome == COMPARE_UPLOAD:
                logger.info(f'  PUT  {item.relative:<44} {pretty_size(item.size)}')
            elif outcome == COMPARE_SAME:
                logger.info(f'  SKIP {item.relative:<44} (unchanged)')
            else:
                logger.info(f'  SKIP {item.relative:<44} (unverified, same size)')
        writes = len(split.uploads) + sum(
            1 for outcome in (barrier_outcome, manifest_outcome) if outcome == COMPARE_UPLOAD)
        logger.info(
            f'  plan: {writes} PUT, {len(split.same) + len(split.unverified)} unchanged, '
            f'{len(split.unverified)} unverified, '
            f'{pretty_size(sum(item.size for item in split.uploads))}')
