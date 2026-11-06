"""
Tests of BatchMakedirs on a real disk, run on CI.

The rest of the suite runs on the in-memory fake filesystem, which models Linux
on every host: a test written there can never tell how the real os answers, and
the answer of a folder operation differs between the platforms.

- a path through a file: ENOTDIR on POSIX, a missing path on Windows
  (ERROR_PATH_NOT_FOUND), so a caller that must not lose the file sees a
  different exception;
- an existing name: os.mkdir answers FileExistsError on both, but the entry
  behind it is only decided by os.stat, which follows a link;
- a file in the way of a folder: os.unlink removes the entry, and Windows
  refuses to unlink a folder where POSIX answers EISDIR or EPERM.

The paths of the instance are normalized to "/" on every platform (the project
rule), so the assertions build their expectations with joinpath() of
alasio.ext.path.calc, not with os.path.join().

The file is skipped outside CI on purpose: the rest of the suite keeps running
on the fake filesystem there, and a developer machine (a Windows one without
the symlink privilege, an antivirus holding a file) would change the answer of
the operating system rather than exercise the code under test.

Both halves run on CI: the fake filesystem always, this file when CI is set.
"""
import os
import stat
import tempfile

import pytest

from alasio.ext import env
from alasio.ext.path.calc import joinpath
from alasio.ext.path.makedir import BatchMakedirs

pytestmark = pytest.mark.skipif(
    not env.CI, reason='the real disk is covered by the CI matrix, the local runs use the fake filesystem')


@pytest.fixture
def folder():
    """
    A folder of the real disk, removed after the test.

    Yields:
        str: Absolute path of an empty folder, '/'-separated
    """
    path = tempfile.mkdtemp(prefix='alasio_makedirs_').replace(os.sep, '/')
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


def write(path, content=b'data'):
    """
    Write a file, creating the parent folders of it.

    Args:
        path (str): Path of the file
        content (bytes): Content to write
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, 'wb') as f:
        f.write(content)


def read(path):
    """
    Read a file of the real disk.

    Args:
        path (str): Path of the file

    Returns:
        bytes: Content of the file
    """
    with open(path, 'rb') as f:
        return f.read()


def make_symlink(target, link, target_is_directory=False):
    """
    Create a symbolic link, skip the test when the platform refuses it.

    Windows needs a privilege (an administrator, or the developer mode) that a
    CI runner does not always grant.

    Args:
        target (str): Path the link points at, which may not exist
        link (str): Path of the link to create
        target_is_directory (bool): True to tell Windows the target is a folder
    """
    try:
        os.symlink(target, link, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f'This platform cannot create a symbolic link: {e}')


class TestParentFoldersOnTheRealDisk:
    """The folders an instance resolves, before anything is created."""

    def test_absolute_paths_are_kept(self, folder):
        """The folders of an absolute filepath are absolute, shallow to deep,
        and reading them creates nothing."""
        maker = BatchMakedirs([joinpath(folder, 'a/b/c.txt')])

        assert maker.parent_folders[-2:] == [joinpath(folder, 'a'), joinpath(folder, 'a/b')]
        assert not os.path.exists(joinpath(folder, 'a'))

    def test_relative_paths_are_resolved_at_construction(self, folder):
        """A relative filepath is resolved against the current directory when
        the instance was built, not when it runs."""
        previous = os.getcwd()
        try:
            os.chdir(folder)
            maker = BatchMakedirs(['a/b/c.txt'])
            # the instance keeps the folders of the directory it was built in
            os.chdir(tempfile.gettempdir())
        finally:
            os.chdir(previous)

        assert maker.filepaths == [joinpath(folder, 'a/b/c.txt')]
        assert joinpath(folder, 'a/b') in maker.parent_folders


class TestRunOnTheRealDisk:
    """The folders the run creates, and the entries it removes."""

    def test_create(self, folder):
        """The parent folders are created, the file itself is not."""
        target = joinpath(folder, 'a/b/c.txt')
        BatchMakedirs([target]).run()

        assert os.path.isdir(joinpath(folder, 'a/b'))
        assert not os.path.exists(target)

    def test_existing_folder(self, folder):
        """An existing folder costs one os.stat and is left as it is."""
        os.makedirs(joinpath(folder, 'a/b'))
        write(joinpath(folder, 'a/b/keep.txt'), b'keep')

        BatchMakedirs([joinpath(folder, 'a/b/c.txt')]).run()

        assert read(joinpath(folder, 'a/b/keep.txt')) == b'keep'

    def test_file_in_the_way(self, folder):
        """A file in the way is removed and the folder takes its place."""
        write(joinpath(folder, 'a'))

        BatchMakedirs([joinpath(folder, 'a/b/c.txt')]).run()

        assert os.path.isdir(joinpath(folder, 'a/b'))
        assert not os.path.exists(joinpath(folder, 'a/b/c.txt'))

    def test_file_in_the_middle_of_the_path_is_removed(self, folder):
        """A file that is an ancestor of the targets is in the way too, at any
        depth: the first folder of the list is the file itself, and the run
        removes it before creating the folders below. The result is the same on
        both platforms, the removal is the price of the rule."""
        write(joinpath(folder, 'a.txt'), b'not a folder')

        BatchMakedirs([joinpath(folder, 'a.txt/b/c.txt')]).run()

        assert os.path.isdir(joinpath(folder, 'a.txt/b'))
        assert not os.path.exists(joinpath(folder, 'a.txt/b/c.txt'))

    def test_link_to_folder_is_followed(self, folder):
        """A link to a folder is the folder: the folders below it are created
        through the link and the link itself stays."""
        target = joinpath(folder, 'target')
        os.makedirs(target)
        link = joinpath(folder, 'link')
        make_symlink(target, link, target_is_directory=True)

        BatchMakedirs([joinpath(link, 'a/b/c.txt')]).run()

        assert os.path.isdir(joinpath(target, 'a/b'))
        assert os.path.islink(link)

    def test_link_to_file_is_replaced(self, folder):
        """A link to a file is in the way: the link goes, its target stays."""
        target = joinpath(folder, 'target.txt')
        write(target, b'the target')
        link = joinpath(folder, 'link')
        make_symlink(target, link)

        BatchMakedirs([joinpath(link, 'a/b/c.txt')]).run()

        assert os.path.isdir(link)
        assert not os.path.islink(link)
        assert read(target) == b'the target'

    def test_dangling_link_is_replaced(self, folder):
        """A dangling link is in the way too: os.stat() cannot see it, so the
        mkdir reports the name as existing, which the run answers by removing
        the entry."""
        link = joinpath(folder, 'link')
        make_symlink(joinpath(folder, 'missing'), link)

        BatchMakedirs([joinpath(link, 'a/b/c.txt')]).run()

        assert os.path.isdir(link)
        assert not os.path.islink(link)
        assert os.path.isdir(joinpath(link, 'a/b'))

    def test_existing_folder_keeps_its_content(self, folder):
        """A folder that already exists is never removed, whatever it holds:
        another process may have put it there with its own content."""
        os.makedirs(joinpath(folder, 'a/b'))
        write(joinpath(folder, 'a/b/other.txt'), b'the other process')

        BatchMakedirs([joinpath(folder, 'a/b/c.txt')]).run()

        assert read(joinpath(folder, 'a/b/other.txt')) == b'the other process'
        assert not os.path.exists(joinpath(folder, 'a/b/c.txt'))

    def test_many_paths_one_run(self, folder):
        """The folders of every filepath are created, deduplicated."""
        paths = [
            joinpath(folder, 'a/b/x.txt'),
            joinpath(folder, 'a/b/y.txt'),
            joinpath(folder, 'a/c/z.txt'),
            joinpath(folder, 'd.txt'),
        ]
        BatchMakedirs(paths).run()

        assert os.path.isdir(joinpath(folder, 'a/b'))
        assert os.path.isdir(joinpath(folder, 'a/c'))
        # a filepath without a folder contributes nothing
        assert not os.path.exists(joinpath(folder, 'd.txt'))
