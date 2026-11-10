"""
The publication of one generation to one remote: resolve, write, verify, retire.

The input is the record of the generator, not a directory listing: the published
contract (`manifest.json`) names the files a mirror serves, and the barrier
(`packrepo/latest.pack`) names the version a client switches to. The local side
of that record is read by `LocalGeneration` (see local.py), shared by every
mirror of the channel; this module only reads the remote.

Every file is a pipeline of its own: what the mirror says about one key decides
that key alone, so the files run concurrently and none of them waits for a
decision about another. The one listing of the run is a shortcut, not a phase:
a key it reports is decided without a request, a key it does not report is
asked about directly.

The order is the contract of a mirror:

1. the content files of the manifest, a few at a time;
2. the barrier, `packrepo/latest.pack`;
3. the contract, `manifest.json`;
4. the keys the new generation no longer serves.

A client that reads the manifest therefore finds a complete generation, and the
retire step runs only once the whole generation landed: a mirror that fails
before step 3 keeps serving the previous generation, which is complete.

What proves a file is what the mirror reports about the stored object: the MD5
of the content as its ETag, or a checksum a HEAD reports. Nothing is ever
downloaded to find out whether it is the right file, and a store that can prove
neither is refused rather than published to.

Every request carries a numbered line, `PUT 3/12 wheel/x.whl 1.18MB`, so a long
run reads as a progress report instead of a silence that ends in a summary.
"""

import msgspec
import trio

try:
    BaseExceptionGroup
except NameError:  # python 3.8 / 3.9
    from exceptiongroup import BaseExceptionGroup

from alasio.base.pretty import pretty_size
from alasio.deploy_dev.pack_server.model import ConfigError
from alasio.deploy_dev.pack_server.publish.common import (
    BARRIER_KEY, MANIFEST_KEY, MAX_CONCURRENCY, MD5_HEX, IncompleteStore, PublishError, SkipChannel, cache_control,
    content_type, find_skip, first_failure, require_capabilities
)
from alasio.deploy_dev.pack_server.publish.retire import DeletePlan, SkipRetire, build_delete_plan
from alasio.deploy_dev.store import BucketNotFound, ObjectNotFound
from alasio.ext.cache import cached_property_async_trio_lock
from alasio.logger import logger

# the status of one channel/remote pair, the value of UploadResult.status
STATUS_OK = 'ok'
STATUS_DRY_RUN = 'dry-run'
STATUS_SKIPPED = 'skipped'
STATUS_FAILED = 'failed'

# the kind of a written file, for the log
KIND_CONTENT = 'content'
KIND_BARRIER = 'barrier'
KIND_MANIFEST = 'manifest'

# which answer of the mirror a RemoteState came from
SOURCE_LIST = 'list'
SOURCE_HEAD = 'head'
SOURCE_ABSENT = 'absent'

# the outcome of comparing one local file with what the mirror reports
COMPARE_UPLOAD = 'upload'
COMPARE_SAME = 'same'


class UploadResult(msgspec.Struct):
    """
    What happened to one channel on one remote.

    Attributes:
        channel (str): Name of the workspace
        remote (str): Name of the remote
        status (str): STATUS_OK / STATUS_DRY_RUN / STATUS_SKIPPED / STATUS_FAILED
        uploaded (int): Files written in this run
        skipped (int): Files that were already on the mirror
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


class RemoteState(msgspec.Struct):
    """
    What the mirror reports about one key, and what that proves.

    Attributes:
        info (ObjectInfo): The state of the object, None when the key is absent
        md5 (str): Content MD5 the answer proves, '' when it proves none
        source (str): SOURCE_LIST / SOURCE_HEAD / SOURCE_ABSENT, which answer
            it was
    """

    info: "ObjectInfo" = None
    md5: str = ''
    source: str = SOURCE_LIST


def compare(item, state):
    """
    Whether the local file has to be written again.

    The size alone never proves a file: an object of the right length can hold
    other bytes. A file is the same only when the mirror reports a digest that
    equals its MD5.

    Args:
        item (UploadItem): The local file
        state (RemoteState): What the mirror reports about its key

    Returns:
        str: COMPARE_SAME / COMPARE_UPLOAD
    """
    if state.info is None or state.info.size != item.size:
        return COMPARE_UPLOAD
    if state.md5 and state.md5 == item.md5:
        return COMPARE_SAME
    return COMPARE_UPLOAD


class ChannelUpload:
    """
    One workspace on one remote: resolve, write, verify, retire.

    The instance owns the store bound to the channel and the local generation
    shared by every mirror of it: it does no local IO of its own, it awaits the
    files of `LocalGeneration`, so three mirrors of one channel read and digest
    the generation once.

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
            concurrency (int): Requests in flight at once, 1 to MAX_CONCURRENCY
        """
        self.local = local
        self.store = store
        self.remote_name = remote_name
        self.grace = max(0, int(grace))
        # the cap is a property of the tool, not of the config: more requests in
        # flight than this only move the queue into the connection pool
        self.concurrency = max(1, min(int(concurrency), MAX_CONCURRENCY))
        # every remote operation of the run, the transfers and the lookups
        # alike, comes out of this one budget
        self.remote_limit = trio.Semaphore(self.concurrency)
        # the progress report: entries announced, the denominator of every line
        self.total = 0

    @property
    def channel(self):
        """
        Returns:
            str: Name of the workspace
        """
        return self.local.workspace.name

    def _where(self):
        """
        Returns:
            str: The pair a message is about, e.g. '[cos-main] LmeSzinc_Repo_master'
        """
        return f'[{self.remote_name}] {self.channel}'

    def _step(self, action, relative, detail='', number=None):
        """
        Log one request or one decision, as a line of a progress report.

        A request is logged before it is sent: a transfer of hundreds of
        megabytes shows up while it is in flight instead of after it, and a run
        that fails still shows what it was doing. The numbering is against the
        count the run announced, so the last entry of a run reads `n/n`.

        Args:
            action (str): 'SAME', 'PUT', 'HEAD' or 'DELETE'
            relative (str): Key of the object
            detail (str): What the line says after the key, e.g. the size.
                Defaults to ''
            number (int): The entry this line belongs to, None for a request
                outside the count, e.g. a HEAD of the barrier before the total
                is known. Defaults to None
        """
        if number is None:
            head = action
        else:
            head = f'{action} {number}/{self.total}'
        suffix = f'  {detail}' if detail else ''
        logger.info(f'  {head} {relative:<44}{suffix}')

    def _etag_md5(self, info):
        """
        The content MD5 an ETag proves, '' when it proves none.

        A store declares that the ETag of a single PUT is the MD5 of the
        content; a bucket switched to a KMS key keeps answering with an ETag
        that is not one, so the shape is checked as well.

        Args:
            info (ObjectInfo): What the mirror reports, None for an absent key

        Returns:
            str: The ETag when it is a usable MD5, '' otherwise
        """
        if info is None or not self.store.CAPABILITIES.etag_md5:
            return ''
        return info.etag if MD5_HEX.match(info.etag) else ''

    @cached_property_async_trio_lock
    async def snapshot(self):
        """
        The mirror as it was at the start of the run: one listing of the workspace.

        Taken once per instance and shared by every lookup: the key that is in
        it is decided from its values, the key that is not is absent from the
        mirror (the lock of the channel keeps another writer out of the run).
        The retire plan reads the same listing, so the generations a run writes
        and retires are judged by one view of the mirror.

        Returns:
            ObjectListing: The objects and the clock of the server

        Raises:
            ConfigError: If the bucket of the remote does not exist: a remote
                that has to be configured again, not a mirror that is down
            PublishError: If the store cannot list the workspace. A store that
                cannot list is not published to file by file: the failure is
                the credential, the prefix or the network, and one request per
                key would only make it slower and noisier
        """
        try:
            return await self.store.list()
        except BucketNotFound as e:
            raise ConfigError(f'{self._where()}: {e}') from None
        except Exception as e:  # noqa: BLE001 - one pair must not stop the run
            raise PublishError(
                f'{self._where()}: cannot list the workspace: {e}') from None

    async def _resolve(self, item, number=None):
        """
        What the mirror reports about one key, with the cheapest answer first.

        The ladder, and each step is only taken when the one above cannot
        decide:

        1. the listing of the run: one request for the whole workspace, taken
           once, and enough for every key whose ETag is an MD5. It is also how a
           key that is not on the mirror is known to be absent, so a first
           publication costs one listing and one PUT per file, no lookup;
        2. a HEAD of the key: the same values for one object, when the listing
           holds the key but cannot prove it;
        3. nothing: a mirror that reports no comparable digest cannot prove the
           file, and the run fails instead of guessing.

        Args:
            item (UploadItem): The local file
            number (int): The entry this lookup belongs to, for the log

        Returns:
            RemoteState: What the mirror reports and what it proves

        Raises:
            IncompleteStore: If the object has the size of the file and the
                mirror reports no digest that can be compared
        """
        info = (await self.snapshot).objects.get(item.relative)
        if info is None:
            # the listing is the whole workspace at the start of the run, and
            # the lock of the channel keeps another writer out of it: a key it
            # does not hold is not on the mirror
            return RemoteState(info=None, md5='', source=SOURCE_ABSENT)
        md5 = self._etag_md5(info)
        # a different size is a decision of its own: the file has to be
        # written, and a HEAD would answer nothing new
        if md5 or info.size != item.size:
            return RemoteState(info=info, md5=md5, source=SOURCE_LIST)
        try:
            async with self.remote_limit:
                self._step('HEAD', item.relative, 'the stored object', number)
                head = await self.store.head(item.relative)
        except ObjectNotFound:
            # the key went away between the listing and the lookup, or the
            # listing named an object the store no longer holds
            return RemoteState(info=None, md5='', source=SOURCE_ABSENT)
        md5 = self._etag_md5(head)
        if md5 or head.size != item.size:
            return RemoteState(info=head, md5=md5, source=SOURCE_HEAD)
        raise IncompleteStore(
            f'{self._where()}: {item.relative} is on the mirror with the size of the local '
            f'file but no digest that can be compared (etag="{head.etag}"), so the store '
            f'cannot prove which of the two is the current one'
        )

    async def _verify(self, info, item, number):
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
        3. neither: the store cannot show that the write landed.

        The size alone is never accepted: an object of the right length can
        hold other bytes.

        Args:
            info (ObjectInfo): What the mirror reports about the written object
            item (UploadItem): The local file
            number (int): The entry of the progress report, for the HEAD line

        Returns:
            bool: True when the object is the local file

        Raises:
            IncompleteStore: If the mirror reports no digest of either kind
            StoreError: If the HEAD fails
        """
        capabilities = self.store.CAPABILITIES
        if self._etag_md5(info):
            return info.etag == item.md5
        if capabilities.head_checksum and info.checksum:
            async with self.remote_limit:
                self._step('HEAD', item.relative, 'the checksum of the stored object', number)
                head = await self.store.head(item.relative)
            if not head.checksum:
                raise IncompleteStore(
                    f'{self._where()}: {item.relative} was written but the HEAD of it '
                    f'reports no checksum, although the store promises one'
                )
            return head.checksum == info.checksum
        raise IncompleteStore(
            f'{self._where()}: {item.relative} was written but neither its ETag '
            f'("{info.etag}") nor a HEAD checksum proves it, so the store cannot show '
            f'that the write landed'
        )

    async def _put(self, item, kind, number):
        """
        Write one file and verify the answer of the mirror.

        Args:
            item (UploadItem): The file to write
            kind (str): KIND_CONTENT / KIND_BARRIER / KIND_MANIFEST
            number (int): The entry of the progress report

        Raises:
            PublishError: If the write fails or the object does not verify
        """
        detail = pretty_size(item.size)
        if kind != KIND_CONTENT:
            detail += f', {kind}'
        async with self.remote_limit:
            self._step('PUT', item.relative, detail, number)
            info = await self.store.put(
                item.relative, item.file, item.size, item.md5,
                content_type=content_type(item.relative),
                cache_control=cache_control(item.relative),
            )
        if not await self._verify(info, item, number):
            raise PublishError(
                f'{self._where()}: {item.relative} was written but the mirror reports '
                f'size={info.size} etag={info.etag}, expected size={item.size} md5={item.md5}'
            )

    async def _sync(self, relatives, dry_run=False):
        """
        Publish the content files of the generation, a few at a time.

        Each file runs its own pipeline — resolve its key, compare, write it if
        it differs — so a key the listing already proves costs no request at
        all and a key it cannot prove is asked about while the others are being
        written. The entry of each file in the progress report is its position
        in this queue, so a number belongs to a file rather than to a moment of
        the schedule.

        Args:
            relatives (List[str]): Keys of the content files, in manifest order
            dry_run (bool): Report the decisions and write nothing. Defaults to
                False

        Returns:
            list[tuple[str, UploadItem]]: (outcome, file) per key, in order

        Raises:
            SkipChannel: If a local file cannot be read
            PublishError: If a write fails or does not verify
        """

        found = {}

        async def one(number, relative):
            item = await self.local.item(relative)
            outcome = compare(item, await self._resolve(item, number))
            detail = pretty_size(item.size)
            if outcome == COMPARE_SAME:
                self._step('SAME', item.relative, detail, number)
            elif dry_run:
                self._step('PUT', item.relative, detail, number)
            else:
                await self._put(item, KIND_CONTENT, number)
            found[relative] = (outcome, item)

        if self.concurrency == 1:
            # one at a time, in the order of the manifest: the progress lines of
            # a serial run follow the manifest, not the scheduler
            for number, relative in enumerate(relatives, 1):
                await one(number, relative)
        else:
            try:
                async with trio.open_nursery() as nursery:
                    for number, relative in enumerate(relatives, 1):
                        nursery.start_soon(one, number, relative)
            except BaseExceptionGroup as group:
                raise first_failure(group) from None
        return [found[relative] for relative in relatives]

    def _skipped(self, error):
        """
        Log a refused generation and build its result.

        Args:
            error (SkipChannel): Why the workspace was refused

        Returns:
            UploadResult: The skipped result
        """
        logger.info(f'{self._where()}: skipped, {error}')
        return UploadResult(
            channel=self.channel, remote=self.remote_name, status=STATUS_SKIPPED,
            reason=str(error))

    async def run(self, dry_run=False):
        """
        Publish the generation of the workspace to the remote, then retire.

        Args:
            dry_run (bool): Report the decisions and write nothing. Defaults to
                False

        Returns:
            UploadResult: What was written, retired, or why the run was refused

        Raises:
            IncompleteStore: If the store cannot prove the objects it holds
            PublishError: If a request fails
        """
        require_capabilities(self.store.CAPABILITIES, self._where())
        # a run takes its own view of the mirror: a second run of the same
        # instance decides on a fresh listing, not on the one before its writes
        cached_property_async_trio_lock.pop(self, 'snapshot')
        try:
            manifest = await self.local.record
        except SkipChannel as e:
            return self._skipped(e)
        try:
            return await self._publish(manifest, dry_run)
        except SkipChannel as e:
            # a serial run reaches the unreadable file without a nursery, so the
            # skip arrives as it is
            return self._skipped(e)
        except BaseExceptionGroup as group:
            # a concurrent nursery wraps every failure of its children, even a
            # single one
            skip = find_skip(group)
            if skip is not None:
                return self._skipped(skip)
            raise first_failure(group) from None

    async def _publish(self, manifest, dry_run):
        """
        The body of a run, with the generation warmed up around it.

        `LocalGeneration.warmup()` validates the record before this body starts
        and reads the content while it runs, so the first request of the run and
        the first read of a content file happen at the same time.

        Args:
            manifest (Manifest): The published contract
            dry_run (bool): Report the decisions and write nothing

        Returns:
            UploadResult: The result of the pair
        """
        async with self.local.warmup():
            listing = await self.snapshot

            # the barrier is decided before the content starts: whether this run
            # switches the version decides whether anything is retired, and the
            # total of the progress report follows from both. Both items are
            # already read when the warmup block is entered.
            barrier_item = await self.local.item(BARRIER_KEY)
            contract_item = await self.local.item(MANIFEST_KEY)
            barrier_outcome = compare(barrier_item, await self._resolve(barrier_item))
            contract_outcome = compare(contract_item, await self._resolve(contract_item))
            write_barrier = barrier_outcome == COMPARE_UPLOAD
            write_contract = contract_outcome == COMPARE_UPLOAD

            content = [relative for relative in manifest.files if relative != BARRIER_KEY]
            retire = self._retire_plan(manifest, listing, write_barrier)
            # the content files take the first numbers, then the barrier, the
            # contract and the keys of the previous generation
            self.total = len(manifest.files) + 1 + len(retire.keys)
            barrier_number = len(manifest.files)
            contract_number = barrier_number + 1

            rows = await self._sync(content, dry_run=dry_run)
            uploaded = [item for outcome, item in rows if outcome == COMPARE_UPLOAD]
            # every entry of the report that is not written is unchanged: the
            # content files, the barrier and the contract alike
            unchanged = (len(rows) - len(uploaded)
                         + int(not write_barrier) + int(not write_contract))
            size = sum(item.size for item in uploaded)
            size += barrier_item.size if write_barrier else 0
            size += contract_item.size if write_contract else 0
            to_write = len(uploaded) + int(write_barrier) + int(write_contract)
            logger.info(
                f'{self._where()}: version={manifest.version} '
                f'{to_write} to upload ({pretty_size(size)}), {unchanged} unchanged, '
                f'{len(retire.keys)} to retire ({pretty_size(retire.size)})')

            # the barrier and the contract land only once every content file did
            for item, outcome, kind, number in (
                (barrier_item, barrier_outcome, KIND_BARRIER, barrier_number),
                (contract_item, contract_outcome, KIND_MANIFEST, contract_number),
            ):
                detail = f'{pretty_size(item.size)}, {kind}'
                if outcome == COMPARE_SAME:
                    self._step('SAME', item.relative, detail, number)
                elif dry_run:
                    self._step('PUT', item.relative, detail, number)
                else:
                    await self._put(item, kind, number)

            deleted_keys = await self._retire(
                retire, contract_number + 1, listing, dry_run)
            # the bytes of the keys that are gone, not of the ones the plan
            # announced: a delete that failed keeps its key, and a report that
            # counted it would tell an operator that space was freed that the
            # mirror still holds
            deleted_size = sum(
                listing.objects[relative].size for relative in deleted_keys)
            return UploadResult(
                channel=self.channel, remote=self.remote_name,
                status=STATUS_DRY_RUN if dry_run else STATUS_OK,
                uploaded=to_write, skipped=unchanged, size=size,
                deleted=len(deleted_keys), deleted_size=deleted_size,
            )

    def _retire_plan(self, manifest, listing, write_barrier):
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
            manifest (Manifest): The generation that was published
            listing (ObjectListing): The mirror as it was before the writes
            write_barrier (bool): True when this run switched the version

        Returns:
            DeletePlan: The keys to delete, empty when the grace is not over
        """
        if write_barrier:
            logger.debug(
                f'{self._where()}: the barrier was written by this run, '
                f'the previous generation is retired by the next one')
            return DeletePlan()
        try:
            return build_delete_plan(
                manifest.files, listing, self.grace, listing.server_date,
                self.store.CAPABILITIES.dirs)
        except SkipRetire as e:
            # a mirror without the barrier of its manifest is not one this run
            # may delete from, and it is not a failure of the publication
            logger.warning(f'{self._where()}: no retire, {e}')
            return DeletePlan()

    async def _retire(self, retire, first_number, listing, dry_run=False):
        """
        Delete the keys of the plan, a few at a time, keeping the run alive.

        A failed delete is logged and counted out: the key stays, the next run
        finds it again, and the other keys are still removed. The publication
        itself already succeeded, so a delete never fails the pair.

        Args:
            retire (DeletePlan): The keys to delete
            first_number (int): The entry of the first key in the report
            listing (ObjectListing): The listing the plan was built on, for the
                size of each key
            dry_run (bool): Report the keys and delete none. Defaults to False

        Returns:
            List[str]: The keys that are gone, in the order of the plan. A dry
                run returns the keys of the plan: nothing was attempted, so the
                plan is the honest answer
        """
        if not retire.keys:
            return []

        async def one(number, relative):
            detail = pretty_size(listing.objects[relative].size)
            if dry_run:
                self._step('WOULD DELETE', relative, detail, number)
                return relative
            try:
                async with self.remote_limit:
                    self._step('DELETE', relative, detail, number)
                    await self.store.delete(relative)
            except Exception as e:  # noqa: BLE001 - one key must not stop the rest
                logger.warning(f'{self._where()}: cannot delete "{relative}": {e}')
                return None
            return relative

        # a failed delete is caught here, so nothing in the nursery raises and
        # the run is never cancelled by one key
        if self.concurrency == 1:
            results = [
                await one(number, relative)
                for number, relative in enumerate(retire.keys, first_number)
            ]
        else:
            results = {}

            async def task(number, relative):
                results[relative] = await one(number, relative)

            async with trio.open_nursery() as nursery:
                for number, relative in enumerate(retire.keys, first_number):
                    nursery.start_soon(task, number, relative)
            results = [results[relative] for relative in retire.keys]

        if retire.folders:
            logger.debug(
                f'{self._where()}: remove {len(retire.folders)} empty folder(s)')
            try:
                await self.store.remove_dirs(retire.folders)
            except Exception as e:  # noqa: BLE001 - an empty folder is cosmetic
                logger.warning(
                    f'{self._where()}: cannot remove {len(retire.folders)} empty '
                    f'folder(s): {e}')
        return [relative for relative in results if relative is not None]
