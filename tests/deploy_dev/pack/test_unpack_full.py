"""
Tests for UnpackJob: interruptible and resumable full pack unpack.

Uses conftest.WEBSITE_FULL_PACK (mock modern full-stack website).
Every test runs in the in-memory fake filesystem, no real files are
written: the app_folder fixture points env.PROJECT_ROOT at the fake
filesystem.
"""
import os
from hashlib import sha1

import pytest
from conftest import COMMIT, FULL_SCENARIO_NEW, FULL_SCENARIO_OLD, WEBSITE_FILES, WEBSITE_FULL_PACK, WEBSITE_INDEX_PACK

from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.deploy.pack.job import DeployJob
from alasio.deploy.pack.job_base import CurrentFile, JobBase, MatchResult
from alasio.deploy.pack.job_unpack import PendingFile, UnpackJob
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


# full upgrade scenario packs, module level singletons built before
# the fake filesystem is active (MockGitRepo reads the real
# .gitattributes file)
OLD_PACK = make_pack(FULL_SCENARIO_OLD, commit='old')
NEW_PACK = make_pack(FULL_SCENARIO_NEW, commit='new')
OLD_DECODER = PackDecodeBase(OLD_PACK)
NEW_DECODER = PackDecodeBase(NEW_PACK)
OLD_INDEX = bytes(OLD_DECODER.extract_index_pack())
NEW_INDEX = bytes(NEW_DECODER.extract_index_pack())
NEW_TREE = {
    path: bytes(NEW_DECODER.catfile(info))
    for path, info in NEW_DECODER.fileinfo.items()
    if info.edit != 2 and not path.startswith('.pack/')
}
# files of the old version that do not exist in the new one, the
# leftover files deleted by an unpack over an old version
OLD_ONLY = ['backend/legacy.py', 'scripts/old_tool.py', 'scripts/run.sh', 'data/cache.pkl']
# packs of the leftover combination matrix: pkg/__init__.py exists
# (present), is an auto deleted marker (no __init__.py under pkg/),
# or is not recorded at all
PKG_NO_INIT = make_pack({'pkg/tool.py': b'x\n'}, commit='no-init')
PKG_WITH_INIT = make_pack({'pkg/__init__.py': b'', 'pkg/tool.py': b'x\n'}, commit='with-init')
PKG_NO_PKG = make_pack({'app.py': b'y\n'}, commit='no-pkg')


def run_job(data=WEBSITE_FULL_PACK):
    """The caller flow: run() does write, unpack and replace."""
    UnpackJob(data).run()


class TestJobFile:
    """write()."""

    def test_write_creates_job_file(self, app_folder):
        """write() stores the data to the job file for crash recovery."""
        UnpackJob(WEBSITE_FULL_PACK).write()
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/workspace/job.pack') == \
            WEBSITE_FULL_PACK


class TestUnpack:
    """unpack() phase: write tmp files, real files untouched."""

    def test_unpack_writes_tmp_only(self, app_folder):
        """unpack() writes tmp files, real files stay untouched."""
        job = UnpackJob(WEBSITE_FULL_PACK)
        job.write()
        job.unpack()
        # real files are not applied yet
        assert not os.path.exists(env.PROJECT_ROOT / 'backend/main.py')
        # the index pack is prepared to the workspace, not applied yet
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/index.pack')
        assert os.path.exists(env.PROJECT_ROOT / f'.pack/workspace/{JobBase.NEW_INDEX}')
        # the workspace has the job file and tmp files
        assert os.listdir(env.PROJECT_ROOT / '.pack/workspace')

    def test_unpack_does_not_write_job_file(self, app_folder):
        """unpack() does not write the job file, the caller does."""
        UnpackJob(WEBSITE_FULL_PACK).unpack()
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace/job.pack')

    def test_index_pack_written(self, app_folder):
        """The front part of the full pack is prepared to the workspace
        and applied to .pack/index.pack in replace()."""
        job = UnpackJob(WEBSITE_FULL_PACK)
        job.write()
        job.unpack()
        tmp = env.PROJECT_ROOT / f'.pack/workspace/{JobBase.NEW_INDEX}'
        data = file_read_bytes(tmp)
        assert data == WEBSITE_INDEX_PACK
        # it must be a valid index pack
        decoder = PackDecodeBase(data)
        decoder.validate_index()
        assert decoder.version == COMMIT
        # the real index pack is not touched until replace()
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/index.pack')
        job.replace()
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == WEBSITE_INDEX_PACK

    def test_pending_records(self, app_folder):
        """unpack() fills self.pending with PendingFile records."""
        job = UnpackJob(WEBSITE_FULL_PACK)
        job.write()
        job.unpack()
        assert isinstance(job.pending, list)
        assert job.pending
        assert all(isinstance(item, PendingFile) for item in job.pending)
        # every fileinfo record is in pending, refinfo is not unpacked
        # + 2 for the D marker and the packed commit history
        # + 1 for the index pack, prepared like every other record
        assert len(job.pending) == len(WEBSITE_FILES) + 3
        # the index pack record is prepared to the workspace, replace()
        # applies it to .pack/index.pack
        index = [
            item for item in job.pending
            if item.info.path == '.pack/index.pack'
        ]
        assert len(index) == 1
        assert index[0].info.edit == 0
        assert index[0].tmp
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
        # backend/main.py is a 644 record, python writes 666 which is
        # accepted as-is
        assert normal[0].mode is None
        # tmp file name is built from the record and the index
        info = normal[0].info
        assert os.path.exists(normal[0].tmp)


class TestUnpackIndex:
    """unpack(): reuse of a leftover workspace index pack."""

    def test_reuse_valid_new_index(self, app_folder, monkeypatch):
        """A valid leftover new_index.tmp is reused without a rewrite."""
        # the workspace tmp left by an interrupted run, already the
        # index pack of this version
        tmp = env.PROJECT_ROOT / f'.pack/workspace/{JobBase.NEW_INDEX}'
        os.makedirs(tmp.uppath(), exist_ok=True)
        with open(tmp, 'wb') as f:
            f.write(WEBSITE_INDEX_PACK)
        import alasio.deploy.pack.job_unpack as module
        writes = []
        original = module.file_write

        def _counting(file, data):
            writes.append(file)
            return original(file, data)
        monkeypatch.setattr(module, 'file_write', _counting)
        job = UnpackJob(WEBSITE_FULL_PACK)
        job.write()
        job.unpack()
        # the valid leftover tmp is reused: the index pack is not
        # written again (job.pack and the other tmp files still are)
        assert tmp not in writes
        assert file_read_bytes(tmp) == WEBSITE_INDEX_PACK

    def test_broken_new_index_rewritten(self, app_folder):
        """A broken leftover new_index.tmp is rewritten."""
        tmp = env.PROJECT_ROOT / f'.pack/workspace/{JobBase.NEW_INDEX}'
        os.makedirs(tmp.uppath(), exist_ok=True)
        with open(tmp, 'wb') as f:
            f.write(b'garbage')
        job = UnpackJob(WEBSITE_FULL_PACK)
        job.write()
        job.unpack()
        # the broken tmp fails the content check and is overwritten
        assert file_read_bytes(tmp) == WEBSITE_INDEX_PACK


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


class TestMatchResult:
    """_matches() returns MatchResult: match and fixable match_data."""

    @staticmethod
    def _match(path, content):
        """_matches() of a record against an existing file content."""
        job = UnpackJob(WEBSITE_FULL_PACK)
        info = PackDecodeBase(WEBSITE_FULL_PACK).fileinfo[path]
        current = CurrentFile(exist=True, data=content, mode=0o100644)
        return job._matches(info, current)

    def test_exact_match(self, app_folder):
        """A file with the record EOL matches, match_data is empty."""
        # backend/config.py is eol=0 (LF)
        result = self._match('backend/config.py', WEBSITE_FILES['backend/config.py'][0])
        assert isinstance(result, MatchResult)
        assert result.match
        assert result.match_data == b''

    def test_crlf_record_crlf_file(self, app_folder):
        """A clean CRLF file of a CRLF record matches as-is."""
        result = self._match(
            'backend/requirements.txt', WEBSITE_FILES['backend/requirements.txt'][0])
        assert result.match
        assert result.match_data == b''

    def test_eol_mismatch_lf_vs_crlf(self, app_folder):
        """A LF file of a CRLF record matches after converting to CRLF."""
        content = WEBSITE_FILES['backend/requirements.txt'][0].replace(b'\r\n', b'\n')
        result = self._match('backend/requirements.txt', content)
        assert not result.match
        assert result.match_data == WEBSITE_FILES['backend/requirements.txt'][0]

    def test_eol_mismatch_crlf_vs_lf(self, app_folder):
        """A CRLF file of a LF record matches after converting to LF."""
        content = WEBSITE_FILES['backend/config.py'][0].replace(b'\n', b'\r\n')
        result = self._match('backend/config.py', content)
        assert not result.match
        assert result.match_data == WEBSITE_FILES['backend/config.py'][0]

    def test_eol_mismatch_mixed_vs_crlf(self, app_folder):
        """A mixed LF/CRLF file of a CRLF record matches after converting."""
        content = WEBSITE_FILES['backend/requirements.txt'][0].replace(b'\r\n', b'\n', 1)
        result = self._match('backend/requirements.txt', content)
        assert not result.match
        assert result.match_data == WEBSITE_FILES['backend/requirements.txt'][0]

    def test_eol_mismatch_mixed_vs_lf(self, app_folder):
        """A mixed LF/CRLF file of a LF record matches after converting."""
        content = WEBSITE_FILES['backend/config.py'][0].replace(b'\n', b'\r\n', 1)
        result = self._match('backend/config.py', content)
        assert not result.match
        assert result.match_data == WEBSITE_FILES['backend/config.py'][0]

    def test_lone_cr_not_fixable(self, app_folder):
        """A lone CR cannot be converted cleanly, no match_data."""
        content = b'HOST = "0.0.0.0"\rPORT = 8000\nDEBUG = False\n'
        result = self._match('backend/config.py', content)
        assert not result.match
        assert result.match_data == b''

    def test_wrong_content_not_fixable(self, app_folder):
        """Wrong content with an EOL mismatch is not fixable."""
        result = self._match('backend/config.py', b'wrong\r\ncontent\r\n')
        assert not result.match
        assert result.match_data == b''

    def test_binary_compared_as_is(self, app_folder):
        """Binary (eol=2) is compared as-is, never converted."""
        content = WEBSITE_FILES['backend/static/logo.png'][0]
        result = self._match('backend/static/logo.png', content)
        assert result.match
        assert result.match_data == b''

    def test_eol0_file_equals_blob_with_cr(self, app_folder):
        """A file that is exactly the record blob matches, even with CR."""
        # a pathological LF record whose blob contains a lone CR
        content = b'HOST = "0.0.0.0"\rPORT = 8000\n'
        info = IdxInfo(path='x', size=len(content), sha1=sha1(content).digest(), eol=0)
        job = UnpackJob(WEBSITE_FULL_PACK)
        current = CurrentFile(exist=True, data=content, mode=0o100644)
        result = job._matches(info, current)
        assert result.match
        assert result.match_data == b''

    def test_missing_file(self, app_folder):
        """A missing file never matches and has no match_data."""
        job = UnpackJob(WEBSITE_FULL_PACK)
        info = PackDecodeBase(WEBSITE_FULL_PACK).fileinfo['backend/config.py']
        result = job._matches(info, CurrentFile(exist=False, data=b'', mode=0))
        assert not result.match
        assert result.match_data == b''

    def test_truthy_is_match_flag(self, app_folder):
        """A result can be used as the old boolean return of _matches()."""
        job = UnpackJob(WEBSITE_FULL_PACK)
        info = PackDecodeBase(WEBSITE_FULL_PACK).fileinfo['backend/config.py']
        match = CurrentFile(exist=True, data=WEBSITE_FILES['backend/config.py'][0],
                            mode=0o100644)
        mismatch = CurrentFile(exist=True, data=b'wrong', mode=0o100644)
        assert bool(job._matches(info, match))
        assert not bool(job._matches(info, mismatch))


class TestCallerFlow:
    """The exact caller usage of UnpackJob."""

    def test_resume_then_new_job(self, app_folder):
        """get_unfinished_job() first, then unpack the new data."""
        # a previous run was interrupted, the job file is left behind
        UnpackJob(WEBSITE_FULL_PACK).write()
        # finish the unfinished job first
        job = DeployJob.get_unfinished_job()
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

    def test_eol_mismatch_fixed_without_decompress(self, app_folder, monkeypatch):
        """A fixable EOL mismatch is converted, catfile is not called."""
        # backend/config.py is eol=0 (LF), the local file is CRLF
        target = env.PROJECT_ROOT / 'backend/config.py'
        os.makedirs(target.uppath(), exist_ok=True)
        with open(target, 'wb') as f:
            f.write(WEBSITE_FILES['backend/config.py'][0].replace(b'\n', b'\r\n'))
        calls = []
        original = PackDecodeBase.catfile

        def _counting(self, info):
            calls.append(info.path)
            return original(self, info)
        monkeypatch.setattr(PackDecodeBase, 'catfile', _counting)
        run_job()
        # the EOL conversion must not decompress the record
        assert 'backend/config.py' not in calls
        assert file_read_bytes(target) == WEBSITE_FILES['backend/config.py'][0]

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
        job = DeployJob.get_unfinished_job()
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
        tmp = env.PROJECT_ROOT / f'.pack/workspace/{info.size}_{info.sha1.hex()}_{index}.tmp'
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
        assert DeployJob.get_unfinished_job() is not None


class TestExecutableMode:
    """Executable bit handling.

    The fake filesystem simulates the POSIX file modes, so the tests
    run on every platform.
    """

    def test_mode_755_is_executable(self, app_folder):
        """Files with mode 755 are executable after replace()."""
        run_job()
        assert os.stat(env.PROJECT_ROOT / 'scripts/deploy.sh').st_mode & 0o111

    def test_mode_644_is_not_executable(self, app_folder):
        """Files with mode 644 are not executable after replace()."""
        run_job()
        assert not os.stat(env.PROJECT_ROOT / 'backend/main.py').st_mode & 0o111

    def test_mode_only_fixed(self, app_folder):
        """A file with the right content but the wrong mode is fixed
        without rewriting the content."""
        # backend/config.py is a 644 record, the local file is 755
        content = WEBSITE_FILES['backend/config.py'][0]
        target = env.PROJECT_ROOT / 'backend/config.py'
        os.makedirs(target.uppath(), exist_ok=True)
        with open(target, 'wb') as f:
            f.write(content)
        os.chmod(target, 0o755)
        run_job()
        assert not os.stat(target).st_mode & 0o111
        assert file_read_bytes(target) == content

    def test_mode_755_restored(self, app_folder):
        """A 755 record whose file lost the execute bit is fixed
        without rewriting the content."""
        # scripts/deploy.sh is a 755 record, the local file is 644
        content = WEBSITE_FILES['scripts/deploy.sh'][0]
        target = env.PROJECT_ROOT / 'scripts/deploy.sh'
        os.makedirs(target.uppath(), exist_ok=True)
        with open(target, 'wb') as f:
            f.write(content)
        os.chmod(target, 0o644)
        run_job()
        assert os.stat(target).st_mode & 0o111
        assert file_read_bytes(target) == content


class TestFileMode:
    """File mode matching rules: the execute bits must match the record.

    The mode check is embedded in MatchResult.mode_matched by
    _matches(), replace() chmod-ed the target to pending.mode when it
    is set. The decision rules are verified on every platform, the
    actual chmod effect is covered by TestExecutableMode on POSIX
    platforms.
    """

    @staticmethod
    def _current(mode):
        """CurrentFile with a given mode, the content does not matter."""
        return CurrentFile(exist=True, data=b'x', mode=mode)

    @staticmethod
    def _info(path):
        """FileInfo of a file in the pack."""
        return PackDecodeBase(WEBSITE_FULL_PACK).fileinfo[path]

    def test_match_embeds_mode(self, app_folder):
        """_matches() embeds the mode check in mode_matched."""
        info = self._info('backend/config.py')
        data = WEBSITE_FILES['backend/config.py'][0]
        result = JobBase._matches(info, CurrentFile(exist=True, data=data, mode=0o755))
        assert result.match
        assert not result.mode_matched
        result = JobBase._matches(info, CurrentFile(exist=True, data=data, mode=0o644))
        assert result.match
        assert result.mode_matched

    def test_mode_ignored_on_windows(self, app_folder, monkeypatch):
        """Windows determines executability by the file extension, the
        exec bits cannot be set, the mode always matches."""
        monkeypatch.setattr(env, 'POSIX', False)
        assert JobBase._mode_matches(self._info('backend/config.py'), self._current(0o777))
        assert JobBase._mode_matches(self._info('scripts/deploy.sh'), self._current(0o644))

    @pytest.mark.parametrize('current', [0o644, 0o666, 0o646, 0o664])
    def test_644_record_accepts_no_exec(self, app_folder, current):
        """A 644 record accepts any mode without execute bits."""
        assert JobBase._mode_matches(self._info('backend/config.py'), self._current(current))

    @pytest.mark.parametrize('current', [0o755, 0o777, 0o757, 0o775])
    def test_644_record_rejects_exec(self, app_folder, current):
        """A 644 record rejects any mode with execute bits."""
        assert not JobBase._mode_matches(self._info('backend/config.py'), self._current(current))

    @pytest.mark.parametrize('current', [0o755, 0o777, 0o757, 0o775])
    def test_755_record_accepts_exec(self, app_folder, current):
        """A 755 record accepts any mode with execute bits."""
        assert JobBase._mode_matches(self._info('scripts/deploy.sh'), self._current(current))

    @pytest.mark.parametrize('current', [0o644, 0o666, 0o646, 0o664])
    def test_755_record_rejects_no_exec(self, app_folder, current):
        """A 755 record rejects any mode without execute bits."""
        assert not JobBase._mode_matches(self._info('scripts/deploy.sh'), self._current(current))


class TestUnpackRebuild:
    """Unpacking over an old version is a local rebuild: the leftover
    files of the old version are removed, the new index pack replaces
    .pack/index.pack last."""

    def test_leftover_files_deleted(self, app_folder):
        """Unpacking the new pack over the old one deletes the old-only
        files and converges the tree."""
        UnpackJob(OLD_PACK).run()
        UnpackJob(NEW_PACK).run()
        assert read_tree() == NEW_TREE
        for path in OLD_ONLY:
            assert not os.path.exists(env.PROJECT_ROOT / path), path
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == NEW_INDEX
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_leftover_and_deleted_marker(self, app_folder):
        """The leftover deletion and the deleted marker of the new
        pack work together."""
        UnpackJob(OLD_PACK).run()
        # a file the new pack marks as deleted
        target = env.PROJECT_ROOT / 'scripts/__init__.py'
        os.makedirs(target.uppath(), exist_ok=True)
        with open(target, 'wb') as f:
            f.write(b'stale')
        UnpackJob(NEW_PACK).run()
        assert read_tree() == NEW_TREE
        for path in OLD_ONLY:
            assert not os.path.exists(env.PROJECT_ROOT / path), path
        assert not os.path.exists(target)
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_user_file_kept(self, app_folder):
        """A file outside every index is not a managed file and is
        kept."""
        UnpackJob(OLD_PACK).run()
        target = env.PROJECT_ROOT / 'user/notes.txt'
        os.makedirs(target.uppath(), exist_ok=True)
        with open(target, 'wb') as f:
            f.write(b'my notes')
        UnpackJob(NEW_PACK).run()
        # the user file is not a managed file: the tree is the new
        # tree plus the user file
        tree = read_tree()
        assert all(tree[path] == content for path, content in NEW_TREE.items())
        assert tree['user/notes.txt'] == b'my notes'
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_old_index_missing_ignored(self, app_folder):
        """A missing old index is ignored: the unpack proceeds, the
        leftovers are kept, a warning is logged."""
        UnpackJob(OLD_PACK).run()
        os.remove(env.PROJECT_ROOT / '.pack/index.pack')
        with logger.mock_capture_writer() as capture:
            UnpackJob(NEW_PACK).run()
        assert capture.backend.any_contains('Failed to read the old index pack')
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == NEW_INDEX
        tree = read_tree()
        assert all(tree[path] == content for path, content in NEW_TREE.items())
        # without the old index the leftovers cannot be computed and
        # are kept
        for path in OLD_ONLY:
            assert os.path.exists(env.PROJECT_ROOT / path), path
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_old_index_corrupted_ignored(self, app_folder):
        """A corrupted old index is ignored: the unpack proceeds, the
        leftovers are kept, a warning is logged."""
        UnpackJob(OLD_PACK).run()
        bad = bytearray(OLD_INDEX)
        # flip a byte inside the checksum digest (the last 20 bytes)
        bad[-5] ^= 0xFF
        with open(env.PROJECT_ROOT / '.pack/index.pack', 'wb') as f:
            f.write(bad)
        with logger.mock_capture_writer() as capture:
            UnpackJob(NEW_PACK).run()
        assert capture.backend.any_contains('Failed to read the old index pack')
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == NEW_INDEX
        tree = read_tree()
        assert all(tree[path] == content for path, content in NEW_TREE.items())
        for path in OLD_ONLY:
            assert os.path.exists(env.PROJECT_ROOT / path), path
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_index_replaced_last(self, app_folder):
        """The new index pack is the last pending record, the leftover
        deletions come before it."""
        UnpackJob(OLD_PACK).run()
        job = UnpackJob(NEW_PACK)
        job.write()
        job.unpack()
        assert job.pending[-1].info.path == '.pack/index.pack'
        # the leftover deletions are scheduled before the index record
        index_pos = len(job.pending) - 1
        leftover = [item for item in job.pending[:index_pos]
                    if item.info.path in OLD_ONLY]
        assert len(leftover) == len(OLD_ONLY)
        for item in leftover:
            assert item.info.edit == 2
            assert item.tmp == ''

    def test_resume_leftover_cleanup(self, app_folder):
        """A run resumed from an interruption before replace()
        recomputes the leftover deletion list from the old index."""
        UnpackJob(OLD_PACK).run()
        # an interruption before replace(): the job file is written,
        # the old index pack is still in place
        UnpackJob(NEW_PACK).write()
        job = DeployJob.get_unfinished_job()
        assert job is not None
        job.run()
        assert read_tree() == NEW_TREE
        for path in OLD_ONLY:
            assert not os.path.exists(env.PROJECT_ROOT / path), path
        assert file_read_bytes(env.PROJECT_ROOT / '.pack/index.pack') == NEW_INDEX
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_old_deleted_new_added_downloaded(self, app_folder):
        """Old D marker + new added record counts as added: the file
        is unpacked, never treated as a leftover."""
        UnpackJob(PKG_NO_INIT).run()
        UnpackJob(PKG_WITH_INIT).run()
        # pkg/__init__.py is a record of the new pack, unpacked
        assert file_read_bytes(env.PROJECT_ROOT / 'pkg/__init__.py') == b''
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_old_deleted_new_missing_local_kept(self, app_folder):
        """Old D marker + new pack without the path: the old D marker
        is ignored by the leftover check, a local file is kept."""
        UnpackJob(PKG_NO_INIT).run()
        target = env.PROJECT_ROOT / 'pkg/__init__.py'
        os.makedirs(target.uppath(), exist_ok=True)
        with open(target, 'wb') as f:
            f.write(b'stale')
        UnpackJob(PKG_NO_PKG).run()
        # not a managed file of the new pack, kept
        assert file_read_bytes(target) == b'stale'
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')

    def test_old_added_new_deleted_local_good_deleted(self, app_folder):
        """Old record + new D marker: the file is removed by the
        deleted marker of the new pack."""
        UnpackJob(PKG_WITH_INIT).run()
        UnpackJob(PKG_NO_INIT).run()
        assert not os.path.exists(env.PROJECT_ROOT / 'pkg/__init__.py')
        assert not os.path.exists(env.PROJECT_ROOT / '.pack/workspace')
