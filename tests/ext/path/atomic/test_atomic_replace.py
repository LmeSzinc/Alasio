"""
Tests for the atomic_* wrappers of the replace.

Covered: atomic_replace() with the Windows PermissionError retry, and
atomic_replace_tmp(). The bare functions they are built on, file_replace() and
is_folder(), are covered by test_file_replace.py, and the answers of the real
operating system by test_atomic_replace_real.py on CI.

The tests run on the in-memory fake filesystem (the fs fixture), the real
disk is never touched. The retry of the Windows loop is observed through the
sleep recorder, the tests as usual.
"""
import os

import pytest

from alasio.ext.path import atomic
from alasio.ext.path.atomic import (
    WINDOWS_MAX_ATTEMPT, atomic_replace, atomic_replace_tmp, file_read_text, windows_attempt_delay
)
from alasio.testing.filesystem import fs  # noqa: F401
from tests.ext.path.atomic.helpers import break_function, record_sleeps


class TestAtomicReplace:
    """Test cases for atomic_replace()."""

    def test_replace(self, fs):
        """A file replaces a file."""
        fs.create_file('/data/new.txt', contents='new')
        fs.create_file('/data/target.txt', contents='old')
        assert atomic_replace('/data/new.txt', '/data/target.txt') is False
        assert file_read_text('/data/target.txt') == 'new'

    def test_replace_folder(self, fs):
        """A folder in the way is removed, like file_replace()."""
        fs.create_file('/data/new.txt', contents='new')
        fs.create_file('/data/folder/a.txt', contents='a')
        assert atomic_replace('/data/new.txt', '/data/folder') is True
        assert file_read_text('/data/folder') == 'new'

    def test_replace_folder_waits_for_no_lock(self, fs, monkeypatch):
        """A folder in the way is decided on the first attempt: it is not a
        file another process holds open, waiting for it would be wasted."""
        monkeypatch.setattr(atomic, 'IS_WINDOWS', True)
        sleeps = record_sleeps(monkeypatch)
        fs.create_file('/data/new.txt', contents='new')
        fs.create_file('/data/folder/a.txt', contents='a')
        assert atomic_replace('/data/new.txt', '/data/folder') is True
        assert sleeps.sleeps == []

    def test_replace_retries_on_windows(self, fs, monkeypatch):
        """PermissionError of a locked target should be retried until it works."""
        monkeypatch.setattr(atomic, 'IS_WINDOWS', True)
        sleeps = record_sleeps(monkeypatch)
        fs.create_file('/data/new.txt', contents='new')
        fs.create_file('/data/target.txt', contents='old')
        error = PermissionError(13, 'Permission denied')
        calls = break_function(monkeypatch, os, 'replace', [error])
        assert atomic_replace('/data/new.txt', '/data/target.txt') is False
        assert len(calls) == 2
        assert sleeps.sleeps == [windows_attempt_delay(0)]
        assert file_read_text('/data/target.txt') == 'new'

    def test_replace_gives_up_on_windows(self, fs, monkeypatch):
        """The last PermissionError should be raised when every attempt failed."""
        monkeypatch.setattr(atomic, 'IS_WINDOWS', True)
        sleeps = record_sleeps(monkeypatch)
        fs.create_file('/data/new.txt', contents='new')
        fs.create_file('/data/target.txt', contents='old')
        error = PermissionError(13, 'Permission denied')
        calls = break_function(monkeypatch, os, 'replace', [error] * WINDOWS_MAX_ATTEMPT)
        with pytest.raises(PermissionError) as e:
            atomic_replace('/data/new.txt', '/data/target.txt')
        assert e.value is error
        assert len(calls) == WINDOWS_MAX_ATTEMPT
        assert sleeps.sleeps == [windows_attempt_delay(attempt) for attempt in range(WINDOWS_MAX_ATTEMPT)]
        assert file_read_text('/data/target.txt') == 'old'

    def test_replace_posix_no_retry(self, fs, monkeypatch):
        """On the other platforms the error is raised right away."""
        monkeypatch.setattr(atomic, 'IS_WINDOWS', False)
        fs.create_file('/data/new.txt', contents='new')
        fs.create_file('/data/target.txt', contents='old')
        error = PermissionError(13, 'Permission denied')
        calls = break_function(monkeypatch, os, 'replace', [error])
        with pytest.raises(PermissionError) as e:
            atomic_replace('/data/new.txt', '/data/target.txt')
        assert e.value is error
        assert len(calls) == 1
        assert file_read_text('/data/target.txt') == 'old'

    def test_missing_source(self, fs):
        """A missing tmp file is an error, the target is untouched."""
        fs.create_file('/data/target.txt', contents='old')
        with pytest.raises(FileNotFoundError):
            atomic_replace('/missing.txt', '/data/target.txt')
        assert file_read_text('/data/target.txt') == 'old'


class TestAtomicReplaceTmp:
    """Test cases for atomic_replace_tmp()."""

    def test_replace(self, fs):
        """The tmp file takes the place of the target."""
        fs.create_file('/data/a.txt.ABC123.tmp', contents='new')
        fs.create_file('/data/a.txt', contents='old')
        assert atomic_replace_tmp('/data/a.txt.ABC123.tmp', '/data/a.txt') is False
        assert file_read_text('/data/a.txt') == 'new'
        assert os.listdir('/data') == ['a.txt']

    def test_replace_folder(self, fs):
        """A folder in the way is removed, like atomic_replace()."""
        fs.create_file('/data/a.txt.ABC123.tmp', contents='new')
        fs.create_file('/data/folder/inside.txt', contents='old')
        assert atomic_replace_tmp('/data/a.txt.ABC123.tmp', '/data/folder') is True
        assert file_read_text('/data/folder') == 'new'

    def test_failure_removes_tmp(self, fs, monkeypatch):
        """A failed replace removes the tmp file of the write."""
        fs.create_file('/data/a.txt.ABC123.tmp', contents='new')
        fs.create_file('/data/target.txt', contents='old')

        def broken_replace(path_from, path_to):
            raise OSError(5, 'I/O error')

        monkeypatch.setattr(atomic, 'os', atomic.os)
        monkeypatch.setattr(os, 'replace', broken_replace)
        with pytest.raises(OSError):
            atomic_replace_tmp('/data/a.txt.ABC123.tmp', '/data/target.txt')
        assert not os.path.exists('/data/a.txt.ABC123.tmp')
        assert file_read_text('/data/target.txt') == 'old'

    def test_missing_tmp_keeps_target(self, fs):
        """A missing tmp file is an error, the target is untouched."""
        fs.create_file('/data/target.txt', contents='old')
        with pytest.raises(FileNotFoundError):
            atomic_replace_tmp('/data/missing.tmp', '/data/target.txt')
        assert file_read_text('/data/target.txt') == 'old'
