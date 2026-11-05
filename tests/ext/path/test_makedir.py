import os
import shutil

import pytest

from alasio.ext import env
from alasio.ext.path.calc import joinpath, normpath
from alasio.ext.path.makedir import BatchMakedirs, _convert_path, _iter_parent_folder


def make_symlink(target, link, target_is_directory=False):
    """
    Create a symbolic link, skip the test when the platform refuses it.

    Windows needs a privilege (an administrator, or the developer mode) that a
    machine, and especially a CI runner, may not have.

    Args:
        target (str): Path the link points at, which may not exist
        link (str): Path of the link to create
        target_is_directory (bool): True to tell Windows the target is a folder
    """
    try:
        os.symlink(target, link, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f'This platform cannot create a symbolic link: {e}')


@pytest.fixture(scope='module')
def temp_dir():
    """
    Create a temporary test folder under env.ALASIO_TEMP,
    removed after all tests in this module finish

    Yields:
        str: Absolute path of the temporary test folder
    """
    folder = env.ALASIO_TEMP.joinpath('test_makedir_tmp')
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder)
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


class TestConvertPath:
    """Tests for _convert_path()."""

    def test_relative_to_absolute(self):
        """Relative filepath should be converted to absolute."""
        cwd = normpath(os.getcwd())
        assert list(_convert_path(['a/b/c.txt'])) == [joinpath(cwd, 'a/b/c.txt')]

    def test_mixed(self):
        """Relative and absolute filepaths should both be converted."""
        cwd = normpath(os.getcwd())
        abs_path = 'C:/a/b.txt' if os.sep == '\\' else '/a/b.txt'
        assert list(_convert_path(['a/b/c.txt', abs_path])) == [joinpath(cwd, 'a/b/c.txt'), abs_path]

    def test_absolute_unchanged(self):
        """Absolute filepath should stay unchanged."""
        abs_path = 'C:/a/b.txt' if os.sep == '\\' else '/a/b.txt'
        assert list(_convert_path([abs_path])) == [abs_path]

    @pytest.mark.skipif(os.sep != '\\', reason='backslash is a path separator on Windows only')
    def test_normalize_backslash(self):
        """Windows-style backslash paths should be normalized."""
        cwd = normpath(os.getcwd())
        assert list(_convert_path([r'a\b\c.txt'])) == [joinpath(cwd, 'a/b/c.txt')]

    def test_empty(self):
        """Empty input should return an empty list."""
        assert list(_convert_path([])) == []


class TestIterParentFolder:
    """Tests for _iter_parent_folder()."""

    def test_single_level(self):
        """Filepath should yield all parent folders, from deep to shallow."""
        assert list(_iter_parent_folder('a/b/c.txt')) == ['a/b', 'a']

    def test_deep(self):
        """Nested filepath should yield all ancestor folders."""
        assert list(_iter_parent_folder('a/b/c/d.txt')) == ['a/b/c', 'a/b', 'a']

    def test_no_folder(self):
        """Filepath without a parent folder should yield nothing."""
        assert list(_iter_parent_folder('file.txt')) == []

    def test_drive_root(self):
        """The Windows drive root already exists, it should not be yielded."""
        assert list(_iter_parent_folder('C:/folder/file.txt')) == ['C:/folder']
        assert list(_iter_parent_folder('C:/file.txt')) == []

    def test_absolute_path(self):
        """Absolute filepath should stop before the root."""
        assert list(_iter_parent_folder('/a/b/c.txt')) == ['/a/b', '/a']


class TestParentFoldersOf:
    """Tests for BatchMakedirs.parent_folders_of()."""

    def test_single_parent(self):
        """One filepath should return its parent folder and ancestors."""
        assert BatchMakedirs.parent_folders_of(['a/b/c.txt']) == ['a', 'a/b']

    def test_deep_parent(self):
        """Nested filepath should return all ancestor folders."""
        assert BatchMakedirs.parent_folders_of(['a/b/c/d.txt']) == ['a', 'a/b', 'a/b/c']

    def test_dedupe(self):
        """Filepaths in the same folder should be deduplicated."""
        assert BatchMakedirs.parent_folders_of(['a/b/x.txt', 'a/b/y.txt']) == ['a', 'a/b']

    def test_skip_when_ancestor_added(self):
        """If a folder was added before, remaining ancestors should be skipped."""
        assert BatchMakedirs.parent_folders_of(['a/b/c.txt', 'a/b/d.txt']) == ['a', 'a/b']

    def test_skip_keeps_new_branches(self):
        """Skipping a known folder should still add new deeper branches."""
        assert BatchMakedirs.parent_folders_of(
            ['a/b/x.txt', 'a/b/y/c.txt']) == ['a', 'a/b', 'a/b/y']

    def test_multiple_folders(self):
        """Filepaths in different folders should all be returned."""
        assert set(BatchMakedirs.parent_folders_of(
            ['a/b/x.txt', 'c/d.txt', 'a/b/y.txt'])) == {'a/b', 'a', 'c'}

    def test_sorted_shallow_to_deep(self):
        """Folders should be sorted from shallow to deep."""
        assert BatchMakedirs.parent_folders_of(['a/b/c.txt', 'a/b/d.txt']) == ['a', 'a/b']

    def test_no_folder(self):
        """Filepath without a parent folder should be skipped."""
        assert BatchMakedirs.parent_folders_of(['file.txt']) == []

    def test_empty(self):
        """Empty input should return an empty list."""
        assert BatchMakedirs.parent_folders_of([]) == []


class TestBatchMakedirs:
    """Tests for BatchMakedirs."""

    def test_filepaths_are_converted_in_init(self):
        """The relative filepaths of the input are absolute on the instance, so
        the folders of the run never depend on the current directory again."""
        cwd = normpath(os.getcwd())
        maker = BatchMakedirs(['a/b/c.txt'])
        assert maker.filepaths == [joinpath(cwd, 'a/b/c.txt')]

    def test_parent_folders(self):
        """The folders of the instance are every ancestor of the converted
        paths, shallow to deep: the folders of the current directory are part
        of them and cost one stat each on the run."""
        cwd = normpath(os.getcwd())
        maker = BatchMakedirs(['a/b/c.txt', 'a/b/d.txt'])
        assert maker.parent_folders[-2:] == [joinpath(cwd, 'a'), joinpath(cwd, 'a/b')]
        assert maker.parent_folders == sorted(
            maker.parent_folders, key=lambda folder: folder.count('/'))

    def test_parent_folders_are_cached(self):
        """The folder list is computed once and read back as the same object."""
        maker = BatchMakedirs(['a/b/c.txt'])
        folders = maker.parent_folders
        assert maker.parent_folders is folders

    def test_parent_folders_do_not_exist_yet(self):
        """Reading the folders of an instance creates nothing: only run() does."""
        cwd = normpath(os.getcwd())
        maker = BatchMakedirs(['not_created/a/b/c.txt'])
        assert maker.parent_folders[-3:] == [
            joinpath(cwd, 'not_created'), joinpath(cwd, 'not_created/a'),
            joinpath(cwd, 'not_created/a/b')]
        assert not os.path.exists(joinpath(cwd, 'not_created'))

    def test_parent_folders_kept_after_chdir(self):
        """The folders are the ones of construction time, not of the run."""
        maker = BatchMakedirs(['a/b/c.txt'])
        folders = maker.parent_folders
        old = os.getcwd()
        try:
            os.chdir(os.path.dirname(old) or old)
            assert maker.parent_folders is folders
        finally:
            os.chdir(old)

    def test_parent_folders_of_relative_paths(self):
        """The static helper works on paths of a caller namespace, relative or
        absolute, without resolving anything against the current directory."""
        assert BatchMakedirs.parent_folders_of(['x/y.py']) == ['x']
        assert BatchMakedirs.parent_folders_of(['x/y.py', 'x/z/deep.py']) == ['x', 'x/z']
        assert BatchMakedirs.parent_folders_of(['mod.py']) == []
        assert BatchMakedirs.parent_folders_of([]) == []

    def test_create_parents(self, temp_dir):
        """Parent folders should be created, but not the filepath itself."""
        file = joinpath(temp_dir, 'test_create_parents/a/b/c.txt')
        BatchMakedirs([file]).run()
        assert os.path.isdir(joinpath(temp_dir, 'test_create_parents/a'))
        assert os.path.isdir(joinpath(temp_dir, 'test_create_parents/a/b'))
        assert not os.path.exists(file)

    def test_create_missing_ancestors(self, temp_dir):
        """Ancestors not in the folder list should be created as well."""
        file = joinpath(temp_dir, 'test_create_missing_ancestors/a/b/c/d.txt')
        BatchMakedirs([file]).run()
        assert os.path.isdir(joinpath(temp_dir, 'test_create_missing_ancestors/a'))
        assert os.path.isdir(joinpath(temp_dir, 'test_create_missing_ancestors/a/b/c'))

    def test_create_multiple(self, temp_dir):
        """Parents of multiple filepaths should be created."""
        BatchMakedirs([
            joinpath(temp_dir, 'test_create_multiple/a/b/x.txt'),
            joinpath(temp_dir, 'test_create_multiple/a/b/y.txt'),
            joinpath(temp_dir, 'test_create_multiple/c/d/e.txt'),
        ]).run()
        assert os.path.isdir(joinpath(temp_dir, 'test_create_multiple/a/b'))
        assert os.path.isdir(joinpath(temp_dir, 'test_create_multiple/c/d'))
        assert not os.path.exists(joinpath(temp_dir, 'test_create_multiple/c/d/e.txt'))

    def test_existing(self, temp_dir):
        """Existing folders should not raise errors."""
        root = joinpath(temp_dir, 'test_existing')
        os.makedirs(joinpath(root, 'a/b'))
        BatchMakedirs([joinpath(root, 'a/b/c.txt'), joinpath(root, 'a/b/d.txt')]).run()
        assert os.path.isdir(joinpath(root, 'a/b'))

    def test_existing_partial(self, temp_dir):
        """Missing folders should be created under existing ones."""
        root = joinpath(temp_dir, 'test_existing_partial')
        os.mkdir(root)
        os.mkdir(joinpath(root, 'a'))
        BatchMakedirs([joinpath(root, 'a/b/c.txt')]).run()
        assert os.path.isdir(joinpath(root, 'a/b'))

    def test_file_in_the_way(self, temp_dir):
        """A file at the folder path should be removed and replaced by a folder."""
        root = joinpath(temp_dir, 'test_file_in_the_way')
        os.mkdir(root)
        os.mkdir(joinpath(root, 'a'))
        file = joinpath(root, 'a/b')
        with open(file, 'w') as f:
            f.write('data')
        BatchMakedirs([joinpath(root, 'a/b/c.txt')]).run()
        assert os.path.isdir(file)
        assert not os.path.exists(joinpath(root, 'a/b/c.txt'))

    def test_deep_file_in_the_way(self, temp_dir):
        """A deep file in the way should be removed along with its ancestors."""
        root = joinpath(temp_dir, 'test_deep_file_in_the_way')
        os.mkdir(root)
        with open(joinpath(root, 'a'), 'w') as f:
            f.write('data')
        BatchMakedirs([joinpath(root, 'a/b/c.txt')]).run()
        assert os.path.isdir(joinpath(root, 'a'))
        assert os.path.isdir(joinpath(root, 'a/b'))
        assert not os.path.exists(joinpath(root, 'a/b/c.txt'))

    def test_absolute_path(self, temp_dir):
        """Absolute filepaths should work."""
        file = joinpath(temp_dir, 'test_absolute_path/x/y.txt')
        BatchMakedirs([file]).run()
        assert os.path.isdir(joinpath(temp_dir, 'test_absolute_path/x'))

    def test_empty_input(self, temp_dir):
        """Empty input should do nothing."""
        BatchMakedirs([]).run()

    def test_relative_under_subdir(self, temp_dir):
        """Relative filepaths should be created under the current directory."""
        work = joinpath(temp_dir, 'test_relative_under_subdir')
        os.mkdir(work)
        old = os.getcwd()
        try:
            os.chdir(work)
            BatchMakedirs(['a/b/c.txt']).run()
            assert os.path.isdir(joinpath(work, 'a/b'))
            assert not os.path.exists(joinpath(work, 'a/b/c.txt'))
        finally:
            os.chdir(old)

    def test_no_folder(self, temp_dir):
        """Filepath without a parent folder should do nothing."""
        file = joinpath(temp_dir, 'test_no_folder.txt')
        BatchMakedirs([file]).run()
        assert not os.path.exists(file)


class TestEntryInTheWay:
    """Every kind of entry at the folder path, not only a regular file."""

    def test_folder_created_between_stat_and_mkdir(self, temp_dir, monkeypatch):
        """A folder another process creates while this instance works is not in
        the way: it is left with everything below it.

        The race is forced: os.stat() claims the folder is missing while the
        folder (and a file of its own) is already on the disk, so the mkdir
        reports the name as existing.
        """
        root = joinpath(temp_dir, 'test_folder_created_between_stat_and_mkdir')
        other = joinpath(root, 'a')
        os.makedirs(other)
        file = joinpath(other, 'another-process.txt')
        with open(file, 'w') as f:
            f.write('the other process was here first')
        real_stat = os.stat
        raced = []

        def racing_stat(path, **kwargs):
            # the first look of the folder loses the race, every later one (the
            # isdir of the recovery, the rest of the tree) reads the disk
            if not raced and normpath(str(path)) == normpath(other):
                raced.append(path)
                raise FileNotFoundError(2, 'No such file or directory', str(path))
            return real_stat(path, **kwargs)
        monkeypatch.setattr(os, 'stat', racing_stat)

        BatchMakedirs([joinpath(root, 'a/b/c.txt')]).run()

        # the folder of the other process survived with its content, and the
        # rest of the tree was built as usual
        with open(file) as f:
            assert f.read() == 'the other process was here first'
        assert os.path.isdir(joinpath(root, 'a/b'))

    def test_link_to_folder_created_between_stat_and_mkdir(self, temp_dir, monkeypatch):
        """A link to a folder that appears during the race is left as it is too:
        unlinking it would break the layout of the process that made it."""
        root = joinpath(temp_dir, 'test_link_to_folder_created_between_stat_and_mkdir')
        target = joinpath(root, 'target')
        link = joinpath(root, 'link')
        os.makedirs(target)
        make_symlink(target, link, target_is_directory=True)
        real_stat = os.stat
        raced = []

        def racing_stat(path, **kwargs):
            if not raced and normpath(str(path)) == normpath(link):
                raced.append(path)
                raise FileNotFoundError(2, 'No such file or directory', str(path))
            return real_stat(path, **kwargs)
        monkeypatch.setattr(os, 'stat', racing_stat)

        BatchMakedirs([joinpath(link, 'a/b/c.txt')]).run()

        assert os.path.islink(link)
        assert os.path.isdir(joinpath(target, 'a/b'))

    def test_link_to_folder_is_followed(self, temp_dir):
        """A link to a folder is the folder: the folders below it are created
        through the link and the link itself stays."""
        root = joinpath(temp_dir, 'test_link_to_folder_is_followed')
        target = joinpath(root, 'target')
        link = joinpath(root, 'link')
        os.makedirs(target)
        make_symlink(target, link, target_is_directory=True)

        BatchMakedirs([joinpath(link, 'a/b/c.txt')]).run()

        assert os.path.isdir(joinpath(target, 'a/b'))
        assert os.path.islink(link)

    def test_link_to_file_is_replaced(self, temp_dir):
        """A link to a file is in the way: the link goes, its target stays."""
        root = joinpath(temp_dir, 'test_link_to_file_is_replaced')
        target = joinpath(root, 'target.txt')
        link = joinpath(root, 'link')
        os.makedirs(root)
        with open(target, 'w') as f:
            f.write('the target')
        make_symlink(target, link)

        BatchMakedirs([joinpath(link, 'a/b/c.txt')]).run()

        assert os.path.isdir(link)
        assert not os.path.islink(link)
        assert os.path.isdir(joinpath(link, 'a/b'))
        with open(target) as f:
            assert f.read() == 'the target'

    def test_dangling_link_is_replaced(self, temp_dir):
        """A dangling link is in the way too: os.stat() cannot see it, the
        mkdir of the missing folder reports the name as existing."""
        root = joinpath(temp_dir, 'test_dangling_link_is_replaced')
        link = joinpath(root, 'link')
        os.makedirs(root)
        make_symlink(joinpath(root, 'missing'), link)

        BatchMakedirs([joinpath(link, 'a/b/c.txt')]).run()

        assert os.path.isdir(link)
        assert not os.path.islink(link)
        assert os.path.isdir(joinpath(link, 'a/b'))

    @pytest.mark.skipif(not hasattr(os, 'mkfifo'), reason='mkfifo is POSIX only')
    def test_fifo_is_replaced(self, temp_dir):
        """A fifo is not a folder either: it goes, the folder takes its place."""
        root = joinpath(temp_dir, 'test_fifo_is_replaced')
        fifo = joinpath(root, 'fifo')
        os.makedirs(root)
        os.mkfifo(fifo)

        BatchMakedirs([joinpath(fifo, 'a/b/c.txt')]).run()

        assert os.path.isdir(fifo)
        assert os.path.isdir(joinpath(fifo, 'a/b'))

    def test_deep_entry_in_the_way(self, temp_dir):
        """An entry in the way of a deep folder is replaced like a shallow one."""
        root = joinpath(temp_dir, 'test_deep_entry_in_the_way')
        os.makedirs(root)
        with open(joinpath(root, 'a'), 'w') as f:
            f.write('data')

        BatchMakedirs([joinpath(root, 'a/b/c/d.txt')]).run()

        assert os.path.isdir(joinpath(root, 'a/b/c'))
