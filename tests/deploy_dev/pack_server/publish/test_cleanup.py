"""
The cleaner, against the fake COS service.

The tests fill the mirror with a generation through the uploader, add the
leftovers a previous generation leaves behind, and drive the cleaner over it.
The decision functions are pure and are tested on their own; the channel and the
run are tested through the service, which is the judge of what survived.
"""

import time

import pytest

from alasio.deploy_dev.pack_server.publish.cleanup import (
    STATUS_DRY_RUN, STATUS_OK, STATUS_SKIPPED, ChannelCleanup, CleanupRun, SkipCleanup, build_delete_plan
)
from alasio.deploy_dev.pack_server.publish.common import BARRIER_KEY, MANIFEST_KEY
from alasio.deploy_dev.store import ObjectInfo, ObjectListing
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack_server.publish.conftest import (
    CHANNEL, REMOTE_PREFIX, make_model, make_remote, make_store, write_config
)

# every test drives an in-process store over the fake filesystem: no port, no
# shared directory, nothing outside this process
pytestmark = [
    pytest.mark.parallel,
]

PREFIX = f'{REMOTE_PREFIX}/{CHANNEL}/'
GRACE = 3600


def listing_of(objects, now=None):
    """
    A listing of the workspace as the store reports it.

    Returns:
        ObjectListing: The objects, their last_modified as given
    """
    return ObjectListing(objects=objects, server_date=time.time() if now is None else now)


def info(size=1, age=0.0, now=None, etag='e'):
    """
    An object of a listing.

    Returns:
        ObjectInfo: The object, written `age` seconds before now
    """
    now = time.time() if now is None else now
    return ObjectInfo(size=size, etag=etag, last_modified=now - age)


class TestPlan:
    """The decision of one cleanup round."""

    def test_keeps_the_manifest_and_the_barrier(self):
        """The contract is kept although the manifest does not list it."""
        now = time.time()
        listing = listing_of({
            MANIFEST_KEY: info(now=now, age=GRACE + 1),
            BARRIER_KEY: info(now=now, age=GRACE + 1),
            'old/full.pack': info(now=now, age=GRACE + 1),
        }, now)
        plan = build_delete_plan([BARRIER_KEY], listing, GRACE, now)
        assert plan.keys == ['old/full.pack']

    def test_keeps_a_young_file(self):
        """A key younger than the grace survives: it may be a write in progress."""
        now = time.time()
        listing = listing_of({
            BARRIER_KEY: info(now=now, age=GRACE + 1),
            'old/full.pack': info(now=now, age=10),
        }, now)
        plan = build_delete_plan([BARRIER_KEY], listing, GRACE, now)
        assert plan.keys == []

    def test_keeps_a_published_file_of_any_age(self):
        """A file the manifest lists is never a candidate, whatever its age."""
        now = time.time()
        listing = listing_of({
            BARRIER_KEY: info(now=now, age=GRACE + 1),
            'packrepo/aaaaaaaa/full.pack': info(now=now, age=GRACE * 100),
        }, now)
        plan = build_delete_plan(['packrepo/aaaaaaaa/full.pack', BARRIER_KEY], listing, GRACE, now)
        assert plan.keys == []

    def test_counts_the_bytes(self):
        """The plan reports what the deletion frees."""
        now = time.time()
        listing = listing_of({
            BARRIER_KEY: info(now=now, age=GRACE + 1),
            'old/a.pack': info(size=100, now=now, age=GRACE + 1),
            'old/b.pack': info(size=20, now=now, age=GRACE + 1),
        }, now)
        plan = build_delete_plan([BARRIER_KEY], listing, GRACE, now)
        assert plan.keys == ['old/a.pack', 'old/b.pack']
        assert plan.size == 120

    def test_folders(self):
        """The collections of the deleted keys are returned, deepest first."""
        now = time.time()
        listing = listing_of({
            BARRIER_KEY: info(now=now, age=GRACE + 1),
            'packrepo/old/full.pack': info(now=now, age=GRACE + 1),
            'wheel/x/1.0/x.whl': info(now=now, age=GRACE + 1),
        }, now)
        plan = build_delete_plan([BARRIER_KEY], listing, GRACE, now, capabilities_dirs=True)
        # 'packrepo' itself is not a candidate: the barrier lives in it, so the
        # collection that holds a kept file survives
        assert plan.folders == [
            'wheel/x/1.0',
            'packrepo/old', 'wheel/x',
            'wheel',
        ]

    def test_a_folder_of_a_kept_file_is_not_returned(self):
        """A collection that still holds a published file stays."""
        now = time.time()
        listing = listing_of({
            BARRIER_KEY: info(now=now, age=GRACE + 1),
            'packrepo/aaaaaaaa/full.pack': info(now=now, age=GRACE + 1),
            'packrepo/aaaaaaaa/update_1.pack': info(now=now, age=GRACE + 1),
        }, now)
        plan = build_delete_plan(
            ['packrepo/aaaaaaaa/full.pack', BARRIER_KEY], listing, GRACE, now,
            capabilities_dirs=True)
        assert 'packrepo/aaaaaaaa' not in plan.folders

    def test_missing_barrier(self):
        """A listing without the barrier is not a listing the cleaner may act on."""
        now = time.time()
        listing = listing_of({'old/full.pack': info(now=now, age=GRACE + 1)}, now)
        with pytest.raises(SkipCleanup):
            build_delete_plan([BARRIER_KEY], listing, GRACE, now)


@pytest.mark.trio
async def fill(workspace, service, remote=None):
    """
    Publish the generation of the workspace to the service.

    Args:
        workspace (Generation): The workspace
        service (FakeCos): The service
        remote (CosRemote): The remote, the one of the tests when None

    Returns:
        CosStore: The store, closed by the caller
    """
    from alasio.deploy_dev.pack_server.publish.channel import ChannelUpload
    from alasio.deploy_dev.pack_server.publish.local import LocalGeneration
    remote = remote or make_remote()
    store = make_store(remote, CHANNEL, service)
    local = LocalGeneration(workspace.workspace, concurrency=1)
    upload = ChannelUpload(local, store, remote_name='cos-main')
    await upload.run()
    return store


def age_objects(service, seconds, keys=None):
    """
    Move the objects of the workspace into the past.

    The cleaner decides the age of a generation from the LastModified of its
    barrier, so a test that wants a generation to be old backdates what the
    service reports instead of waiting an hour.

    Args:
        service (FakeCos): The service
        seconds (float): Seconds to subtract from every last_modified
        keys (Iterable[str]): Only these keys, every key of the prefix by default
    """
    wanted = keys if keys is not None else service.objects
    for key in wanted:
        if key.startswith(PREFIX):
            service.objects[key].last_modified -= seconds


def cleanup_of(service, store, grace=GRACE, concurrency=1):
    """
    A ChannelCleanup of the workspace of the test.

    The concurrency is 1 by default so the order of a round is the order of the
    plan: a test can then assert the deletes in the order they are logged.

    Returns:
        ChannelCleanup: The cleanup, ready to run
    """
    return ChannelCleanup(
        CHANNEL, store, grace, remote_name='cos-main', concurrency=concurrency)


class TestGuards:
    """A mirror that does not hold one complete generation is left alone."""

    @pytest.mark.trio
    async def test_missing_manifest(self, workspace, credentials_env):
        """Without the contract there is no keep set to compute."""
        store = make_store(make_remote(), CHANNEL, credentials_env)
        try:
            result = await cleanup_of(credentials_env, store).run()
        finally:
            await store.aclose()
        assert result.status == STATUS_SKIPPED
        assert 'manifest.json' in result.reason

    @pytest.mark.trio
    async def test_missing_barrier(self, workspace, credentials_env):
        """Without the barrier the age of the generation is unknown."""
        store = await fill(workspace, credentials_env)
        try:
            credentials_env.remove(f'{PREFIX}{BARRIER_KEY}')
            result = await cleanup_of(credentials_env, store).run()
        finally:
            await store.aclose()
        assert result.status == STATUS_SKIPPED
        assert 'latest.pack' in result.reason

    @pytest.mark.trio
    async def test_version_mismatch(self, workspace, credentials_env):
        """A contract of another generation than the barrier is a switch in progress."""
        store = await fill(workspace, credentials_env)
        try:
            barrier = credentials_env.objects[f'{PREFIX}{BARRIER_KEY}']
            barrier.data = b'bbbbbbbb' + barrier.data[8:]
            result = await cleanup_of(credentials_env, store).run()
        finally:
            await store.aclose()
        assert result.status == STATUS_SKIPPED
        assert 'switch is in progress' in result.reason


class TestGrace:
    """Nothing is deleted before the barrier of this mirror is old enough."""

    @pytest.mark.trio
    async def test_a_young_barrier_deletes_nothing(self, workspace, credentials_env):
        """A generation that was just switched keeps its predecessor."""
        store = await fill(workspace, credentials_env)
        try:
            credentials_env.store(f'{PREFIX}old/full.pack', b'old', 'application/octet-stream',
                                 None, last_modified=time.time() - GRACE * 10)
            result = await cleanup_of(credentials_env, store).run()
        finally:
            await store.aclose()
        assert result.status == STATUS_OK
        assert result.deleted == 0
        assert f'{PREFIX}old/full.pack' in credentials_env.objects

    @pytest.mark.trio
    async def test_an_old_barrier_deletes_the_leftovers(self, workspace, credentials_env):
        """Once the barrier is old enough the leftovers of the last generation go."""
        store = await fill(workspace, credentials_env)
        try:
            credentials_env.store(f'{PREFIX}old/full.pack', b'old', 'application/octet-stream',
                                 None, last_modified=time.time() - GRACE * 10)
            age_objects(credentials_env, GRACE + 1)
            result = await cleanup_of(credentials_env, store).run()
        finally:
            await store.aclose()
        assert result.status == STATUS_OK
        assert result.deleted == 1
        assert f'{PREFIX}old/full.pack' not in credentials_env.objects

    @pytest.mark.trio
    async def test_a_young_leftover_survives_an_old_barrier(self, workspace, credentials_env):
        """A file younger than the grace is an interrupted upload, not a leftover."""
        store = await fill(workspace, credentials_env)
        try:
            credentials_env.store(f'{PREFIX}old/full.pack', b'old', 'application/octet-stream', None)
            age_objects(credentials_env, GRACE + 1, keys=[
                f'{PREFIX}{BARRIER_KEY}', f'{PREFIX}{MANIFEST_KEY}'])
            result = await cleanup_of(credentials_env, store).run()
        finally:
            await store.aclose()
        assert result.deleted == 0
        assert f'{PREFIX}old/full.pack' in credentials_env.objects


class TestDelete:
    """What the cleaner removes, and what it must never remove."""

    @pytest.mark.trio
    async def test_keeps_the_manifest_and_the_barrier(self, workspace, credentials_env):
        """The two files that name a generation are never deleted."""
        store = await fill(workspace, credentials_env)
        try:
            age_objects(credentials_env, GRACE + 1)
            result = await cleanup_of(credentials_env, store).run()
        finally:
            await store.aclose()
        assert result.status == STATUS_OK
        assert f'{PREFIX}{MANIFEST_KEY}' in credentials_env.objects
        assert f'{PREFIX}{BARRIER_KEY}' in credentials_env.objects

    @pytest.mark.trio
    async def test_keeps_the_published_files(self, workspace, credentials_env):
        """The files of the generation, however old, stay."""
        store = await fill(workspace, credentials_env)
        try:
            age_objects(credentials_env, GRACE * 100)
            await cleanup_of(credentials_env, store).run()
        finally:
            await store.aclose()
        for relative in workspace.files:
            assert f'{PREFIX}{relative}' in credentials_env.objects

    @pytest.mark.trio
    async def test_a_retired_generation(self, workspace, credentials_env):
        """The files of the previous version go, the files of the current one stay."""
        store = await fill(workspace, credentials_env)
        try:
            workspace.write('bbbbbbbb', {
                'packrepo/bbbbbbbb/full.pack': b'full pack of bbbbbbbb',
                'wheel/msgspec/0.22.0/msgspec-0.22.0-py3-none-any.whl': b'wheel bytes',
            })
            await fill(workspace, credentials_env)
            age_objects(credentials_env, GRACE + 1)
            result = await cleanup_of(credentials_env, store).run()
        finally:
            await store.aclose()
        assert result.deleted == 2
        assert f'{PREFIX}packrepo/aaaaaaaa/full.pack' not in credentials_env.objects
        assert f'{PREFIX}packrepo/aaaaaaaa/update_00000001.pack' not in credentials_env.objects
        assert f'{PREFIX}packrepo/bbbbbbbb/full.pack' in credentials_env.objects


class TestProgress:
    """A delete round logs one numbered line per removed key.

    The round of these tests deletes one key at a time, so the log is compared
    whole: the order of the deletes is the order of the plan.
    """

    def _fill(self, service):
        """
        Two leftovers of an older generation, already past the grace.

        Args:
            service (FakeCos): The service
        """
        for index in range(2):
            service.store(
                f'{PREFIX}old/w{index}.pack', b'old', 'application/octet-stream', None)
        age_objects(service, GRACE + 1)

    @pytest.mark.trio
    async def test_the_log_of_a_delete_round(self, workspace, credentials_env):
        """The two leftovers, numbered, then the tally of the round."""
        store = await fill(workspace, credentials_env)
        cleanup = cleanup_of(credentials_env, store)
        try:
            self._fill(credentials_env)
            with logger.mock_capture_writer() as capture:
                result = await cleanup.run()
        finally:
            await store.aclose()
        assert result.deleted == 2
        assert capture.concat() == f"""\
INFO | [cos-main] {CHANNEL}: manifest=aaaaaaaa barrier=aaaaaaaa, barrier age=1.0h, grace=1.0h
INFO |   DELETE 1/2 old/w0.pack                                  age=1.0h 3.00B
INFO |   DELETE 2/2 old/w1.pack                                  age=1.0h 3.00B
INFO | [cos-main] {CHANNEL}: deleted 2 key(s), 6 bytes
"""

    @pytest.mark.trio
    async def test_the_log_of_a_dry_run(self, workspace, credentials_env):
        """The plan of a dry run is `WOULD DELETE`, with no request count."""
        store = await fill(workspace, credentials_env)
        cleanup = cleanup_of(credentials_env, store)
        try:
            self._fill(credentials_env)
            with logger.mock_capture_writer() as capture:
                result = await cleanup.run(dry_run=True)
        finally:
            await store.aclose()
        assert result.status == STATUS_DRY_RUN
        assert capture.concat() == f"""\
INFO | [cos-main] {CHANNEL}: manifest=aaaaaaaa barrier=aaaaaaaa, barrier age=1.0h, grace=1.0h
INFO |   WOULD DELETE old/w0.pack                                  age=1.0h 3.00B
INFO |   WOULD DELETE old/w1.pack                                  age=1.0h 3.00B
"""


class TestDryRun:
    """A dry run reports the plan and deletes nothing."""

    @pytest.mark.trio
    async def test_nothing_is_deleted(self, workspace, credentials_env):
        """The leftovers are reported and kept."""
        store = await fill(workspace, credentials_env)
        try:
            credentials_env.store(f'{PREFIX}old/full.pack', b'old', 'application/octet-stream',
                                 None, last_modified=time.time() - GRACE * 10)
            age_objects(credentials_env, GRACE + 1)
            result = await cleanup_of(credentials_env, store).run(dry_run=True)
        finally:
            await store.aclose()
        assert result.status == STATUS_DRY_RUN
        assert result.deleted == 1
        assert f'{PREFIX}old/full.pack' in credentials_env.objects


class TestRun:
    """The run of a repo config, against the fake service."""

    @pytest.mark.trio
    async def test_one_repo_one_remote(self, workspace, credentials_env, config):
        """The workspace of the config is cleaned on the remote it names."""
        await fill(workspace, credentials_env)
        age_objects(credentials_env, GRACE + 1)
        results = await CleanupRun(config, client=credentials_env.client()).run()
        assert [result.status for result in results] == [STATUS_OK]

    @pytest.mark.trio
    async def test_a_repo_that_publishes_nowhere(self, workspace, credentials_env, run_dir):
        """An empty Remotes map is a run that does nothing, not a failure."""
        config = write_config(run_dir, make_model(remotes={}))
        results = await CleanupRun(config, client=credentials_env.client()).run()
        assert results == []
