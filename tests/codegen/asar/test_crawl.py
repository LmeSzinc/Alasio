"""
Tests of the directory crawl of the pack side.

The fake filesystem provides a small tree, the expected order below is the
deterministic order the reference implementation produces as well: a directory
before its content, siblings sorted by name in Unicode code point order.
"""
import pytest

from alasio.codegen.asar import crawl
from alasio.codegen.asar.crawl import crawl_folder, crawl_tree, list_dir
from alasio.codegen.asar.errors import AsarError
from alasio.codegen.asar.model import KIND_DIR, KIND_FILE
from alasio.codegen.asar.pattern import GlobPattern
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

    def test_crawl_tree_include_prunes_the_walk(self, fs, monkeypatch):
        """A directory that no include pattern can reach is not entered."""
        root = build_tree(fs)
        fs.create_dir(f'{root}/node_modules/pkg')
        fs.create_file(f'{root}/node_modules/pkg/index.js', contents='x')
        listed = []
        real_list_dir = crawl.list_dir

        def list_dir_spy(local_path, arc_prefix=None, missing_ok=False):
            listed.append(arc_prefix)
            return real_list_dir(local_path, arc_prefix=arc_prefix, missing_ok=missing_ok)

        monkeypatch.setattr(crawl, 'list_dir', list_dir_spy)
        order = crawl_tree(root, include=GlobPattern(['dir/**']))
        # The whole subtree of node_modules is left unread
        assert 'node_modules' not in listed
        assert 'dir/sub' in listed
        # The directory itself stays an entry, the include filter below sees the
        # same tree either way
        assert ('node_modules', f'{root}/node_modules', KIND_DIR) in order
        assert all(path != 'node_modules/pkg' for path, *_ in order)

    def test_crawl_tree_without_include_walks_every_directory(self, fs, monkeypatch):
        """Without an include list every directory is entered."""
        root = build_tree(fs)
        fs.create_dir(f'{root}/node_modules')
        listed = []
        real_list_dir = crawl.list_dir

        def list_dir_spy(local_path, arc_prefix=None, missing_ok=False):
            listed.append(arc_prefix)
            return real_list_dir(local_path, arc_prefix=arc_prefix, missing_ok=missing_ok)

        monkeypatch.setattr(crawl, 'list_dir', list_dir_spy)
        crawl_tree(root)
        assert 'node_modules' in listed


class TestCrawlFolder:
    def test_crawl_folder_all(self, fs):
        """Without a filter the whole tree is added, empty directories included."""
        root = build_tree(fs)
        entries = crawl_folder(root)
        assert [(path, kind, unpacked) for path, _, kind, unpacked in entries] == [
            ('.hidden.txt', KIND_FILE, False),
            ('dir', KIND_DIR, False),
            ('empty_dir', KIND_DIR, False),
            ('z.txt', KIND_FILE, False),
            ('dir/a.txt', KIND_FILE, False),
            ('dir/b.log', KIND_FILE, False),
            ('dir/sub', KIND_DIR, False),
            ('dir/sub/c.txt', KIND_FILE, False),
        ]

    def test_crawl_folder_include_files(self, fs):
        """Only the matching files are kept, their parent directories follow."""
        root = build_tree(fs)
        entries = crawl_folder(root, include=['dir/sub/**'])
        assert [(path, kind) for path, _, kind, _ in entries] == [
            ('dir', KIND_DIR),
            ('dir/sub', KIND_DIR),
            ('dir/sub/c.txt', KIND_FILE),
        ]

    def test_crawl_folder_include_dir(self, fs):
        """A directory that matches include is kept even when empty."""
        root = build_tree(fs)
        entries = crawl_folder(root, include=['empty_dir', 'z.txt'])
        assert [(path, kind) for path, _, kind, _ in entries] == [
            ('empty_dir', KIND_DIR),
            ('z.txt', KIND_FILE),
        ]

    def test_crawl_folder_include_star(self, fs):
        """A pattern with a separator does not match nested files."""
        root = build_tree(fs)
        entries = crawl_folder(root, include=['*.txt'])
        assert [(path, kind) for path, _, kind, _ in entries] == [
            ('.hidden.txt', KIND_FILE),
            ('z.txt', KIND_FILE),
        ]

    def test_crawl_folder_exclude_file(self, fs):
        """An excluded file is dropped, its directory stays."""
        root = build_tree(fs)
        entries = crawl_folder(root, exclude=['dir/sub/c.txt'])
        assert [(path, kind) for path, _, kind, _ in entries] == [
            ('.hidden.txt', KIND_FILE),
            ('dir', KIND_DIR),
            ('empty_dir', KIND_DIR),
            ('z.txt', KIND_FILE),
            ('dir/a.txt', KIND_FILE),
            ('dir/b.log', KIND_FILE),
            ('dir/sub', KIND_DIR),
        ]

    def test_crawl_folder_exclude_glob(self, fs):
        """A glob exclude drops every match."""
        root = build_tree(fs)
        entries = crawl_folder(root, exclude=['**/*.log', '**/*.txt'])
        assert [(path, kind) for path, _, kind, _ in entries] == [
            ('dir', KIND_DIR),
            ('empty_dir', KIND_DIR),
            ('dir/sub', KIND_DIR),
        ]

    def test_crawl_folder_exclude_dir(self, fs):
        """A directory exclude drops its whole subtree, the directory included."""
        root = build_tree(fs)
        entries = crawl_folder(root, exclude=['dir'])
        assert [(path, kind) for path, _, kind, _ in entries] == [
            ('.hidden.txt', KIND_FILE),
            ('empty_dir', KIND_DIR),
            ('z.txt', KIND_FILE),
        ]

    def test_crawl_folder_exclude_dir_glob(self, fs):
        """A glob that matches a directory drops its subtree as well."""
        root = build_tree(fs)
        entries = crawl_folder(root, exclude=['dir*'])
        assert [(path, kind) for path, _, kind, _ in entries] == [
            ('.hidden.txt', KIND_FILE),
            ('empty_dir', KIND_DIR),
            ('z.txt', KIND_FILE),
        ]

    def test_crawl_folder_exclude_and_include(self, fs):
        """Include first, then exclude."""
        root = build_tree(fs)
        entries = crawl_folder(root, include=['**/*.txt'], exclude=['dir/**'])
        assert [(path, kind) for path, _, kind, _ in entries] == [
            ('.hidden.txt', KIND_FILE),
            ('z.txt', KIND_FILE),
        ]

    def test_crawl_folder_include_prunes_the_walk(self, fs, monkeypatch):
        """The entries do not change, the walk is only shorter."""
        root = build_tree(fs)
        fs.create_dir(f'{root}/node_modules/pkg')
        fs.create_file(f'{root}/node_modules/pkg/index.js', contents='x')
        listed = []
        real_list_dir = crawl.list_dir

        def list_dir_spy(local_path, arc_prefix=None, missing_ok=False):
            listed.append(arc_prefix)
            return real_list_dir(local_path, arc_prefix=arc_prefix, missing_ok=missing_ok)

        monkeypatch.setattr(crawl, 'list_dir', list_dir_spy)
        entries = crawl_folder(root, include=['dir/**'])
        assert 'node_modules' not in listed
        assert [(path, kind) for path, _, kind, _ in entries] == [
            ('dir', KIND_DIR),
            ('dir/a.txt', KIND_FILE),
            ('dir/b.log', KIND_FILE),
            ('dir/sub', KIND_DIR),
            ('dir/sub/c.txt', KIND_FILE),
        ]

    @pytest.mark.parametrize('patterns, expected', [
        # A pattern without a separator matches the file name
        (['*.log'], ['dir/b.log']),
        (['b.log'], ['dir/b.log']),
        # A pattern with a separator is matched on the whole path
        (['**/*.txt'], ['.hidden.txt', 'z.txt', 'dir/a.txt', 'dir/sub/c.txt']),
        (['dir/*'], ['dir/a.txt', 'dir/b.log']),
        (['dir/**'], ['dir/a.txt', 'dir/b.log', 'dir/sub/c.txt']),
        ([], []),
    ])
    def test_crawl_folder_unpack(self, fs, patterns, expected):
        """The unpack patterns select files that are stored next to the archive."""
        root = build_tree(fs)
        entries = crawl_folder(root, unpack=patterns)
        assert [path for path, _, _, unpacked in entries if unpacked] == expected

    @pytest.mark.parametrize('patterns, expected', [
        # A directory pattern is a literal prefix as well as a glob, so
        # `dir` also selects `dir/sub`, like the asar CLI
        (['dir'], ['dir', 'dir/sub']),
        (['dir/sub'], ['dir/sub']),
        (['**/sub'], ['dir/sub']),
        (['dir*'], ['dir']),
        (['empty_dir'], ['empty_dir']),
        ([], []),
    ])
    def test_crawl_folder_unpack_dir(self, fs, patterns, expected):
        """The unpack_dir patterns select directories."""
        root = build_tree(fs)
        entries = crawl_folder(root, unpack_dir=patterns)
        assert [path for path, _, _, unpacked in entries if unpacked] == expected
