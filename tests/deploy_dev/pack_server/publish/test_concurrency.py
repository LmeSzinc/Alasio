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
from alasio.deploy_dev.pack_server.publish.common import MAX_CONCURRENCY
from alasio.deploy_dev.pack_server.publish.local import LocalGeneration
from alasio.ext.path import PathStr
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack_server.publish.conftest import CHANNEL, REMOTE_PREFIX, make_remote, make_store

PREFIX = f'{REMOTE_PREFIX}/{CHANNEL}/'
GRACE = 3600

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
        """The disk and the mirror are asked at the same time.

        The digest of the first file waits for the listing to start, and the
        listing answers only after that: a serial implementation (digest
        everything, then ask the mirror) would wait for itself and fail the
        timeout instead of passing.
        """
        remote = make_remote()
        store = make_store(remote, CHANNEL, credentials_env)
        listing_started = trio.Event()
        original_list = store.list
        local = LocalGeneration(workspace.workspace, MAX_CONCURRENCY)
        upload = ChannelUpload(local, store, remote_name='cos-main')
        original_item = local._item
        first = []

        async def list_objects():
            listing_started.set()
            return await original_list()

        async def item(relative):
            if not first:
                first.append(relative)
                with trio.fail_after(5):
                    await listing_started.wait()
            return await original_item(relative)

        monkeypatch.setattr(store, 'list', list_objects)
        monkeypatch.setattr(local, '_item', item)
        try:
            result = await upload.run()
        finally:
            await store.aclose()
        assert result.status == STATUS_OK
        assert first


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
        item = await LocalGeneration(workspace.workspace)._item(
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
        manifest = await local.record
        monkeypatch.setattr(builtins, 'open', spy)
        items = await local._digest(manifest)

        assert [item.relative for item in items] == list(manifest.files)
        assert sorted(opened) == sorted(
            PathStr.new(relative).name for relative in manifest.files)
