"""
Tests for UnpackJob: interruptible and resumable full pack unpack.

Uses conftest.WEBSITE_FULL_PACK (mock modern full-stack website).
Every test runs in a pyfakefs in-memory filesystem, no real files are
written: the app_folder fixture points env.PROJECT_ROOT at the fake
filesystem.
"""
import os

import pytest
from conftest import COMMIT, WEBSITE_FILES, WEBSITE_FULL_PACK, WEBSITE_INDEX_PACK

from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.deploy.pack.job_unpack import CurrentFile, PendingFile, UnpackJob
from alasio.deploy.pack.pack_model import IdxInfo
from alasio.ext import env
from alasio.ext.path.atomic import file_read_bytes
from alasio.logger import logger


def run_job(data=WEBSITE_FULL_PACK):
    """The caller flow: run() does write, unpack and replace."""
    UnpackJob(data).run()


class TestJobFile:
    """write() and get_unfinished_job()."""

    def test_write_creates_job_file(self, app_folder):
        """write() stores the data to the job file for crash recovery."""
        UnpackJob(WEBSITE_FULL_PACK).write()
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/workspace/job.pack') == \
            WEBSITE_FULL_PACK

    def test_get_unfinished_job_none(self, app_folder):
        """No job file, get_unfinished_job() returns None."""
        assert UnpackJob.get_unfinished_job() is None

    def test_get_unfinished_job_resumes(self, app_folder):
        """A leftover job file is read into an UnpackJob and resumed."""
        UnpackJob(WEBSITE_FULL_PACK).write()
        job = UnpackJob.get_unfinished_job()
        assert job is not None
        # resume does not need write() again, the data comes from the file
        job.run()
        for path, (content, _) in WEBSITE_FILES.items():
            assert file_read_bytes(env.PROJECT_ROOT / path) == content, path
        # the finished job is cleaned
        assert UnpackJob.get_unfinished_job() is None


class TestUnpack:
    """unpack() phase: write tmp files, real files untouched."""

    def test_unpack_writes_tmp_only(self, app_folder):
        """unpack() writes tmp files, real files stay untouched."""
        job = UnpackJob(WEBSITE_FULL_PACK)
        job.write()
        job.unpack()
        # real files are not applied yet
        assert not os.path.exists(env.PROJECT_ROOT / 'backend/main.py')
        # index pack is written in unpack()
        assert os.path.exists(env.PROJECT_ROOT / '.pack/index.pack')
        # the workspace has the job file and tmp files
        assert os.listdir(env.PROJECT_ROOT / '.pack/workspace')

    def test_unpack_does_not_write_job_file(self, app_folder):
        """unpack() does not write the job file, the caller does."""
        UnpackJob(WEBSITE_FULL_PACK).unpack()
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace/job.pack')

    def test_index_pack_written(self, app_folder):
        """The front part of the full pack is written to .pack/index.pack."""
        job = UnpackJob(WEBSITE_FULL_PACK)
        job.write()
        job.unpack()
        data = file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack')
        assert data == WEBSITE_INDEX_PACK
        # it must be a valid index pack
        decoder = PackDecodeBase(data)
        decoder.validate_index()
        assert decoder.version == COMMIT

    def test_pending_records(self, app_folder):
        """unpack() fills self.pending with PendingFile records."""
        job = UnpackJob(WEBSITE_FULL_PACK)
        job.write()
        job.unpack()
        assert isinstance(job.pending, list)
        assert job.pending
        assert all(isinstance(item, PendingFile) for item in job.pending)
        # every fileinfo record is in pending, refinfo is not unpacked
        assert len(job.pending) == len(WEBSITE_FILES) + 1  # + D marker
        # deleted marker record, its target is removed in replace()
        deleted = [
            item for item in job.pending
            if item.info.path == 'backend/tools/__init__.py'
        ]
        assert len(deleted) == 1
        assert deleted[0].info.edit == 2
        assert deleted[0].tmp == ''
        # a normal record carries the file info, the tmp file and the
        # mode after replace(), python writes 666 by default
        normal = [
            item for item in job.pending
            if item.info.path == 'backend/main.py'
        ]
        assert len(normal) == 1
        assert normal[0].info.edit == 0
        assert isinstance(normal[0].info, IdxInfo)
        assert normal[0].tmp
        assert normal[0].current_mode == 0o666
        # tmp file name is built from the record and the index
        info = normal[0].info
        assert os.path.exists(normal[0].tmp)


class TestUnpackReplace:
    """Full flow: unpack() then replace()."""

    def test_unpack_replace_all_files(self, app_folder):
        """Every file in the pack exists with the exact content."""
        run_job()
        for path, (content, _) in WEBSITE_FILES.items():
            assert file_read_bytes(env.PROJECT_ROOT / path) == content, path

    def test_empty_file(self, app_folder):
        """Empty files are created as empty files."""
        run_job()
        assert file_read_bytes(env.PROJECT_ROOT / 'backend/__init__.py') == b''

    def test_deleted_marker_removes_file(self, app_folder):
        """D (deleted) marker files must not exist after replace()."""
        # simulate a stale file left by a previous version
        stale = env.PROJECT_ROOT / 'backend/tools/__init__.py'
        os.makedirs(stale.uppath(), exist_ok=True)
        with open(stale, 'wb') as f:
            f.write(b'old')
        run_job()
        assert not os.path.exists(stale)

    def test_workspace_cleaned(self, app_folder):
        """job.pack and tmp files are removed after a successful run."""
        run_job()
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_unpack_replace_twice_is_idempotent(self, app_folder):
        """Running into a folder with valid files succeeds and skips."""
        run_job()
        run_job()
        for path, (content, _) in WEBSITE_FILES.items():
            assert file_read_bytes(env.PROJECT_ROOT / path) == content, path
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')


class TestCurrentRead:
    """_read_current() and _matches() with CurrentFile."""

    def test_read_current(self, app_folder):
        """_read_current() reads data and st_mode in one file open."""
        target = env.PROJECT_ROOT / 'backend/config.py'
        os.makedirs(target.uppath(), exist_ok=True)
        content = WEBSITE_FILES['backend/config.py'][0]
        with open(target, 'wb') as f:
            f.write(content)
        current = UnpackJob(WEBSITE_FULL_PACK)._read_current(target)
        assert isinstance(current, CurrentFile)
        assert current.exist
        assert current.data == content
        # st_mode is stored as-is, type bits included
        assert current.mode == 0o100666

    def test_read_current_missing(self, app_folder):
        """_read_current() returns exist=False for a missing file."""
        current = UnpackJob(WEBSITE_FULL_PACK)._read_current(
            env.PROJECT_ROOT / 'not/exist.py')
        assert isinstance(current, CurrentFile)
        assert not current.exist
        assert current.data == b''
        assert current.mode == 0

    def test_matches_with_current(self, app_folder):
        """_matches() takes a CurrentFile, a missing file never matches."""
        job = UnpackJob(WEBSITE_FULL_PACK)
        decoder = PackDecodeBase(WEBSITE_FULL_PACK)
        info = decoder.fileinfo['backend/config.py']
        target = env.PROJECT_ROOT / 'backend/config.py'
        os.makedirs(target.uppath(), exist_ok=True)
        with open(target, 'wb') as f:
            f.write(WEBSITE_FILES['backend/config.py'][0])
        assert job._matches(info, job._read_current(target))
        assert not job._matches(info, job._read_current(env.PROJECT_ROOT / 'not/exist.py'))

    def test_matches_wrong_content(self, app_folder):
        """A CurrentFile with wrong content does not match."""
        job = UnpackJob(WEBSITE_FULL_PACK)
        decoder = PackDecodeBase(WEBSITE_FULL_PACK)
        info = decoder.fileinfo['backend/config.py']
        current = CurrentFile(exist=True, data=b'wrong content', mode=0o100644)
        assert not job._matches(info, current)


class TestCallerFlow:
    """The exact caller usage of UnpackJob."""

    def test_resume_then_new_job(self, app_folder):
        """get_unfinished_job() first, then unpack the new data."""
        # a previous run was interrupted, the job file is left behind
        UnpackJob(WEBSITE_FULL_PACK).write()
        # finish the unfinished job first
        job = UnpackJob.get_unfinished_job()
        if job is not None:
            job.run()
        # then unpack the new data
        job = UnpackJob(WEBSITE_FULL_PACK)
        job.run()
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')
        for path, (content, _) in WEBSITE_FILES.items():
            assert file_read_bytes(env.PROJECT_ROOT / path) == content, path


class TestUnpackSkip:
    """Skip logic: existing files that pass the size + sha1 check."""

    def test_skip_existing_valid_file(self, app_folder):
        """A valid existing file is kept as-is."""
        content = WEBSITE_FILES['backend/config.py'][0]
        target = env.PROJECT_ROOT / 'backend/config.py'
        os.makedirs(target.uppath(), exist_ok=True)
        with open(target, 'wb') as f:
            f.write(content)
        run_job()
        assert file_read_bytes(target) == content

    def test_skip_existing_crlf_file(self, app_folder):
        """A valid CRLF file (eol=1) is recognized and skipped."""
        content = WEBSITE_FILES['backend/requirements.txt'][0]
        target = env.PROJECT_ROOT / 'backend/requirements.txt'
        os.makedirs(target.uppath(), exist_ok=True)
        with open(target, 'wb') as f:
            f.write(content)
        run_job()
        assert file_read_bytes(target) == content

    def test_eol_mismatch_lf_vs_crlf(self, app_folder):
        """A LF file is replaced when the record expects CRLF (eol=1)."""
        # backend/requirements.txt is eol=1 (CRLF), the local file is LF
        lf_content = WEBSITE_FILES['backend/requirements.txt'][0].replace(b'\r\n', b'\n')
        target = env.PROJECT_ROOT / 'backend/requirements.txt'
        os.makedirs(target.uppath(), exist_ok=True)
        with open(target, 'wb') as f:
            f.write(lf_content)
        run_job()
        # replaced with the CRLF content of the record
        assert file_read_bytes(target) == WEBSITE_FILES['backend/requirements.txt'][0]

    def test_eol_mismatch_crlf_vs_lf(self, app_folder):
        """A CRLF file is replaced when the record expects LF (eol=0)."""
        # backend/config.py is eol=0 (LF), the local file is CRLF
        crlf_content = WEBSITE_FILES['backend/config.py'][0].replace(b'\n', b'\r\n')
        target = env.PROJECT_ROOT / 'backend/config.py'
        os.makedirs(target.uppath(), exist_ok=True)
        with open(target, 'wb') as f:
            f.write(crlf_content)
        run_job()
        # replaced with the LF content of the record
        assert file_read_bytes(target) == WEBSITE_FILES['backend/config.py'][0]

    def test_eol_mismatch_mixed_vs_crlf(self, app_folder):
        """A mixed LF/CRLF file is replaced when the record expects CRLF."""
        # backend/requirements.txt is eol=1 (CRLF), the local file is mixed
        content = WEBSITE_FILES['backend/requirements.txt'][0]
        mixed = content.replace(b'\r\n', b'\n', 1)
        target = env.PROJECT_ROOT / 'backend/requirements.txt'
        os.makedirs(target.uppath(), exist_ok=True)
        with open(target, 'wb') as f:
            f.write(mixed)
        run_job()
        # replaced with the pure CRLF content of the record
        assert file_read_bytes(target) == content

    def test_eol_mismatch_mixed_vs_lf(self, app_folder):
        """A mixed LF/CRLF file is replaced when the record expects LF."""
        # backend/config.py is eol=0 (LF), the local file is mixed
        content = WEBSITE_FILES['backend/config.py'][0]
        mixed = content.replace(b'\n', b'\r\n', 1)
        target = env.PROJECT_ROOT / 'backend/config.py'
        os.makedirs(target.uppath(), exist_ok=True)
        with open(target, 'wb') as f:
            f.write(mixed)
        run_job()
        # replaced with the pure LF content of the record
        assert file_read_bytes(target) == content

    def test_overwrite_invalid_file(self, app_folder):
        """A file with wrong content is overwritten by the pack data."""
        target = env.PROJECT_ROOT / 'backend/config.py'
        os.makedirs(target.uppath(), exist_ok=True)
        with open(target, 'wb') as f:
            f.write(b'stale content, should be replaced')
        run_job()
        assert file_read_bytes(target) == WEBSITE_FILES['backend/config.py'][0]

    def test_resume_from_job_file(self, app_folder):
        """get_unfinished_job() resumes the interrupted unpack."""
        UnpackJob(WEBSITE_FULL_PACK).write()
        job = UnpackJob.get_unfinished_job()
        assert job is not None
        job.run()
        assert file_read_bytes(env.PROJECT_ROOT / 'backend/main.py') == \
            WEBSITE_FILES['backend/main.py'][0]
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_reuse_tmp_file(self, app_folder):
        """A valid leftover tmp file is moved without decompressing again."""
        # locate the record of backend/main.py in the pack
        decoder = PackDecodeBase(WEBSITE_FULL_PACK)
        index = next(
            i for i, info in enumerate(decoder.idx_info)
            if info.path == 'backend/main.py'
        )
        info = decoder.idx_info[index]
        # write a valid tmp file, unpack() should reuse it
        tmp = env.PROJECT_ROOT / f'.pack/workspace/{info.size}_{info.sha1}_{index}.tmp'
        os.makedirs(tmp.uppath(), exist_ok=True)
        with open(tmp, 'wb') as f:
            f.write(WEBSITE_FILES['backend/main.py'][0])
        run_job()
        assert file_read_bytes(env.PROJECT_ROOT / 'backend/main.py') == \
            WEBSITE_FILES['backend/main.py'][0]
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')


class TestFailure:
    """Failure keeps the workspace for the next run to resume."""

    def test_invalid_pack_raises(self, app_folder):
        """Not a pack file raises PackDecodeError."""
        with pytest.raises(PackDecodeError):
            UnpackJob(b'not a pack file').unpack()

    def test_corrupt_pack_raises(self, app_folder):
        """A pack with a corrupted data section fails validation."""
        decoder = PackDecodeBase(WEBSITE_FULL_PACK)
        index_end = 5 + len(decoder.index_section)
        bad = bytearray(WEBSITE_FULL_PACK)
        bad[index_end + 100] ^= 0xFF
        with pytest.raises(PackDecodeError):
            UnpackJob(bytes(bad)).unpack()

    def test_failure_keeps_job_file(self, app_folder):
        """job.pack survives a failed run for crash recovery."""
        decoder = PackDecodeBase(WEBSITE_FULL_PACK)
        index_end = 5 + len(decoder.index_section)
        bad = bytearray(WEBSITE_FULL_PACK)
        bad[index_end + 100] ^= 0xFF
        job = UnpackJob(bytes(bad))
        job.write()
        with pytest.raises(PackDecodeError):
            job.unpack()
        assert os.path.exists(env.PROJECT_ROOT / '.pack/workspace/job.pack')
        # the unfinished job can still be found
        assert UnpackJob.get_unfinished_job() is not None


@pytest.mark.skipif(os.name == 'nt', reason='file mode is meaningless on Windows')
class TestExecutableMode:
    """Executable bit handling."""

    def test_mode_755_is_executable(self, app_folder):
        """Files with mode 755 are executable after replace()."""
        run_job()
        assert os.stat(env.PROJECT_ROOT / 'scripts/deploy.sh').st_mode & 0o111

    def test_mode_644_is_not_executable(self, app_folder):
        """Files with mode 644 are not executable after replace()."""
        run_job()
        assert not os.stat(env.PROJECT_ROOT / 'backend/main.py').st_mode & 0o111


class TestFileMode:
    """File mode adjustment rules: the execute bits must match the record.

    os.chmod is patched to capture the calls, so the decision logic can
    be verified on every platform, the actual chmod effect is covered
    by TestExecutableMode on POSIX platforms.
    """

    @staticmethod
    def _patch_chmod(monkeypatch):
        """Capture os.chmod calls, no real chmod happens."""
        calls = []
        monkeypatch.setattr(os, 'chmod', lambda path, mode: calls.append((path, mode)))
        return calls

    @staticmethod
    def _info(path):
        """FileInfo of a file in the pack."""
        return PackDecodeBase(WEBSITE_FULL_PACK).fileinfo[path]

    def test_755_new_file_chmod(self, app_folder, monkeypatch):
        """A new 755 file (written 666) is chmod-ed to 755."""
        calls = self._patch_chmod(monkeypatch)
        info = self._info('scripts/deploy.sh')
        target = env.PROJECT_ROOT / 'scripts/deploy.sh'
        UnpackJob._adjust_mode(target, info, 0o666)
        assert calls == [(target, 0o755)]

    def test_644_new_file_kept(self, app_folder, monkeypatch):
        """A new 644 file (written 666) needs no chmod."""
        calls = self._patch_chmod(monkeypatch)
        info = self._info('backend/config.py')
        target = env.PROJECT_ROOT / 'backend/config.py'
        UnpackJob._adjust_mode(target, info, 0o666)
        assert calls == []

    @pytest.mark.parametrize('current', [0o644, 0o666, 0o646, 0o664])
    def test_644_record_accepts_no_exec(self, app_folder, monkeypatch, current):
        """A 644 record accepts any mode without execute bits."""
        calls = self._patch_chmod(monkeypatch)
        info = self._info('backend/config.py')
        target = env.PROJECT_ROOT / 'backend/config.py'
        UnpackJob._adjust_mode(target, info, current)
        assert calls == []

    @pytest.mark.parametrize('current', [0o755, 0o777, 0o757, 0o775])
    def test_755_record_accepts_exec(self, app_folder, monkeypatch, current):
        """A 755 record accepts any mode with execute bits."""
        calls = self._patch_chmod(monkeypatch)
        info = self._info('scripts/deploy.sh')
        target = env.PROJECT_ROOT / 'scripts/deploy.sh'
        UnpackJob._adjust_mode(target, info, current)
        assert calls == []

    def test_644_record_rejects_755(self, app_folder, monkeypatch):
        """A 644 record, a 755 file is chmod-ed to 644."""
        calls = self._patch_chmod(monkeypatch)
        info = self._info('backend/config.py')
        target = env.PROJECT_ROOT / 'backend/config.py'
        UnpackJob._adjust_mode(target, info, 0o755)
        assert calls == [(target, 0o644)]

    def test_755_record_rejects_644(self, app_folder, monkeypatch):
        """A 755 record, a 644 file is chmod-ed to 755."""
        calls = self._patch_chmod(monkeypatch)
        info = self._info('scripts/deploy.sh')
        target = env.PROJECT_ROOT / 'scripts/deploy.sh'
        UnpackJob._adjust_mode(target, info, 0o644)
        assert calls == [(target, 0o755)]
