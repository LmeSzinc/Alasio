"""
Tests of the directory crawl of the pack side.

The fake filesystem provides a small tree, the expected order below is the
deterministic order the reference implementation produces as well: a directory
before its content, siblings sorted by name in Unicode code point order.
"""
import pytest

from alasio.codegen.asar.crawl import crawl_folder, crawl_tree, list_dir
from alasio.codegen.asar.errors import AsarError
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
        """Listing a missing directory is an error."""
        build_tree(fs)
        with pytest.raises(AsarError) as e:
            list_dir(f'{fs.root_dir.path}/nope')
        assert str(e.value).startswith('Unable to list directory')


class TestCrawlTree:
    def test_crawl_tree_order(self, fs):
        """The walk is depth first, directory entries come before their content."""
        root = build_tree(fs)
        assert crawl_tree(root) == [
            ('.hidden.txt', f'{root}/.hidden.txt', KIND_FILE),
            ('dir', f'{root}/dir', KIND_DIR),
            ('dir/a.txt', f'{root}/dir/a.txt', KIND_FILE),
            ('dir/b.log', f'{root}/dir/b.log', KIND_FILE),
            ('dir/sub', f'{root}/dir/sub', KIND_DIR),
            ('dir/sub/c.txt', f'{root}/dir/sub/c.txt', KIND_FILE),
            ('empty_dir', f'{root}/empty_dir', KIND_DIR),
            ('z.txt', f'{root}/z.txt', KIND_FILE),
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


class TestCrawlFolder:
    def test_crawl_folder_all(self, fs):
        """Without a filter the whole tree is added, empty directories included."""
        root = build_tree(fs)
        entries = crawl_folder(root)
        assert [(path, kind, unpacked) for path, _, kind, unpacked in entries] == [
            ('.hidden.txt', KIND_FILE, False),
            ('dir', KIND_DIR, False),
            ('dir/a.txt', KIND_FILE, False),
            ('dir/b.log', KIND_FILE, False),
            ('dir/sub', KIND_DIR, False),
            ('dir/sub/c.txt', KIND_FILE, False),
            ('empty_dir', KIND_DIR, False),
            ('z.txt', KIND_FILE, False),
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
            ('dir/a.txt', KIND_FILE),
            ('dir/b.log', KIND_FILE),
            ('dir/sub', KIND_DIR),
            ('empty_dir', KIND_DIR),
            ('z.txt', KIND_FILE),
        ]

    def test_crawl_folder_exclude_glob(self, fs):
        """A glob exclude drops every match."""
        root = build_tree(fs)
        entries = crawl_folder(root, exclude=['**/*.log', '**/*.txt'])
        assert [(path, kind) for path, _, kind, _ in entries] == [
            ('dir', KIND_DIR),
            ('dir/sub', KIND_DIR),
            ('empty_dir', KIND_DIR),
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

    @pytest.mark.parametrize('patterns, expected', [
        # A pattern without a separator matches the file name
        (['*.log'], ['dir/b.log']),
        (['b.log'], ['dir/b.log']),
        # A pattern with a separator is matched on the whole path
        (['**/*.txt'], ['.hidden.txt', 'dir/a.txt', 'dir/sub/c.txt', 'z.txt']),
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
