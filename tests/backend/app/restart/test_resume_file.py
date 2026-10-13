"""
Tests for the resume file protocol (alasio/backend/app/restart/resume_file.py)

The file lives in the in-memory filesystem (fs fixture): every test points
env.PROJECT_ROOT at the fake root. The credential announcement is spied on
(without a supervisor the pipe send is a silent no-op, so the announcement is
the only observable part of a publication).
"""
import os
import time

import msgspec
import pytest

from alasio.backend.app.restart.resume_file import (
    OWNER_RESTART, OWNER_UPDATE, RESUME_FILE_PREFIX, RESUME_TOKEN_ENV, ResumeFiles, ResumeRecord
)
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.logger.writer import LogWriter
from alasio.testing.filesystem import FakeFilesystem


@pytest.fixture
def fake_fs():
    """
    An explicitly activated fake filesystem (see the pattern of the legacy
    resume tests: the log fd cache must be dropped around the swap)
    """
    fake = FakeFilesystem()
    fake.activate()
    LogWriter().close_fd()
    yield fake
    LogWriter().close_fd()
    fake.deactivate()


@pytest.fixture
def project_root(fake_fs):
    """Point env.PROJECT_ROOT at a folder of the fake filesystem root"""
    old = env.PROJECT_ROOT
    root = PathStr.new(fake_fs.root_dir.path).joinpath('project')
    root.makedirs()
    env.PROJECT_ROOT = root
    yield root
    env.PROJECT_ROOT = old


@pytest.fixture
def files(project_root, monkeypatch):
    """A ResumeFiles instance with the credential announcement captured"""
    instance = ResumeFiles()
    instance.announced = []
    monkeypatch.setattr(instance, 'announce', instance.announced.append)
    return instance


class TestWriteRead:
    """The publication and the one-shot consume"""

    @pytest.mark.trio
    async def test_roundtrip_carries_updated_all_at(self, files, monkeypatch):
        credential = await files.write(['cfg_a', 'cfg_b'], updated_all_at=1234.5)
        token, sep, checksum = credential.partition('-')
        assert sep and token and checksum
        file = files.file_of(token)
        assert file.isfile()
        assert files.file is not None and files.owner == OWNER_RESTART
        # the credential is the HMAC of the exact payload, announced to the
        # supervisor
        assert files.announced == [credential]
        assert files.checksum(token, file.atomic_read_bytes()) == checksum

        monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
        record = files.read()
        assert record is not None
        assert record.configs == ['cfg_a', 'cfg_b']
        assert record.owner == OWNER_RESTART
        assert record.actions == []
        assert record.updated_all_at == 1234.5
        # read once: the file never survives a read
        assert not file.isfile()

    @pytest.mark.trio
    async def test_write_encodes_the_record(self, files, monkeypatch):
        credentials = []
        monkeypatch.setattr(files, 'announce', credentials.append)
        await files.write(['cfg_a'], owner=OWNER_UPDATE, actions=['cleanup'], updated_all_at=7.0)
        token = credentials[0].partition('-')[0]
        payload = files.file_of(token).atomic_read_bytes()
        record = msgspec.json.decode(payload, type=ResumeRecord)
        assert record.configs == ['cfg_a']
        assert record.owner == OWNER_UPDATE
        assert record.actions == ['cleanup']
        assert record.updated_all_at == 7.0
        assert record.ts > 0

    @pytest.mark.trio
    async def test_updated_all_at_defaults_to_zero(self, files, monkeypatch):
        credentials = []
        monkeypatch.setattr(files, 'announce', credentials.append)
        await files.write(['cfg_a'])
        token = credentials[0].partition('-')[0]
        payload = files.file_of(token).atomic_read_bytes()
        assert msgspec.json.decode(payload, type=ResumeRecord).updated_all_at == 0.0

    @pytest.mark.trio
    async def test_read_without_credential_touches_nothing(self, files, monkeypatch):
        credentials = []
        monkeypatch.setattr(files, 'announce', credentials.append)
        await files.write(['cfg_a'])
        token = credentials[0].partition('-')[0]
        monkeypatch.delenv(RESUME_TOKEN_ENV, raising=False)
        assert files.read() is None
        assert files.file_of(token).isfile()

    @pytest.mark.trio
    async def test_read_tampered_payload_is_dropped_and_deleted(self, files, monkeypatch):
        credentials = []
        monkeypatch.setattr(files, 'announce', credentials.append)
        credential = await files.write(['cfg_a'])
        token = credential.partition('-')[0]
        file = files.file_of(token)
        # rewrite the file with a different payload under the same token
        file.atomic_write(b'{"ts": 0, "owner": "restart", "configs": ["evil"], "actions": []}')
        monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
        assert files.read() is None
        assert not file.isfile()

    @pytest.mark.trio
    async def test_read_malformed_credential(self, files, monkeypatch):
        monkeypatch.setenv(RESUME_TOKEN_ENV, 'no-separator')
        assert files.read() is None
        monkeypatch.setenv(RESUME_TOKEN_ENV, '-')
        assert files.read() is None

    @pytest.mark.trio
    async def test_write_keeps_foreign_files(self, files, monkeypatch):
        credentials = []
        monkeypatch.setattr(files, 'announce', credentials.append)
        foreign = files.folder.joinpath(f'{RESUME_FILE_PREFIX}deadbeef.json')
        foreign.atomic_write(b'{}')
        await files.write(['cfg_a'])
        assert foreign.isfile()


class TestWithdraw:
    """Withdrawing the publication of this process"""

    @pytest.mark.trio
    async def test_withdraw_removes_the_file_and_retracts(self, files, monkeypatch):
        credentials = []
        monkeypatch.setattr(files, 'announce', credentials.append)
        credential = await files.write(['cfg_a'])
        file = files.file_of(credential.partition('-')[0])
        removed = await files.withdraw()
        assert removed == file
        assert not file.isfile()
        # the retraction is the last announcement
        assert credentials[-1] == ''
        assert files.file is None and files.owner == ''
        # an idle withdrawal is a no-op
        assert await files.withdraw() is None

    @pytest.mark.trio
    async def test_withdraw_only_touches_its_own_file(self, files, monkeypatch):
        credentials = []
        monkeypatch.setattr(files, 'announce', credentials.append)
        other = ResumeFiles()
        other.announced = []
        monkeypatch.setattr(other, 'announce', other.announced.append)
        credential = await other.write(['cfg_a'])
        file = other.file_of(credential.partition('-')[0])
        # a second process (a second instance) withdrew nothing of the first
        assert await files.withdraw() is None
        assert file.isfile()
        assert await other.withdraw() is not None
        assert not file.isfile()

    @pytest.mark.trio
    async def test_failed_removal_still_retracts(self, files, monkeypatch):
        credentials = []
        monkeypatch.setattr(files, 'announce', credentials.append)
        await files.write(['cfg_a'])

        def locked(file):
            raise OSError('file is locked')

        monkeypatch.setattr('alasio.backend.app.restart.resume_file.atomic_remove', locked)
        assert await files.withdraw() is None
        assert credentials[-1] == ''


class TestCleanup:
    """The stale file cleanup"""

    def test_removes_only_stale_resume_files(self, files, monkeypatch):
        monkeypatch.setattr('alasio.backend.app.restart.resume_file.RESUME_CLEANUP_AGE', 100.0)
        fresh = files.file_of('aaaa1111')
        stale = files.file_of('bbbb2222')
        other = files.folder.joinpath('notes.json')
        for file in (fresh, stale, other):
            file.atomic_write(b'{}')
        old = time.time() - 1000
        os.utime(stale, (old, old))
        files.cleanup()
        assert fresh.isfile()
        assert not stale.isfile()
        # unrelated files are not touched
        assert other.isfile()

    def test_missing_folder_is_noop(self, files):
        assert not files.folder.isdir()
        files.cleanup()
        assert not files.folder.isdir()

    def test_empty_folder_is_noop(self, files):
        files.folder.makedirs()
        files.cleanup()
        assert files.folder.isdir()


class TestHelpers:
    """Paths, checksums and the process slot"""

    def test_folder_and_file_of(self, project_root):
        files = ResumeFiles()
        assert files.folder == project_root.joinpath('log/resume')
        assert files.file_of('abc') == project_root.joinpath('log/resume/resume-abc.json')

    def test_explicit_folder_wins(self, project_root):
        folder = project_root.joinpath('other')
        files = ResumeFiles(folder=folder)
        assert files.folder == folder

    def test_iter_files_filters_by_name(self, files):
        files.folder.makedirs()
        good = files.file_of('abc')
        good.atomic_write(b'{}')
        files.folder.joinpath('resume-x.txt').atomic_write(b'{}')
        files.folder.joinpath('other.json').atomic_write(b'{}')
        assert files.iter_files() == [good]

    def test_iter_files_missing_folder(self, files):
        assert files.iter_files() == []

    def test_reset_drops_the_slot(self, files):
        files.file = files.file_of('abc')
        files.owner = OWNER_RESTART
        files.reset()
        assert files.file is None and files.owner == ''
