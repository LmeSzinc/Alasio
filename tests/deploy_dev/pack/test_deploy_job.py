"""
Tests for DeployJob: the unified entry of deploy jobs.

Uses conftest.WEBSITE_FULL_PACK (mock modern full-stack website).
Every test runs in the in-memory fake filesystem, no real files are
written: the app_folder fixture points env.PROJECT_ROOT at the fake
filesystem.
"""
import os

from conftest import WEBSITE_FILES, WEBSITE_FULL_PACK, WEBSITE_SERVER

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job import DeployJob
from alasio.deploy.pack.job_reset import ResetJob
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.ext import env
from alasio.ext.path.atomic import file_read_bytes
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401


class TestDeployJob:
    """The unified entry of DeployJob."""

    def test_unpack(self, app_folder):
        """DeployJob.unpack() writes the job file and unpacks all files."""
        with logger.mock_capture_writer():
            DeployJob.unpack(WEBSITE_FULL_PACK)
        for path, (content, _) in WEBSITE_FILES.items():
            assert file_read_bytes(env.PROJECT_ROOT / path) == content, path
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_get_unfinished_job_none(self, app_folder):
        """No job file, DeployJob.get_unfinished_job() returns None."""
        assert DeployJob.get_unfinished_job() is None

    def test_get_unfinished_job(self, app_folder):
        """A leftover job file is found and resumed."""
        UnpackJob(WEBSITE_FULL_PACK).write()
        job = DeployJob.get_unfinished_job()
        assert job is not None
        assert isinstance(job, UnpackJob)
        with logger.mock_capture_writer():
            job.run()
        assert file_read_bytes(env.PROJECT_ROOT / 'backend/main.py') == \
            WEBSITE_FILES['backend/main.py'][0]

    def test_get_unfinished_job_reset(self, app_folder):
        """A REST marker job file is a reset job."""
        ResetJob(WEBSITE_SERVER).write()
        job = DeployJob.get_unfinished_job(WEBSITE_SERVER)
        assert job is not None
        assert isinstance(job, ResetJob)
        with logger.mock_capture_writer():
            assert job.run()
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_unpack_finishes_reset_job(self, app_folder):
        """unpack() finishes the unfinished reset job first."""
        ResetJob(WEBSITE_SERVER).write()
        with logger.mock_capture_writer():
            DeployJob.unpack(WEBSITE_FULL_PACK)
        for path, (content, _) in WEBSITE_FILES.items():
            assert file_read_bytes(env.PROJECT_ROOT / path) == content, path
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_get_unfinished_job_corrupted(self, app_folder):
        """A corrupted job file is cleaned up with a warning."""
        job_file = env.PROJECT_ROOT / '.pack/workspace/job.pack'
        os.makedirs(job_file.uppath(), exist_ok=True)
        with open(job_file, 'wb') as f:
            f.write(b'garbage')
        with logger.mock_capture_writer() as capture:
            job = DeployJob.get_unfinished_job()
        assert job is None
        assert capture.backend.any_contains('Failed to read the unfinished job:')
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_unpack_finishes_unfinished_job(self, app_folder):
        """unpack() finishes the unfinished job first, no extra call."""
        UnpackJob(WEBSITE_FULL_PACK).write()
        with logger.mock_capture_writer():
            DeployJob.unpack(WEBSITE_FULL_PACK)
        for path, (content, _) in WEBSITE_FILES.items():
            assert file_read_bytes(env.PROJECT_ROOT / path) == content, path
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_run_resumed_skips_write(self, app_folder, monkeypatch):
        """A resumed job skips write(), the data is already in the file."""
        UnpackJob(WEBSITE_FULL_PACK).write()

        def _fail(self):
            raise AssertionError('write() should not be called on resume')
        monkeypatch.setattr(UnpackJob, 'write', _fail)
        job = DeployJob.get_unfinished_job()
        assert job is not None
        with logger.mock_capture_writer():
            job.run()
        assert file_read_bytes(env.PROJECT_ROOT / 'backend/main.py') == \
            WEBSITE_FILES['backend/main.py'][0]

    def test_unpack_corrupted_job_file(self, app_folder):
        """A corrupted job file is cleaned up, the new job still runs."""
        job_file = env.PROJECT_ROOT / '.pack/workspace/job.pack'
        os.makedirs(job_file.uppath(), exist_ok=True)
        with open(job_file, 'wb') as f:
            f.write(b'garbage')
        with logger.mock_capture_writer() as capture:
            DeployJob.unpack(WEBSITE_FULL_PACK)
        assert capture.backend.any_contains('Failed to read the unfinished job:')
        # the new job still unpacked all files
        assert file_read_bytes(env.PROJECT_ROOT / 'backend/main.py') == \
            WEBSITE_FILES['backend/main.py'][0]
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_unpack_error_logged_warning(self, app_folder):
        """A write/unpack error is logged as warning and cleaned up."""
        decoder = PackDecodeBase(WEBSITE_FULL_PACK)
        index_end = 5 + len(decoder.index_section)
        bad = bytearray(WEBSITE_FULL_PACK)
        bad[index_end + 100] ^= 0xFF
        with logger.mock_capture_writer() as capture:
            DeployJob.unpack(bytes(bad))
        assert capture.backend.any_contains('Failed to unpack:')
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_replace_error_logged_error(self, app_folder, monkeypatch):
        """A replace error is logged as error and cleaned up."""
        def _raise(self):
            raise RuntimeError('replace failed')
        monkeypatch.setattr(UnpackJob, 'replace', _raise)
        with logger.mock_capture_writer() as capture:
            DeployJob.unpack(WEBSITE_FULL_PACK)
        assert capture.backend.any_contains('Failed to replace file:')
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')
