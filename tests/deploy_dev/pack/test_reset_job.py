"""
Tests for ResetJob: validate local files against the local index pack.

Uses conftest.WEBSITE_FULL_PACK (mock modern full-stack website).
Every test runs in a pyfakefs in-memory filesystem, no real files are
written: the app_folder fixture points env.PROJECT_ROOT at the fake
filesystem.
"""
import os

import pytest
from conftest import WEBSITE_FILES, WEBSITE_FULL_PACK, WEBSITE_INDEX_PACK

from alasio.deploy.pack.job import DeployJob
from alasio.deploy.pack.job_base import PendingFile
from alasio.deploy.pack.job_reset import ResetJob
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.deploy.pack.pack_model import IdxInfo
from alasio.ext import env
from alasio.ext.path.atomic import file_read_bytes
from alasio.logger import logger


def setup_app(fs):
    """
    Unpack the website, so .pack/index.pack and all files exist.

    The 755 records are recreated with the execute bits on every
    platform: pyfakefs does not apply os.chmod on Windows, so the
    unpacked 755 files would have no execute bits there.
    """
    UnpackJob(WEBSITE_FULL_PACK).run()
    for path in ('scripts/deploy.sh', 'scripts/run.sh'):
        target = env.PROJECT_ROOT / path
        fs.remove(target)
        fs.create_file(target, st_mode=0o100755, contents=WEBSITE_FILES[path][0])


class TestJobFile:
    """write() and the DeployJob dispatch."""

    def test_write_creates_job_file(self, app_folder):
        """write() stores the REST marker to the job file."""
        ResetJob().write()
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/workspace/job.pack') == b'REST\x00'

    def test_get_unfinished_job_none(self, app_folder):
        """No job file, DeployJob.get_unfinished_job() returns None."""
        assert DeployJob.get_unfinished_job() is None

    def test_get_unfinished_job_marker(self, app_folder):
        """A marker job file is dispatched to a resumed ResetJob."""
        ResetJob().write()
        job = DeployJob.get_unfinished_job()
        assert job is not None
        assert isinstance(job, ResetJob)
        job.run()
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_get_unfinished_job_pack(self, app_folder):
        """A pack job file is dispatched to UnpackJob, not ResetJob."""
        UnpackJob(WEBSITE_FULL_PACK).write()
        job = DeployJob.get_unfinished_job()
        assert job is not None
        assert isinstance(job, UnpackJob)


class TestValidateIndex:
    """validate_index()."""

    def test_valid(self, app_folder, fs):
        """A valid index pack passes."""
        setup_app(fs)
        assert ResetJob().validate_index()

    def test_missing(self, app_folder):
        """A missing index pack fails with a warning."""
        with logger.mock_capture_writer() as capture:
            assert not ResetJob().validate_index()
        assert capture.backend.any_contains('Failed to validate the index pack:')

    def test_corrupted(self, app_folder):
        """An index pack with a bad checksum fails."""
        bad = bytearray(WEBSITE_INDEX_PACK)
        # flip a byte inside the checksum digest (the last 20 bytes)
        bad[-5] ^= 0xFF
        os.makedirs(env.PROJECT_ROOT / '.pack', exist_ok=True)
        with open(env.PROJECT_ROOT / '.pack/index.pack', 'wb') as f:
            f.write(bad)
        assert not ResetJob().validate_index()

    def test_broken_structure(self, app_folder):
        """A file that is not a pack fails."""
        os.makedirs(env.PROJECT_ROOT / '.pack', exist_ok=True)
        with open(env.PROJECT_ROOT / '.pack/index.pack', 'wb') as f:
            f.write(b'garbage')
        assert not ResetJob().validate_index()

    def test_index_pack_read_once(self, app_folder, fs, monkeypatch):
        """validate_index() and validate_files() share one index read."""
        setup_app(fs)
        import alasio.deploy.pack.job_reset as module
        reads = []

        def _counting(file):
            reads.append(file)
            return file_read_bytes(file)
        monkeypatch.setattr(module, 'atomic_read_bytes', _counting)
        job = ResetJob()
        assert job.validate_index()
        assert job.validate_files()
        assert len(reads) == 1


class TestValidateFiles:
    """validate_files()."""

    def test_all_valid(self, app_folder, fs):
        """Every recorded file matches after a fresh unpack."""
        setup_app(fs)
        job = ResetJob()
        assert job.validate_files()
        assert job.error == []

    def test_missing_file(self, app_folder, fs):
        """A missing file is recorded in error."""
        setup_app(fs)
        os.remove(env.PROJECT_ROOT / 'backend/__init__.py')
        job = ResetJob()
        assert not job.validate_files()
        assert [item.info.path for item in job.error] == ['backend/__init__.py']

    @pytest.mark.parametrize('content', [
        b'wrong content',
        b'',
        b'A' * len(WEBSITE_FILES['backend/config.py'][0]),
    ])
    def test_wrong_content(self, app_folder, fs, content):
        """A file with wrong size or sha1 is recorded in error."""
        setup_app(fs)
        with open(env.PROJECT_ROOT / 'backend/config.py', 'wb') as f:
            f.write(content)
        job = ResetJob()
        assert not job.validate_files()
        assert [item.info.path for item in job.error] == ['backend/config.py']

    def test_mode_mismatch(self, app_folder, fs):
        """A file with the wrong execute bits is recorded in error."""
        setup_app(fs)
        target = env.PROJECT_ROOT / 'backend/main.py'
        # pyfakefs does not apply os.chmod on Windows, recreate the file
        fs.remove(target)
        fs.create_file(target, st_mode=0o100755, contents=WEBSITE_FILES['backend/main.py'][0])
        job = ResetJob()
        assert not job.validate_files()
        error = job.error[0]
        # the current mode is recorded, it guides the caller to fix the mode
        assert error.info.path == 'backend/main.py'
        assert error.tmp == ''
        assert error.current_mode & 0o111 == 0o111

    @pytest.mark.skipif(os.name == 'nt', reason='file mode is meaningless on Windows')
    def test_mode_755_matches(self, app_folder, fs):
        """A 755 record with execute bits passes."""
        setup_app(fs)
        # after unpack, 755 records are chmod-ed in replace()
        target = env.PROJECT_ROOT / 'scripts/deploy.sh'
        assert os.stat(target).st_mode & 0o111 == 0o111
        job = ResetJob()
        assert job.validate_files()
        assert job.error == []

    def test_deleted_marker_file_exists(self, app_folder, fs):
        """A file that should be deleted is recorded in error."""
        setup_app(fs)
        stale = env.PROJECT_ROOT / 'backend/tools/__init__.py'
        os.makedirs(stale.uppath(), exist_ok=True)
        with open(stale, 'wb') as f:
            f.write(b'old')
        job = ResetJob()
        assert not job.validate_files()
        assert [item.info.path for item in job.error] == ['backend/tools/__init__.py']

    def test_deleted_marker_missing(self, app_folder, fs):
        """A deleted marker file that does not exist passes."""
        setup_app(fs)
        assert not os.path.exists(env.PROJECT_ROOT / 'backend/tools/__init__.py')
        assert ResetJob().validate_files()

    def test_error_records(self, app_folder, fs):
        """Failed files are recorded as PendingFile with an empty tmp."""
        setup_app(fs)
        os.remove(env.PROJECT_ROOT / 'backend/__init__.py')
        with open(env.PROJECT_ROOT / 'backend/main.py', 'wb') as f:
            f.write(b'wrong')
        job = ResetJob()
        assert not job.validate_files()
        assert len(job.error) == 2
        for item in job.error:
            assert isinstance(item, PendingFile)
            assert isinstance(item.info, IdxInfo)
            assert item.tmp == ''
            assert item.current_mode == 0o666

    def test_files_without_index_raises(self, app_folder):
        """validate_files() assumes the caller validated the index pack."""
        with pytest.raises(FileNotFoundError):
            ResetJob().validate_files()


class TestRun:
    """run(): write the marker, validate, clean the workspace."""

    def test_run_valid(self, app_folder, fs):
        """A valid folder passes and the workspace is cleaned."""
        setup_app(fs)
        job = ResetJob()
        assert job.run()
        assert job.error == []
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_run_failed_files(self, app_folder, fs):
        """Failed files are collected and the workspace is cleaned."""
        setup_app(fs)
        os.remove(env.PROJECT_ROOT / 'backend/__init__.py')
        job = ResetJob()
        assert not job.run()
        assert [item.info.path for item in job.error] == ['backend/__init__.py']
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_run_missing_index(self, app_folder):
        """A missing index pack fails the run."""
        job = ResetJob()
        with logger.mock_capture_writer() as capture:
            assert not job.run()
        assert capture.backend.any_contains('Failed to validate the index pack:')
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_run_resumed_skips_write(self, app_folder, fs, monkeypatch):
        """A resumed job skips write(), the marker is already there."""
        setup_app(fs)
        ResetJob().write()

        def _fail(self):
            raise AssertionError('write() should not be called on resume')
        monkeypatch.setattr(ResetJob, 'write', _fail)
        job = DeployJob.get_unfinished_job()
        assert job is not None
        assert job.run()
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_run_write_error_cleaned(self, app_folder, monkeypatch):
        """A write error is logged as warning and the workspace is cleaned."""
        def _fail(self):
            raise RuntimeError('write failed')
        monkeypatch.setattr(ResetJob, 'write', _fail)
        with logger.mock_capture_writer() as capture:
            job = ResetJob()
            assert not job.run()
        assert capture.backend.any_contains('Failed to validate:')
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')
