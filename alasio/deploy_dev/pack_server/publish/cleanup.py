"""
The cleaner: remove what a later generation of a mirror no longer serves.

The cleaner reads the mirror, never the local tree, and it reads in one order:

    manifest.json -> packrepo/latest.pack -> list the workspace

The order is the safety argument. The two reads must name one generation, or the
run is abandoned; the listing then supplies the clock the age is measured with,
and the barrier of THAT listing supplies the age of the generation. A generation
that was switched a minute ago is therefore never deleted, whatever the age of
the files it replaced.

The keep set is the manifest the cleaner just read, plus `manifest.json` itself.
A key the manifest does not list is deleted once BOTH the barrier of this remote
has been in place for the grace, and the key itself is older than the grace: the
first condition gives every client the full window to notice the new version, the
second protects the leftovers of an interrupted upload.
"""


import msgspec
import trio

try:
    BaseExceptionGroup
except NameError:  # python 3.8 / 3.9
    from exceptiongroup import BaseExceptionGroup

from alasio.base.pretty import pretty_size
from alasio.deploy.pack.server_file import LatestInfo
from alasio.deploy_dev.pack_server.manifest import Manifest
from alasio.deploy_dev.pack_server.model import ConfigError, require_valid
from alasio.deploy_dev.pack_server.publish.common import (
    BARRIER_KEY, MANIFEST_KEY, MAX_CONCURRENCY, PublishError, PublishRunError, find_skip, first_failure
)
from alasio.deploy_dev.pack_server.publish.drivers import create_store
from alasio.deploy_dev.pack_server.publish.retire import SkipRetire as SkipCleanup, build_delete_plan
from alasio.deploy_dev.store import BucketNotFound, ObjectNotFound, StoreError
from alasio.ext.path.validate import validate_filepath
from alasio.logger import logger
from alasio.logger.utils import stringify_event

# the status of one channel/remote pair, the value of CleanupResult.status
STATUS_OK = 'ok'
STATUS_DRY_RUN = 'dry-run'
STATUS_SKIPPED = 'skipped'
STATUS_FAILED = 'failed'


class CleanupResult(msgspec.Struct):
    """
    What happened to one channel on one remote.

    Attributes:
        channel (str): Name of the workspace
        remote (str): Name of the remote
        status (str): STATUS_OK / STATUS_DRY_RUN / STATUS_SKIPPED / STATUS_FAILED
        deleted (int): Keys deleted in this run
        size (int): Bytes the deleted keys held
        kept (int): Keys the manifest keeps
        reason (str): Why the pair was skipped or failed, '' otherwise
    """

    channel: str
    remote: str
    status: str
    deleted: int = 0
    size: int = 0
    kept: int = 0
    reason: str = ''

    @property
    def ok(self):
        """
        Returns:
            bool: True when nothing failed
        """
        return self.status != STATUS_FAILED


def read_generation(workspace_name, store, manifest_data, barrier_data):
    """
    Check that the two files of a mirror name one generation of this workspace.

    Args:
        workspace_name (str): Name of the workspace the store is bound to
        store (ObjectStore): The store, for the message only
        manifest_data (bytes): Content of the manifest read from the mirror
        barrier_data (bytes): Content of the barrier read from the mirror

    Returns:
        Manifest: The published contract of the mirror

    Raises:
        SkipCleanup: If the two files do not name one usable generation
    """
    try:
        manifest = Manifest.decode(manifest_data)
    except msgspec.DecodeError as e:
        raise SkipCleanup(f'{MANIFEST_KEY} is not a manifest: {e}') from None
    try:
        barrier = LatestInfo.parse(barrier_data)
    except Exception as e:  # noqa: BLE001 - PackDecodeError and friends
        raise SkipCleanup(f'{BARRIER_KEY} is not readable: {e}') from None

    if manifest.channel != workspace_name:
        raise SkipCleanup(
            f'{MANIFEST_KEY} names channel "{manifest.channel}", '
            f'the workspace is "{workspace_name}"')
    if (manifest.version, manifest.checksum) != (barrier.version, barrier.checksum):
        raise SkipCleanup(
            f'{MANIFEST_KEY} is version {manifest.version}, '
            f'{BARRIER_KEY} is {barrier.version}: a switch is in progress')
    if BARRIER_KEY not in manifest.files:
        raise SkipCleanup(f'{BARRIER_KEY} is not one of the published files')
    seen = set()
    for relative in manifest.files:
        if relative in seen:
            raise SkipCleanup(f'{MANIFEST_KEY} lists "{relative}" twice')
        seen.add(relative)
        if relative == MANIFEST_KEY:
            raise SkipCleanup(f'{MANIFEST_KEY} lists itself as a published file')
        try:
            validate_filepath(relative)
        except ValueError as e:
            raise SkipCleanup(f'{MANIFEST_KEY} lists an unusable path: {e}') from None
    return manifest


class ChannelCleanup:
    """
    One workspace on one remote: read, compare, delete.

    Usage:
        cleanup = ChannelCleanup('LmeSzinc_Repo_master', store, grace=3600)
        result = await cleanup.run(dry_run=True)
    """

    def __init__(self, workspace_name, store, grace, remote_name='',
                 concurrency=MAX_CONCURRENCY):
        """
        Args:
            workspace_name (str): Name of the workspace the store is bound to
            store (ObjectStore): The store bound to the workspace
            grace (int): Retire grace in seconds
            remote_name (str): Name of the remote, for the log and the result
            concurrency (int): Deletes in flight at once, at most
                MAX_CONCURRENCY. Defaults to MAX_CONCURRENCY
        """
        self.workspace_name = workspace_name
        self.store = store
        self.grace = int(grace)
        if self.grace <= 0:
            # a grace of zero deletes every key of the previous generation the
            # moment the barrier is older than zero seconds, i.e. on the run
            # after the switch: the window that protects a client which just
            # read the old barrier is the whole point of the value. The config
            # refuses it as well; this is the same refusal for a caller that
            # builds the cleaner by hand
            raise ValueError(f'grace must be > 0 seconds, got {self.grace}')
        self.remote_name = remote_name
        self.concurrency = max(1, min(int(concurrency), MAX_CONCURRENCY))
        # the progress lines of a delete round: requests done of the requests
        # the plan announced
        self.done = 0
        self.total = 0

    async def run(self, dry_run=False):
        """
        Clean the mirror of the workspace.

        Args:
            dry_run (bool): Print the plan and delete nothing. Defaults to False

        Returns:
            CleanupResult: What was deleted, kept, or why the run was refused
        """
        try:
            manifest = await self._read_generation()
            logger.debug(
                f'[{self.remote_name}] {self.workspace_name}: LIST the workspace')
            listing = await self.store.list()
            logger.debug(
                f'[{self.remote_name}] {self.workspace_name}: '
                f'{len(listing.objects)} object(s) in the listing')
            barrier = listing.objects.get(BARRIER_KEY)
            if barrier is None:
                # the barrier the two reads above just found is not in the
                # listing made after them: the mirror changed under the run,
                # and an age computed from a missing object would decide the
                # grace of a generation that is no longer the one read
                raise SkipCleanup(f'{BARRIER_KEY} is not in the listing of the mirror')
        except SkipCleanup as e:
            logger.info(f'[{self.remote_name}] {self.workspace_name}: skipped, {e}')
            return CleanupResult(
                channel=self.workspace_name, remote=self.remote_name,
                status=STATUS_SKIPPED, reason=str(e))
        except BucketNotFound as e:
            # no object of this workspace exists, because the bucket itself does
            # not: the remote has to be configured, so the run stops instead of
            # reporting a workspace that was never published
            raise ConfigError(f'[{self.remote_name}] {self.workspace_name}: {e}') from None
        except (PublishError, StoreError) as e:
            # a store that cannot serve a read is a failed pair, not an
            # exception of the run: the mirror keeps what it has and the next
            # run asks again
            return self._failed(stringify_event(e))

        now = listing.server_date
        age = now - barrier.last_modified
        logger.info(
            f'[{self.remote_name}] {self.workspace_name}: manifest={manifest.version} '
            f'barrier={manifest.version}, barrier age={age / 3600:.1f}h, '
            f'grace={self.grace / 3600:.1f}h')

        if age < self.grace:
            logger.info(
                f'[{self.remote_name}] {self.workspace_name}: within grace '
                f'({age:.0f}s < {self.grace}s), nothing to delete')
            return CleanupResult(
                channel=self.workspace_name, remote=self.remote_name, status=STATUS_OK,
                kept=len(manifest.files), reason='within grace')

        plan = build_delete_plan(
            manifest.files, listing, self.grace, now, self.store.CAPABILITIES.dirs)
        if dry_run:
            for relative in plan.keys:
                info = listing.objects[relative]
                logger.info(
                    f'  WOULD DELETE {relative:<44} '
                    f'age={(now - info.last_modified) / 3600:.1f}h '
                    f'{pretty_size(info.size)}')
            return CleanupResult(
                channel=self.workspace_name, remote=self.remote_name,
                status=STATUS_DRY_RUN, deleted=len(plan.keys), size=plan.size,
                kept=len(manifest.files))

        deleted_keys = await self._delete(plan.keys, listing, now)
        if plan.folders:
            logger.debug(
                f'[{self.remote_name}] {self.workspace_name}: '
                f'remove {len(plan.folders)} empty folder(s)')
            try:
                await self.store.remove_dirs(plan.folders)
            except Exception as e:  # noqa: BLE001 - an empty folder is cosmetic
                logger.warning(
                    f'[{self.remote_name}] {self.workspace_name}: '
                    f'cannot remove {len(plan.folders)} empty folder(s): {e}')
        # the bytes of the keys that are gone, not of the plan: a delete that
        # failed keeps its key, and counting it would report space freed that
        # the mirror still holds
        deleted_size = sum(listing.objects[relative].size for relative in deleted_keys)
        logger.info(
            f'[{self.remote_name}] {self.workspace_name}: '
            f'deleted {len(deleted_keys)} key(s), {pretty_size(deleted_size)}')
        return CleanupResult(
            channel=self.workspace_name, remote=self.remote_name, status=STATUS_OK,
            deleted=len(deleted_keys), size=deleted_size, kept=len(manifest.files))

    async def _read_generation(self):
        """
        Read the manifest and the barrier of the mirror, then check them.

        The two reads are sent at the same time: they are independent requests
        for two small objects, and a round trip each is the whole cost of the
        step. Reading both before the listing is what makes the run safe: the
        age of the generation comes from the barrier of the listing, so a
        generation that was switched between the two reads is caught by the
        version mismatch and a switch that happens after them shows up as a
        young barrier.

        Returns:
            Manifest: The published contract of the mirror

        Raises:
            SkipCleanup: If the mirror holds no complete generation
            BucketNotFound: If the bucket of the remote does not exist, which
                is a config error and never a workspace that was not published
            PublishError: If a read fails
        """
        found = {}

        async def one(key):
            logger.debug(f'[{self.remote_name}] {self.workspace_name}: GET {key}')
            try:
                found[key] = await self.store.get(key)
            except ObjectNotFound:
                found[key] = None

        try:
            async with trio.open_nursery() as nursery:
                nursery.start_soon(one, MANIFEST_KEY)
                nursery.start_soon(one, BARRIER_KEY)
        except BaseExceptionGroup as group:
            skip = find_skip(group)
            if skip is not None:
                raise skip
            raise first_failure(group) from None
        # the contract is reported first: it is the file a mirror is read for,
        # and the order of the two concurrent reads must not decide the message
        for key in (MANIFEST_KEY, BARRIER_KEY):
            if found[key] is None:
                raise SkipCleanup(f'{key} is not on the mirror')
        return read_generation(
            self.workspace_name, self.store, found[MANIFEST_KEY], found[BARRIER_KEY])

    async def _delete(self, keys, listing, now):
        """
        Delete the keys, a few at a time, keeping the run alive on a failure.

        Each key is logged as a numbered line before its request, so a retired
        generation of a few hundred keys reads as a progress report.

        A failed delete is logged and counted out: the key stays, the next run
        finds it again, and the other keys are still removed. A failure of the
        network is not a reason to abandon a mirror half cleaned.

        The deletes are concurrent up to the cap of the tool: a retired
        generation can be a few hundred keys, and one request at a time spends
        the round trip of each on nothing.

        Args:
            keys (List[str]): Keys to delete
            listing (ObjectListing): The listing the plan was built on, for the
                age of each key
            now (float): The clock of the server

        Returns:
            List[str]: The keys that are gone, in the order of the plan
        """
        if not keys:
            return []
        deleted = []
        limit = trio.Semaphore(self.concurrency)
        self.total = len(keys)
        self.done = 0

        async def one(relative):
            async with limit:
                self.done += 1
                info = listing.objects[relative]
                logger.info(
                    f'  DELETE {self.done}/{self.total} {relative:<44} '
                    f'age={(now - info.last_modified) / 3600:.1f}h '
                    f'{pretty_size(info.size)}')
                try:
                    await self.store.delete(relative)
                except Exception as e:  # noqa: BLE001 - one key must not stop the rest
                    logger.warning(
                        f'[{self.remote_name}] {self.workspace_name}: '
                        f'cannot delete "{relative}": {e}')
                    return
                deleted.append(relative)

        if self.concurrency == 1:
            # one at a time, in the order of the plan: a serial round logs the
            # keys in a stable order instead of the order of the scheduler
            for relative in keys:
                await one(relative)
            return deleted

        async with trio.open_nursery() as nursery:
            for relative in keys:
                nursery.start_soon(one, relative)
        return deleted

    def _failed(self, reason):
        """
        Log a failure and build its result.

        Args:
            reason (str): Why it failed

        Returns:
            CleanupResult: The failed result
        """
        logger.error(f'[{self.remote_name}] {self.workspace_name}: {reason}')
        return CleanupResult(
            channel=self.workspace_name, remote=self.remote_name,
            status=STATUS_FAILED, reason=reason)


class CleanupRun:
    """
    The workspace of one repo config, on every remote it names.

    The workspace does not have to be on the local disk: the cleaner works from
    the mirror, so a run on a machine that only holds the config still retires
    the generations of the repo.

    Usage:
        config = PackRepoConfig('LmeSzinc_AzurLaneAutoScript_master.yaml')
        results = await CleanupRun(config).run(dry_run=True)
    """

    def __init__(self, config, client=None):
        """
        Args:
            config (PackRepoConfig): The config of the repo to clean
            client (httpx2.AsyncClient): Client the stores send their requests
                with, e.g. the in-process client of a test. Its lifetime belongs
                to the caller. Defaults to None: every store creates its own
        """
        self.config = config
        self.client = client

    async def run(self, remotes=None, dry_run=False):
        """
        Clean the workspace on every selected remote.

        Args:
            remotes (Iterable[str]): Remote names to clean, None for every
                remote of the config. Defaults to None
            dry_run (bool): Print the plan and delete nothing. Defaults to False

        Returns:
            list[CleanupResult]: One result per remote, in a stable order

        Raises:
            ConfigError: If a remote of the config is not usable, or a
                requested remote is not in the config
            PublishRunError: If at least one remote failed
        """
        model = self.config.data
        require_valid(self.config)
        name = model.workspace_name
        selected = self._select_remotes(model, remotes)
        results = []
        failed = []
        for remote_name in selected:
            remote = model.Publish[remote_name]
            result = await self._one(name, remote_name, remote, dry_run)
            results.append(result)
            if result.status == STATUS_FAILED:
                failed.append((result.channel, result.remote, result.reason))
        if failed:
            raise PublishRunError(failed)
        return results

    async def _one(self, name, remote_name, remote, dry_run):
        """
        Clean one workspace on one remote, catching what it raises.

        Args:
            name (str): Name of the workspace
            remote_name (str): Name of the remote
            remote (RemoteConfig): The configured remote, its grace included
            dry_run (bool): Print the plan and delete nothing

        Returns:
            CleanupResult: The result of the pair

        Raises:
            ConfigError: If the remote is not usable. It is not a failed pair:
                the run stops at once, because another attempt cannot fix a
                configuration
        """
        logger.hr(f'Clean {name} <- {remote_name}', level=2)
        store = None
        try:
            store = create_store(remote_name, remote, name, client=self.client)
            cleanup = ChannelCleanup(
                name, store, remote.GraceSeconds, remote_name=remote_name,
                concurrency=remote.Concurrency)
            return await cleanup.run(dry_run=dry_run)
        except ConfigError:
            raise
        except Exception as e:  # noqa: BLE001 - one pair must not stop the run
            logger.exception(f'[{remote_name}] {name}: cleanup failed')
            return CleanupResult(
                channel=name, remote=remote_name, status=STATUS_FAILED,
                reason=f'{type(e).__name__}: {e}')
        finally:
            if store is not None:
                await store.aclose()

    def _select_remotes(self, model, remotes):
        """
        The remotes this run cleans.

        Args:
            model (PackRepoModel): The config
            remotes (Iterable[str]): Remote names of the call, None for every
                remote of the config

        Returns:
            list[str]: Names of the remotes, sorted

        Raises:
            ConfigError: If a requested remote is not in the config
        """
        configured = model.Publish
        wanted = set(remotes) if remotes else set(configured)
        unknown = sorted(wanted - set(configured))
        if unknown:
            raise ConfigError(
                f'No remote named {", ".join(unknown)} in Publish of '
                f'"{self.config.file}"')
        return sorted(wanted)
