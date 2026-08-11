"""
Tests for alasio/testing/filesystem/fake_fs.py.

The FakeFilesystem class and the mocked os / builtins functions.
"""
import builtins
import io
import os
import stat as statmod

import pytest
from conftest import join

from alasio.ext.path import PathStr
from alasio.ext.path.atomic import file_read_bytes
from alasio.testing.filesystem import FakeDir, FakeFile, FakeFilesystem, fs  # noqa: F401

FILE = os.path.abspath(__file__)


class TestCreateFile:
    """create_file(), create_dir() and the get_object family."""

    def test_create_file_str_contents(self, fs):
        """str contents should be encoded with the encoding."""
        file = fs.create_file(join(fs, 'a.txt'), contents='你好', encoding='utf-8')
        assert file.content == '你好'.encode('utf-8')

    def test_create_file_default_encoding(self, fs):
        """str contents should default to utf-8."""
        file = fs.create_file(join(fs, 'a.txt'), contents='hello')
        assert file.content == b'hello'

    def test_create_file_auto_parents(self, fs):
        """Parent directories should be created automatically."""
        fs.create_file(join(fs, 'a', 'b', 'c.txt'))
        assert fs.isdir(join(fs, 'a'))
        assert fs.isdir(join(fs, 'a', 'b'))
        assert fs.isfile(join(fs, 'a', 'b', 'c.txt'))

    def test_create_file_exists(self, fs):
        """Creating an existing file should raise FileExistsError."""
        fs.create_file(join(fs, 'a.txt'))
        with pytest.raises(FileExistsError):
            fs.create_file(join(fs, 'a.txt'))

    def test_create_file_over_dir(self, fs):
        """Creating a file over a directory should raise IsADirectoryError."""
        fs.create_dir(join(fs, 'folder'))
        with pytest.raises(IsADirectoryError):
            fs.create_file(join(fs, 'folder'))

    def test_create_file_parent_is_file(self, fs):
        """A file parent should raise NotADirectoryError."""
        fs.create_file(join(fs, 'a.txt'))
        with pytest.raises(NotADirectoryError):
            fs.create_file(join(fs, 'a.txt', 'b.txt'))

    def test_create_file_bad_contents(self, fs):
        """Non str / bytes contents should raise TypeError."""
        with pytest.raises(TypeError):
            fs.create_file(join(fs, 'a.txt'), contents=123)

    def test_create_dir(self, fs):
        """create_dir() should create the directory and its parents."""
        folder = fs.create_dir(join(fs, 'a', 'b'))
        assert folder.path == join(fs, 'a', 'b')
        assert fs.isdir(join(fs, 'a', 'b'))

    def test_create_dir_exists(self, fs):
        """Creating an existing directory should raise FileExistsError."""
        fs.create_dir(join(fs, 'folder'))
        with pytest.raises(FileExistsError):
            fs.create_dir(join(fs, 'folder'))

    def test_get_object(self, fs):
        """get_object() should return the record of a file or directory."""
        fs.create_file(join(fs, 'a.txt'))
        fs.create_dir(join(fs, 'folder'))
        assert isinstance(fs.get_object(join(fs, 'a.txt')), FakeFile)
        assert isinstance(fs.get_object(join(fs, 'folder')), FakeDir)
        with pytest.raises(FileNotFoundError):
            fs.get_object(join(fs, 'missing'))

    def test_get_file_get_dir(self, fs):
        """get_file() / get_dir() should type check the path."""
        fs.create_file(join(fs, 'a.txt'))
        fs.create_dir(join(fs, 'folder'))
        assert isinstance(fs.get_file(join(fs, 'a.txt')), FakeFile)
        assert isinstance(fs.get_dir(join(fs, 'folder')), FakeDir)
        with pytest.raises(FileNotFoundError):
            fs.get_file(join(fs, 'folder'))
        with pytest.raises(FileNotFoundError):
            fs.get_dir(join(fs, 'a.txt'))

    def test_remove_file(self, fs):
        """remove() should delete a file."""
        fs.create_file(join(fs, 'a.txt'))
        fs.remove(join(fs, 'a.txt'))
        assert not fs.exists(join(fs, 'a.txt'))

    def test_remove_empty_dir(self, fs):
        """remove() should delete an empty directory."""
        fs.create_dir(join(fs, 'folder'))
        fs.remove(join(fs, 'folder'))
        assert not fs.exists(join(fs, 'folder'))

    def test_remove_nonempty_dir(self, fs):
        """remove() of a non-empty directory should raise OSError."""
        fs.create_file(join(fs, 'folder', 'a.txt'))
        with pytest.raises(OSError):
            fs.remove(join(fs, 'folder'))

    def test_remove_missing(self, fs):
        """remove() of a missing path should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            fs.remove(join(fs, 'missing'))

    def test_rmtree(self, fs):
        """rmtree() should recursively remove a directory."""
        fs.create_file(join(fs, 'folder', 'a', 'b.txt'))
        fs.create_file(join(fs, 'folder', 'c.txt'))
        fs.rmtree(join(fs, 'folder'))
        assert not fs.exists(join(fs, 'folder'))
        assert 'folder' not in fs.listdir(fs.root_dir.path)

    def test_rmtree_file(self, fs):
        """rmtree() should remove a single file too."""
        fs.create_file(join(fs, 'a.txt'))
        fs.rmtree(join(fs, 'a.txt'))
        assert not fs.exists(join(fs, 'a.txt'))


class TestOpenModes:
    """The open() mode handling."""

    def test_read_default(self, fs):
        """open() should default to text read mode."""
        fs.create_file(join(fs, 'a.txt'), contents='hello')
        with open(join(fs, 'a.txt')) as f:
            assert f.read() == 'hello'
            assert f.mode == 'r'

    def test_read_missing(self, fs):
        """Reading a missing file should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            open(join(fs, 'missing.txt'))

    def test_write_creates(self, fs):
        """Write mode should create the file and the parents."""
        with open(join(fs, 'a', 'b.txt'), 'w') as f:
            f.write('data')
        assert file_read_bytes(join(fs, 'a', 'b.txt')) == b'data'

    def test_write_truncates(self, fs):
        """Write mode should truncate the existing file at open."""
        fs.create_file(join(fs, 'a.txt'), contents='old')
        with open(join(fs, 'a.txt'), 'w') as f:
            f.write('new')
        assert open(join(fs, 'a.txt')).read() == 'new'

    def test_append(self, fs):
        """Append mode should write at the end."""
        fs.create_file(join(fs, 'a.txt'), contents='line1\n')
        with open(join(fs, 'a.txt'), 'a') as f:
            f.write('line2\n')
        assert open(join(fs, 'a.txt')).read() == 'line1\nline2\n'

    def test_append_creates(self, fs):
        """Append mode should create a missing file."""
        with open(join(fs, 'a.txt'), 'a') as f:
            f.write('data')
        assert open(join(fs, 'a.txt')).read() == 'data'

    def test_exclusive_creates(self, fs):
        """Exclusive mode should create a missing file."""
        with open(join(fs, 'a.txt'), 'x') as f:
            f.write('data')
        assert open(join(fs, 'a.txt')).read() == 'data'

    def test_exclusive_exists(self, fs):
        """Exclusive mode on an existing file should raise FileExistsError."""
        fs.create_file(join(fs, 'a.txt'))
        with pytest.raises(FileExistsError):
            open(join(fs, 'a.txt'), 'x')

    def test_read_write_plus(self, fs):
        """r+ should read and write at the position."""
        fs.create_file(join(fs, 'a.txt'), contents='hello')
        with open(join(fs, 'a.txt'), 'r+') as f:
            assert f.read() == 'hello'
        with open(join(fs, 'a.txt'), 'r+') as f:
            f.write('X')
            f.seek(0)
            assert f.read() == 'Xello'

    def test_write_read_plus(self, fs):
        """w+ should truncate and allow reading back."""
        with open(join(fs, 'a.txt'), 'w+') as f:
            f.write('data')
            f.seek(0)
            assert f.read() == 'data'

    def test_binary_mode(self, fs):
        """Binary mode should read and write bytes."""
        with open(join(fs, 'a.bin'), 'wb') as f:
            f.write(b'\x00\x01\xff')
        with open(join(fs, 'a.bin'), 'rb') as f:
            assert f.read() == b'\x00\x01\xff'
            assert f.encoding is None

    def test_buffering_ignored(self, fs):
        """buffering=0 should be accepted like file_read_bytes uses."""
        fs.create_file(join(fs, 'a.bin'), contents=b'data')
        with open(join(fs, 'a.bin'), 'rb', buffering=0) as f:
            assert f.read() == b'data'

    def test_open_dir(self, fs):
        """Opening a directory should raise IsADirectoryError."""
        fs.create_dir(join(fs, 'folder'))
        with pytest.raises(IsADirectoryError):
            open(join(fs, 'folder'))
        with pytest.raises(IsADirectoryError):
            open(join(fs, 'folder'), 'rb')

    def test_invalid_mode(self, fs):
        """An invalid mode should raise ValueError."""
        with pytest.raises(ValueError):
            open(join(fs, 'a.txt'), 'q')
        with pytest.raises(ValueError):
            open(join(fs, 'a.txt'), '')

    def test_pathstr_path(self, fs):
        """PathStr paths should be accepted by open()."""
        fs.create_file(join(fs, 'a.txt'), contents='hello')
        path = PathStr.new(join(fs, 'a.txt'))
        with open(path) as f:
            assert f.read() == 'hello'


class TestOsPath:
    """The mocked os.path functions."""

    def test_exists(self, fs):
        """os.path.exists() should work for files and directories."""
        fs.create_file(join(fs, 'a.txt'))
        fs.create_dir(join(fs, 'folder'))
        assert os.path.exists(join(fs, 'a.txt'))
        assert os.path.exists(join(fs, 'folder'))
        assert not os.path.exists(join(fs, 'missing'))

    def test_isfile_isdir(self, fs):
        """os.path.isfile() and isdir() should distinguish the types."""
        fs.create_file(join(fs, 'a.txt'))
        fs.create_dir(join(fs, 'folder'))
        assert os.path.isfile(join(fs, 'a.txt'))
        assert not os.path.isdir(join(fs, 'a.txt'))
        assert os.path.isdir(join(fs, 'folder'))
        assert not os.path.isfile(join(fs, 'folder'))
        assert not os.path.isfile(join(fs, 'missing'))
        assert not os.path.isdir(join(fs, 'missing'))

    def test_islink(self, fs):
        """os.path.islink() should always be False."""
        fs.create_file(join(fs, 'a.txt'))
        assert not os.path.islink(join(fs, 'a.txt'))
        assert not os.path.islink(join(fs, 'missing'))

    def test_lexists(self, fs):
        """os.path.lexists() should match exists()."""
        fs.create_file(join(fs, 'a.txt'))
        assert os.path.lexists(join(fs, 'a.txt'))
        assert not os.path.lexists(join(fs, 'missing'))

    def test_getsize(self, fs):
        """os.path.getsize() should return the content length."""
        fs.create_file(join(fs, 'a.txt'), contents='hello')
        assert os.path.getsize(join(fs, 'a.txt')) == 5
        with pytest.raises(FileNotFoundError):
            os.path.getsize(join(fs, 'missing'))

    def test_stat(self, fs):
        """os.stat() should return the record stat."""
        fs.create_file(join(fs, 'a.txt'), contents='hello', st_mode=0o100600)
        st = os.stat(join(fs, 'a.txt'))
        assert statmod.S_ISREG(st.st_mode)
        assert st.st_size == 5
        assert st.st_mode & 0o7777 == 0o600

    def test_stat_dir(self, fs):
        """os.stat() of a directory should report a directory."""
        fs.create_dir(join(fs, 'folder'))
        assert statmod.S_ISDIR(os.stat(join(fs, 'folder')).st_mode)

    def test_stat_missing(self, fs):
        """os.stat() of a missing path should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            os.stat(join(fs, 'missing'))

    def test_relative_paths(self, fs):
        """Relative paths should be resolved against the fake cwd."""
        folder = join(fs, 'cwd')
        os.makedirs(folder, exist_ok=True)
        os.chdir(folder)
        fs.create_file('a.txt', contents='x')
        assert os.path.exists('a.txt')
        assert os.path.isfile('a.txt')
        assert os.path.exists(join(fs, 'cwd', 'a.txt'))
        assert os.path.isdir('.')
        assert os.path.exists('..')


class TestOsDir:
    """The mocked os directory functions."""

    def test_makedirs(self, fs):
        """os.makedirs() should create nested directories."""
        os.makedirs(join(fs, 'a', 'b'))
        assert os.path.isdir(join(fs, 'a', 'b'))
        assert os.path.isdir(join(fs, 'a'))

    def test_makedirs_exist_ok(self, fs):
        """os.makedirs(exist_ok=True) should not raise on an existing dir."""
        os.makedirs(join(fs, 'a'))
        os.makedirs(join(fs, 'a'), exist_ok=True)

    def test_makedirs_existing_raises(self, fs):
        """os.makedirs() on an existing dir should raise FileExistsError."""
        os.makedirs(join(fs, 'a'))
        with pytest.raises(FileExistsError):
            os.makedirs(join(fs, 'a'))

    def test_mkdir(self, fs):
        """os.mkdir() should create a directory in an existing parent."""
        os.makedirs(join(fs, 'a'), exist_ok=True)
        os.mkdir(join(fs, 'a', 'b'))
        assert os.path.isdir(join(fs, 'a', 'b'))

    def test_mkdir_no_parent(self, fs):
        """os.mkdir() without a parent should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            os.mkdir(join(fs, 'a', 'b'))

    def test_mkdir_exists(self, fs):
        """os.mkdir() on an existing path should raise FileExistsError."""
        os.mkdir(join(fs, 'a'))
        with pytest.raises(FileExistsError):
            os.mkdir(join(fs, 'a'))

    def test_rmdir(self, fs):
        """os.rmdir() should remove an empty directory."""
        os.mkdir(join(fs, 'a'))
        os.rmdir(join(fs, 'a'))
        assert not os.path.exists(join(fs, 'a'))

    def test_rmdir_nonempty(self, fs):
        """os.rmdir() of a non-empty directory should raise OSError."""
        os.mkdir(join(fs, 'a'))
        fs.create_file(join(fs, 'a', 'b.txt'))
        with pytest.raises(OSError):
            os.rmdir(join(fs, 'a'))

    def test_rmdir_missing(self, fs):
        """os.rmdir() of a missing path should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            os.rmdir(join(fs, 'missing'))

    def test_rmdir_file(self, fs):
        """os.rmdir() of a file should raise NotADirectoryError."""
        fs.create_file(join(fs, 'a.txt'))
        with pytest.raises(NotADirectoryError):
            os.rmdir(join(fs, 'a.txt'))

    def test_unlink(self, fs):
        """os.unlink() should remove a file."""
        fs.create_file(join(fs, 'a.txt'))
        os.unlink(join(fs, 'a.txt'))
        assert not os.path.exists(join(fs, 'a.txt'))

    def test_unlink_missing(self, fs):
        """os.unlink() of a missing path should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            os.unlink(join(fs, 'missing'))

    def test_unlink_dir(self, fs):
        """os.unlink() of a directory should raise IsADirectoryError."""
        os.mkdir(join(fs, 'a'))
        with pytest.raises(IsADirectoryError):
            os.unlink(join(fs, 'a'))

    def test_remove_alias(self, fs):
        """os.remove() should behave like os.unlink()."""
        fs.create_file(join(fs, 'a.txt'))
        os.remove(join(fs, 'a.txt'))
        assert not os.path.exists(join(fs, 'a.txt'))

    def test_rename_file(self, fs):
        """os.rename() should move a file."""
        fs.create_file(join(fs, 'a.txt'), contents='data')
        os.rename(join(fs, 'a.txt'), join(fs, 'b.txt'))
        assert not os.path.exists(join(fs, 'a.txt'))
        assert open(join(fs, 'b.txt')).read() == 'data'

    def test_rename_dir(self, fs):
        """os.rename() should move a directory with its content."""
        fs.create_file(join(fs, 'a', 'b', 'c.txt'), contents='nested')
        os.rename(join(fs, 'a'), join(fs, 'd'))
        assert not os.path.exists(join(fs, 'a'))
        assert open(join(fs, 'd', 'b', 'c.txt')).read() == 'nested'
        assert os.path.isdir(join(fs, 'd', 'b'))

    def test_rename_missing(self, fs):
        """os.rename() of a missing source should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            os.rename(join(fs, 'missing'), join(fs, 'b.txt'))

    def test_replace_overwrites(self, fs):
        """os.replace() should replace the target file."""
        fs.create_file(join(fs, 'a.txt'), contents='new')
        fs.create_file(join(fs, 'b.txt'), contents='old')
        os.replace(join(fs, 'a.txt'), join(fs, 'b.txt'))
        assert not os.path.exists(join(fs, 'a.txt'))
        assert open(join(fs, 'b.txt')).read() == 'new'

    def test_replace_dir(self, fs):
        """os.replace() should move a directory."""
        fs.create_file(join(fs, 'a', 'c.txt'), contents='x')
        os.replace(join(fs, 'a'), join(fs, 'b'))
        assert open(join(fs, 'b', 'c.txt')).read() == 'x'

    def test_listdir(self, fs):
        """os.listdir() should return the sorted names."""
        fs.create_file(join(fs, 'dir', 'b.txt'))
        fs.create_file(join(fs, 'dir', 'a.txt'))
        os.mkdir(join(fs, 'dir', 'folder'))
        assert os.listdir(join(fs, 'dir')) == ['a.txt', 'b.txt', 'folder']

    def test_listdir_nested(self, fs):
        """os.listdir() should only return direct children."""
        fs.create_file(join(fs, 'a', 'b', 'c.txt'))
        assert os.listdir(join(fs, 'a')) == ['b']

    def test_listdir_missing(self, fs):
        """os.listdir() of a missing path should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            os.listdir(join(fs, 'missing'))

    def test_listdir_file(self, fs):
        """os.listdir() of a file should raise NotADirectoryError."""
        fs.create_file(join(fs, 'a.txt'))
        with pytest.raises(NotADirectoryError):
            os.listdir(join(fs, 'a.txt'))

    def test_scandir(self, fs):
        """os.scandir() should yield entries with the cached information."""
        fs.create_file(join(fs, 'dir', 'a.txt'), contents='data')
        fs.create_file(join(fs, 'dir', 'b.txt'))
        os.mkdir(join(fs, 'dir', 'folder'))
        with os.scandir(join(fs, 'dir')) as entries:
            result = {entry.name: entry for entry in entries}
        assert set(result) == {'a.txt', 'b.txt', 'folder'}
        assert result['a.txt'].is_file()
        assert not result['a.txt'].is_dir()
        assert not result['a.txt'].is_symlink()
        assert result['folder'].is_dir()
        assert not result['folder'].is_file()
        assert statmod.S_IFMT(result['folder'].stat().st_mode) == statmod.S_IFDIR
        assert result['a.txt'].stat().st_size == 4
        assert result['a.txt'].path == join(fs, 'dir', 'a.txt')

    def test_scandir_missing(self, fs):
        """os.scandir() of a missing path should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            os.scandir(join(fs, 'missing'))

    def test_scandir_file(self, fs):
        """os.scandir() of a file should raise NotADirectoryError."""
        fs.create_file(join(fs, 'a.txt'))
        with pytest.raises(NotADirectoryError):
            os.scandir(join(fs, 'a.txt'))


class TestOsFd:
    """The mocked low level fd functions os.open / write / close."""

    def test_os_open_create(self, fs):
        """os.open() with O_CREAT should create the file."""
        fd = os.open(join(fs, 'a.bin'), os.O_CREAT | os.O_WRONLY, 0o644)
        assert isinstance(fd, int)
        assert os.path.exists(join(fs, 'a.bin'))
        os.close(fd)

    def test_os_open_missing(self, fs):
        """os.open() without O_CREAT on a missing file should raise."""
        with pytest.raises(FileNotFoundError):
            os.open(join(fs, 'a.bin'), os.O_WRONLY)

    def test_os_open_excl(self, fs):
        """os.open() with O_CREAT | O_EXCL should raise on an existing file."""
        fd = os.open(join(fs, 'a.bin'), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        with pytest.raises(FileExistsError):
            os.open(join(fs, 'a.bin'), os.O_CREAT | os.O_EXCL | os.O_WRONLY)

    def test_os_open_trunc(self, fs):
        """os.open() with O_TRUNC should truncate the file."""
        fs.create_file(join(fs, 'a.bin'), contents=b'old')
        fd = os.open(join(fs, 'a.bin'), os.O_WRONLY | os.O_TRUNC)
        os.close(fd)
        assert os.path.getsize(join(fs, 'a.bin')) == 0

    def test_os_write(self, fs):
        """os.write() should write to the fd."""
        fd = os.open(join(fs, 'a.bin'), os.O_CREAT | os.O_WRONLY)
        assert os.write(fd, b'data') == 4
        os.close(fd)
        assert open(join(fs, 'a.bin'), 'rb').read() == b'data'

    def test_os_open_append(self, fs):
        """os.open() with O_APPEND should write at the end."""
        fs.create_file(join(fs, 'a.bin'), contents=b'old')
        fd = os.open(join(fs, 'a.bin'), os.O_WRONLY | os.O_APPEND)
        os.write(fd, b'new')
        os.close(fd)
        assert open(join(fs, 'a.bin'), 'rb').read() == b'oldnew'

    def test_os_close(self, fs):
        """os.close() should release the fd."""
        fd = os.open(join(fs, 'a.bin'), os.O_CREAT | os.O_WRONLY)
        os.close(fd)
        with pytest.raises(OSError):
            os.close(fd)
        with pytest.raises(OSError):
            os.write(fd, b'x')

    def test_os_fsync(self, fs):
        """os.fsync() should be a no-op for a valid fd."""
        fd = os.open(join(fs, 'a.bin'), os.O_CREAT | os.O_WRONLY)
        os.fsync(fd)
        os.close(fd)
        with pytest.raises(OSError):
            os.fsync(fd)

    def test_os_fstat(self, fs):
        """os.fstat() should return the stat of the open file."""
        fd = os.open(join(fs, 'a.bin'), os.O_CREAT | os.O_WRONLY, 0o600)
        os.write(fd, b'data')
        st = os.fstat(fd)
        assert st.st_size == 4
        assert st.st_mode & 0o7777 == 0o600
        os.close(fd)
        with pytest.raises(OSError):
            os.fstat(fd)


class TestOsMisc:
    """The mocked os.utime / chmod / getcwd / chdir."""

    def test_utime(self, fs):
        """os.utime() should set the access and modification time."""
        fs.create_file(join(fs, 'a.txt'))
        os.utime(join(fs, 'a.txt'), (111.0, 222.0))
        st = os.stat(join(fs, 'a.txt'))
        assert st.st_atime == 111.0
        assert st.st_mtime == 222.0

    def test_utime_now(self, fs):
        """os.utime() without times should set the current time."""
        fs.create_file(join(fs, 'a.txt'))
        os.utime(join(fs, 'a.txt'))
        st = os.stat(join(fs, 'a.txt'))
        assert st.st_atime > 0
        assert st.st_mtime > 0

    def test_utime_missing(self, fs):
        """os.utime() of a missing path should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            os.utime(join(fs, 'missing'))

    def test_chmod(self, fs):
        """os.chmod() should update the permission bits."""
        fs.create_file(join(fs, 'a.txt'))
        os.chmod(join(fs, 'a.txt'), 0o755)
        assert os.stat(join(fs, 'a.txt')).st_mode & 0o7777 == 0o755

    def test_chmod_dir(self, fs):
        """os.chmod() should work on directories."""
        os.mkdir(join(fs, 'a'))
        os.chmod(join(fs, 'a'), 0o700)
        assert os.stat(join(fs, 'a')).st_mode & 0o7777 == 0o700

    def test_chmod_missing(self, fs):
        """os.chmod() of a missing path should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            os.chmod(join(fs, 'missing'), 0o755)

    def test_getcwd(self, fs):
        """os.getcwd() should return the fake cwd."""
        cwd = os.getcwd()
        assert cwd
        assert os.path.isdir(cwd)

    def test_chdir(self, fs):
        """os.chdir() should change the fake cwd."""
        os.makedirs(join(fs, 'a', 'b'), exist_ok=True)
        os.chdir(join(fs, 'a', 'b'))
        assert os.getcwd() == join(fs, 'a', 'b')
        # relative paths now resolve under the new cwd
        fs.create_file('c.txt', contents='x')
        assert os.path.exists(join(fs, 'a', 'b', 'c.txt'))

    def test_chdir_missing(self, fs):
        """os.chdir() of a missing path should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            os.chdir(join(fs, 'missing'))

    def test_chdir_file(self, fs):
        """os.chdir() of a file should raise NotADirectoryError."""
        fs.create_file(join(fs, 'a.txt'))
        with pytest.raises(NotADirectoryError):
            os.chdir(join(fs, 'a.txt'))


class TestActivate:
    """activate() / deactivate() and the mock scope."""

    def test_context_manager(self):
        """The context manager should patch and restore the functions."""
        real_open = builtins.open
        real_exists = os.path.exists
        with FakeFilesystem() as fs:
            assert builtins.open.__self__ is fs
            assert os.path.exists.__self__ is fs
            fs.create_file('/x.txt', contents='y')
            with open('/x.txt') as f:
                assert f.read() == 'y'
        assert builtins.open is real_open
        assert os.path.exists is real_exists

    def test_io_open_patched(self):
        """io.open() should be patched too, for pathlib and zipfile."""
        with FakeFilesystem() as fs:
            assert io.open.__self__ is fs
            fs.create_file('/x.txt', contents='y')
            assert io.open('/x.txt').read() == 'y'

    def test_manual_activate_deactivate(self, monkeypatch):
        """activate(monkeypatch) should work and undo at the end."""
        fake = FakeFilesystem()
        fake.activate(monkeypatch)
        fake.create_file('/x.txt', contents='y')
        assert open('/x.txt').read() == 'y'
        fake.deactivate()

    def test_fixture_type(self, fs):
        """The fs fixture should yield a FakeFilesystem, not pyfakefs'."""
        assert isinstance(fs, FakeFilesystem)
        assert os.path.exists.__self__ is fs

    def test_isolation_from_real_disk(self, fs):
        """Real disk paths should be invisible in the fake filesystem."""
        # FILE is a real file of this test module
        assert not fs.exists(FILE)
        with pytest.raises(FileNotFoundError):
            open(FILE, 'rb')

    def test_real_disk_untouched(self):
        """Creating a fake file at a real path must not modify the real file."""
        with open(FILE, 'rb') as f:
            before = f.read()
        with FakeFilesystem() as fs:
            fs.create_file(FILE, contents=b'overwrite?')
            assert open(FILE, 'rb').read() == b'overwrite?'
        with open(FILE, 'rb') as f:
            after = f.read()
        assert after == before

    def test_in_test_import_works(self, fs):
        """Python imports inside a test should keep working."""
        import alasio.deploy.pack.job_reset as module
        assert module.__name__ == 'alasio.deploy.pack.job_reset'
