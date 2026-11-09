"""
The concurrency of the flow, and the promise that local IO stays off the loop.

Two properties are checked here that no other test states:

- an upload and a cleanup run several transfers at once, and never more than the
  cap of the tool (common.MAX_CONCURRENCY, 8). A mirror round trip is dominated
  by the latency of the request, so one at a time would waste it; more than the
  cap only moves the queue into the connection pool;
- the local reads of a generation (stat, MD5, the walk of the workspace) run in
  worker threads. A synchronous call inside a coroutine stalls every other
  transfer of the run, which is exactly what this suite would not notice.
"""

import threading

import pytest
import trio

from alasio.deploy_dev.pack_server.publish.channel import STATUS_OK, ChannelUpload
from alasio.deploy_dev.pack_server.publish.cleanup import ChannelCleanup
from alasio.deploy_dev.pack_server.publish.common import BARRIER_KEY, MANIFEST_KEY, MAX_CONCURRENCY
from alasio.deploy_dev.pack_server.publish.local import LocalGeneration
from alasio.ext.path import PathStr
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack_server.publish.conftest import CHANNEL, REMOTE_PREFIX, make_remote, make_store

PREFIX = f'{REMOTE_PREFIX}/{CHANNEL}/'
GRACE = 3600

# every file of the fixture generation, the contract included
ALL_FILES = (
    BARRIER_KEY,
    MANIFEST_KEY,
    'packrepo/aaaaaaaa/full.pack',
    'packrepo/aaaaaaaa/update_00000001.pack',
    'wheel/msgspec/0.22.0/msgspec-0.22.0-py3-none-any.whl',
)

# the files a generation of this test holds: more than the cap, so a run that
# serialises them is visible, and small, so the test stays fast
FILE_COUNT = 20


def many_files():
    """
    Returns:
        dict: Relative key -> bytes, FILE_COUNT files of the same size
    """
    return {
        'packrepo/aaaaaaaa/w%02d.pack' % index: b'content %02d' % index
        for index in range(FILE_COUNT)
    }


def count_in_flight(store, method):
    """
    Wrap one method of a store so the test sees how many calls overlap.

    Args:
        store (ObjectStore): The store to instrument
        method (str): Name of the method to wrap

    Returns:
        dict: {'peak': int, 'calls': int}, filled as the calls happen
    """
    stats = {'peak': 0, 'calls': 0, 'now': 0}
    original = getattr(store, method)

    async def counted(*args, **kwargs):
        stats['now'] += 1
        stats['calls'] += 1
        stats['peak'] = max(stats['peak'], stats['now'])
        try:
            # a request always waits; the in-process service answers without a
            # single checkpoint of the scheduler, so the yield is put here to
            # make a fake transfer overlap the way a real one does
            await trio.sleep(0)
            return await original(*args, **kwargs)
        finally:
            stats['now'] -= 1

    setattr(store, method, counted)
    return stats


class TestUploadConcurrency:
    """The content files of a generation are written a few at a time."""

    @pytest.mark.trio
    async def test_uploads_run_in_parallel_up_to_the_cap(self, workspace, credentials_env):
        """Twenty files reach the mirror, never more than eight at once."""
        workspace.write('aaaaaaaa', many_files())
        remote = make_remote()
        store = make_store(remote, CHANNEL, credentials_env)
        stats = count_in_flight(store, 'put')
        upload = ChannelUpload(
            LocalGeneration(workspace.workspace, MAX_CONCURRENCY), store,
            remote_name='cos-main', concurrency=MAX_CONCURRENCY)
        try:
            result = await upload.run()
        finally:
            await store.aclose()
        assert result.status == STATUS_OK
        assert stats['calls'] == FILE_COUNT + 2, stats  # files, barrier, manifest
        assert stats['peak'] == MAX_CONCURRENCY, stats
        assert len(credentials_env.objects) == FILE_COUNT + 2, stats  # and the barrier

    @pytest.mark.trio
    async def test_the_cap_is_clamped(self, workspace, credentials_env):
        """A config that asks for more than the cap gets the cap."""
        remote = make_remote()
        store = make_store(remote, CHANNEL, credentials_env)
        try:
            assert ChannelUpload(
                LocalGeneration(workspace.workspace), store, concurrency=64
            ).concurrency == MAX_CONCURRENCY
            assert ChannelUpload(
                LocalGeneration(workspace.workspace), store, concurrency=0
            ).concurrency == 1
        finally:
            await store.aclose()

    @pytest.mark.trio
    async def test_the_local_digest_and_the_listing_overlap(self, workspace, credentials_env,
                                                           monkeypatch):
        """The record is validated before the first request, the content reads overlap it.

        The listing refuses to answer until a content file has started, and a
        content file refuses to read until the listing has started: a serial
        implementation (digest everything, then ask the mirror, or the reverse)
        deadlocks here instead of passing. The record is on the disk before the
        listing is sent, so a broken generation never reaches the remote.
        """
        remote = make_remote()
        store = make_store(remote, CHANNEL, credentials_env)
        local = LocalGeneration(workspace.workspace, MAX_CONCURRENCY)
        upload = ChannelUpload(local, store, remote_name='cos-main')
        listing_started = trio.Event()
        read_started = trio.Event()
        original_list = store.list
        original_read = local._read

        async def list_objects():
            # the record is read and checked before the remote is asked anything
            assert 'record' in local.__dict__
            listing_started.set()
            with trio.fail_after(5):
                await read_started.wait()
            return await original_list()

        async def read(relative):
            if relative not in (BARRIER_KEY, MANIFEST_KEY):
                read_started.set()
                with trio.fail_after(5):
                    await listing_started.wait()
            return await original_read(relative)

        monkeypatch.setattr(store, 'list', list_objects)
        monkeypatch.setattr(local, '_read', read)
        try:
            result = await upload.run()
        finally:
            await store.aclose()
        assert result.status == STATUS_OK


class TestCleanupConcurrency:
    """The keys of a retired generation are deleted a few at a time."""

    @pytest.mark.trio
    async def test_deletes_run_in_parallel_up_to_the_cap(self, workspace, credentials_env):
        """Twenty leftovers are removed, never more than eight at once."""
        from tests.deploy_dev.pack_server.publish.test_cleanup import age_objects, fill
        await fill(workspace, credentials_env)
        for index in range(FILE_COUNT):
            credentials_env.store(
                f'{PREFIX}old/w%02d.pack' % index, b'old', 'application/octet-stream', None)
        age_objects(credentials_env, GRACE + 1)
        store = make_store(make_remote(), CHANNEL, credentials_env)
        stats = count_in_flight(store, 'delete')
        cleanup = ChannelCleanup(
            CHANNEL, store, GRACE, remote_name='cos-main',
            concurrency=MAX_CONCURRENCY)
        try:
            result = await cleanup.run()
        finally:
            await store.aclose()
        assert result.status == STATUS_OK, result
        assert result.deleted == FILE_COUNT, result
        assert stats['peak'] == MAX_CONCURRENCY, stats
        assert not [key for key in credentials_env.objects if '/old/' in key], sorted(
            credentials_env.objects)

    @pytest.mark.trio
    async def test_the_cap_is_clamped(self):
        """A caller that asks for more than the cap gets the cap."""
        assert ChannelCleanup('c', None, 0, concurrency=64).concurrency == MAX_CONCURRENCY
        assert ChannelCleanup('c', None, 0, concurrency=-1).concurrency == 1


class TestLocalIo:
    """The local files of a generation are read off the event loop."""

    @pytest.mark.trio
    async def test_the_local_digest_runs_in_a_worker_thread(self, workspace, monkeypatch):
        """The MD5 of a file is computed from a worker thread.

        Reading the file is the check that it exists, so this is the only
        local read of the uploader: a synchronous call inside a coroutine
        stalls every other transfer of the run.
        """
        import builtins

        loop_thread = threading.current_thread()
        threads = []
        real_open = builtins.open

        def spy(*args, **kwargs):
            threads.append(threading.current_thread())
            return real_open(*args, **kwargs)

        monkeypatch.setattr(builtins, 'open', spy)
        item = await LocalGeneration(workspace.workspace)._read(
            'packrepo/aaaaaaaa/full.pack')

        assert item.size > 0
        assert threads
        assert all(thread is not loop_thread for thread in threads)

    @pytest.mark.trio
    async def test_every_published_file_is_opened_once(self, workspace, monkeypatch):
        """One pass over the manifest: no stat() sweep in front of the digests.

        Reading the file is the check that it exists, so a generation of N files
        costs N opens. A pass of stat() calls first would open the same tree
        twice and would still race with a file that disappears between them.
        """
        import builtins

        opened = []
        real_open = builtins.open

        def spy(file, *args, **kwargs):
            opened.append(PathStr.new(str(file)).name)
            return real_open(file, *args, **kwargs)

        local = LocalGeneration(workspace.workspace)
        # the record is read before the spy: the test is about the files of the
        # generation, not about the two files that name it
        await local.record
        monkeypatch.setattr(builtins, 'open', spy)
        async with local.warmup():
            for relative in ALL_FILES:
                await local.item(relative)

        assert sorted(opened) == sorted(
            PathStr.new(relative).name for relative in ALL_FILES)
