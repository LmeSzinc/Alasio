"""
The local side of a publication: the generation is read once for every mirror of
a channel, not once per mirror.

The tests here pin the property that made `LocalGeneration` exist: a run with
three remotes must open and digest the files of the generation once. The other
tests drive a single `ChannelUpload` and would not notice a second read.
"""

import pytest

import alasio.deploy_dev.pack_server.publish.local as local_module
from alasio.deploy_dev.pack_server.publish.channel import STATUS_OK, STATUS_SKIPPED, ChannelUpload
from alasio.deploy_dev.pack_server.publish.common import SkipChannel
from alasio.deploy_dev.pack_server.publish.local import LocalGeneration
from alasio.deploy_dev.pack_server.publish.upload import UploadRun
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
    """One instance is a snapshot: it reads and digests the generation once."""

    @pytest.mark.trio
    async def test_files_are_cached(self, workspace):
        """The second await returns the same object, no second read."""
        local = LocalGeneration(workspace.workspace)
        files = await local.files
        assert await local.files is files

    @pytest.mark.trio
    async def test_files_hold_the_whole_generation(self, workspace):
        """The items are the published files in order, plus the contract."""
        local = LocalGeneration(workspace.workspace)
        files = await local.files
        assert [item.relative for item in files.items] == list(files.manifest.files)
        assert files.manifest_item.relative == 'manifest.json'
        assert files.manifest.version == workspace.version

    @pytest.mark.trio
    async def test_a_missing_manifest_is_a_skip(self, workspace):
        """The guard runs once per instance and refuses a broken generation."""
        workspace.remove('manifest.json')
        local = LocalGeneration(workspace.workspace)
        with pytest.raises(SkipChannel) as caught:
            await local.files
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
        """A channel uploads the generation it was handed, it does not read one."""
        local = LocalGeneration(workspace.workspace)
        files = await local.files
        store = make_store(make_remote(), CHANNEL, credentials_env)
        try:
            result = await ChannelUpload(local, store, remote_name=REMOTE).run()
        finally:
            await store.aclose()
        assert result.status == STATUS_OK
        assert result.uploaded == len(files.items) + 1

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
