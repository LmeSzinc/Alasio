"""
Tests for file_open() and the makedir_on_write switch of atomic_open().

Covered: read modes behaving like open(), write modes creating the
missing parent directory, and disabling the directory creation. The
atomic_open() tests check that the switch is forwarded to file_open().

The tests run on the in-memory fake filesystem (the fs fixture), the
real disk is never touched.
"""
import os

import pytest

from alasio.ext.path import atomic
from alasio.ext.path.atomic import atomic_open, file_open, file_read_bytes, file_read_text
from alasio.testing.filesystem import fs  # noqa: F401
from tests.ext.path.atomic.helpers import break_function


class TestFileOpenWhenRead:
    """Read modes should behave exactly like open()."""

    def test_read_text(self, fs):
        """An existing text file should be readable."""
        fs.create_file('/data/a.txt', contents='data')
        with file_open('/data/a.txt', 'r', encoding='utf-8') as f:
            assert f.read() == 'data'

    def test_read_bytes(self, fs):
        """An existing binary file should be readable."""
        fs.create_file('/data/a.bin', contents=b'data')
        with file_open('/data/a.bin', 'rb') as f:
            assert f.read() == b'data'

    def test_read_missing_file(self, fs):
        """A missing file should raise FileNotFoundError, like open()."""
        with pytest.raises(FileNotFoundError):
            file_open('/data/a.txt', 'r', encoding='utf-8')

    def test_read_missing_parent(self, fs):
        """Read modes should not create the missing parent directory."""
        with pytest.raises(FileNotFoundError):
            file_open('/data/a/b.txt', 'r', encoding='utf-8')
        assert not os.path.exists('/data/a')

    def test_read_update_missing_parent(self, fs):
        """'r+' does not create the file, so it should behave like open()."""
        with pytest.raises(FileNotFoundError):
            file_open('/data/a/b.txt', 'r+', encoding='utf-8')
        assert not os.path.exists('/data/a')


class TestFileOpenWhenWrite:
    """Write modes should create the missing parent directory."""

    def test_write_creates_parents(self, fs):
        """The parent folders should be created on write."""
        with file_open('/data/a/b/c.txt', 'w', encoding='utf-8') as f:
            f.write('data')
        assert file_read_text('/data/a/b/c.txt') == 'data'

    def test_write_existing_file(self, fs):
        """An existing file should be truncated, in its existing folder."""
        fs.create_file('/data/a.txt', contents='old')
        with file_open('/data/a.txt', 'w', encoding='utf-8') as f:
            f.write('new')
        assert file_read_text('/data/a.txt') == 'new'

    def test_append_creates_parents(self, fs):
        """The append mode should create the missing parent folders too."""
        with file_open('/data/a/b.txt', 'a', encoding='utf-8') as f:
            f.write('data')
        assert file_read_text('/data/a/b.txt') == 'data'

    def test_exclusive_creates_parents(self, fs):
        """The exclusive create mode should create the missing parent folders."""
        with file_open('/data/a/b.txt', 'x', encoding='utf-8') as f:
            f.write('data')
        assert file_read_text('/data/a/b.txt') == 'data'

    def test_binary_creates_parents(self, fs):
        """The binary write mode should create the missing parent folders."""
        with file_open('/data/a/b.bin', 'wb') as f:
            f.write(b'data')
        assert file_read_bytes('/data/a/b.bin') == b'data'

    def test_write_no_makedirs_when_parent_exists(self, fs, monkeypatch):
        """The parent folders should only be created after open() failed."""
        makedirs = break_function(monkeypatch, os, 'makedirs', [])
        fs.create_file('/data/a.txt', contents='old')
        with file_open('/data/a.txt', 'a', encoding='utf-8') as f:
            f.write('new')
        assert makedirs == []
        assert file_read_text('/data/a.txt') == 'oldnew'

    def test_write_retries_open_after_makedirs(self, fs, monkeypatch):
        """open() should be called again after the parent folders are created."""
        error = FileNotFoundError(2, 'No such file or directory')
        calls = break_function(monkeypatch, atomic, 'open', [error])
        makedirs = break_function(monkeypatch, os, 'makedirs', [])
        with file_open('/data/a/b/c.txt', 'w', encoding='utf-8') as f:
            f.write('data')
        assert len(calls) == 2
        assert makedirs == [(('/data/a/b',), {'exist_ok': True})]
        assert file_read_text('/data/a/b/c.txt') == 'data'

    def test_write_kwargs_forwarded(self, fs, monkeypatch):
        """Extra keyword arguments should be forwarded, makedir_on_write not."""
        calls = break_function(monkeypatch, atomic, 'open', [])
        with file_open('/data/a.txt', 'w', encoding='utf-8', newline='') as f:
            f.write('data')
        assert calls[0][0] == ('/data/a.txt',)
        assert calls[0][1] == {'mode': 'w', 'encoding': 'utf-8', 'newline': ''}
        assert file_read_text('/data/a.txt') == 'data'


class TestFileOpenWhenMakedirDisabled:
    """makedir_on_write=False should behave exactly like open()."""

    def test_write_missing_parent(self, fs):
        """The write mode should raise instead of creating the folders."""
        with pytest.raises(FileNotFoundError):
            file_open('/data/a/b.txt', 'w', encoding='utf-8', makedir_on_write=False)
        assert not os.path.exists('/data/a')

    def test_append_missing_parent(self, fs):
        """The append mode should raise instead of creating the folders."""
        with pytest.raises(FileNotFoundError):
            file_open('/data/a/b.txt', 'a', encoding='utf-8', makedir_on_write=False)
        assert not os.path.exists('/data/a')

    def test_read_existing_file(self, fs):
        """Read modes should not be affected by the switch."""
        fs.create_file('/data/a.txt', contents='data')
        with file_open('/data/a.txt', 'r', encoding='utf-8', makedir_on_write=False) as f:
            assert f.read() == 'data'


class TestAtomicOpenMakedir:
    """atomic_open() should pass makedir_on_write through to file_open()."""

    def test_write_creates_parents(self, fs):
        """The parent folders should be created on write."""
        with atomic_open('/data/a/b.txt', 'w', encoding='utf-8') as f:
            f.write('data')
        assert file_read_text('/data/a/b.txt') == 'data'

    def test_write_creates_parents_on_windows(self, fs, monkeypatch):
        """The Windows retry loop should create the parent folders as well."""
        monkeypatch.setattr(atomic, 'IS_WINDOWS', True)
        with atomic_open('/data/a/b.txt', 'w', encoding='utf-8') as f:
            f.write('data')
        assert file_read_text('/data/a/b.txt') == 'data'

    def test_write_makedir_disabled(self, fs):
        """The switch should be forwarded to file_open()."""
        with pytest.raises(FileNotFoundError):
            atomic_open('/data/a/b.txt', 'a', encoding='utf-8', makedir_on_write=False)
        assert not os.path.exists('/data/a')

    def test_read_missing_parent(self, fs):
        """Read modes should not create the missing parent directory."""
        with pytest.raises(FileNotFoundError):
            atomic_open('/data/a/b.txt', 'r', encoding='utf-8')
        assert not os.path.exists('/data/a')
