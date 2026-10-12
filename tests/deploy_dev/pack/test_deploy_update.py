"""
Tests for DeployJob.update: the unified entry of the update flow.

The server is an in-memory MockServerFile serving two versions and the
update pack between them. The flow follows the draft in PackEncodeBase:
latest.pack is compared with the local version, a version mismatch
downloads the update pack /{new}/from_{old}.pack and applies it with
UpdateJob, the same version continues with ResetJob. The update(version)
form, covered by TestVersionDrivenUpdate, adds one verification: latest.pack
must publish the given version, otherwise the server provides no valid
update and the flow is refused.

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
from alasio.deploy.pack.job_update import UpdateJob
from alasio.deploy_dev.pack.pack_full import PackFull
from alasio.deploy_dev.pack.pack_update import PackUpdate
from alasio.ext import env
from alasio.ext.path.atomic import file_read_bytes, file_write
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


def workspace_file(name):
    """
    Path of a file in the workspace of the default target.

    Args:
        name (str): File name

    Returns:
        PathStr: Full path
    """
    return env.PROJECT_ROOT / '.pack' / 'workspace' / name


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
# the whole-pack checksum of the update pack: the trailing 20 bytes, the
# value the server serves and a kept pack is compared against
UPDATE_CHECKSUM = UPDATE[-20:].hex()
# a republished update pack: the same version pair, other records (what a
# server that regenerated the pack serves, the kept pack must not be reused)
OTHER_UPDATE = b''.join(PackUpdate(
    PackFull(make_repo({
        'old': {'other.txt': b'old\n'},
        'new': {'other.txt': b'new\n'},
    }), commit='new'), 'old').iter_pack_data())


def make_server():
    """
    A MockServerFile serving the two scenario versions and their update pack,
    like the module level SERVER (every test gets its own transfer counters).

    Returns:
        MockServerFile: The server
    """
    server = MockServerFile()
    server.register_version('old', OLD_PACK, OLD_INDEX)
    server.register_version('new', NEW_PACK, NEW_INDEX)
    server.register_update('old', 'new', UPDATE)
    return server


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
    follows what the job is really doing ('checking' for the version check,
    'downloading' while it downloads, 'updating' from the first real file
    change on)."""

    @pytest.mark.trio
    async def test_incremental_reports_checking_downloading_updating(self, app_folder):
        await UnpackJob(OLD_PACK).run()
        phases = FakePhaseManager()
        with logger.mock_capture_writer():
            assert await DeployJob(server=SERVER).update(phases)
        assert phases.phases == ['checking', 'downloading', 'updating']
        assert read_tree() == NEW_TREE

    @pytest.mark.trio
    async def test_rebuild_fallback_reports_checking_downloading_updating(self, app_folder):
        """The rebuild fallback (no update pack) reports the same phases: the
        download was attempted (and failed), the rebuild follows."""
        await UnpackJob(OLD_PACK).run()
        phases = FakePhaseManager()
        with logger.mock_capture_writer():
            assert await DeployJob(server=SERVER_NO_UPDATE).update(phases)
        assert phases.phases == ['checking', 'downloading', 'updating']
        assert read_tree() == NEW_TREE

    @pytest.mark.trio
    async def test_local_missing_reports_checking_then_updating(self, app_folder):
        """A missing local index has no version to compare and no pack to
        download: the rebuild from the latest index is one apply."""
        phases = FakePhaseManager()
        with logger.mock_capture_writer():
            assert await DeployJob(server=SERVER).update(phases)
        assert phases.phases == ['checking', 'updating']
        assert read_tree() == NEW_TREE

    @pytest.mark.trio
    async def test_up_to_date_reports_checking_then_updating(self, app_folder):
        """The same version has no pack to download: the repair path reports
        the check and the local changes."""
        await UnpackJob(NEW_PACK).run()
        phases = FakePhaseManager()
        with logger.mock_capture_writer():
            assert await DeployJob(server=SERVER).update(phases)
        assert phases.phases == ['checking', 'updating']
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
        assert phases == ['checking', 'downloading', 'updating']
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
            return phase != 'downloading'

        with logger.mock_capture_writer():
            with pytest.raises(UpdateAborted):
                await DeployJob(server=SERVER).update(on_job_phase)
        assert phases == ['checking', 'downloading']
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')
        assert read_tree() == OLD_TREE


class TestVersionDrivenUpdate:
    """update(version): the caller's version is verified against latest.pack.

    The dependency install of a mod passes the pin its requirements file
    holds for a distribution (alasio.deploy.simple_pip.pip_pack): the check
    of the flow verifies that the server publishes exactly that version as
    its latest one, a server that publishes another version provides no
    valid update and the flow is refused. The rest of the flow is the
    latest-driven one, the target is the latest version the check verified;
    the counting wrapper of TestLatestInfoSnapshot shows the pair is still
    fetched once per flow (the resumed flow fetches it twice, like the
    latest-driven one).
    """

    @pytest.mark.trio
    async def test_incremental(self, app_folder, monkeypatch):
        """A version mismatch applies the update pack of the version."""
        await UnpackJob(OLD_PACK).run()
        latest_calls = TestLatestInfoSnapshot.count_latest(SERVER, monkeypatch)
        with logger.mock_capture_writer():
            assert await DeployJob(server=SERVER).update(version='new')
        assert len(latest_calls) == 1
        assert read_tree() == NEW_TREE
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_rebuild_when_local_missing(self, app_folder, monkeypatch):
        """No local index: the rebuild reads the index pack of the version
        the check verified."""
        latest_calls = TestLatestInfoSnapshot.count_latest(SERVER, monkeypatch)
        index_calls = []
        original_index = SERVER.get_index_pack

        async def counting_index(version):
            index_calls.append(version)
            return await original_index(version)

        monkeypatch.setattr(SERVER, 'get_index_pack', counting_index)
        with logger.mock_capture_writer():
            assert await DeployJob(server=SERVER).update(version='new')
        assert len(latest_calls) == 1
        assert index_calls == ['new']
        assert read_tree() == NEW_TREE
        decoder = PackDecodeBase(file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack'))
        assert decoder.current_version == 'new'

    @pytest.mark.trio
    async def test_same_version_repairs(self, app_folder, monkeypatch):
        """The same version verifies the local files against the published
        index and repairs a damaged one from the full pack of the version."""
        await UnpackJob(NEW_PACK).run()
        file_write(env.PROJECT_ROOT / 'backend/main.py', b'damaged\n')
        latest_calls = TestLatestInfoSnapshot.count_latest(SERVER, monkeypatch)
        with logger.mock_capture_writer():
            assert await DeployJob(server=SERVER).update(version='new')
        assert len(latest_calls) == 1
        assert read_tree() == NEW_TREE

    @pytest.mark.trio
    async def test_update_pack_missing_falls_back(self, app_folder, monkeypatch):
        """A 404 of the update pack falls back to a rebuild from the index
        pack of the version."""
        await UnpackJob(OLD_PACK).run()
        latest_calls = TestLatestInfoSnapshot.count_latest(SERVER_NO_UPDATE, monkeypatch)
        with logger.mock_capture_writer():
            assert await DeployJob(server=SERVER_NO_UPDATE).update(version='new')
        assert len(latest_calls) == 1
        assert read_tree() == NEW_TREE
        decoder = PackDecodeBase(file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack'))
        assert decoder.current_version == 'new'

    @pytest.mark.trio
    async def test_version_not_published(self, app_folder):
        """A server that does not publish the requested version provides no
        valid update: the flow is refused before anything runs."""
        with pytest.raises(ValueError, match='no valid update'):
            await DeployJob(server=SERVER).update(version='gone')
        assert read_tree() == {}
        assert not os.path.exists(env.PROJECT_ROOT / '.pack')

    @pytest.mark.trio
    async def test_phases(self, app_folder):
        """The version-driven flow reports the same phases as the
        latest-driven one."""
        await UnpackJob(OLD_PACK).run()
        phases = FakePhaseManager()
        with logger.mock_capture_writer():
            assert await DeployJob(server=SERVER).update(phases, version='new')
        assert phases.phases == ['checking', 'downloading', 'updating']
        assert read_tree() == NEW_TREE


class TestKeptPack:
    """DeployJob._load_kept_pack: what the flow offers for reuse.

    The workspace of an interrupted run keeps the update pack it downloaded
    (job.pack): only the completed pack of the requested version pair is
    offered, with the digest of its whole content. The checks are a sanity
    gate, whether the kept pack may be used is decided by the server, see
    DeployJob._get_update_pack.
    """

    def test_kept_pack_of_the_pair(self, app_folder):
        """The pack of the version pair is offered with its whole-pack digest."""
        UpdateJob(UPDATE, server=SERVER).write()
        kept, checksum = DeployJob(server=SERVER)._load_kept_pack('old', 'new')
        assert kept == UPDATE
        assert checksum == UPDATE_CHECKSUM

    def test_pack_of_another_pair(self, app_folder):
        """A kept pack of another version pair is not offered."""
        UpdateJob(UPDATE, server=SERVER).write()
        assert DeployJob(server=SERVER)._load_kept_pack('other', 'new') == (None, '')

    def test_damaged_pack(self, app_folder):
        """A kept pack that fails a checksum is not offered."""
        damaged = bytearray(UPDATE)
        damaged[-1] ^= 0xFF
        file_write(workspace_file('job.pack'), bytes(damaged))
        assert DeployJob(server=SERVER)._load_kept_pack('old', 'new') == (None, '')

    def test_without_the_pack(self, app_folder):
        """Without a kept pack there is nothing to reuse."""
        assert DeployJob(server=SERVER)._load_kept_pack('old', 'new') == (None, '')


class TestUpdatePackReuse:
    """The kept update pack is reused only when the server confirms its digest.

    The download is skipped when the pack kept in the workspace is the pack of
    the version pair and the server still serves the very same file: the
    trailing 20 bytes digest of the pack (the sha1 of the whole file) must be
    the digest of the kept one, only those 20 bytes are transferred, see
    ServerFile.get_update_pack_checksum.
    """

    @pytest.mark.trio
    async def test_kept_pack_reused_when_the_server_confirms_it(self, app_folder):
        """The server serves the digest of the kept pack: the flow applies it
        without transferring the body again."""
        server = make_server()
        await UnpackJob(OLD_PACK).run()
        UpdateJob(UPDATE, server=server).write()
        with logger.mock_capture_writer() as capture:
            assert await DeployJob(server=server).update()
        assert capture.backend.any_contains(
            'Update pack unchanged, reusing the kept pack: old -> new')
        assert read_tree() == NEW_TREE
        # the trailing 20 bytes digest was transferred, not the body
        assert server.update_bytes == 20
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_download_is_reused_by_the_next_run(self, app_folder, monkeypatch):
        """A run killed after its download: the next run reuses the kept pack
        once the server confirmed its digest."""
        server = make_server()
        await UnpackJob(OLD_PACK).run()

        # the first run dies right after the download, before the apply: the
        # job file stays in the workspace (a killed process leaves exactly
        # this state behind)
        with monkeypatch.context() as dying:
            def failing_unpack(self):
                raise KeyboardInterrupt

            dying.setattr(UpdateJob, 'unpack', failing_unpack)
            with logger.mock_capture_writer():
                with pytest.raises(KeyboardInterrupt):
                    await DeployJob(server=server).update()
        assert file_read_bytes(workspace_file('job.pack')) == UPDATE
        assert server.update_bytes == len(UPDATE)

        # the next run reuses the kept pack, the body is not transferred again
        with logger.mock_capture_writer() as capture:
            assert await DeployJob(server=server).update()
        assert capture.backend.any_contains('reusing the kept pack')
        assert read_tree() == NEW_TREE
        assert server.update_bytes == len(UPDATE) + 20

    @pytest.mark.trio
    async def test_another_pack_served_downloads(self, app_folder):
        """The server serves another pack of the pair (another digest): the
        kept pack is not reused, the body is downloaded."""
        server = make_server()
        server.register_update('old', 'new', OTHER_UPDATE)
        await UnpackJob(OLD_PACK).run()
        UpdateJob(UPDATE, server=server).write()
        data = await DeployJob(server=server)._get_update_pack('old', 'new')
        assert data == OTHER_UPDATE
        assert server.update_bytes == 20 + len(OTHER_UPDATE)

    @pytest.mark.trio
    async def test_without_a_kept_pack_downloads(self, app_folder):
        """Without a kept pack the flow downloads the body as before."""
        server = make_server()
        await UnpackJob(OLD_PACK).run()
        with logger.mock_capture_writer():
            assert await DeployJob(server=server).update()
        assert read_tree() == NEW_TREE
        assert server.update_bytes == len(UPDATE)
