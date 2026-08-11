"""
In-memory fake filesystem for tests, a simplified and faster pyfakefs.

Every file is stored in memory, tests never read or write the real
disk. Files are recorded as msgspec Struct (FakeFile / FakeDir), and
stored in flat dicts keyed by normalized absolute path, so path
lookups are O(1) dict hits instead of the per-segment tree walks of
pyfakefs.

Usage as a pytest fixture: add the fixture to a conftest.py, then the
file functions are mocked in every test that requests it.

    from alasio.testing.filesystem import fs

    def test_write(fs):
        fs.create_file('/data.txt', contents='hello')
        with open('/data.txt') as f:
            assert f.read() == 'hello'

The fixture is a full replacement of the filesystem: every path is
served by the fake filesystem, nothing touches the real disk. Python
imports inside a test keep working because the import machinery reads
sources with io.open_code(), which is not patched.

Mocked functions:

- builtins.open() and io.open(), text and binary modes
- os.path.exists / isfile / isdir / islink / lexists / getsize
- os.stat / lstat / fstat
- os.makedirs / mkdir / rmdir / unlink / remove / rename / replace
- os.scandir / listdir
- os.open / write / close / fsync, low level fd operations
- os.utime / chmod / getcwd / chdir

The whole alasio/ext/path stack (PathStr, atomic read/write, iter
folders, makedir) works on the fake filesystem without changes, so it
is a drop-in replacement of pyfakefs for the deploy tests:

    fs.create_file('/full.pack', contents=...)
    fs.remove(target)
    env.PROJECT_ROOT = PathStr.new(fs.root_dir.path)

Not supported (documented limitations):

- symbolic links, islink() always returns False
- os.walk(), os.access() and other rarely used os functions
- tarfile writes through its own open(), it is not mocked
  (zipfile goes through io.open and works)
- do not combine the fixture with pytest tmp_path / tmpdir, the fake
  os.makedirs would create the tmp dirs in the fake filesystem
"""
import pytest

from .base import FakeDir, FakeFile
from .entry import FakeDirEntry, FakeScandirIterator
from .fake_fs import FakeFilesystem
from .file_object import FakeFileObject

__all__ = [
    'FakeDir',
    'FakeDirEntry',
    'FakeFile',
    'FakeFileObject',
    'FakeFilesystem',
    'FakeScandirIterator',
    'fs',
]


@pytest.fixture
def fs(monkeypatch):
    """
    An in-memory filesystem pytest fixture.

    The fixture patches builtins.open, io.open and the os / os.path
    file functions, all file operations go to the memory filesystem
    and the real disk is never touched. The patches are undone
    automatically when the test finishes.

    Yields:
        FakeFilesystem: The active fake filesystem
    """
    fake = FakeFilesystem()
    fake.activate(monkeypatch)
    yield fake
    fake.deactivate()
