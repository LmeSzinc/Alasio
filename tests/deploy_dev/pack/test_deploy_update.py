"""
Tests for DeployJob.update: the unified entry of the update flow.

The server is an in-memory MockServerFile serving two versions and the
update pack between them. The flow follows the draft in PackEncodeBase:
latest.pack is compared with the local version, a version mismatch
downloads the update pack /{new}/from_{old}.pack and applies it with
UpdateJob, the same version continues with ResetJob.

Every test is async (pytest-trio): update() and the jobs await their
network phases on the test event loop, the local phases run in worker
threads.

The packs are module level singletons, built before the fake
filesystem is active: MockGitRepo reads the real .gitattributes file,
which the fake filesystem does not provide.
"""
import os

import pytest

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job import DeployJob, UpdateAborted
from alasio.deploy.pack.job_rebuild import RebuildJob
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.deploy_dev.pack.pack_full import PackFull
from alasio.deploy_dev.pack.pack_update import PackUpdate
from alasio.ext import env
from alasio.ext.path.atomic import file_read_bytes
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack.conftest import FULL_SCENARIO_NEW, FULL_SCENARIO_OLD, MockServerFile, make_repo


def make_pack(files, commit='c1'):
    """
    Build a full pack of a version.

    Args:
        files (dict[str, bytes | tuple[bytes, int]]): {path: content}
            or {path: (content, mode)}
        commit (str): Version of the pack. Defaults to 'c1'.

    Returns:
        bytes: Full pack data
    """
    repo = MockGitRepo()
    for path, value in files.items():
        if isinstance(value, tuple):
            content, mode = value
        else:
            content, mode = value, 644
        repo.register_file(commit, path, content, mode=mode)
    repo.register_commit(commit, author_name='Author', message='')
    return b''.join(PackFull(repo, commit=commit).iter_pack_data())


def read_tree():
    """
    Read the working tree of the app folder as {path: content}.

    Returns:
        dict[str, bytes]: Working tree content
    """
    tree = {}
    for root, dirs, files in os.walk(env.PROJECT_ROOT):
        # the pack structure and the logger files are not part of the
        # working tree
        dirs[:] = [dir for dir in dirs if dir not in ('.pack', 'log')]
        for name in files:
            path = os.path.join(root, name)
            key = os.path.relpath(path, env.PROJECT_ROOT).replace(os.sep, '/')
            if key.startswith(('.pack/', 'log/')):
                continue
            tree[key] = file_read_bytes(path)
    return tree


# module level singletons, built before the fake filesystem is active
SCENARIO_REPO = make_repo({'old': FULL_SCENARIO_OLD, 'new': FULL_SCENARIO_NEW})
OLD_PACK = b''.join(PackFull(SCENARIO_REPO, commit='old').iter_pack_data())
NEW_PACK = b''.join(PackFull(SCENARIO_REPO, commit='new').iter_pack_data())
OLD_DECODER = PackDecodeBase(OLD_PACK)
NEW_DECODER = PackDecodeBase(NEW_PACK)
OLD_INDEX = bytes(OLD_DECODER.extract_index_pack())
NEW_INDEX = bytes(NEW_DECODER.extract_index_pack())
# the update pack is generated from the repo: the new version is a PackFull,
# the old version is its commit
UPDATE = b''.join(PackUpdate(
    PackFull(SCENARIO_REPO, commit='new'), 'old').iter_pack_data())
NEW_TREE = {
    path: bytes(NEW_DECODER.catfile(info))
    for path, info in NEW_DECODER.fileinfo.items()
    if info.edit != 2 and not path.startswith('.pack/')
}
OLD_TREE = {
    path: bytes(OLD_DECODER.catfile(info))
    for path, info in OLD_DECODER.fileinfo.items()
    if info.edit != 2 and not path.startswith('.pack/')
}
SERVER = MockServerFile()
SERVER.register_version('old', OLD_PACK, OLD_INDEX)
SERVER.register_version('new', NEW_PACK, NEW_INDEX)
SERVER.register_update('old', 'new', UPDATE)
# servers of the fallback tests: the same versions without an update
# pack (404), or with a corrupt one
SERVER_NO_UPDATE = MockServerFile()
SERVER_NO_UPDATE.register_version('old', OLD_PACK, OLD_INDEX)
SERVER_NO_UPDATE.register_version('new', NEW_PACK, NEW_INDEX)
SERVER_CORRUPT_UPDATE = MockServerFile()
SERVER_CORRUPT_UPDATE.register_version('old', OLD_PACK, OLD_INDEX)
SERVER_CORRUPT_UPDATE.register_version('new', NEW_PACK, NEW_INDEX)
SERVER_CORRUPT_UPDATE.register_update('old', 'new', b'garbage')
# a third version of the mid-flow publish test: the server publishes it
# right after the entry fetch of the flow
OTHER_PACK = make_pack({'other.txt': b'other\n'}, commit='other')
OTHER_INDEX = bytes(PackDecodeBase(OTHER_PACK).extract_index_pack())


class TestDeployUpdate:
    """The unified update entry of DeployJob."""

    @pytest.mark.trio
    async def test_update_to_new_version(self, app_folder):
        """A version mismatch downloads the update pack and applies it."""
        with logger.mock_capture_writer():
            await UnpackJob(OLD_PACK).run()
            assert await DeployJob(server=SERVER).update()
        assert read_tree() == NEW_TREE
        # the local index pack is the new one
        decoder = PackDecodeBase(file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack'))
        assert decoder.current_version == 'new'
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_up_to_date(self, app_folder):
        """The same version continues with ResetJob, nothing changes."""
        with logger.mock_capture_writer():
            await UnpackJob(NEW_PACK).run()
            assert await DeployJob(server=SERVER).update()
        assert read_tree() == NEW_TREE
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_missing_local_index(self, app_folder):
        """A missing local index falls back to RebuildJob, the tree is
        rebuilt from the server."""
        with logger.mock_capture_writer() as capture:
            assert await DeployJob(server=SERVER).update()
        assert capture.backend.any_contains('Failed to read the local version')
        assert read_tree() == NEW_TREE
        decoder = PackDecodeBase(file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack'))
        assert decoder.current_version == 'new'
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_update_pack_missing_falls_back(self, app_folder):
        """A 404 of the update pack falls back to RebuildJob, the tree
        is rebuilt from the latest index."""
        await UnpackJob(OLD_PACK).run()
        with logger.mock_capture_writer() as capture:
            assert await DeployJob(server=SERVER_NO_UPDATE).update()
        assert capture.backend.any_contains('Failed to get the update pack')
        assert read_tree() == NEW_TREE
        decoder = PackDecodeBase(file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack'))
        assert decoder.current_version == 'new'
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_update_pack_corrupt_falls_back(self, app_folder):
        """A corrupt update pack fails to apply and falls back to
        RebuildJob, the tree is rebuilt from the latest index."""
        await UnpackJob(OLD_PACK).run()
        with logger.mock_capture_writer() as capture:
            assert await DeployJob(server=SERVER_CORRUPT_UPDATE).update()
        assert capture.backend.any_contains('Failed to apply the update pack')
        assert read_tree() == NEW_TREE
        decoder = PackDecodeBase(file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack'))
        assert decoder.current_version == 'new'
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_unfinished_rebuild_finished_first(self, app_folder):
        """An unfinished rebuild job is finished before the update."""
        await UnpackJob(OLD_PACK).run()
        RebuildJob(SERVER).write()
        with logger.mock_capture_writer():
            assert await DeployJob(server=SERVER).update()
        assert read_tree() == NEW_TREE
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_unfinished_job_finished_first(self, app_folder):
        """An unfinished job is finished before the update."""
        with logger.mock_capture_writer():
            UnpackJob(OLD_PACK).write()
            assert await DeployJob(server=SERVER).update()
        assert read_tree() == NEW_TREE
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_update_without_server(self, app_folder):
        """A target created without a server cannot update."""
        with pytest.raises(ValueError, match='no server provided'):
            await DeployJob().update()
        assert not os.path.exists(env.PROJECT_ROOT / '.pack')


class TestLatestInfoSnapshot:
    """latest.pack is requested once per flow, the job reuses the snapshot.

    DeployJob.update() fetches latest.pack to decide which path to take
    and hands the snapshot to the job created for that path (the
    _latest_info attribute of the job is seeded), so ResetJob and
    RebuildJob do not request it again. A resumed Reset/Rebuild job
    still fetches twice: it runs before the decision and takes its own
    snapshot.
    """

    @staticmethod
    def count_latest(server, monkeypatch):
        """
        Wrap the get_latest_info() of a server with a call counter.

        Args:
            server (MockServerFile): Server to wrap
            monkeypatch (pytest.MonkeyPatch): The fixture

        Returns:
            list: The call list, its length is the request count
        """
        calls = []
        original = server.get_latest_info

        async def counting():
            calls.append(1)
            return await original()

        monkeypatch.setattr(server, 'get_latest_info', counting)
        return calls

    @pytest.mark.trio
    async def test_up_to_date(self, app_folder, monkeypatch):
        """The same version path: the entry fetch serves ResetJob too."""
        await UnpackJob(NEW_PACK).run()
        calls = self.count_latest(SERVER, monkeypatch)
        with logger.mock_capture_writer():
            assert await DeployJob(server=SERVER).update()
        assert len(calls) == 1

    @pytest.mark.trio
    async def test_local_index_missing(self, app_folder, monkeypatch):
        """The unknown local version path: RebuildJob reuses the entry snapshot."""
        calls = self.count_latest(SERVER, monkeypatch)
        with logger.mock_capture_writer():
            assert await DeployJob(server=SERVER).update()
        assert len(calls) == 1
        assert read_tree() == NEW_TREE

    @pytest.mark.trio
    async def test_update_pack_missing(self, app_folder, monkeypatch):
        """The update pack 404 fallback: RebuildJob reuses the entry snapshot."""
        await UnpackJob(OLD_PACK).run()
        calls = self.count_latest(SERVER_NO_UPDATE, monkeypatch)
        with logger.mock_capture_writer():
            assert await DeployJob(server=SERVER_NO_UPDATE).update()
        assert len(calls) == 1

    @pytest.mark.trio
    async def test_update_pack_corrupt(self, app_folder, monkeypatch):
        """The corrupt update pack fallback: RebuildJob reuses the entry snapshot."""
        await UnpackJob(OLD_PACK).run()
        calls = self.count_latest(SERVER_CORRUPT_UPDATE, monkeypatch)
        with logger.mock_capture_writer():
            assert await DeployJob(server=SERVER_CORRUPT_UPDATE).update()
        assert len(calls) == 1

    @pytest.mark.trio
    async def test_incremental(self, app_folder, monkeypatch):
        """The incremental path: UpdateJob never reads latest.pack."""
        await UnpackJob(OLD_PACK).run()
        calls = self.count_latest(SERVER, monkeypatch)
        with logger.mock_capture_writer():
            assert await DeployJob(server=SERVER).update()
        assert len(calls) == 1

    @pytest.mark.trio
    async def test_resumed_job(self, app_folder, monkeypatch):
        """A resumed rebuild job takes its own snapshot before the decision
        fetches the post-job version: two requests."""
        await UnpackJob(OLD_PACK).run()
        RebuildJob(SERVER).write()
        calls = self.count_latest(SERVER, monkeypatch)
        with logger.mock_capture_writer():
            assert await DeployJob(server=SERVER).update()
        assert len(calls) == 2

    @pytest.mark.trio
    async def test_mid_flow_publish(self, app_folder, monkeypatch):
        """A version published after the entry fetch is left to the next
        update: the flow converges to the snapshot, no second fetch and
        no index download."""
        server = MockServerFile()
        server.register_version('new', NEW_PACK, NEW_INDEX)
        server.register_version('other', OTHER_PACK, OTHER_INDEX)
        # the flow starts on 'new', the entry fetch publishes 'other'
        server.latest_version = 'new'
        calls = []
        index_calls = []
        original_latest = server.get_latest_info
        original_index = server.get_index_pack

        async def latest_then_publish():
            calls.append(1)
            info = await original_latest()
            server.latest_version = 'other'
            return info

        async def counting_index(version):
            index_calls.append(version)
            return await original_index(version)

        monkeypatch.setattr(server, 'get_latest_info', latest_then_publish)
        monkeypatch.setattr(server, 'get_index_pack', counting_index)
        with logger.mock_capture_writer():
            await UnpackJob(NEW_PACK).run()
            assert await DeployJob(server=server).update()
        # the snapshot of the entry fetch decided the whole flow
        assert len(calls) == 1
        assert index_calls == []
        assert read_tree() == NEW_TREE
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')


class FakePhaseManager:
    """Phase callback stand-in: records the phases the job reports."""

    def __init__(self):
        self.phases = []

    async def __call__(self, phase):
        self.phases.append(phase)
        return True


class TestUpdatePhases:
    """The phase contract of update(update_manager): the backend transaction
    follows what the job is really doing ('downloading' while it downloads,
    'updating' from the first real file change on)."""

    @pytest.mark.trio
    async def test_incremental_reports_downloading_then_updating(self, app_folder):
        await UnpackJob(OLD_PACK).run()
        phases = FakePhaseManager()
        with logger.mock_capture_writer():
            assert await DeployJob(server=SERVER).update(phases)
        assert phases.phases == ['downloading', 'updating']
        assert read_tree() == NEW_TREE

    @pytest.mark.trio
    async def test_rebuild_reports_downloading_then_updating(self, app_folder):
        """The rebuild fallback (no update pack) reports the same pair."""
        await UnpackJob(OLD_PACK).run()
        phases = FakePhaseManager()
        with logger.mock_capture_writer():
            assert await DeployJob(server=SERVER_NO_UPDATE).update(phases)
        assert phases.phases == ['downloading', 'updating']
        assert read_tree() == NEW_TREE

    @pytest.mark.trio
    async def test_local_missing_reports_downloading_then_updating(self, app_folder):
        phases = FakePhaseManager()
        with logger.mock_capture_writer():
            assert await DeployJob(server=SERVER).update(phases)
        assert phases.phases == ['downloading', 'updating']
        assert read_tree() == NEW_TREE

    @pytest.mark.trio
    async def test_without_manager_nothing_is_reported(self, app_folder):
        """The CLI form runs the same flow without a callback."""
        await UnpackJob(OLD_PACK).run()
        with logger.mock_capture_writer():
            assert await DeployJob(server=SERVER).update()
        assert read_tree() == NEW_TREE

    @pytest.mark.trio
    async def test_abort_at_the_updating_phase(self, app_folder):
        """A phase callback returning False aborts the flow before the apply:
        nothing was changed and the temporary files are removed."""
        await UnpackJob(OLD_PACK).run()
        phases = []

        async def on_job_phase(phase):
            phases.append(phase)
            return phase != 'updating'

        with logger.mock_capture_writer():
            with pytest.raises(UpdateAborted):
                await DeployJob(server=SERVER).update(on_job_phase)
        assert phases == ['downloading', 'updating']
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')
        # the local tree was not changed (the pack was never applied)
        assert read_tree() == OLD_TREE

    @pytest.mark.trio
    async def test_abort_at_the_downloading_phase(self, app_folder):
        """The callback also guards the downloading phase (a cancel that
        arrived before the job could start): nothing was read or changed."""
        await UnpackJob(OLD_PACK).run()
        phases = []

        async def on_job_phase(phase):
            phases.append(phase)
            return False

        with logger.mock_capture_writer():
            with pytest.raises(UpdateAborted):
                await DeployJob(server=SERVER).update(on_job_phase)
        assert phases == ['downloading']
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')
        assert read_tree() == OLD_TREE
