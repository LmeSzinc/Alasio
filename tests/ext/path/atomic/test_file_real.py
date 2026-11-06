"""
Tests of the file_* and atomic_* functions on a real disk, run on CI.

The rest of the suite runs on the in-memory fake filesystem, which models Linux
on every host. These tests are the ones that touch the disk of the runner, so
the answers are the ones of the operating system under test: the Windows
sharing rules, the POSIX permissions, the real os.open flags.

The file is skipped outside CI on purpose: the rest of the suite keeps running
on the fake filesystem there, and a developer machine (an antivirus holding a
file, a directory that is not writable for the user, a Windows one without the
symlink privilege) would change the answer of the operating system rather than
exercise the code under test.

Both halves run on CI: the fake filesystem always, this file when CI is set.
"""
import os
import stat
import tempfile

import pytest

from alasio.ext import env
from alasio.ext.path.atomic import (
    atomic_copy, atomic_remove, atomic_rmtree, atomic_write, file_copy, file_ensure_exist, file_open, file_read_bytes,
    file_read_bytes_into, file_read_bytes_stream, file_read_text, file_read_text_stream, file_remove, file_touch,
    file_write, file_write_stream, folder_rmtree, folder_rmtree_empty, is_empty_folder
)
from alasio.ext.path.calc import joinpath

pytestmark = [
    pytest.mark.skipif(
        not env.CI, reason='the real disk is covered by the CI matrix, the local runs use the fake filesystem'),
    pytest.mark.parallel,
]


@pytest.fixture
def folder():
    """
    A folder of the real disk, removed after the test.

    Yields:
        str: Absolute path of an empty folder, '/'-separated
    """
    path = tempfile.mkdtemp(prefix='alasio_file_').replace(os.sep, '/')
    try:
        yield path
    finally:
        _rmtree(path)


def _rmtree(path):
    """
    Remove a folder of the real disk, read-only entries included.

    Args:
        path (str): Folder to remove
    """
    def clear_and_retry(func, target, _):
        try:
            os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
            func(target)
        except OSError:
            pass

    try:
        import shutil
        shutil.rmtree(path, onerror=clear_and_retry)
    except OSError:
        pass


class TestWriteAndRead:
    """file_write() and the readers on a real disk."""

    def test_write_read_text(self, folder):
        """Text is written as utf-8 and read back as it was."""
        path = joinpath(folder, 'a/b/c.txt')
        file_write(path, '你好\nworld\n')

        assert file_read_text(path) == '你好\nworld\n'
        assert os.path.isdir(joinpath(folder, 'a/b'))

    def test_write_read_bytes(self, folder):
        """Bytes that are not text survive the round trip."""
        path = joinpath(folder, 'a.bin')
        content = bytes(range(256))
        file_write(path, content)

        assert file_read_bytes(path) == content

    def test_write_overwrite(self, folder):
        """An existing file is replaced by the new content."""
        path = joinpath(folder, 'a.txt')
        file_write(path, 'old content')
        file_write(path, 'new')

        assert file_read_text(path) == 'new'

    def test_write_stream(self, folder):
        """The chunks of a generator land in order."""
        path = joinpath(folder, 'a/b/c.txt')
        file_write_stream(path, iter(['a', 'b', 'c']))

        assert file_read_text(path) == 'abc'

    def test_read_stream(self, folder):
        """The stream readers concatenate to the whole content."""
        binary = joinpath(folder, 'a.bin')
        content = bytes(range(256)) * 4
        file_write(binary, content)

        assert b''.join(file_read_bytes_stream(binary, chunk_size=16)) == content

        text = joinpath(folder, 'a.txt')
        expected = ''.join(f'line {index}\n' for index in range(40))
        file_write(text, expected)
        assert ''.join(file_read_text_stream(text, chunk_size=16)) == expected

    def test_read_into(self, folder):
        """The buffer is filled in chunks, the lengths sum to the size."""
        path = joinpath(folder, 'a.bin')
        content = b'0123456789'
        file_write(path, content)
        buffer = bytearray(4)

        total = 0
        data = bytearray()
        for length in file_read_bytes_into(path, memoryview(buffer)):
            data += bytes(buffer[:length])
            total += length

        assert bytes(data) == content
        assert total == len(content)

    def test_read_missing(self, folder):
        """A missing file raises on every reader."""
        path = joinpath(folder, 'missing.txt')
        with pytest.raises(FileNotFoundError):
            file_read_text(path)
        with pytest.raises(FileNotFoundError):
            file_read_bytes(path)


class TestOpen:
    """file_open() on a real disk."""

    def test_write_creates_parent(self, folder):
        """A write mode prepares the parent folder."""
        path = joinpath(folder, 'a/b/c.txt')
        with file_open(path, 'w', encoding='utf-8') as f:
            f.write('data')

        assert file_read_text(path) == 'data'

    def test_read_missing_raises(self, folder):
        """A read mode behaves like open(), nothing is created."""
        path = joinpath(folder, 'a/b/c.txt')
        with pytest.raises(FileNotFoundError):
            file_open(path, 'r')
        assert not os.path.exists(joinpath(folder, 'a'))

    def test_no_makedir(self, folder):
        """makedir_on_write=False behaves exactly like open()."""
        with pytest.raises(FileNotFoundError):
            file_open(joinpath(folder, 'a/b/c.txt'), 'w', makedir_on_write=False)

    def test_binary(self, folder):
        """A binary write mode round trips the bytes."""
        path = joinpath(folder, 'a.bin')
        with file_open(path, 'wb') as f:
            f.write(b'\x00\xff')

        assert file_read_bytes(path) == b'\x00\xff'


class TestCopy:
    """file_copy() and atomic_copy() on a real disk."""

    def test_copy(self, folder):
        """The content is copied, the source stays."""
        source = joinpath(folder, 'source.bin')
        target = joinpath(folder, 'a/b/target.bin')
        content = bytes(range(256)) * 4
        file_write(source, content)

        file_copy(source, target, chunk_size=16)

        assert file_read_bytes(target) == content
        assert file_read_bytes(source) == content

    def test_copy_empty(self, folder):
        """An empty source produces an existing empty target."""
        source = joinpath(folder, 'source.bin')
        target = joinpath(folder, 'target.bin')
        file_write(source, b'')

        file_copy(source, target)

        assert os.path.isfile(target)
        assert file_read_bytes(target) == b''

    def test_atomic_copy(self, folder):
        """The atomic copy leaves no tmp file behind."""
        source = joinpath(folder, 'source.bin')
        target = joinpath(folder, 'a/b/target.bin')
        content = bytes(range(256))
        file_write(source, content)

        atomic_copy(source, target)

        assert file_read_bytes(target) == content
        assert sorted(os.listdir(joinpath(folder, 'a/b'))) == ['target.bin']

    def test_atomic_copy_missing_source(self, folder):
        """A missing source is an error, no target and no tmp file is left."""
        with pytest.raises(FileNotFoundError):
            atomic_copy(joinpath(folder, 'missing.bin'), joinpath(folder, 'target.bin'))

        assert os.listdir(folder) == []


class TestEnsureExistAndTouch:
    """file_ensure_exist() and file_touch() on a real disk."""

    def test_ensure_exist_creates(self, folder):
        """A missing file is created with the default content, the parent
        folders included."""
        path = joinpath(folder, 'a/b/c.txt')

        assert file_ensure_exist(path, default=b'default') is True
        assert file_read_bytes(path) == b'default'

    def test_ensure_exist_keeps(self, folder):
        """An existing file keeps its content."""
        path = joinpath(folder, 'a.txt')
        file_write(path, b'the content')

        assert file_ensure_exist(path, default=b'default') is False
        assert file_read_bytes(path) == b'the content'

    def test_ensure_exist_mode(self, folder):
        """The mode of the caller is applied to the new file."""
        path = joinpath(folder, 'a.txt')
        file_ensure_exist(path, mode=0o600)

        if env.POSIX:
            assert os.stat(path).st_mode & 0o777 == 0o600

    def test_touch_creates(self, folder):
        """A missing file is created empty, the parent folders included."""
        path = joinpath(folder, 'a/b/c.txt')
        file_touch(path)

        assert file_read_bytes(path) == b''

    def test_touch_keeps_content(self, folder):
        """An existing file keeps its content."""
        path = joinpath(folder, 'a.txt')
        file_write(path, b'the content')
        file_touch(path)

        assert file_read_bytes(path) == b'the content'

    def test_touch_exclusive(self, folder):
        """exist_ok=False refuses an existing file."""
        path = joinpath(folder, 'a.txt')
        file_write(path, b'the content')
        with pytest.raises(FileExistsError):
            file_touch(path, exist_ok=False)

        assert file_read_bytes(path) == b'the content'


class TestRemove:
    """The remove functions on a real disk."""

    def test_file_remove(self, folder):
        """An existing file is removed, a missing one reports False."""
        path = joinpath(folder, 'a.txt')
        file_write(path, b'data')

        assert file_remove(path) is True
        assert file_remove(path) is False

    def test_file_remove_folder(self, folder):
        """A folder is never removed by file_remove(), the platform raises its
        own error, and the folder keeps its content."""
        path = joinpath(folder, 'dir')
        os.makedirs(path)
        file_write(joinpath(path, 'a.txt'), b'x')

        # os.unlink() refuses a folder on every platform, but the error is not
        # the same one: Linux answers EISDIR, macOS answers EPERM and Windows
        # answers EACCES. Only Linux names the kind in the error, the other two
        # raise PermissionError, so the kind is what the caller looks up
        with pytest.raises((IsADirectoryError, PermissionError)):
            file_remove(path)
        assert file_read_bytes(joinpath(path, 'a.txt')) == b'x'

    def test_file_remove_symlink(self, folder):
        """Only the link is removed, its target stays."""
        target = joinpath(folder, 'target.txt')
        file_write(target, b'the target')
        link = joinpath(folder, 'link.txt')
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError) as e:
            pytest.skip(f'This platform cannot create a symbolic link: {e}')

        assert file_remove(link) is True
        assert not os.path.lexists(link)
        assert file_read_bytes(target) == b'the target'

    def test_atomic_remove(self, folder):
        """The atomic removal removes the file and reports a missing one."""
        path = joinpath(folder, 'a.txt')
        file_write(path, b'data')

        assert atomic_remove(path) is True
        assert atomic_remove(path) is False

    def test_folder_rmtree(self, folder):
        """The folder and its whole content are removed."""
        path = joinpath(folder, 'dir')
        file_write(joinpath(path, 'a.txt'), b'x')
        file_write(joinpath(path, 'sub/b.txt'), b'y')

        assert folder_rmtree(path) is True
        assert not os.path.exists(path)
        assert os.path.isdir(folder)

    def test_folder_rmtree_symlink(self, folder):
        """A link is unlinked, the folder it points at is kept."""
        target = joinpath(folder, 'real')
        os.makedirs(target)
        file_write(joinpath(target, 'a.txt'), b'x')
        link = joinpath(folder, 'link')
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError) as e:
            pytest.skip(f'This platform cannot create a symbolic link: {e}')

        assert folder_rmtree(link) is True
        assert not os.path.lexists(link)
        assert file_read_bytes(joinpath(target, 'a.txt')) == b'x'

    def test_atomic_rmtree(self, folder):
        """The atomic rmtree removes the folder and leaves no tmp entry."""
        path = joinpath(folder, 'dir')
        file_write(joinpath(path, 'a.txt'), b'x')

        assert atomic_rmtree(path) is True
        assert os.listdir(folder) == []

    def test_is_empty_folder(self, folder):
        """The emptiness check follows the content of the folder."""
        path = joinpath(folder, 'dir')
        os.makedirs(path)
        assert is_empty_folder(path) is True

        file_write(joinpath(path, 'a.txt'), b'x')
        assert is_empty_folder(path) is False

        os.remove(joinpath(path, 'a.txt'))
        assert folder_rmtree_empty(path) is True
        assert not os.path.exists(path)


class TestAtomicWrite:
    """atomic_write() on a real disk."""

    def test_write(self, folder):
        """The target is replaced and no tmp file is left."""
        path = joinpath(folder, 'a.txt')
        file_write(path, b'old')

        atomic_write(path, b'new')

        assert file_read_bytes(path) == b'new'
        assert os.listdir(folder) == ['a.txt']

    def test_write_creates_parent(self, folder):
        """The parent folders of the target are created."""
        path = joinpath(folder, 'a/b/c.txt')
        atomic_write(path, 'data')

        assert file_read_text(path) == 'data'

    def test_write_failure_removes_tmp(self, folder):
        """A content the writer cannot handle leaves the target untouched and
        no tmp file behind."""
        path = joinpath(folder, 'a.txt')
        file_write(path, b'old')

        with pytest.raises(TypeError):
            atomic_write(path, object())

        assert file_read_bytes(path) == b'old'
        assert os.listdir(folder) == ['a.txt']
