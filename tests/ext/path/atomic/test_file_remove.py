"""
Tests for the remove functions of alasio.ext.path.atomic.

Covered: file_remove(), folder_rmtree(), is_empty_folder(),
folder_rmtree_empty() and file_replace()'s is_folder(): the bare layer the
atomic_* wrappers are built on, without the Windows retry.

The tests run on the in-memory fake filesystem (the fs fixture), the real disk
is never touched.
"""
import os

import pytest

from alasio.ext.path.atomic import (
    file_read_bytes, file_remove, file_write, folder_rmtree, folder_rmtree_empty, is_empty_folder, is_folder
)
from alasio.testing.filesystem import fs  # noqa: F401


class TestFileRemove:
    """Test cases for file_remove()."""

    def test_remove(self, fs):
        """An existing file is removed."""
        file_write('/data/a.txt', b'data')
        assert file_remove('/data/a.txt') is True
        assert not os.path.exists('/data/a.txt')

    def test_remove_missing(self, fs):
        """A missing file reports False, it is not an error."""
        assert file_remove('/data/missing.txt') is False

    def test_remove_folder(self, fs):
        """A folder is never removed by file_remove(), the error names the kind."""
        os.makedirs('/data/folder')
        with pytest.raises(IsADirectoryError):
            file_remove('/data/folder')
        assert os.path.isdir('/data/folder')

    def test_remove_symlink(self, fs):
        """Only the link is removed, never what it points at."""
        file_write('/data/a.txt', b'data')
        fs.create_symlink('/data/link.txt', '/data/a.txt')

        assert file_remove('/data/link.txt') is True
        assert not os.path.lexists('/data/link.txt')
        assert file_read_bytes('/data/a.txt') == b'data'

    def test_remove_dangling_symlink(self, fs):
        """A dangling link can be removed: the entry exists even when its
        target does not."""
        fs.create_symlink('/data/link.txt', '/data/missing.txt')

        assert file_remove('/data/link.txt') is True
        assert not os.path.lexists('/data/link.txt')

    def test_no_retry(self, fs, monkeypatch):
        """The bare function reports a refusal at once: the retry is the work of
        atomic_remove()."""
        file_write('/data/a.txt', b'data')
        original = os.unlink

        def locked(path):
            if os.path.basename(path) == 'a.txt':
                raise PermissionError(13, 'Permission denied', path)
            return original(path)

        monkeypatch.setattr(os, 'unlink', locked)
        with pytest.raises(PermissionError):
            file_remove('/data/a.txt')
        assert file_read_bytes('/data/a.txt') == b'data'


class TestFolderRmtree:
    """Test cases for folder_rmtree()."""

    def test_rmtree(self, fs):
        """The folder and its whole content are removed."""
        file_write('/data/folder/a.txt', b'x')
        file_write('/data/folder/sub/b.txt', b'y')

        assert folder_rmtree('/data/folder') is True
        assert not os.path.exists('/data/folder')
        assert os.path.isdir('/data')

    def test_rmtree_missing(self, fs):
        """A missing folder reports False."""
        assert folder_rmtree('/data/missing') is False

    def test_rmtree_file(self, fs):
        """A path that is a file is removed like file_remove() does."""
        file_write('/data/a.txt', b'x')
        assert folder_rmtree('/data/a.txt') is True
        assert not os.path.exists('/data/a.txt')

    def test_rmtree_symlink(self, fs):
        """A link is unlinked, the folder it points at is kept."""
        file_write('/data/real/a.txt', b'x')
        fs.create_symlink('/data/link', '/data/real')

        assert folder_rmtree('/data/link') is True
        assert not os.path.lexists('/data/link')
        assert file_read_bytes('/data/real/a.txt') == b'x'

    def test_rmtree_nested_symlink(self, fs):
        """A link inside the folder is unlinked, its target is kept."""
        file_write('/data/outside/a.txt', b'x')
        file_write('/data/folder/a.txt', b'x')
        fs.create_symlink('/data/folder/link', '/data/outside')

        assert folder_rmtree('/data/folder') is True
        assert not os.path.exists('/data/folder')
        assert file_read_bytes('/data/outside/a.txt') == b'x'

    def test_rmtree_locked_file(self, fs, monkeypatch):
        """A file that cannot be removed is skipped, the folder is kept."""
        file_write('/data/folder/locked.txt', b'x')
        file_write('/data/folder/free.txt', b'x')
        original = os.unlink

        def locked(path):
            if os.path.basename(path) == 'locked.txt':
                raise PermissionError(13, 'Permission denied', path)
            return original(path)

        monkeypatch.setattr(os, 'unlink', locked)
        assert folder_rmtree('/data/folder') is False
        assert file_read_bytes('/data/folder/locked.txt') == b'x'
        assert not os.path.exists('/data/folder/free.txt')

    def test_rmtree_without_symlink(self, fs):
        """may_symlinks=False removes a folder that is not a link."""
        file_write('/data/folder/a.txt', b'x')
        assert folder_rmtree('/data/folder', may_symlinks=False) is True
        assert not os.path.exists('/data/folder')


class TestIsEmptyFolder:
    """Test cases for is_empty_folder()."""

    def test_empty(self, fs):
        """An empty folder reports True."""
        os.makedirs('/data/folder')
        assert is_empty_folder('/data/folder') is True

    def test_with_file(self, fs):
        """A folder holding a file reports False."""
        file_write('/data/folder/a.txt', b'x')
        assert is_empty_folder('/data/folder') is False

    def test_with_folder(self, fs):
        """A folder holding a folder reports False."""
        os.makedirs('/data/folder/sub')
        assert is_empty_folder('/data/folder') is False

    def test_missing(self, fs):
        """A missing path reports False."""
        assert is_empty_folder('/data/missing') is False

    def test_file(self, fs):
        """A path that is a file reports False."""
        file_write('/data/a.txt', b'x')
        assert is_empty_folder('/data/a.txt') is False

    def test_ignore_pycache(self, fs):
        """A folder holding only __pycache__ is empty with ignore_pycache."""
        os.makedirs('/data/folder/__pycache__')
        assert is_empty_folder('/data/folder', ignore_pycache=True) is True
        assert is_empty_folder('/data/folder') is False

    def test_ignore_pycache_other_entry(self, fs):
        """A folder holding __pycache__ and anything else is not empty."""
        os.makedirs('/data/folder/__pycache__')
        file_write('/data/folder/a.py', b'x')
        assert is_empty_folder('/data/folder', ignore_pycache=True) is False


class TestFolderRmtreeEmpty:
    """Test cases for folder_rmtree_empty()."""

    def test_rmtree_empty(self, fs):
        """An empty folder is removed."""
        os.makedirs('/data/folder')
        assert folder_rmtree_empty('/data/folder') is True
        assert not os.path.exists('/data/folder')

    def test_not_empty(self, fs):
        """A folder that holds an entry is kept as it is."""
        file_write('/data/folder/a.txt', b'x')
        assert folder_rmtree_empty('/data/folder') is False
        assert file_read_bytes('/data/folder/a.txt') == b'x'

    def test_missing(self, fs):
        """A missing folder reports False."""
        assert folder_rmtree_empty('/data/missing') is False

    def test_file(self, fs):
        """A path that is a file is kept as it is."""
        file_write('/data/a.txt', b'x')
        assert folder_rmtree_empty('/data/a.txt') is False
        assert file_read_bytes('/data/a.txt') == b'x'


class TestIsFolder:
    """Test cases for is_folder(), the kind check of file_replace()."""

    def test_folder(self, fs):
        """A folder reports True."""
        os.makedirs('/data/folder')
        assert is_folder('/data/folder') is True

    def test_file(self, fs):
        """A file reports False."""
        file_write('/data/a.txt', b'x')
        assert is_folder('/data/a.txt') is False

    def test_missing(self, fs):
        """A missing path reports False."""
        assert is_folder('/data/missing') is False

    def test_symlink_to_folder(self, fs):
        """The check follows a link: a link to a folder is the folder."""
        os.makedirs('/data/folder')
        fs.create_symlink('/data/link', '/data/folder')
        assert is_folder('/data/link') is True

    def test_symlink_to_file(self, fs):
        """A link to a file is not a folder."""
        file_write('/data/a.txt', b'x')
        fs.create_symlink('/data/link', '/data/a.txt')
        assert is_folder('/data/link') is False

    def test_dangling_symlink(self, fs):
        """A dangling link points at nothing, so it is not a folder."""
        fs.create_symlink('/data/link', '/data/missing')
        assert is_folder('/data/link') is False
