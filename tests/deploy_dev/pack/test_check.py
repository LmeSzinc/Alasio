"""
Tests for DeployJob.check: the read-only version check of the update flow.

The server is an in-memory MockServerFile, the local ledger is built by
UnpackJob like the other e2e tests. check() is read-only: no lock is
taken and nothing is written, a check never conflicts with an update of
the same target.

Every test is async (pytest-trio): the network phase awaits the mock
transport on the test event loop, the local read runs in a worker thread.
"""
import os

import pytest

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job import DeployCheck, DeployJob
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.ext import env
from alasio.ext.path.atomic import file_read_bytes
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack.conftest import WEBSITE_FULL_PACK, WEBSITE_INDEX_PACK, MockServerFile

# the server publishes c1, a second server publishes c2 on top of it (the
# same packs under another version: the check only compares version strings
# and the latest checksum)
SERVER_C1 = MockServerFile()
SERVER_C1.register_version('c1', WEBSITE_FULL_PACK, WEBSITE_INDEX_PACK)
SERVER_C2 = MockServerFile()
SERVER_C2.register_version('c1', WEBSITE_FULL_PACK, WEBSITE_INDEX_PACK)
SERVER_C2.register_version('c2', WEBSITE_FULL_PACK, WEBSITE_INDEX_PACK)
# the checksum of the latest index pack, the trailing digest of the pack
LATEST_CHECKSUM = PackDecodeBase(WEBSITE_INDEX_PACK).index_checksum


class TestDeployCheck:
    """The read-only check of one deploy target."""

    @pytest.mark.trio
    async def test_uptodate(self, app_folder):
        """The local version is the latest one."""
        await UnpackJob(WEBSITE_FULL_PACK).run()

        check = await DeployJob(server=SERVER_C1).check()

        assert isinstance(check, DeployCheck)
        assert check.local == 'c1'
        assert check.latest == 'c1'
        assert check.checksum == LATEST_CHECKSUM
        assert check.uptodate() is True

    @pytest.mark.trio
    async def test_available(self, app_folder):
        """The local version is older than the latest one."""
        await UnpackJob(WEBSITE_FULL_PACK).run()

        check = await DeployJob(server=SERVER_C2).check()

        assert (check.local, check.latest) == ('c1', 'c2')
        assert check.checksum == LATEST_CHECKSUM
        assert check.uptodate() is False

    @pytest.mark.trio
    async def test_local_missing(self, app_folder):
        """Without a local index pack the version is unknown: never up to date."""
        check = await DeployJob(server=SERVER_C1).check()

        assert (check.local, check.latest) == ('', 'c1')
        assert check.uptodate() is False

    @pytest.mark.trio
    async def test_local_corrupted(self, app_folder):
        """A malformed local index pack reads as an unknown version."""
        os.makedirs(env.PROJECT_ROOT.joinpath('.pack'))
        with open(env.PROJECT_ROOT.joinpath('.pack/index.pack'), 'wb') as f:
            f.write(b'garbage')

        check = await DeployJob(server=SERVER_C1).check()

        assert check.local == ''
        assert check.uptodate() is False

    @pytest.mark.trio
    async def test_no_server(self, app_folder):
        """A target created without a server cannot check."""
        with pytest.raises(ValueError, match='no server'):
            await DeployJob().check()

    @pytest.mark.trio
    async def test_read_only(self, app_folder):
        """check() takes no lock and writes nothing."""
        await UnpackJob(WEBSITE_FULL_PACK).run()
        index_before = file_read_bytes(env.PROJECT_ROOT.joinpath('.pack/index.pack'))

        await DeployJob(server=SERVER_C2).check()

        assert file_read_bytes(env.PROJECT_ROOT.joinpath('.pack/index.pack')) == index_before
        assert not os.path.exists(env.PROJECT_ROOT.joinpath('.pack/workspace'))
        assert not os.path.exists(env.PROJECT_ROOT.joinpath('.pack/lock'))


class TestCheckLogs:
    """The version pair is logged like the version step of update()."""

    @pytest.mark.trio
    async def test_check_logs_the_versions(self, app_folder):
        await UnpackJob(WEBSITE_FULL_PACK).run()
        with logger.mock_capture_writer() as capture:
            await DeployJob(server=SERVER_C2).check()
        assert capture.backend.any_contains('[CurrentVersion] c1')
        assert capture.backend.any_contains('[LatestVersion] c2')

    @pytest.mark.trio
    async def test_update_logs_the_versions_once(self, app_folder):
        """update() takes the versions from check(), the pair is logged once."""
        await UnpackJob(WEBSITE_FULL_PACK).run()
        with logger.mock_capture_writer() as capture:
            assert await DeployJob(server=SERVER_C2).update()
        current = [log for log in capture.backend.logs if '[CurrentVersion]' in log['m']]
        latest = [log for log in capture.backend.logs if '[LatestVersion]' in log['m']]
        assert len(current) == 1
        assert len(latest) == 1
