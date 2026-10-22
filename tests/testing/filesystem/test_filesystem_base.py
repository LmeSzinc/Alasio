"""
Tests for alasio/testing/filesystem/base.py.

Path normalization and the FakeFile / FakeDir records (msgspec Struct).
"""
import os
import stat as statmod

import msgspec
import pytest

from alasio.ext.path import PathStr
from alasio.ext.path.calc import joinpath
from alasio.testing.filesystem import FakeDir, FakeFile, FakeSymlink, fs  # noqa: F401
from alasio.testing.filesystem.base import IS_WINDOWS, _normpath, _parent_path, _split_abspath
from tests.testing.filesystem.conftest import join


class TestNormpath:
    """Tests for _normpath()."""

    def _cases(self):
        """
        Returns:
            list[tuple[str, str]]: (path, expected) pairs of one platform
        """
        root = 'C:' if IS_WINDOWS else '/'
        cwd = joinpath(root, 'work')
        return [
            # absolute paths
            ('/a/b', joinpath(root, 'a/b')),
            ('/a/../b', joinpath(root, 'b')),
            ('/a/./b', joinpath(root, 'a/b')),
            ('//a//b', joinpath(root, 'a/b')),
            ('/a/../..', root),
            ('/data.txt', joinpath(root, 'data.txt')),
            # relative paths
            ('a.txt', joinpath(cwd, 'a.txt')),
            ('a/b/../c', joinpath(cwd, 'a/c')),
            ('a/../../b', joinpath(root, 'b')),
            ('a/../..', root),
            ('./a', joinpath(cwd, 'a')),
            ('', cwd),
        ]

    def test_normpath(self):
        """Paths should normalize to absolute "/" separated paths."""
        for path, expected in self._cases():
            cwd = 'C:/work' if IS_WINDOWS else '/work'
            assert _normpath(path, cwd) == expected, path

    def test_root_cwd(self):
        """Relative paths against the root cwd should work."""
        root = 'C:' if IS_WINDOWS else '/'
        assert _normpath('a/b', root) == joinpath(root, 'a/b')
        assert _normpath('..', root) == root
        assert _normpath('a/../..', root) == root

    @pytest.mark.skipif(not IS_WINDOWS, reason='backslash is a separator on Windows only')
    def test_backslash(self):
        """Windows style backslash paths should be normalized."""
        assert _normpath(r'a\b\c.txt', 'C:/work') == 'C:/work/a/b/c.txt'
        assert _normpath(r'C:\a\b.txt', 'C:/work') == 'C:/a/b.txt'

    @pytest.mark.skipif(not IS_WINDOWS, reason='drive letters are Windows only')
    def test_drive_letters(self):
        """Drive letters should be kept, drive root is "C:"."""
        assert _normpath('C:/a/b', 'C:/work') == 'C:/a/b'
        assert _normpath('C:/', 'C:/work') == 'C:'
        assert _normpath('C:', 'C:/work') == 'C:'
        assert _normpath('C:/a/..', 'C:/work') == 'C:'
        assert _normpath('D:/a', 'C:/work') == 'D:/a'

    @pytest.mark.skipif(IS_WINDOWS, reason='POSIX paths are Windows only')
    def test_posix_root(self):
        """The POSIX root should stay "/"."""
        assert _normpath('/a/b', '/work') == '/a/b'
        assert _normpath('/', '/work') == '/'
        assert _normpath('/a/..', '/work') == '/'

    def test_pathstr_input(self):
        """PathStr and PathLike inputs should be accepted."""
        cwd = 'C:/work' if IS_WINDOWS else '/work'
        assert _normpath(PathStr.new('/a/b'), cwd) == _normpath('/a/b', cwd)


class TestSplitAbspath:
    """Tests for _split_abspath()."""

    def test_dot_segments_are_kept(self):
        """The caller resolves "." and "..", a link has to be resolved first."""
        assert _split_abspath('/a/./b/../c', '/work') == ('/', ['a', '.', 'b', '..', 'c'])

    def test_empty_components_are_dropped(self):
        """Duplicate separators do not become components."""
        assert _split_abspath('//a//b', '/') == ('/', ['a', 'b'])

    def test_relative_path(self):
        """A relative path is resolved against cwd, the segments are kept."""
        cwd = 'C:/work' if IS_WINDOWS else '/work'
        root = 'C:' if IS_WINDOWS else '/'
        assert _split_abspath('a/../b', cwd) == (root, ['work', 'a', '..', 'b'])

    @pytest.mark.skipif(not IS_WINDOWS, reason='drive letters are Windows only')
    def test_windows_drive(self):
        """A drive letter is the root of the path, the root is "C:"."""
        assert _split_abspath('C:/a', 'C:/work') == ('C:', ['a'])
        assert _split_abspath('C:a', 'C:/work') == ('C:', ['a'])
        assert _split_abspath('C:', 'C:/work') == ('C:', [])
        assert _split_abspath('D:/a', 'C:/work') == ('D:', ['a'])
        # an absolute path without a drive takes the drive of cwd
        assert _split_abspath('/a', 'C:/work') == ('C:', ['a'])

    @pytest.mark.skipif(IS_WINDOWS, reason='the POSIX root is not a drive')
    def test_posix_root(self):
        """The root of a POSIX path is "/"."""
        assert _split_abspath('/a', '/work') == ('/', ['a'])
        assert _split_abspath('/', '/work') == ('/', [])


class TestParentPath:
    """Tests for _parent_path()."""

    def test_child(self):
        """The parent of a child is its directory."""
        root = 'C:' if IS_WINDOWS else '/'
        assert _parent_path(joinpath(root, 'a/b.txt')) == joinpath(root, 'a')

    def test_root_is_its_own_parent(self):
        """The parent of the root is the root, never an empty path."""
        assert _parent_path('/') == '/'
        assert _parent_path('/a') == '/'

    @pytest.mark.skipif(not IS_WINDOWS, reason='drive letters are Windows only')
    def test_drive_root(self):
        """The parent of a drive root is the drive root, not "/"."""
        assert _parent_path('C:/') == 'C:'
        assert _parent_path('C:/a') == 'C:'
        assert _parent_path('C:') == 'C:'


class TestFakeFileRecord:
    """FakeFile and FakeDir records, msgspec Struct of file information."""

    def test_fake_file_is_msgspec_struct(self, fs):
        """create_file() should return a msgspec Struct record."""
        file = fs.create_file(join(fs, 'a.txt'), contents=b'data')
        assert isinstance(file, FakeFile)
        assert isinstance(file, msgspec.Struct)
        assert file.path == join(fs, 'a.txt')
        assert file.content == b'data'
        assert file.mode == 0o666

    def test_fake_dir_is_msgspec_struct(self, fs):
        """create_dir() should return a msgspec Struct record."""
        folder = fs.create_dir(join(fs, 'folder'))
        assert isinstance(folder, FakeDir)
        assert isinstance(folder, msgspec.Struct)
        assert folder.path == join(fs, 'folder')
        assert folder.mode == 0o777

    def test_stat_result(self, fs):
        """stat() should return a real os.stat_result with mode and size."""
        fs.create_file(join(fs, 'a.txt'), contents=b'data', st_mode=0o100755)
        st = fs.stat(join(fs, 'a.txt'))
        assert isinstance(st, os.stat_result)
        assert statmod.S_ISREG(st.st_mode)
        assert not statmod.S_ISDIR(st.st_mode)
        assert st.st_mode & 0o7777 == 0o755
        assert st.st_size == 4

    def test_stat_dir(self, fs):
        """stat() of a directory should have the directory type bit."""
        fs.create_dir(join(fs, 'folder'))
        st = fs.stat(join(fs, 'folder'))
        assert statmod.S_ISDIR(st.st_mode)
        assert not statmod.S_ISREG(st.st_mode)

    def test_stat_missing(self, fs):
        """stat() of a missing path should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            fs.stat(join(fs, 'missing.txt'))

    def test_inode_unique(self, fs):
        """Every record should get a unique inode number."""
        a = fs.create_file(join(fs, 'a.txt'))
        b = fs.create_file(join(fs, 'b.txt'))
        c = fs.create_dir(join(fs, 'folder'))
        assert len({a.ino, b.ino, c.ino}) == 3

    def test_timestamps(self, fs):
        """Timestamps should be set at creation."""
        file = fs.create_file(join(fs, 'a.txt'))
        assert file.atime > 0
        assert file.mtime > 0
        assert file.ctime > 0


class TestFakeSymlinkRecord:
    """FakeSymlink record, msgspec Struct of a symbolic link."""

    def test_fake_symlink_is_msgspec_struct(self, fs):
        """create_symlink() should return a msgspec Struct record."""
        link = fs.create_symlink(join(fs, 'link'), join(fs, 'a.txt'))
        assert isinstance(link, FakeSymlink)
        assert isinstance(link, msgspec.Struct)
        assert link.path == join(fs, 'link')
        assert link.target == join(fs, 'a.txt')

    def test_stat_result(self, fs):
        """stat() of the link itself should have the S_IFLNK type bit."""
        link = fs.create_symlink(join(fs, 'link'), join(fs, 'a.txt'))
        st = link.stat()
        assert isinstance(st, os.stat_result)
        assert statmod.S_ISLNK(st.st_mode)
        assert not statmod.S_ISREG(st.st_mode)
        assert not statmod.S_ISDIR(st.st_mode)
        # the size is the length of the target string, like the real os
        assert st.st_size == len(join(fs, 'a.txt'))

    def test_inode_unique(self, fs):
        """A symlink should get its own inode number."""
        file = fs.create_file(join(fs, 'a.txt'))
        link = fs.create_symlink(join(fs, 'link'), join(fs, 'a.txt'))
        assert file.ino != link.ino
