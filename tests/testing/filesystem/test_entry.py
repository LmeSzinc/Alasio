"""
Tests for alasio/testing/filesystem/entry.py.

The mocks of os.DirEntry and the iterator returned by os.scandir().
"""
import os
import stat as statmod

import pytest
from conftest import join

from alasio.testing.filesystem import FakeDirEntry, FakeScandirIterator


def make_stat(st_mode, size=0):
    """
    Build a stat_result for a fake entry.

    Args:
        st_mode (int): Full mode with the file type bits
        size (int): File size. Defaults to 0.

    Returns:
        os.stat_result:
    """
    return os.stat_result((st_mode, 1, 0, 1, 0, 0, size, 0.0, 0.0, 0.0))


class TestFakeDirEntry:
    """Tests for FakeDirEntry."""

    def test_file_entry(self):
        """A file entry should report is_file() and its stat."""
        st = make_stat(0o100644, size=4)
        entry = FakeDirEntry('a.txt', '/dir/a.txt', False, st)
        assert entry.name == 'a.txt'
        assert entry.path == '/dir/a.txt'
        assert entry.is_file()
        assert not entry.is_dir()
        assert not entry.is_symlink()
        assert entry.is_file(follow_symlinks=False)
        assert not entry.is_dir(follow_symlinks=False)
        assert entry.stat() is st
        assert entry.stat(follow_symlinks=False) is st

    def test_dir_entry(self):
        """A directory entry should report is_dir() and its stat."""
        st = make_stat(0o40755)
        entry = FakeDirEntry('folder', '/dir/folder', True, st)
        assert entry.is_dir()
        assert not entry.is_file()
        assert not entry.is_symlink()
        assert statmod.S_ISDIR(entry.stat().st_mode)

    def test_repr(self):
        """The repr should contain the path."""
        entry = FakeDirEntry('a.txt', '/dir/a.txt', False, make_stat(0o100644))
        assert '/dir/a.txt' in repr(entry)


class TestFakeScandirIterator:
    """Tests for FakeScandirIterator."""

    def test_iteration(self):
        """Iterating should yield the entries in order."""
        st = make_stat(0o100644)
        entries = [FakeDirEntry('a', '/x/a', False, st), FakeDirEntry('b', '/x/b', False, st)]
        iterator = FakeScandirIterator(entries)
        assert list(iterator) == entries

    def test_stop_iteration(self):
        """A finished iterator should raise StopIteration."""
        iterator = FakeScandirIterator([])
        with pytest.raises(StopIteration):
            next(iterator)

    def test_context_manager(self):
        """The context manager should return the iterator."""
        st = make_stat(0o100644)
        entries = [FakeDirEntry('a', '/x/a', False, st)]
        with FakeScandirIterator(entries) as iterator:
            assert list(iterator) == entries

    def test_scandir_entries(self, fs):
        """fs.scandir() should yield FakeDirEntry objects."""
        fs.create_file(join(fs, 'a.txt'), contents='data')
        with fs.scandir(fs.root_dir.path) as entries:
            entry = next(entries)
        assert isinstance(entry, FakeDirEntry)
        assert entry.name == 'a.txt'
        assert entry.is_file()
        assert entry.stat().st_size == 4
