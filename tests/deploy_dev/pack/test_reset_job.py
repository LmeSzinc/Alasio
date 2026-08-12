"""
Tests for ResetJob: validate local files against the local index pack,
download the failed files from the server and replace them.

Uses conftest.WEBSITE_FULL_PACK (mock modern full-stack website) and
WEBSITE_SERVER (in-memory MockServerFile).
Every test runs in the in-memory fake filesystem, no real files are
written: the app_folder fixture points env.PROJECT_ROOT at the fake
filesystem.
"""
import os

import pytest
from conftest import WEBSITE_FILES, WEBSITE_FULL_PACK, WEBSITE_INDEX_PACK, WEBSITE_SERVER

from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.deploy.pack.job import DeployJob
from alasio.deploy.pack.job_base import PendingFile
from alasio.deploy.pack.job_reset import ResetJob
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.deploy.pack.pack_model import IdxInfo
from alasio.deploy_dev.pack.pack_repo import PackFull
from alasio.ext import env
from alasio.ext.path.atomic import file_read_bytes
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401


def make_pack(files, commit='c1'):
    """
    Build a full pack of a version.

    Args:
        files (dict[str, bytes]): {path: content}
        commit (str): Version of the pack. Defaults to 'c1'.

    Returns:
        bytes: Full pack data
    """
    repo = MockGitRepo()
    for path, content in files.items():
        repo.register_file(commit, path, content)
    return b''.join(PackFull(repo, commit=commit).iter_pack_data())


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


# a valid index pack of another version, its own checksum passes but
# the latest checksum differs, built before the fake filesystem is
# active (MockGitRepo reads the real .gitattributes file)
OTHER_INDEX = bytes(PackDecodeBase(
    make_pack({'x.txt': b'x'}, commit='other')).extract_index_pack())


class TestJobFile:
    """write() and the DeployJob dispatch."""

    def test_write_creates_job_file(self, app_folder):
        """write() stores the REST marker to the job file."""
        ResetJob(WEBSITE_SERVER).write()
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/workspace/job.pack') == b'REST\x00'

    def test_get_unfinished_job_none(self, app_folder):
        """No job file, DeployJob.get_unfinished_job() returns None."""
        assert DeployJob.get_unfinished_job() is None

    def test_get_unfinished_job_marker(self, app_folder):
        """A marker job file is dispatched to a resumed ResetJob."""
        ResetJob(WEBSITE_SERVER).write()
        job = DeployJob.get_unfinished_job(WEBSITE_SERVER)
        assert job is not None
        assert isinstance(job, ResetJob)
        assert job.run()
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
        assert ResetJob(WEBSITE_SERVER).validate_index()

    def test_missing(self, app_folder):
        """A missing index pack fails with a warning."""
        with logger.mock_capture_writer() as capture:
            assert not ResetJob(WEBSITE_SERVER).validate_index()
        assert capture.backend.any_contains('Failed to validate the index pack:')

    def test_corrupted(self, app_folder):
        """An index pack with a bad checksum fails."""
        bad = bytearray(WEBSITE_INDEX_PACK)
        # flip a byte inside the checksum digest (the last 20 bytes)
        bad[-5] ^= 0xFF
        os.makedirs(env.PROJECT_ROOT / '.pack', exist_ok=True)
        with open(env.PROJECT_ROOT / '.pack/index.pack', 'wb') as f:
            f.write(bad)
        assert not ResetJob(WEBSITE_SERVER).validate_index()

    def test_broken_structure(self, app_folder):
        """A file that is not a pack fails."""
        os.makedirs(env.PROJECT_ROOT / '.pack', exist_ok=True)
        with open(env.PROJECT_ROOT / '.pack/index.pack', 'wb') as f:
            f.write(b'garbage')
        assert not ResetJob(WEBSITE_SERVER).validate_index()

    def test_index_pack_read_once(self, app_folder, fs, monkeypatch):
        """validate_index() and validate_files() share one index read."""
        setup_app(fs)
        import alasio.deploy.pack.job_reset as module
        reads = []

        def _counting(file):
            reads.append(file)
            return file_read_bytes(file)
        monkeypatch.setattr(module, 'atomic_read_bytes', _counting)
        job = ResetJob(WEBSITE_SERVER)
        assert job.validate_index()
        assert job.validate_files()
        assert len(reads) == 1


class TestValidateLatest:
    """validate_latest(): compare the local index pack with the latest one."""

    def test_latest_matches(self, app_folder, fs):
        """A fresh index pack matches the server checksum."""
        setup_app(fs)
        assert ResetJob(WEBSITE_SERVER).validate_latest()

    def test_latest_outdated(self, app_folder, fs):
        """A self-consistent but outdated index pack fails the check."""
        setup_app(fs)
        # a valid index pack of another version, its own checksum
        # passes but the latest checksum differs
        with open(env.PROJECT_ROOT / '.pack/index.pack', 'wb') as f:
            f.write(OTHER_INDEX)
        job = ResetJob(WEBSITE_SERVER)
        with logger.mock_capture_writer() as capture:
            assert not job.validate_latest()
        assert capture.backend.any_contains('Failed to validate the latest index:')

    def test_no_server(self, app_folder):
        """A missing server raises PackDecodeError."""
        with pytest.raises(PackDecodeError, match='no server provided'):
            ResetJob(None).validate_latest()


class TestValidateFiles:
    """validate_files()."""

    def test_all_valid(self, app_folder, fs):
        """Every recorded file matches after a fresh unpack."""
        setup_app(fs)
        job = ResetJob(WEBSITE_SERVER)
        assert job.validate_files()
        assert job.error == []

    def test_missing_file(self, app_folder, fs):
        """A missing file is recorded in error."""
        setup_app(fs)
        os.remove(env.PROJECT_ROOT / 'backend/__init__.py')
        job = ResetJob(WEBSITE_SERVER)
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
        job = ResetJob(WEBSITE_SERVER)
        assert not job.validate_files()
        assert [item.info.path for item in job.error] == ['backend/config.py']

    def test_mode_mismatch(self, app_folder, fs):
        """A file with the wrong execute bits is recorded in error."""
        setup_app(fs)
        target = env.PROJECT_ROOT / 'backend/main.py'
        # pyfakefs does not apply os.chmod on Windows, recreate the file
        fs.remove(target)
        fs.create_file(target, st_mode=0o100755, contents=WEBSITE_FILES['backend/main.py'][0])
        job = ResetJob(WEBSITE_SERVER)
        assert not job.validate_files()
        error = job.error[0]
        assert error.info.path == 'backend/main.py'
        # the current content is written to a tmp file, replace()
        # chmod-ed the target to the record mode, no download is needed
        assert error.tmp
        assert error.mode == 0o644
        assert file_read_bytes(error.tmp) == WEBSITE_FILES['backend/main.py'][0]
        assert job._matches(error.info, job._read_current(error.tmp)).match

    def test_mode_755_matches(self, app_folder, fs):
        """A 755 record with execute bits passes."""
        setup_app(fs)
        # after unpack, 755 records are chmod-ed in replace()
        target = env.PROJECT_ROOT / 'scripts/deploy.sh'
        assert os.stat(target).st_mode & 0o111 == 0o111
        job = ResetJob(WEBSITE_SERVER)
        assert job.validate_files()
        assert job.error == []

    def test_deleted_marker_file_exists(self, app_folder, fs):
        """A file that should be deleted is recorded in error."""
        setup_app(fs)
        stale = env.PROJECT_ROOT / 'backend/tools/__init__.py'
        os.makedirs(stale.uppath(), exist_ok=True)
        with open(stale, 'wb') as f:
            f.write(b'old')
        job = ResetJob(WEBSITE_SERVER)
        assert not job.validate_files()
        assert [item.info.path for item in job.error] == ['backend/tools/__init__.py']

    def test_deleted_marker_missing(self, app_folder, fs):
        """A deleted marker file that does not exist passes."""
        setup_app(fs)
        assert not os.path.exists(env.PROJECT_ROOT / 'backend/tools/__init__.py')
        assert ResetJob(WEBSITE_SERVER).validate_files()

    def test_error_records(self, app_folder, fs):
        """Failed files are recorded as PendingFile with an empty tmp."""
        setup_app(fs)
        os.remove(env.PROJECT_ROOT / 'backend/__init__.py')
        with open(env.PROJECT_ROOT / 'backend/main.py', 'wb') as f:
            f.write(b'wrong')
        job = ResetJob(WEBSITE_SERVER)
        assert not job.validate_files()
        assert len(job.error) == 2
        for item in job.error:
            assert isinstance(item, PendingFile)
            assert isinstance(item.info, IdxInfo)
            assert item.tmp == ''
            # 644 records, the rewrite with mode 666 needs no chmod
            assert item.mode is None

    def test_files_without_index_raises(self, app_folder):
        """validate_files() assumes the caller validated the index pack."""
        with pytest.raises(FileNotFoundError):
            ResetJob(WEBSITE_SERVER).validate_files()


class TestValidateEolFix:
    """validate_files(): a fixable EOL mismatch is written to a tmp file."""

    def test_eol_fix_writes_tmp(self, app_folder, fs):
        """The converted content is written to a tmp, the record has it set."""
        setup_app(fs)
        # backend/config.py is eol=0 (LF), the local file is CRLF
        target = env.PROJECT_ROOT / 'backend/config.py'
        with open(target, 'wb') as f:
            f.write(WEBSITE_FILES['backend/config.py'][0].replace(b'\n', b'\r\n'))
        job = ResetJob(WEBSITE_SERVER)
        assert not job.validate_files()
        assert len(job.error) == 1
        item = job.error[0]
        assert item.info.path == 'backend/config.py'
        assert item.tmp
        # backend/config.py is a 644 record, the converted content is
        # written with mode 666 which needs no chmod
        assert item.mode is None
        # the tmp carries the converted content, it passes the check
        assert file_read_bytes(item.tmp) == WEBSITE_FILES['backend/config.py'][0]
        assert job._matches(item.info, job._read_current(item.tmp)).match

    def test_eol_fix_no_download(self, app_folder, fs):
        """download() moves a validation-fixed tmp to pending directly."""
        setup_app(fs)
        target = env.PROJECT_ROOT / 'backend/config.py'
        with open(target, 'wb') as f:
            f.write(WEBSITE_FILES['backend/config.py'][0].replace(b'\n', b'\r\n'))
        job = ResetJob(WEBSITE_SERVER)
        job.validate_index()
        job.validate_files()
        job.download()
        assert job.error == []
        assert len(job.pending) == 1
        item = job.pending[0]
        assert item.info.path == 'backend/config.py'
        assert file_read_bytes(item.tmp) == WEBSITE_FILES['backend/config.py'][0]

    def test_run_eol_fix_without_download(self, app_folder, fs, monkeypatch):
        """run() repairs an EOL mismatch locally, no download is needed."""
        setup_app(fs)
        target = env.PROJECT_ROOT / 'backend/config.py'
        with open(target, 'wb') as f:
            f.write(WEBSITE_FILES['backend/config.py'][0].replace(b'\n', b'\r\n'))

        def _fail(self, *a, **k):
            raise AssertionError('no download expected for an EOL mismatch')
        monkeypatch.setattr(WEBSITE_SERVER, 'get_file_content', _fail)
        job = ResetJob(WEBSITE_SERVER)
        assert job.run()
        assert job.error == []
        assert file_read_bytes(target) == WEBSITE_FILES['backend/config.py'][0]
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_run_eol_fix_755_record(self, app_folder, fs, monkeypatch):
        """An EOL-fixed 755 file keeps the execute bits after replace()."""
        setup_app(fs)
        target = env.PROJECT_ROOT / 'scripts/deploy.sh'
        # deploy.sh is eol=0 (LF) mode=755, the local file is CRLF
        with open(target, 'wb') as f:
            f.write(WEBSITE_FILES['scripts/deploy.sh'][0].replace(b'\n', b'\r\n'))
        monkeypatch.setattr(WEBSITE_SERVER, 'get_file_content', lambda *a, **k: b'bad data')
        job = ResetJob(WEBSITE_SERVER)
        assert job.run()
        assert file_read_bytes(target) == WEBSITE_FILES['scripts/deploy.sh'][0]
        # the fake filesystem simulates the POSIX file modes
        assert os.stat(target).st_mode & 0o111 == 0o111


class TestValidateModeFix:
    """validate_files(): a mode-only mismatch is fixed without a download."""

    def test_mode_fix_no_download(self, app_folder, fs, monkeypatch):
        """download() moves a validation-fixed tmp to pending directly."""
        setup_app(fs)
        target = env.PROJECT_ROOT / 'backend/main.py'
        # 644 record, the local file has the execute bits
        fs.remove(target)
        fs.create_file(target, st_mode=0o100755, contents=WEBSITE_FILES['backend/main.py'][0])
        job = ResetJob(WEBSITE_SERVER)
        job.validate_index()
        job.validate_files()
        job.download()
        assert job.error == []
        assert len(job.pending) == 1
        item = job.pending[0]
        assert item.info.path == 'backend/main.py'
        assert item.mode == 0o644
        assert file_read_bytes(item.tmp) == WEBSITE_FILES['backend/main.py'][0]

    def test_run_mode_fix_without_download(self, app_folder, fs, monkeypatch):
        """run() fixes a mode mismatch locally, no download is needed."""
        setup_app(fs)
        target = env.PROJECT_ROOT / 'backend/main.py'
        fs.remove(target)
        fs.create_file(target, st_mode=0o100755, contents=WEBSITE_FILES['backend/main.py'][0])

        def _fail(self, *a, **k):
            raise AssertionError('no download expected for a mode mismatch')
        monkeypatch.setattr(WEBSITE_SERVER, 'get_file_content', _fail)
        job = ResetJob(WEBSITE_SERVER)
        assert job.run()
        assert job.error == []
        assert file_read_bytes(target) == WEBSITE_FILES['backend/main.py'][0]
        # the fake filesystem simulates the POSIX file modes, the
        # target is chmod-ed to the record mode
        assert os.stat(target).st_mode & 0o111 == 0

    def test_run_mode_fix_755_record(self, app_folder, fs, monkeypatch):
        """A mode-only mismatch of a 755 record sets the execute bits."""
        setup_app(fs)
        target = env.PROJECT_ROOT / 'scripts/deploy.sh'
        # deploy.sh is mode=755, the local file has no execute bits
        fs.remove(target)
        fs.create_file(target, st_mode=0o100644, contents=WEBSITE_FILES['scripts/deploy.sh'][0])
        monkeypatch.setattr(WEBSITE_SERVER, 'get_file_content', lambda *a, **k: b'bad data')
        job = ResetJob(WEBSITE_SERVER)
        assert job.run()
        assert file_read_bytes(target) == WEBSITE_FILES['scripts/deploy.sh'][0]
        assert os.stat(target).st_mode & 0o111 == 0o111


class TestDownloadIndex:
    """download_index(): download the index pack from the server."""

    def test_download_index(self, app_folder, monkeypatch):
        """A corrupted index pack is replaced by the download."""
        bad = bytearray(WEBSITE_INDEX_PACK)
        bad[-5] ^= 0xFF
        os.makedirs(env.PROJECT_ROOT / '.pack', exist_ok=True)
        with open(env.PROJECT_ROOT / '.pack/index.pack', 'wb') as f:
            f.write(bad)
        import alasio.deploy.pack.job_reset as module
        reads = []

        def _counting(file):
            reads.append(file)
            return file_read_bytes(file)
        monkeypatch.setattr(module, 'atomic_read_bytes', _counting)
        job = ResetJob(WEBSITE_SERVER)
        assert not job.validate_index()
        job.download_index()
        # the decoder of the downloaded index pack is cached, the next
        # validation reads it without the file again
        assert job.validate_index()
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == WEBSITE_INDEX_PACK
        assert len(reads) == 1

    def test_download_index_invalid(self, app_folder, monkeypatch):
        """An index pack that fails to decode or validate raises."""
        server = WEBSITE_SERVER
        monkeypatch.setattr(server, 'get_index_pack', lambda version: b'bad data')
        job = ResetJob(server)
        with pytest.raises(PackDecodeError):
            job.download_index()


class TestDownload:
    """download(): fetch the failed files from the server."""

    def test_download_failed_files(self, app_folder, fs):
        """Failed files are downloaded to tmp files and moved to pending."""
        setup_app(fs)
        os.remove(env.PROJECT_ROOT / 'backend/__init__.py')
        with open(env.PROJECT_ROOT / 'backend/main.py', 'wb') as f:
            f.write(b'wrong')
        job = ResetJob(WEBSITE_SERVER)
        job.validate_index()
        job.validate_files()
        job.download()
        assert job.error == []
        assert len(job.pending) == 2
        for item in job.pending:
            assert isinstance(item, PendingFile)
            assert item.tmp
            assert os.path.exists(item.tmp)
            # the tmp content passes the size + sha1 check
            assert job._matches(item.info, job._read_current(item.tmp))

    def test_download_unsolvable(self, app_folder, fs, monkeypatch):
        """A file that cannot be downloaded stays in error."""
        setup_app(fs)
        os.remove(env.PROJECT_ROOT / 'backend/__init__.py')
        server = WEBSITE_SERVER
        monkeypatch.setattr(server, 'get_file_content', lambda *a, **k: b'bad data')
        job = ResetJob(server)
        job.validate_index()
        job.validate_files()
        with logger.mock_capture_writer() as capture:
            job.download()
        assert capture.backend.any_contains('Failed to download')
        assert [item.info.path for item in job.error] == ['backend/__init__.py']
        assert job.pending == []

    def test_download_deleted_marker(self, app_folder, fs):
        """A deleted marker needs no download, its target is removed."""
        setup_app(fs)
        stale = env.PROJECT_ROOT / 'backend/tools/__init__.py'
        os.makedirs(stale.uppath(), exist_ok=True)
        with open(stale, 'wb') as f:
            f.write(b'old')
        job = ResetJob(WEBSITE_SERVER)
        job.validate_index()
        job.validate_files()
        job.download()
        # no download for the deleted marker, but it is ready to replace
        assert job.error == []
        assert len(job.pending) == 1
        assert job.pending[0].info.edit == 2
        assert job.pending[0].tmp == ''

    def test_download_reuse_tmp(self, app_folder, fs):
        """A leftover tmp file that passes the check is reused."""
        setup_app(fs)
        os.remove(env.PROJECT_ROOT / 'backend/__init__.py')
        job = ResetJob(WEBSITE_SERVER)
        job.validate_index()
        job.validate_files()
        # write a valid tmp file, download() should reuse it
        item = job.error[0]
        tmp = job.workspace.joinpath(f'{item.info.size}_{item.info.sha1}_0.tmp')
        os.makedirs(tmp.uppath(), exist_ok=True)
        with open(tmp, 'wb') as f:
            f.write(b'')
        job.download()
        assert job.error == []
        assert job.pending[0].tmp == tmp


class TestReplace:
    """replace(): apply the downloaded files to the real files."""

    def test_replace_downloaded_files(self, app_folder, fs):
        """The tmp files are moved to the target paths."""
        setup_app(fs)
        os.remove(env.PROJECT_ROOT / 'backend/__init__.py')
        with open(env.PROJECT_ROOT / 'backend/main.py', 'wb') as f:
            f.write(b'wrong')
        job = ResetJob(WEBSITE_SERVER)
        job.validate_index()
        job.validate_files()
        job.download()
        job.replace()
        assert file_read_bytes(env.PROJECT_ROOT / 'backend/__init__.py') == b''
        assert file_read_bytes(env.PROJECT_ROOT / 'backend/main.py') == \
            WEBSITE_FILES['backend/main.py'][0]
        # the workspace is kept, run() cleans it up
        assert os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_replace_deleted_marker(self, app_folder, fs):
        """A deleted marker removes the stale file."""
        setup_app(fs)
        stale = env.PROJECT_ROOT / 'backend/tools/__init__.py'
        os.makedirs(stale.uppath(), exist_ok=True)
        with open(stale, 'wb') as f:
            f.write(b'old')
        job = ResetJob(WEBSITE_SERVER)
        job.validate_index()
        job.validate_files()
        job.download()
        job.replace()
        assert not os.path.exists(stale)

    def test_replace_mode(self, app_folder, fs):
        """A downloaded 755 file is executable after replace."""
        setup_app(fs)
        # remove the 755 record, download and replace it
        target = env.PROJECT_ROOT / 'scripts/deploy.sh'
        os.remove(target)
        job = ResetJob(WEBSITE_SERVER)
        job.validate_index()
        job.validate_files()
        job.download()
        job.replace()
        assert file_read_bytes(target) == WEBSITE_FILES['scripts/deploy.sh'][0]
        assert os.stat(target).st_mode & 0o111 == 0o111


class TestRun:
    """run(): write the marker, validate, download, replace."""

    def test_run_valid(self, app_folder, fs):
        """A valid folder passes and the workspace is cleaned."""
        setup_app(fs)
        job = ResetJob(WEBSITE_SERVER)
        assert job.run()
        assert job.error == []
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_run_repairs_files(self, app_folder, fs):
        """Failed files are downloaded and replaced."""
        setup_app(fs)
        os.remove(env.PROJECT_ROOT / 'backend/__init__.py')
        with open(env.PROJECT_ROOT / 'backend/main.py', 'wb') as f:
            f.write(b'wrong')
        job = ResetJob(WEBSITE_SERVER)
        assert job.run()
        assert job.error == []
        assert file_read_bytes(env.PROJECT_ROOT / 'backend/__init__.py') == b''
        assert file_read_bytes(env.PROJECT_ROOT / 'backend/main.py') == \
            WEBSITE_FILES['backend/main.py'][0]
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_run_downloads_index(self, app_folder):
        """A missing index pack is downloaded from the server."""
        job = ResetJob(WEBSITE_SERVER)
        with logger.mock_capture_writer() as capture:
            assert job.run()
        assert capture.backend.any_contains('Failed to validate the index pack:')
        # the index pack is downloaded and all files are unpacked
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == WEBSITE_INDEX_PACK
        assert file_read_bytes(env.PROJECT_ROOT / 'backend/main.py') == \
            WEBSITE_FILES['backend/main.py'][0]
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_run_corrupted_index(self, app_folder):
        """A corrupted index pack is downloaded from the server."""
        bad = bytearray(WEBSITE_INDEX_PACK)
        bad[-5] ^= 0xFF
        os.makedirs(env.PROJECT_ROOT / '.pack', exist_ok=True)
        with open(env.PROJECT_ROOT / '.pack/index.pack', 'wb') as f:
            f.write(bad)
        job = ResetJob(WEBSITE_SERVER)
        assert job.run()
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == WEBSITE_INDEX_PACK
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_run_outdated_index_repaired(self, app_folder, fs):
        """A self-consistent but outdated index pack is downloaded
        again, the files are checked against the latest index."""
        setup_app(fs)
        # a valid index pack of another version, its own checksum
        # passes but the latest checksum differs
        with open(env.PROJECT_ROOT / '.pack/index.pack', 'wb') as f:
            f.write(OTHER_INDEX)
        job = ResetJob(WEBSITE_SERVER)
        assert job.run()
        assert job.error == []
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == WEBSITE_INDEX_PACK
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_run_download_index_invalid(self, app_folder, monkeypatch):
        """A downloaded index pack that fails validation is cleaned up."""
        # the local index pack is broken, the server one is broken too
        bad = bytearray(WEBSITE_INDEX_PACK)
        bad[-5] ^= 0xFF
        os.makedirs(env.PROJECT_ROOT / '.pack', exist_ok=True)
        with open(env.PROJECT_ROOT / '.pack/index.pack', 'wb') as f:
            f.write(bad)
        server = WEBSITE_SERVER
        monkeypatch.setattr(server, 'get_index_pack', lambda version: bytes(bad))
        job = ResetJob(server)
        with logger.mock_capture_writer() as capture:
            assert not job.run()
        assert capture.backend.any_contains('Failed to reset:')
        # the broken index pack is not replaced, the workspace is cleaned
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == bytes(bad)
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_run_unsolvable(self, app_folder, fs, monkeypatch):
        """A file that cannot be downloaded stays in error."""
        setup_app(fs)
        os.remove(env.PROJECT_ROOT / 'backend/__init__.py')
        server = WEBSITE_SERVER
        monkeypatch.setattr(server, 'get_file_content', lambda *a, **k: b'bad data')
        job = ResetJob(server)
        assert not job.run()
        assert [item.info.path for item in job.error] == ['backend/__init__.py']
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_run_no_server(self, app_folder):
        """A missing server fails the run with a warning."""
        job = ResetJob(None)
        with logger.mock_capture_writer() as capture:
            assert not job.run()
        assert capture.backend.any_contains('Failed to reset:')
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_run_resumed_skips_write(self, app_folder, fs, monkeypatch):
        """A resumed job skips write(), the marker is already there."""
        setup_app(fs)
        ResetJob(WEBSITE_SERVER).write()

        def _fail(self):
            raise AssertionError('write() should not be called on resume')
        monkeypatch.setattr(ResetJob, 'write', _fail)
        job = DeployJob.get_unfinished_job(WEBSITE_SERVER)
        assert job is not None
        assert job.run()
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_run_write_error_cleaned(self, app_folder, monkeypatch):
        """A write error is logged as warning and the workspace is cleaned."""
        def _fail(self):
            raise RuntimeError('write failed')
        monkeypatch.setattr(ResetJob, 'write', _fail)
        with logger.mock_capture_writer() as capture:
            job = ResetJob(WEBSITE_SERVER)
            assert not job.run()
        assert capture.backend.any_contains('Failed to reset:')
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')
