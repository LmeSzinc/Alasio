"""
The local side of a publication: one reader per file, and one generation per
channel.

The tests here pin the properties that separated reading from publishing: the
record of a generation is read before anything else and before the remote is
asked, the generation is read once for every mirror of a channel, and a mirror
waits for the file it is about to publish rather than for the generation.
"""

import hashlib

import pytest
import trio

import alasio.deploy_dev.pack_server.publish.local as local_module
from alasio.deploy_dev.pack_server.publish.channel import STATUS_OK, STATUS_SKIPPED, ChannelUpload
from alasio.deploy_dev.pack_server.publish.common import BARRIER_KEY, MANIFEST_KEY, SkipChannel
from alasio.deploy_dev.pack_server.publish.local import LocalGeneration
from alasio.deploy_dev.pack_server.publish.upload import UploadRun
from alasio.ext.path import PathStr
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack_server.publish.conftest import (
    CHANNEL, REMOTE, make_model, make_remote, make_store, write_config
)

pytestmark = [
    pytest.mark.parallel,
]

# the files of the fixture generation: three content files, the barrier and the
# contract, each read and digested once
GENERATION_FILES = 5

WHEEL = 'wheel/msgspec/0.22.0/msgspec-0.22.0-py3-none-any.whl'
FULL_PACK = 'packrepo/aaaaaaaa/full.pack'

# every file of the fixture generation, the contract included
ALL_FILES = (BARRIER_KEY, MANIFEST_KEY, FULL_PACK,
             'packrepo/aaaaaaaa/update_00000001.pack', WHEEL)


def count_digests(monkeypatch):
    """
    Count the calls of the local digest, the one IO the generation costs.

    Args:
        monkeypatch (pytest.MonkeyPatch): The fixture of the test

    Returns:
        list[str]: The paths digested, filled as the run goes
    """
    calls = []
    real = local_module.md5_local

    async def counting(path):
        calls.append(str(path))
        return await real(path)

    monkeypatch.setattr(local_module, 'md5_local', counting)
    return calls


class TestLocalGeneration:
    """One instance is a snapshot: it reads and digests each file once."""

    @pytest.mark.trio
    async def test_warmup_reads_the_whole_generation(self, workspace):
        """Every published file and the contract are readable in the block."""
        local = LocalGeneration(workspace.workspace)
        async with local.warmup():
            expected = dict(workspace.files)
            expected[BARRIER_KEY] = (
                workspace.version.encode() + bytes.fromhex(workspace.checksum))
            expected[MANIFEST_KEY] = workspace.path(MANIFEST_KEY).atomic_read_bytes()
            for relative, data in expected.items():
                item = await local.item(relative)
                assert item.relative == relative
                assert item.size == len(data)
                assert item.md5 == hashlib.md5(data).hexdigest()
                assert item.file.endswith(relative.rpartition('/')[2])

    @pytest.mark.trio
    async def test_the_block_only_waits_for_the_record(self, workspace, monkeypatch):
        """Entering the block validates the record and reads no content file.

        The barrier and the contract are published last, so they are read like
        any other file: the caller awaits them when it needs their decision,
        not when it opens the generation.
        """
        reads = []
        real = local_module.md5_local

        async def spy(path):
            reads.append(PathStr.new(str(path)).name)
            return await real(path)

        monkeypatch.setattr(local_module, 'md5_local', spy)
        local = LocalGeneration(workspace.workspace)
        async with local.warmup():
            assert reads == []
            assert (await local.item(BARRIER_KEY)).relative == BARRIER_KEY
            assert (await local.item(MANIFEST_KEY)).relative == MANIFEST_KEY

    @pytest.mark.trio
    async def test_a_serial_warmup_reads_in_the_order_of_the_manifest(self, workspace,
                                                                       monkeypatch):
        """Concurrency 1 reads one file at a time, in the order of the queue.

        The order is the manifest followed by the contract, and every read is
        done before the block is entered: a serial run is a loop, not a nursery
        handing the order to the scheduler, so a test can pin it.
        """
        reads = []
        real = local_module.md5_local

        async def spy(path):
            reads.append(PathStr.new(str(path)).name)
            return await real(path)

        monkeypatch.setattr(local_module, 'md5_local', spy)
        local = LocalGeneration(workspace.workspace, concurrency=1)
        async with local.warmup():
            assert reads == [
                'full.pack',
                'update_00000001.pack',
                'latest.pack',
                'msgspec-0.22.0-py3-none-any.whl',
                'manifest.json',
            ]

    @pytest.mark.trio
    async def test_warmup_is_idempotent(self, workspace, monkeypatch):
        """A second mirror warms the same generation up without reading again."""
        calls = count_digests(monkeypatch)
        local = LocalGeneration(workspace.workspace)
        async with local.warmup():
            for relative in ALL_FILES:
                await local.item(relative)
        async with local.warmup():
            for relative in ALL_FILES:
                await local.item(relative)
        assert len(calls) == GENERATION_FILES
        assert len(set(calls)) == GENERATION_FILES

    @pytest.mark.trio
    async def test_a_cancelled_read_is_queued_again(self, workspace, monkeypatch):
        """A run that fails while the content is read does not lose the file.

        A failure in the block cancels the content reads of that run. The next
        run of the same channel finds those slots unread and queues them again,
        while the record it already has costs nothing.
        """
        calls = count_digests(monkeypatch)
        local = LocalGeneration(workspace.workspace)

        class Boom(Exception):
            pass

        with pytest.raises(BaseException):
            async with local.warmup():
                raise Boom('the mirror refused the run')
        assert len(calls) < GENERATION_FILES
        async with local.warmup():
            for relative in ALL_FILES:
                await local.item(relative)
        assert len(calls) == GENERATION_FILES
        assert len(set(calls)) == GENERATION_FILES

    @pytest.mark.trio
    async def test_an_item_waits_for_its_own_file_only(self, workspace, monkeypatch):
        """A file that is still being read does not hold up the others.

        The pack of the fixture is held open by the test, and the wheel is
        awaited while it is: a generation-wide await would deadlock here.
        """
        local = LocalGeneration(workspace.workspace)
        real = local_module.md5_local
        reading = trio.Event()
        release = trio.Event()

        async def slow(path):
            if PathStr.new(str(path)).name == 'full.pack':
                reading.set()
                await release.wait()
            return await real(path)

        monkeypatch.setattr(local_module, 'md5_local', slow)
        async with local.warmup():
            await reading.wait()
            with trio.fail_after(5):
                item = await local.item(WHEEL)
            assert item.size == len(workspace.files[WHEEL])
            release.set()

    @pytest.mark.trio
    async def test_a_missing_file_fails_its_waiter_only(self, workspace):
        """One file that is gone is a skip of that file, not of the generation."""
        workspace.remove(WHEEL)
        local = LocalGeneration(workspace.workspace)
        async with local.warmup():
            with pytest.raises(SkipChannel) as caught:
                await local.item(WHEEL)
            assert 'missing on the disk' in str(caught.value)
            assert (await local.item(FULL_PACK)).size

    @pytest.mark.trio
    async def test_an_unknown_key_is_a_bug(self, workspace):
        """A key the warmup did not queue is a KeyError, not a state of the disk.

        Both lists come from the manifest, so a key that one has and the other
        does not is a bug of the flow: it must not be silently read here.
        """
        local = LocalGeneration(workspace.workspace)
        async with local.warmup():
            with pytest.raises(KeyError):
                await local.item('wheel/not/in/the/manifest.whl')

    @pytest.mark.trio
    async def test_the_record_is_validated_before_the_first_request(self, workspace,
                                                                    credentials_env):
        """A broken generation is refused without a single request.

        The record is the first thing a run reads, so a workspace without one
        never reaches the remote: no listing, no HEAD, no PUT.
        """
        workspace.remove('manifest.json')
        store = make_store(make_remote(), CHANNEL, credentials_env)
        upload = ChannelUpload(
            LocalGeneration(workspace.workspace), store, remote_name=REMOTE)
        try:
            result = await upload.run()
        finally:
            await store.aclose()
        assert result.status == STATUS_SKIPPED
        assert credentials_env.requests == []

    @pytest.mark.trio
    async def test_a_missing_manifest_is_a_skip(self, workspace):
        """The guard runs once per instance and refuses a broken generation."""
        workspace.remove('manifest.json')
        local = LocalGeneration(workspace.workspace)
        with pytest.raises(SkipChannel) as caught:
            async with local.warmup():
                pass
        assert 'not there yet' in str(caught.value)


class TestOncePerChannel:
    """Every mirror of a channel shares the generation of the channel."""

    @pytest.mark.trio
    async def test_two_remotes_digest_the_generation_once(self, workspace, credentials_env,
                                                         run_dir, monkeypatch):
        """Three files, the barrier and the contract: five digests for two hosts.

        Without the shared generation the second host reads and digests the same
        five files again; with it the digest of a generation costs one pass over
        the channel, whatever the number of mirrors.
        """
        config = write_config(
            run_dir, make_model({REMOTE: make_remote(), 'backup': make_remote()}))
        calls = count_digests(monkeypatch)
        results = await UploadRun(config, client=credentials_env.client()).run()
        assert [result.status for result in results] == [STATUS_OK, STATUS_OK]
        assert len(calls) == GENERATION_FILES
        assert len(set(calls)) == GENERATION_FILES

    @pytest.mark.trio
    async def test_the_run_holds_one_generation(self, workspace, credentials_env, run_dir):
        """`UploadRun.local` is the snapshot every channel of the run awaits."""
        config = write_config(run_dir)
        run = UploadRun(config, client=credentials_env.client())
        assert isinstance(run.local, LocalGeneration)
        assert run.local.workspace.name == CHANNEL

    @pytest.mark.trio
    async def test_a_channel_shares_the_generation_it_is_given(self, workspace,
                                                               credentials_env):
        """A channel publishes the generation it was handed, it does not read one."""
        local = LocalGeneration(workspace.workspace)
        store = make_store(make_remote(), CHANNEL, credentials_env)
        try:
            result = await ChannelUpload(local, store, remote_name=REMOTE).run()
        finally:
            await store.aclose()
        assert result.status == STATUS_OK
        assert result.uploaded == len(workspace.files) + 2
        # every file of the report was read through this instance
        assert set(local._slots) == set(workspace.files) | {BARRIER_KEY, MANIFEST_KEY}

    @pytest.mark.trio
    async def test_a_broken_generation_skips_every_remote(self, workspace, credentials_env,
                                                          run_dir):
        """One broken workspace is a skip of the channel, not a failed mirror."""
        workspace.remove('packrepo/latest.pack')
        config = write_config(
            run_dir, make_model({REMOTE: make_remote(), 'backup': make_remote()}))
        results = await UploadRun(config, client=credentials_env.client()).run()
        assert [result.status for result in results] == [STATUS_SKIPPED, STATUS_SKIPPED]
        assert all('latest.pack' in result.reason for result in results)
