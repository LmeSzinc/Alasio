"""
Tests for DeployJob: the unified entry of deploy jobs.

Uses conftest.WEBSITE_FULL_PACK (mock modern full-stack website).
Every test runs in the in-memory fake filesystem, no real files are
written: the app_folder fixture points env.PROJECT_ROOT at the fake
filesystem. Request-performing tests are async (pytest-trio), the pure
local job file tests stay sync.
"""
import os

import pytest

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job import DeployJob
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.ext import env
from alasio.ext.path.atomic import file_read_bytes
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack.conftest import WEBSITE_FILES, WEBSITE_FULL_PACK


class TestDeployJob:
    """The unified entry of DeployJob, one instance per deploy target."""

    @pytest.mark.trio
    async def test_unpack(self, app_folder):
        """DeployJob().unpack() writes the job file and unpacks all files."""
        with logger.mock_capture_writer():
            await DeployJob().unpack(WEBSITE_FULL_PACK)
        for path, (content, _) in WEBSITE_FILES.items():
            assert file_read_bytes(env.PROJECT_ROOT / path) == content, path
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_unpack_changed(self, app_folder):
        """unpack() records whether the flow changed real files: the first
        run unpacked everything, the second one found every file in place
        (its index commit alone does not count as a change)."""
        job = DeployJob()
        with logger.mock_capture_writer():
            await job.unpack(WEBSITE_FULL_PACK)
        assert job.changed is True
        job = DeployJob()
        with logger.mock_capture_writer():
            await job.unpack(WEBSITE_FULL_PACK)
        assert job.changed is False

    @pytest.mark.trio
    async def test_unpack_ignores_a_job_file(self, app_folder):
        """The job file of a previous run is a record, not a task: a new
        unpack never works from it (a corrupted one changes nothing), it
        re-derives every file and cleans the workspace up at the end."""
        job_file = env.PROJECT_ROOT / '.pack/workspace/job.pack'
        os.makedirs(job_file.uppath(), exist_ok=True)
        with open(job_file, 'wb') as f:
            f.write(b'garbage')
        with logger.mock_capture_writer():
            await DeployJob().unpack(WEBSITE_FULL_PACK)
        for path, (content, _) in WEBSITE_FILES.items():
            assert file_read_bytes(env.PROJECT_ROOT / path) == content, path
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    @pytest.mark.trio
    async def test_unpack_error_logged_warning(self, app_folder):
        """A write/unpack error is logged as warning, the workspace is
        kept for the next run to resume."""
        decoder = PackDecodeBase(WEBSITE_FULL_PACK)
        index_end = 5 + len(decoder.index_section)
        bad = bytearray(WEBSITE_FULL_PACK)
        bad[index_end + 100] ^= 0xFF
        with logger.mock_capture_writer() as capture:
            await DeployJob().unpack(bytes(bad))
        assert capture.backend.any_contains('Failed to unpack:')
        # the job file of the failed run is the resume base of the next
        # run, the tmp files written so far are its cache
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/workspace/job.pack') == bytes(bad)

    @pytest.mark.trio
    async def test_replace_error_logged_error(self, app_folder, monkeypatch):
        """A replace error is logged as error, the workspace (including
        the tmp files of the unpacked records) is kept."""
        def _raise(self):
            raise RuntimeError('replace failed')
        monkeypatch.setattr(UnpackJob, 'replace', _raise)
        with logger.mock_capture_writer() as capture:
            await DeployJob().unpack(WEBSITE_FULL_PACK)
        assert capture.backend.any_contains('Failed to replace file:')
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/workspace/job.pack') == WEBSITE_FULL_PACK
        # the unpack wrote every tmp file, the workspace holds them
        assert os.listdir(env.PROJECT_ROOT / '.pack/workspace')
