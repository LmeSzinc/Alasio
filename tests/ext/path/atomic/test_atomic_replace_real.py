"""
Tests of the replace functions on a real disk, run on CI.

The rest of the suite runs on the in-memory fake filesystem, which models Linux
on every host: a test written there can never tell how the real os.replace()
answers, and the answer differs between the platforms.

- a file never replaces a folder: EISDIR on POSIX, an access error on Windows;
- a folder replaces a file on Windows, POSIX refuses it with ENOTDIR;
- a folder only replaces an empty folder on both, ENOTEMPTY otherwise.

file_replace() hides the first two, and these tests are the only place the real
answers are observed: the CI matrix runs the file on Windows, Linux and macOS,
so both branches of the function are exercised on a real disk. The file is
skipped outside CI on purpose: the rest of the suite keeps running on the fake
filesystem there, and a developer machine (a Windows one without the privilege,
an antivirus holding a file) would change the answer of the operating system
rather than exercise the code under test.

Both halves run on CI: the fake filesystem always, this file when CI is set.
"""
import errno
import os
import stat
import tempfile
import time

import pytest

from alasio.ext import env
from alasio.ext.path.atomic import atomic_replace, file_read_text, file_replace, is_folder

pytestmark = pytest.mark.skipif(
    not env.CI, reason='the real disk is covered by the CI matrix, the local runs use the fake filesystem')


@pytest.fixture
def folder():
    """
    A folder of the real disk, removed after the test.

    Yields:
        str: Absolute path of an empty folder
    """
    path = tempfile.mkdtemp(prefix='alasio_replace_')
    try:
        yield path
    finally:
        _rmtree(path)


def _rmtree(path):
    """
    Remove a folder of the real disk, read-only entries included.

    A test may leave a read-only file behind (the real Windows refuses to
    replace one), and the removal of its folder would fail on it.

    Args:
        path (str): Folder to remove
    """
    def clear_and_retry(func, target, _):
        # give the owner the write bit back and try the removal once more
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


def make_file(folder, name, content):
    """
    Create a file of the real disk.

    Args:
        folder (str): Folder of the file
        name (str): Name of the file
        content (bytes): Content to write

    Returns:
        str: Path of the file
    """
    path = os.path.join(folder, name)
    with open(path, 'wb') as f:
        f.write(content)
    return path


def make_folder(folder, name, files=()):
    """
    Create a folder of the real disk with files inside.

    Args:
        folder (str): Folder of the folder
        name (str): Name of the folder
        files (Iterable[str]): Paths of the files to put inside, relative to
            the folder, the parent folders are created

    Returns:
        str: Path of the folder
    """
    path = os.path.join(folder, name)
    os.makedirs(path)
    for file in files:
        target = os.path.join(path, *file.split('/'))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'wb') as f:
            f.write(b'inside')
    return path


class TestOsReplaceOnTheRealDisk:
    """The answers of os.replace() the fake filesystem cannot show."""

    def test_file_over_folder_is_refused(self, folder):
        """A file never replaces a folder: EISDIR on POSIX, an access error on
        Windows, an empty folder included."""
        source = make_file(folder, 'source.txt', b'new')
        target = make_folder(folder, 'target')
        with pytest.raises(OSError) as e:
            os.replace(source, target)
        assert e.value.errno == errno.EACCES if env.WINDOWS else e.value.errno == errno.EISDIR
        assert is_folder(target)
        assert os.path.isfile(source)

    def test_file_over_non_empty_folder_is_refused(self, folder):
        """The content of the folder does not change the answer."""
        source = make_file(folder, 'source.txt', b'new')
        target = make_folder(folder, 'target', files=('a.txt',))
        with pytest.raises(OSError):
            os.replace(source, target)
        assert is_folder(target)

    def test_folder_over_file(self, folder):
        """The platforms disagree: Windows moves the folder over the file,
        POSIX refuses with ENOTDIR. This is the difference file_replace() covers."""
        source = make_folder(folder, 'source', files=('a.txt',))
        target = make_file(folder, 'target.txt', b'old')

        if env.WINDOWS:
            os.replace(source, target)
            assert is_folder(target)
            assert not os.path.exists(source)
        else:
            with pytest.raises(OSError) as e:
                os.replace(source, target)
            assert e.value.errno == errno.ENOTDIR
            assert is_folder(source)
            assert file_read_text(target) == 'old'

    def test_folder_over_folder(self, folder):
        """The platforms disagree on the empty folder too: POSIX replaces it,
        the Windows API refuses it whether it is empty or not."""
        source = make_folder(folder, 'source', files=('a.txt',))
        empty = make_folder(folder, 'empty')
        if env.WINDOWS:
            with pytest.raises(PermissionError):
                os.replace(source, empty)
            assert is_folder(source)
            assert os.listdir(empty) == []
        else:
            os.replace(source, empty)
            assert os.path.isfile(os.path.join(empty, 'a.txt'))

        source = make_folder(folder, 'source2', files=('b.txt',))
        full = make_folder(folder, 'full', files=('c.txt',))
        with pytest.raises(OSError):
            os.replace(source, full)
        assert is_folder(source)
        assert is_folder(full)


class TestFileReplaceOnTheRealDisk:
    """file_replace() gives the same result on every platform."""

    def test_file_over_file(self, folder):
        """A file replaces a file, nothing had to be removed first."""
        source = make_file(folder, 'source.txt', b'new')
        target = make_file(folder, 'target.txt', b'old')
        assert file_replace(source, target) is False
        assert file_read_text(target) == 'new'
        assert not os.path.exists(source)

    def test_file_over_folder(self, folder):
        """The folder in the way is removed with its content."""
        source = make_file(folder, 'source.txt', b'new')
        target = make_folder(folder, 'target', files=('a.txt', 'sub/b.txt'))
        assert file_replace(source, target) is True
        assert not is_folder(target)
        assert file_read_text(target) == 'new'

    def test_folder_over_file(self, folder):
        """The file in the way goes, the folder takes its place on both
        platforms. Windows reaches the result with its own os.replace(), so no
        entry is removed by file_replace() itself."""
        source = make_folder(folder, 'source', files=('a.txt',))
        target = make_file(folder, 'target.txt', b'old')
        assert file_replace(source, target) is not env.WINDOWS
        assert is_folder(target)
        assert file_read_text(os.path.join(target, 'a.txt')) == 'inside'

    def test_folder_over_non_empty_folder_is_refused(self, folder):
        """Two folders of the different content keep the rule of os.replace():
        the same kind is not a kind change, the failure is reported."""
        source = make_folder(folder, 'source', files=('a.txt',))
        target = make_folder(folder, 'target', files=('b.txt',))
        with pytest.raises(OSError):
            file_replace(source, target)
        assert is_folder(source)
        assert os.path.isfile(os.path.join(target, 'b.txt'))

    def test_missing_source(self, folder):
        """A missing source is an error, the target is untouched."""
        target = make_file(folder, 'target.txt', b'old')
        with pytest.raises(FileNotFoundError):
            file_replace(os.path.join(folder, 'missing.txt'), target)
        assert file_read_text(target) == 'old'


class TestAtomicReplaceOnTheRealDisk:
    """The Windows retry is spent on a lock, never on a kind change."""

    def test_folder_in_the_way_costs_no_retry(self, folder):
        """A folder in the way is decided on the first attempt: the Windows
        retry of a locked file would spend its whole window (about 4.5s of
        backoff) on something that can never become replaceable."""
        source = make_file(folder, 'source.txt', b'new')
        target = make_folder(folder, 'target', files=('a.txt',))

        started = time.perf_counter()
        assert atomic_replace(source, target) is True
        elapsed = time.perf_counter() - started

        assert file_read_text(target) == 'new'
        # the retry window of a refused replace is over 4.5s, one attempt is
        # milliseconds even on a slow runner
        assert elapsed < 2.0
