import pytest

from alasio.deploy.simple_pip import cleanup_folder
from alasio.deploy.simple_pip.cleanup_folder import CleanupFolder


def folder_chain(depth):
    """
    Build the paths of a chain of nested folders.

    Args:
        depth (int): Number of folders

    Returns:
        list[str]: Paths of the folders, the shallowest first, e.g.
            ["a", "a/b", "a/b/c"] for a depth of 3
    """
    folders = []
    folder = 'a'
    for _ in range(depth):
        folders.append(folder)
        folder = f'{folder}/b'
    return folders


class TestRegisterPath:
    @pytest.mark.parametrize('path', [
        'a/b.py',
        {'a/b.py'},
        {'a/b.py': None},
        ['a/b.py'],
        ('a/b.py',),
    ])
    def test_one_path_or_many(self, path):
        """A path is accepted as a str, or in a set, a dict, a list or a tuple."""
        cleaner = CleanupFolder()
        cleaner.register_deleted(path)
        assert cleaner.get_cleanup_folders() == ['a']

    def test_many_paths(self):
        """Every path of the argument is registered."""
        cleaner = CleanupFolder()
        cleaner.register_deleted({'a/one.py', 'b/two.py', 'c/three.py'})
        assert cleaner.get_cleanup_folders() == ['a', 'b', 'c']

    def test_dict_keys(self):
        """A dict registers its keys, the values are ignored."""
        cleaner = CleanupFolder()
        cleaner.register_file({'a/keep.py': 1, 'a/sub/other.py': 2})
        cleaner.register_deleted({'a/gone.py': 3})
        assert cleaner.get_cleanup_folders() == []

    @pytest.mark.parametrize('path', [
        None,
        123,
        b'a/b.py',
        ('a' for _ in range(1)),
    ])
    def test_type_error(self, path):
        """A path that is not a str nor a supported container is refused."""
        cleaner = CleanupFolder()
        with pytest.raises(TypeError):
            cleaner.register_file(path)

    def test_no_registration(self):
        """An empty cleaner has no folder to clean up."""
        assert CleanupFolder().get_cleanup_folders() == []

    def test_empty_collections(self):
        """A registration with an empty container changes nothing."""
        cleaner = CleanupFolder()
        cleaner.register_file(set())
        cleaner.register_folder([])
        cleaner.register_deleted(())
        assert cleaner.get_cleanup_folders() == []


class TestRegisteredFile:
    def test_file_alone_keeps_its_folder(self):
        """A file that exists is never a removal candidate, alone it removes nothing."""
        cleaner = CleanupFolder()
        cleaner.register_file({'a/b/c.py', 'a/b/d.py', 'a/e.py'})
        assert cleaner.get_cleanup_folders() == []

    def test_unrelated_file_keeps_its_own_folders(self):
        """A file keeps its own branch alive, the registered folder of an unrelated branch is still returned."""
        cleaner = CleanupFolder()
        cleaner.register_folder({'a', 'a/b'})
        cleaner.register_file('x/y/z.py')
        assert cleaner.get_cleanup_folders() == ['a/b', 'a']


class TestDeletedFile:
    def test_removes_the_folder_of_the_file(self):
        """The folder of a removed file and the parent folders left empty are returned."""
        cleaner = CleanupFolder()
        cleaner.register_deleted('a/b/c.py')
        assert cleaner.get_cleanup_folders() == ['a/b', 'a']

    def test_file_keeps_the_folder(self):
        """A file that exists keeps its folder and every parent folder alive."""
        cleaner = CleanupFolder()
        cleaner.register_file('a/b/d.py')
        cleaner.register_deleted('a/b/c.py')
        assert cleaner.get_cleanup_folders() == []

    def test_file_of_a_parent_folder_keeps_it(self):
        cleaner = CleanupFolder()
        cleaner.register_file('a/d.py')
        cleaner.register_deleted('a/b/c.py')
        assert cleaner.get_cleanup_folders() == ['a/b']

    def test_file_of_a_child_folder_keeps_it(self):
        cleaner = CleanupFolder()
        cleaner.register_file('a/b/d/e.py')
        cleaner.register_deleted('a/b/c.py')
        assert cleaner.get_cleanup_folders() == []

    def test_deleted_files_of_the_same_folder(self):
        """Many removed files of a folder give the folder once."""
        cleaner = CleanupFolder()
        cleaner.register_deleted({'a/one.py', 'a/two.py', 'a/sub/three.py'})
        assert cleaner.get_cleanup_folders() == ['a/sub', 'a']

    def test_deleted_file_without_extension(self):
        """A removed file without an extension is treated like any file of the folder."""
        cleaner = CleanupFolder()
        cleaner.register_deleted({'a/module', 'a/inner/core.py'})
        assert cleaner.get_cleanup_folders() == ['a/inner', 'a']


class TestRegisteredFolder:
    def test_registered_folder_is_a_candidate(self):
        """A folder registered without any file at or below it is returned."""
        cleaner = CleanupFolder()
        cleaner.register_folder('a/b')
        assert cleaner.get_cleanup_folders() == ['a/b', 'a']

    def test_empty_folder_chain(self):
        """The chain of the folders left without any entry is returned, the deepest first."""
        cleaner = CleanupFolder()
        cleaner.register_folder({'a/b', 'a/b/c'})
        assert cleaner.get_cleanup_folders() == ['a/b/c', 'a/b', 'a']

    def test_folder_holding_a_file_kept(self):
        cleaner = CleanupFolder()
        cleaner.register_folder('a/b')
        cleaner.register_file('a/b/c.py')
        assert cleaner.get_cleanup_folders() == []

    def test_folder_emptied_by_a_removal(self):
        cleaner = CleanupFolder()
        cleaner.register_folder('a/b')
        cleaner.register_deleted('a/b/c.py')
        assert cleaner.get_cleanup_folders() == ['a/b', 'a']

    def test_folder_holding_a_file_of_the_new_version(self):
        cleaner = CleanupFolder()
        cleaner.register_folder('a/b')
        cleaner.register_file('a/b/c.py')
        cleaner.register_deleted('a/b/d.py')
        assert cleaner.get_cleanup_folders() == []


class TestPycache:
    def test_pycache_of_removed_modules(self):
        """A __pycache__ folder emptied by the removal of its pyc files and the folder that only held it are returned."""
        cleaner = CleanupFolder()
        cleaner.register_folder({'demo', 'demo/__pycache__'})
        cleaner.register_deleted('demo/__pycache__/core.cpython-38.pyc')
        assert cleaner.get_cleanup_folders() == ['demo/__pycache__', 'demo']

    def test_pycache_of_a_kept_module(self):
        """The pyc file of a module that exists keeps the __pycache__ folder and its parents."""
        cleaner = CleanupFolder()
        cleaner.register_folder({'demo', 'demo/__pycache__'})
        cleaner.register_file('demo/__pycache__/mod.cpython-38.pyc')
        cleaner.register_deleted('demo/__pycache__/gone.cpython-38.pyc')
        assert cleaner.get_cleanup_folders() == []


class TestRoot:
    def test_file_at_the_root(self):
        """A file at the root of the tree has no folder to clean up."""
        cleaner = CleanupFolder()
        cleaner.register_deleted('a.py')
        assert cleaner.get_cleanup_folders() == []

    def test_root_never_returned(self):
        """The root itself (the empty string) is never a folder to remove."""
        cleaner = CleanupFolder()
        cleaner.register_folder('a')
        cleaner.register_deleted('a/b.py')
        assert cleaner.get_cleanup_folders() == ['a']

    def test_root_registered_as_a_folder(self):
        """The root is never returned, even when it is registered as a folder."""
        cleaner = CleanupFolder()
        cleaner.register_folder('')
        assert cleaner.get_cleanup_folders() == []

    def test_root_folder_with_a_removed_file(self):
        """The root folder of the caller is kept out of the result, the folder of the removal is returned."""
        cleaner = CleanupFolder()
        cleaner.register_folder('')
        cleaner.register_deleted('a/b.py')
        assert cleaner.get_cleanup_folders() == ['a']

    def test_root_folder_with_a_removed_file_chain(self):
        cleaner = CleanupFolder()
        cleaner.register_folder('')
        cleaner.register_deleted('a/b/c.py')
        assert cleaner.get_cleanup_folders() == ['a/b', 'a']

    def test_filesystem_root_registered_as_a_folder(self):
        """The filesystem root is not a folder of the caller, it is never returned."""
        cleaner = CleanupFolder()
        cleaner.register_folder('/')
        assert cleaner.get_cleanup_folders() == []

    def test_drive_root_registered_as_a_folder(self):
        """The drive root is not a folder of the caller, it is never returned."""
        cleaner = CleanupFolder()
        cleaner.register_folder('C:')
        assert cleaner.get_cleanup_folders() == []

    def test_empty_string_paths(self):
        """The empty string registered as a file or a removed path is the root, nothing to clean up."""
        cleaner = CleanupFolder()
        cleaner.register_file('')
        cleaner.register_deleted('')
        assert cleaner.get_cleanup_folders() == []


class TestDeletedPath:
    def test_removed_file_dropped_from_the_files(self):
        """A path registered as removed is not a file that is left, its folder is returned."""
        cleaner = CleanupFolder()
        cleaner.register_file('a/b.py')
        cleaner.register_deleted('a/b.py')
        assert cleaner.get_cleanup_folders() == ['a']

    def test_removed_folder_dropped_from_the_folders(self):
        """A folder registered as removed is not a folder that is left, only its parent is returned."""
        cleaner = CleanupFolder()
        cleaner.register_folder('a/b')
        cleaner.register_deleted('a/b')
        assert cleaner.get_cleanup_folders() == ['a']

    def test_file_registered_after_the_removal(self):
        """The last registration wins: a file registered again keeps its folder."""
        cleaner = CleanupFolder()
        cleaner.register_deleted('a/b.py')
        cleaner.register_file('a/b.py')
        assert cleaner.get_cleanup_folders() == []

    def test_folder_registered_after_the_removal(self):
        cleaner = CleanupFolder()
        cleaner.register_deleted('a/b')
        cleaner.register_folder('a/b')
        assert cleaner.get_cleanup_folders() == ['a/b', 'a']


class TestNestedPath:
    def test_deep_chain_with_a_removed_file_at_the_bottom(self):
        """A deep chain of empty folders holding a removed file at the bottom is returned."""
        folders = folder_chain(6)
        cleaner = CleanupFolder()
        cleaner.register_folder(folders)
        cleaner.register_deleted(f'{folders[-1]}/gone.py')
        assert cleaner.get_cleanup_folders() == list(reversed(folders))

    def test_deep_chain_of_empty_folders(self):
        """An empty folder holding only empty folders is returned with the whole chain."""
        folders = folder_chain(5)
        cleaner = CleanupFolder()
        cleaner.register_folder(folders)
        assert cleaner.get_cleanup_folders() == list(reversed(folders))

    def test_file_at_the_bottom_keeps_the_chain(self):
        """A file at the bottom of a deep chain keeps every folder of the chain."""
        folders = folder_chain(5)
        cleaner = CleanupFolder()
        cleaner.register_folder(folders)
        cleaner.register_file(f'{folders[-1]}/keep.py')
        cleaner.register_deleted(f'{folders[-1]}/gone.py')
        assert cleaner.get_cleanup_folders() == []

    def test_only_the_branch_without_any_file_is_returned(self):
        """A deep branch holding a file is kept, the sibling branch is returned."""
        cleaner = CleanupFolder()
        cleaner.register_folder({'a', 'a/b', 'a/b/keep', 'a/b/gone'})
        cleaner.register_file('a/b/keep/file.py')
        cleaner.register_deleted('a/b/gone/file.py')
        assert cleaner.get_cleanup_folders() == ['a/b/gone']


class TestMultipleBranches:
    def test_removed_files_of_sibling_folders(self):
        """Every branch emptied by a removal is returned, the shared parent once."""
        cleaner = CleanupFolder()
        cleaner.register_deleted({'a/b/x.py', 'a/c/y.py'})
        assert cleaner.get_cleanup_folders() == ['a/b', 'a/c', 'a']

    def test_empty_branch_kept_by_a_sibling_file(self):
        """An empty branch is returned, the parent kept by the sibling branch holding a file."""
        cleaner = CleanupFolder()
        cleaner.register_folder({'a/b', 'a/c'})
        cleaner.register_file('a/c/keep.py')
        assert cleaner.get_cleanup_folders() == ['a/b']

    def test_removed_branch_kept_by_a_sibling_file(self):
        cleaner = CleanupFolder()
        cleaner.register_folder({'a/b', 'a/c'})
        cleaner.register_file('a/c/keep.py')
        cleaner.register_deleted('a/b/x.py')
        assert cleaner.get_cleanup_folders() == ['a/b']

    def test_empty_child_of_a_folder_holding_a_file(self):
        """A child folder without any file is returned, the parent holding a file is kept."""
        cleaner = CleanupFolder()
        cleaner.register_folder({'a', 'a/b'})
        cleaner.register_file('a/keep.py')
        assert cleaner.get_cleanup_folders() == ['a/b']

    def test_folder_of_a_shared_name_prefix(self):
        """A file keeps the folder it is in, not a folder of a similar name."""
        cleaner = CleanupFolder()
        cleaner.register_folder({'a/b', 'a/bc'})
        cleaner.register_file('a/bc/d.py')
        assert cleaner.get_cleanup_folders() == ['a/b']

    def test_deep_file_keeps_the_chain_of_a_removed_sibling_branch(self):
        """A deep file keeps its chain, the removed branch beside it is returned."""
        cleaner = CleanupFolder()
        cleaner.register_folder({'a', 'a/b', 'a/b/c'})
        cleaner.register_file('a/b/c/deep/keep.py')
        cleaner.register_deleted('a/b/c/other/gone.py')
        assert cleaner.get_cleanup_folders() == ['a/b/c/other']


class TestRegistrationOrder:
    def test_order_does_not_change_the_result(self):
        """The result depends on what is registered, not on the order of the calls."""

        def run(ops):
            cleaner = CleanupFolder()
            for name, path in ops:
                getattr(cleaner, name)(path)
            return cleaner.get_cleanup_folders()

        folder = ('register_folder', {'a/b', 'a/c'})
        file = ('register_file', 'a/c/keep.py')
        deleted = ('register_deleted', 'a/b/x.py')
        assert run([folder, file, deleted]) == ['a/b']
        assert run([deleted, file, folder]) == ['a/b']
        assert run([file, deleted, folder]) == ['a/b']

    def test_stale_removal_does_not_drop_the_folder(self):
        """A file registered again after its removal keeps its folder, the removed sibling does not empty it."""
        cleaner = CleanupFolder()
        cleaner.register_deleted({'a/keep.py', 'a/gone.py'})
        cleaner.register_file('a/keep.py')
        assert cleaner.get_cleanup_folders() == []

    def test_repeated_registration(self):
        """Registering the same path twice gives the same result as registering it once."""
        cleaner = CleanupFolder()
        cleaner.register_folder('a/b')
        cleaner.register_folder('a/b')
        cleaner.register_deleted('a/b/c.py')
        cleaner.register_deleted('a/b/c.py')
        assert cleaner.get_cleanup_folders() == ['a/b', 'a']


class TestAbsolutePath:
    def test_linux(self):
        """A Linux absolute path walks up to the top level folder, the filesystem root is not a candidate."""
        cleaner = CleanupFolder()
        cleaner.register_deleted('/env/Lib/site-packages/demo/gone.py')
        assert cleaner.get_cleanup_folders() == [
            '/env/Lib/site-packages/demo',
            '/env/Lib/site-packages',
            '/env/Lib',
            '/env',
        ]

    def test_linux_file_keeps_the_folders(self):
        cleaner = CleanupFolder()
        cleaner.register_file('/env/Lib/site-packages/demo/keep.py')
        cleaner.register_deleted('/env/Lib/site-packages/demo/gone.py')
        assert cleaner.get_cleanup_folders() == []

    def test_windows(self):
        """A Windows absolute path walks up to the top level folder, the drive root is not a candidate."""
        cleaner = CleanupFolder()
        cleaner.register_deleted('C:/env/Lib/site-packages/demo/gone.py')
        assert cleaner.get_cleanup_folders() == [
            'C:/env/Lib/site-packages/demo',
            'C:/env/Lib/site-packages',
            'C:/env/Lib',
            'C:/env',
        ]

    def test_windows_file_keeps_the_folders(self):
        cleaner = CleanupFolder()
        cleaner.register_file('C:/env/Lib/site-packages/demo/keep.py')
        cleaner.register_deleted('C:/env/Lib/site-packages/demo/gone.py')
        assert cleaner.get_cleanup_folders() == []

    def test_windows_drive_root_is_not_a_folder(self):
        """A file at the root of a drive has no folder to clean up."""
        cleaner = CleanupFolder()
        cleaner.register_deleted('C:/gone.py')
        assert cleaner.get_cleanup_folders() == []

    def test_linux_file_at_the_top_level(self):
        """A file at the top level of the filesystem has no folder to clean up."""
        cleaner = CleanupFolder()
        cleaner.register_deleted('/x.py')
        assert cleaner.get_cleanup_folders() == []

    def test_linux_top_level_folder(self):
        """The top level folder is returned, the filesystem root is not a candidate."""
        cleaner = CleanupFolder()
        cleaner.register_deleted('/x/y.py')
        assert cleaner.get_cleanup_folders() == ['/x']

    def test_windows_top_level_folder(self):
        """The top level folder is returned, the drive root is not a candidate."""
        cleaner = CleanupFolder()
        cleaner.register_deleted('C:/x/y.py')
        assert cleaner.get_cleanup_folders() == ['C:/x']


class TestRepeatable:
    def test_registrations_kept(self):
        """The registrations are kept, asking again gives the same result."""
        cleaner = CleanupFolder()
        cleaner.register_folder('a/b')
        cleaner.register_deleted('a/b/c.py')
        first = cleaner.get_cleanup_folders()
        assert first == ['a/b', 'a']
        assert cleaner.get_cleanup_folders() == first


class TestSorting:
    def test_deepest_first_and_deterministic(self):
        """The folders are returned the deepest first, the path orders the folders of the same depth."""
        cleaner = CleanupFolder()
        cleaner.register_folder({'a', 'b', 'b/c', 'd/e'})
        assert cleaner.get_cleanup_folders() == ['b/c', 'd/e', 'a', 'b', 'd']


class TestWalkStopsEarly:
    def test_chain_walked_once(self, monkeypatch):
        """The parent folders above a folder already collected are not walked again."""
        folders = folder_chain(10)
        cleaner = CleanupFolder()
        cleaner.register_folder(folders)
        cleaner.register_deleted(f'{folders[-1]}/gone.py')
        calls = []
        uppath = cleanup_folder.uppath

        def uppath_record(path):
            calls.append(path)
            return uppath(path)

        monkeypatch.setattr(cleanup_folder, 'uppath', uppath_record)
        assert cleaner.get_cleanup_folders() == list(reversed(folders))
        # one step per registered folder, the deleted path stops at the
        # deepest folder of the chain
        assert len(calls) == len(folders) + 1

    def test_shared_folder_walked_once(self, monkeypatch):
        """The walks of the files sharing a folder stop at the folder."""
        cleaner = CleanupFolder()
        cleaner.register_file({f'a/file{i}.py' for i in range(10)})
        calls = []
        uppath = cleanup_folder.uppath

        def uppath_record(path):
            calls.append(path)
            return uppath(path)

        monkeypatch.setattr(cleanup_folder, 'uppath', uppath_record)
        assert cleaner.get_cleanup_folders() == []
        # the first file walks the folder and the root, the others stop
        # at the folder
        assert len(calls) == 11

    def test_removed_shared_folder_walked_once(self, monkeypatch):
        """The walks of the removed files sharing a folder stop at the folder."""
        cleaner = CleanupFolder()
        cleaner.register_deleted({f'a/b/file{i}.py' for i in range(10)})
        calls = []
        uppath = cleanup_folder.uppath

        def uppath_record(path):
            calls.append(path)
            return uppath(path)

        monkeypatch.setattr(cleanup_folder, 'uppath', uppath_record)
        assert cleaner.get_cleanup_folders() == ['a/b', 'a']
        # the first file walks its folder, the parent folder and the
        # root, the others stop at the folder
        assert len(calls) == 12
