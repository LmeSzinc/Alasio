"""
Tests for file_replace() and is_folder(), the bare layer of the replace.

Covered: is_folder() and file_replace(). The kind of the two paths decides what
os.replace() does, and the answer differs between the platforms: a folder is
never replaced by a file (EISDIR on POSIX, PermissionError on Windows), a file
is replaced by a folder on Windows while POSIX refuses it (ENOTDIR).
file_replace() removes the entry in the way, so every platform ends with the
same result.

The tests run on the in-memory fake filesystem (the fs fixture), the fake
answers the POSIX errno on every host, the real disk is never touched.
The Windows branch is covered by test_atomic_replace_real.py on CI, where the
real answers are the ones of the runner.
"""
import os

import pytest

from alasio.ext.path import atomic
from alasio.ext.path.atomic import file_read_text, file_replace, is_folder
from alasio.testing.filesystem import fs  # noqa: F401
from tests.ext.path.atomic.helpers import break_function


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
