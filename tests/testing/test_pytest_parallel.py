import os
import shutil

import pytest

from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.testing import pytest_parallel


class FakeConfig:
    """Minimal stand-in of pytest.Config for selects_folders()"""

    def __init__(self, args):
        self.args = list(args)


class FakeItem:
    """Minimal stand-in of pytest.Item for group_of()"""

    def __init__(self, path, marked=False):
        self.path = path
        self.marked = marked

    def get_closest_marker(self, name):
        """Report the mark of the item, the way pytest.Item does"""
        if self.marked and name == pytest_parallel.MARK_NAME:
            return object()
        return None


class TestNormalizeArgs:
    """Tests for normalize_args()."""

    @pytest.mark.parametrize(
        'args, expected',
        [
            # Nothing to remove
            ([], ['--dist=loadgroup']),
            (['tests'], ['tests', '--dist=loadgroup']),
            (['tests', '-q'], ['tests', '-q', '--dist=loadgroup']),
            (['tests', '--durations=10'], ['tests', '--durations=10', '--dist=loadgroup']),
            (['tests', '-k', 'not slow'], ['tests', '-k', 'not slow', '--dist=loadgroup']),
            # The worker count, separated, attached and long
            (['tests', '-n', '4'], ['tests', '--dist=loadgroup']),
            (['tests', '-n', 'auto'], ['tests', '--dist=loadgroup']),
            (['tests', '-n4'], ['tests', '--dist=loadgroup']),
            (['tests', '-nauto'], ['tests', '--dist=loadgroup']),
            (['tests', '-n=4'], ['tests', '--dist=loadgroup']),
            (['tests', '--numprocesses', '2'], ['tests', '--dist=loadgroup']),
            (['tests', '--numprocesses=2'], ['tests', '--dist=loadgroup']),
            (['tests', '--maxprocesses', '3'], ['tests', '--dist=loadgroup']),
            (['tests', '--maxprocesses=3'], ['tests', '--dist=loadgroup']),
            # The scheduler, separated, attached and the shortcut
            (['tests', '--dist', 'loadfile'], ['tests', '--dist=loadgroup']),
            (['tests', '--dist=load'], ['tests', '--dist=loadgroup']),
            (['tests', '--dist', 'loadgroup'], ['tests', '--dist=loadgroup']),
            (['tests', '-d'], ['tests', '--dist=loadgroup']),
            # Order of the options does not matter, the rest keeps its order
            (['-n', '2', 'tests', '--dist', 'load', '-q'], ['tests', '-q', '--dist=loadgroup']),
            (['tests', '-q', '--dist', 'load', '-n', '2', '-k', 'foo'], ['tests', '-q', '-k', 'foo', '--dist=loadgroup']),
            # A value is taken along with its option, not left as a test path
            (['-n', '2', 'tests', '-x'], ['tests', '-x', '--dist=loadgroup']),
            # Only one scheduler is pinned, even when the caller passed two
            (['tests', '--dist', 'load', '-d'], ['tests', '--dist=loadgroup']),
        ],
    )
    def test_removed(self, args, expected):
        """Pool options are removed, every other argument is kept in order."""
        assert pytest_parallel.normalize_args(args) == expected

    def test_does_not_mutate_the_argument(self):
        """The command line of the caller is not modified."""
        args = ['tests', '-n', '2']
        pytest_parallel.normalize_args(args)
        assert args == ['tests', '-n', '2']

    def test_a_word_starting_with_n_is_kept(self):
        """Only the short option of the pool is removed, not any word starting with -n."""
        assert pytest_parallel.normalize_args(['-not-a-test.py']) == ['-not-a-test.py', '--dist=loadgroup']


class TestGroupOf:
    """Tests for group_of()."""

    def test_unmarked_is_serial(self):
        """An item without the mark goes to the one serial group."""
        assert pytest_parallel.group_of(FakeItem('/repo/tests/test_a.py')) == pytest_parallel.SERIAL_GROUP

    def test_marked_is_the_file(self):
        """A marked item goes to the group of its file."""
        item = FakeItem('/repo/tests/test_a.py', marked=True)
        assert pytest_parallel.group_of(item) == pytest_parallel.PARALLEL_PREFIX + '/repo/tests/test_a.py'

    def test_one_group_per_file(self):
        """Items of the same file share a group, items of another file do not."""
        first = pytest_parallel.group_of(FakeItem('/repo/tests/test_a.py', marked=True))
        second = pytest_parallel.group_of(FakeItem('/repo/tests/test_a.py', marked=True))
        other = pytest_parallel.group_of(FakeItem('/repo/tests/test_b.py', marked=True))
        assert first == second
        assert first != other

    def test_marked_files_never_share_the_serial_group(self):
        """A marked item is never put in the serial group by accident."""
        item = FakeItem('/repo/tests/test_a.py', marked=True)
        assert pytest_parallel.group_of(item) != pytest_parallel.SERIAL_GROUP


class TestSelectsFolders:
    """Tests for selects_folders()."""

    @pytest.fixture
    def tree(self):
        """
        A folder under the disposable folder of the repository with one test file

        Yields:
            tuple: Absolute path of the folder, and of the file inside it
        """
        root = PathStr.new(str(env.ALASIO_TEMP.joinpath('test_pytest_parallel_selects')))
        shutil.rmtree(root, ignore_errors=True)
        os.makedirs(root)
        file = root.joinpath('test_a.py')
        with open(file, 'w', encoding='utf-8') as f:
            f.write('def test_a():\n    pass\n')
        yield root, file
        shutil.rmtree(root, ignore_errors=True)

    def test_a_folder(self, tree):
        """A folder is a whole tree, the one thing a pool can spread."""
        root, _ = tree
        assert pytest_parallel.selects_folders(FakeConfig([str(root)])) is True

    def test_two_folders(self, tree):
        """Several folders are still a tree run."""
        root, _ = tree
        assert pytest_parallel.selects_folders(FakeConfig([str(root), str(root)])) is True

    def test_a_file(self, tree):
        """A file picks one module, the pool has nothing to spread."""
        _, file = tree
        assert pytest_parallel.selects_folders(FakeConfig([str(file)])) is False

    def test_a_class_node_id(self, tree):
        """A node id picks a class of a module."""
        _, file = tree
        assert pytest_parallel.selects_folders(FakeConfig([f'{file}::TestA'])) is False

    def test_a_test_node_id(self, tree):
        """A node id picks a single test."""
        _, file = tree
        assert pytest_parallel.selects_folders(FakeConfig([f'{file}::test_a'])) is False

    def test_a_file_beside_a_folder(self, tree):
        """One narrow argument is enough to keep the run in process."""
        root, file = tree
        assert pytest_parallel.selects_folders(FakeConfig([str(root), str(file)])) is False

    def test_a_missing_folder(self, tree):
        """A path that is not a folder is not a tree run."""
        root, _ = tree
        assert pytest_parallel.selects_folders(FakeConfig([str(root.joinpath('missing'))])) is False

    def test_no_argument(self):
        """pytest resolves a bare run to the invocation folder before the plugins load."""
        assert pytest_parallel.selects_folders(FakeConfig([])) is False
