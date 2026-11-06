"""
Tests for the replace functions of alasio.ext.path.atomic.

Covered: is_folder(), file_replace(), atomic_replace() with the Windows
PermissionError retry, and atomic_replace_tmp().

The kind of the two paths decides what os.replace() does, and the answer
differs between the platforms: a folder is never replaced by a file (EISDIR
on POSIX, PermissionError on Windows), a file is replaced by a folder on
Windows while POSIX refuses it (ENOTDIR). file_replace() removes the entry in
the way, so every platform ends with the same result.

The tests run on the in-memory fake filesystem (the fs fixture), the real
disk is never touched. The retry of the Windows loop is observed through the
sleep recorder, the tests as usual.
"""
import os

import pytest

from alasio.ext.path import atomic
from alasio.ext.path.atomic import (
    WINDOWS_MAX_ATTEMPT, atomic_replace, atomic_replace_tmp, file_read_text, file_replace, is_folder,
    windows_attempt_delay
)
from alasio.testing.filesystem import fs  # noqa: F401
from tests.ext.path.atomic.helpers import break_function, record_sleeps


class TestIsFolder:
    """Test cases for is_folder()."""

    def test_folder(self, fs):
        """A folder should report True."""
        fs.create_dir('/data/folder')
        assert is_folder('/data/folder') is True

    def test_file(self, fs):
        """A file should report False."""
        fs.create_file('/data/a.txt', contents='x')
        assert is_folder('/data/a.txt') is False

    def test_missing(self, fs):
        """A missing path should report False."""
        assert is_folder('/missing') is False

    def test_symlink_to_folder(self, fs):
        """A link to a folder is the folder, the check follows the link."""
        fs.create_dir('/data/folder')
        fs.create_symlink('/data/link', '/data/folder')
        assert is_folder('/data/link') is True

    def test_symlink_to_file(self, fs):
        """A link to a file is not a folder."""
        fs.create_file('/data/a.txt', contents='x')
        fs.create_symlink('/data/link', '/data/a.txt')
        assert is_folder('/data/link') is False

    def test_dangling_symlink(self, fs):
        """A dangling link points at nothing, so it is not a folder."""
        fs.create_symlink('/data/link', '/data/missing')
        assert is_folder('/data/link') is False


class TestFileReplace:
    """Test cases for file_replace()."""

    def test_file_over_file(self, fs):
        """A file replaces a file, nothing had to be removed first."""
        fs.create_file('/data/new.txt', contents='new')
        fs.create_file('/data/old.txt', contents='old')
        assert file_replace('/data/new.txt', '/data/old.txt') is False
        assert file_read_text('/data/old.txt') == 'new'
        assert not os.path.exists('/data/new.txt')

    def test_file_over_missing(self, fs):
        """A file replaces a missing path, like os.replace()."""
        fs.create_file('/data/new.txt', contents='new')
        assert file_replace('/data/new.txt', '/data/target.txt') is False
        assert file_read_text('/data/target.txt') == 'new'

    def test_file_over_empty_folder(self, fs):
        """An empty folder in the way is removed."""
        fs.create_file('/data/new.txt', contents='new')
        fs.create_dir('/data/folder')
        assert file_replace('/data/new.txt', '/data/folder') is True
        assert file_read_text('/data/folder') == 'new'
        assert not is_folder('/data/folder')

    def test_file_over_folder(self, fs):
        """A folder in the way is removed with its content."""
        fs.create_file('/data/new.txt', contents='new')
        fs.create_file('/data/folder/a.txt', contents='a')
        fs.create_file('/data/folder/sub/b.txt', contents='b')
        assert file_replace('/data/new.txt', '/data/folder') is True
        assert file_read_text('/data/folder') == 'new'
        # the source moved, the target is the only entry left
        assert os.listdir('/data') == ['folder']

    def test_folder_over_file(self, fs):
        """A file in the way is removed when a folder takes its place."""
        fs.create_file('/data/folder/a.txt', contents='a')
        fs.create_file('/data/target.txt', contents='old')
        assert file_replace('/data/folder', '/data/target.txt') is True
        assert is_folder('/data/target.txt')
        assert file_read_text('/data/target.txt/a.txt') == 'a'

    def test_folder_over_empty_folder(self, fs):
        """A folder replaces an empty folder, as os.replace() does."""
        fs.create_file('/data/source/a.txt', contents='a')
        fs.create_dir('/data/target')
        assert file_replace('/data/source', '/data/target') is False
        assert file_read_text('/data/target/a.txt') == 'a'

    def test_missing_source(self, fs):
        """A missing source is an error, the target is untouched."""
        fs.create_file('/data/target.txt', contents='old')
        with pytest.raises(FileNotFoundError):
            file_replace('/missing.txt', '/data/target.txt')
        assert file_read_text('/data/target.txt') == 'old'

    def test_locked_file_no_retry(self, fs, monkeypatch):
        """A file the system refuses to replace is raised at once: the bare
        function never waits, the retry belongs to atomic_replace()."""
        fs.create_file('/data/new.txt', contents='new')
        fs.create_file('/data/target.txt', contents='old')
        error = PermissionError(13, 'Permission denied')
        calls = break_function(monkeypatch, os, 'replace', [error])
        with pytest.raises(PermissionError) as e:
            file_replace('/data/new.txt', '/data/target.txt')
        assert e.value is error
        assert len(calls) == 1
        assert file_read_text('/data/target.txt') == 'old'

    def test_never_calls_an_atomic_function(self, fs, monkeypatch):
        """file_replace() is the bare layer: it must not reach for an atomic_*
        wrapper, atomic_rmtree() would come straight back here through its own
        rename."""
        def forbidden(*args, **kwargs):
            raise AssertionError('a file_* function must not call an atomic_* one')

        monkeypatch.setattr(atomic, 'atomic_rmtree', forbidden)
        monkeypatch.setattr(atomic, 'atomic_replace', forbidden)
        monkeypatch.setattr(atomic, 'atomic_remove', forbidden)
        fs.create_file('/data/new.txt', contents='new')
        fs.create_file('/data/folder/a.txt', contents='a')

        assert file_replace('/data/new.txt', '/data/folder') is True
        assert file_read_text('/data/folder') == 'new'

    def test_folder_that_can_not_be_emptied(self, fs, monkeypatch):
        """A folder that keeps an entry raises: the move can not happen, and the
        folder is still there with the entry that stopped it."""
        fs.create_file('/data/new.txt', contents='new')
        fs.create_file('/data/folder/locked.txt', contents='a')
        original = os.unlink

        def locked_unlink(path):
            if os.path.basename(path) == 'locked.txt':
                raise PermissionError(13, 'Permission denied', path)
            return original(path)

        monkeypatch.setattr(os, 'unlink', locked_unlink)
        with pytest.raises(OSError):
            file_replace('/data/new.txt', '/data/folder')
        assert file_read_text('/data/folder/locked.txt') == 'a'
        assert file_read_text('/data/new.txt') == 'new'


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
