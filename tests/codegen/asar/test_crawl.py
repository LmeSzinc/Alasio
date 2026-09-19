"""
Tests of the directory crawl of the pack side.

The fake filesystem provides a small tree, the expected order below is the
deterministic order the reference implementation produces as well: a directory
before its content, siblings sorted by name in Unicode code point order.
"""
import pytest

from alasio.codegen.asar import crawl
from alasio.codegen.asar.crawl import crawl_tree, list_dir
from alasio.codegen.asar.errors import AsarError, AsarUnsupportedError
from alasio.codegen.asar.model import KIND_DIR, KIND_FILE
from alasio.testing.filesystem import fs  # noqa: F401


def build_tree(fs):
    """
    Create the tree used by these tests under the fake filesystem root.

    Args:
        fs (FakeFilesystem): Fake filesystem

    Returns:
        str: Root path of the tree
    """
    root = f'{fs.root_dir.path}/root'
    fs.create_dir(root)
    fs.create_dir(f'{root}/dir/sub')
    fs.create_dir(f'{root}/empty_dir')
    fs.create_file(f'{root}/.hidden.txt', contents='hidden')
    fs.create_file(f'{root}/z.txt', contents='z')
    fs.create_file(f'{root}/dir/a.txt', contents='a')
    fs.create_file(f'{root}/dir/b.log', contents='b')
    fs.create_file(f'{root}/dir/sub/c.txt', contents='c')
    return root


class TestListDir:
    def test_list_dir_order(self, fs):
        """Siblings are sorted by name, directories and files interleaved."""
        root = build_tree(fs)
        assert list_dir(f'{root}/dir', arc_prefix='dir') == [
            ('dir/a.txt', f'{root}/dir/a.txt', KIND_FILE),
            ('dir/b.log', f'{root}/dir/b.log', KIND_FILE),
            ('dir/sub', f'{root}/dir/sub', KIND_DIR),
        ]

    def test_list_dir_root_prefix(self, fs):
        """Without a prefix the name itself is the archive path."""
        root = build_tree(fs)
        assert list_dir(root) == [
            ('.hidden.txt', f'{root}/.hidden.txt', KIND_FILE),
            ('dir', f'{root}/dir', KIND_DIR),
            ('empty_dir', f'{root}/empty_dir', KIND_DIR),
            ('z.txt', f'{root}/z.txt', KIND_FILE),
        ]

    def test_list_dir_missing(self, fs):
        """Listing a missing directory is an error, the OSError is its cause."""
        build_tree(fs)
        with pytest.raises(AsarError) as e:
            list_dir(f'{fs.root_dir.path}/nope')
        assert str(e.value).startswith('Unable to list directory')
        assert isinstance(e.value.__cause__, FileNotFoundError)

    def test_list_dir_missing_ok(self, fs):
        """A directory that is not there is empty when the caller allows it."""
        build_tree(fs)
        assert list_dir(f'{fs.root_dir.path}/nope', missing_ok=True) == []


class TestCrawlTree:
    def test_crawl_tree_order(self, fs):
        """The walk is level by level, directory entries come before their content."""
        root = build_tree(fs)
        assert crawl_tree(root) == [
            ('.hidden.txt', f'{root}/.hidden.txt', KIND_FILE),
            ('dir', f'{root}/dir', KIND_DIR),
            ('empty_dir', f'{root}/empty_dir', KIND_DIR),
            ('z.txt', f'{root}/z.txt', KIND_FILE),
            ('dir/a.txt', f'{root}/dir/a.txt', KIND_FILE),
            ('dir/b.log', f'{root}/dir/b.log', KIND_FILE),
            ('dir/sub', f'{root}/dir/sub', KIND_DIR),
            ('dir/sub/c.txt', f'{root}/dir/sub/c.txt', KIND_FILE),
        ]

    def test_crawl_tree_deep(self, fs):
        """A deep tree is walked iteratively."""
        fs.create_dir('/deep')
        path = '/deep'
        for index in range(300):
            path = f'{path}/d{index}'
        fs.create_dir(path)
        fs.create_file(f'{path}/a.txt', contents='a')
        order = crawl_tree('/deep')
        assert len(order) == 301
        assert order[-1] == (f'd0/d1', order[-1][0], KIND_DIR) or order[-1][2] == KIND_FILE
        assert order[-1][2] == KIND_FILE

    def test_crawl_tree_empty(self, fs):
        """An empty directory has no entry of its own."""
        fs.create_dir('/empty')
        assert crawl_tree('/empty') == []

    def test_crawl_tree_directory_that_is_gone(self, fs, monkeypatch):
        """A directory that is not there any more is packed as an empty one."""
        root = build_tree(fs)
        real_list_dir = crawl.list_dir

        def list_dir_gone(local_path, arc_prefix=None, missing_ok=False):
            if local_path == f'{root}/dir/sub':
                # What os.scandir() reports for a Windows junction whose target
                # is gone: the directory is listed, nothing lists it
                assert missing_ok, 'the walk asks for a missing directory to be empty'
                return []
            return real_list_dir(local_path, arc_prefix=arc_prefix, missing_ok=missing_ok)

        monkeypatch.setattr(crawl, 'list_dir', list_dir_gone)
        assert crawl_tree(root) == [
            ('.hidden.txt', f'{root}/.hidden.txt', KIND_FILE),
            ('dir', f'{root}/dir', KIND_DIR),
            ('empty_dir', f'{root}/empty_dir', KIND_DIR),
            ('z.txt', f'{root}/z.txt', KIND_FILE),
            ('dir/a.txt', f'{root}/dir/a.txt', KIND_FILE),
            ('dir/b.log', f'{root}/dir/b.log', KIND_FILE),
            ('dir/sub', f'{root}/dir/sub', KIND_DIR),
        ]

    def test_crawl_tree_root_that_is_not_there(self, fs):
        """A source directory that is not there fails the walk."""
        build_tree(fs)
        with pytest.raises(AsarError) as e:
            crawl_tree(f'{fs.root_dir.path}/nope')
        assert str(e.value).startswith('Unable to list directory')

    def test_crawl_tree_directory_that_can_not_be_read(self, fs, monkeypatch):
        """A directory that is there but can not be read fails the walk."""
        root = build_tree(fs)
        real_list_dir = crawl.list_dir

        def list_dir_denied(local_path, arc_prefix=None, missing_ok=False):
            if local_path == f'{root}/dir':
                raise AsarError(
                    f'Unable to list directory "{local_path}"'
                ) from PermissionError(13, 'Permission denied', local_path)
            return real_list_dir(local_path, arc_prefix=arc_prefix, missing_ok=missing_ok)

        monkeypatch.setattr(crawl, 'list_dir', list_dir_denied)
        with pytest.raises(AsarError) as e:
            crawl_tree(root)
        assert isinstance(e.value.__cause__, PermissionError)

    def test_crawl_tree_enters_every_directory(self, fs, monkeypatch):
        """Every directory of the tree is read, nothing is skipped."""
        root = build_tree(fs)
        fs.create_dir(f'{root}/node_modules/pkg')
        fs.create_file(f'{root}/node_modules/pkg/index.js', contents='x')
        listed = []
        real_list_dir = crawl.list_dir

        def list_dir_spy(local_path, arc_prefix=None, missing_ok=False):
            listed.append(arc_prefix)
            return real_list_dir(local_path, arc_prefix=arc_prefix, missing_ok=missing_ok)

        monkeypatch.setattr(crawl, 'list_dir', list_dir_spy)
        order = crawl_tree(root)
        assert 'dir/sub' in listed
        assert 'node_modules' in listed
        assert 'node_modules/pkg' in listed
        assert ('node_modules', f'{root}/node_modules', KIND_DIR) in order
        assert ('node_modules/pkg/index.js', f'{root}/node_modules/pkg/index.js', KIND_FILE) in order


class TestSymbolicLinks:
    """
    Symbolic links of a source tree.

    A link to a file is followed, the content of the file it points at is
    packed, and so is a link to a directory that is not an ancestor of itself.
    What the walk refuses is a link that points back at a directory above it:
    following it would walk the same directory again and again, forever. The
    check is done on the identity of the directory, because a junction of
    Windows is not a symbolic link and aliases of a directory are what both of
    them are.

    The in-memory filesystem does not list a directory through a symbolic link,
    so a test below can only assert that the walk enters a linked directory
    instead of refusing it: the children are the work of the next level.
    """
    def test_a_link_to_a_file_is_packed_as_a_file(self, fs):
        """A symbolic link to a file is the file it points at."""
        root = build_tree(fs)
        fs.create_symlink(f'{root}/alias.txt', f'{root}/z.txt')
        assert ('alias.txt', f'{root}/alias.txt', KIND_FILE) in list_dir(root)
        assert ('alias.txt', f'{root}/alias.txt', KIND_FILE) in crawl_tree(root)

    def test_a_link_to_another_directory_is_walked(self, fs):
        """A link to a directory that is not an ancestor is not refused."""
        root = build_tree(fs)
        fs.create_symlink(f'{root}/alias_dir', f'{root}/dir/sub')
        assert ('alias_dir', f'{root}/alias_dir', KIND_DIR) in crawl_tree(root)

    def test_a_link_to_the_root_is_refused(self, fs):
        """A link to the root is a walk that can never end."""
        root = build_tree(fs)
        fs.create_symlink(f'{root}/loop', root)
        with pytest.raises(AsarUnsupportedError) as e:
            crawl_tree(root)
        assert str(e.value) == (
            'Directory "loop" is a link back to a directory above it, it would be walked forever'
        )

    def test_a_link_to_a_directory_above_it_is_refused(self, fs):
        """The entry that closes the cycle is named with its archive path."""
        root = build_tree(fs)
        fs.create_symlink(f'{root}/dir/sub/loop', f'{root}/dir')
        with pytest.raises(AsarUnsupportedError) as e:
            crawl_tree(root)
        assert str(e.value) == (
            'Directory "dir/sub/loop" is a link back to a directory above it, '
            'it would be walked forever'
        )

    def test_two_paths_to_one_directory_are_not_a_cycle(self, fs):
        """A directory that is linked twice on the same level is packed twice."""
        root = build_tree(fs)
        fs.create_symlink(f'{root}/dir/alias', f'{root}/dir/sub')
        order = crawl_tree(root)
        assert ('dir/alias', f'{root}/dir/alias', KIND_DIR) in order
        assert ('dir/sub', f'{root}/dir/sub', KIND_DIR) in order
