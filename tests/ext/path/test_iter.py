"""
Tests for alasio.ext.path.iter.

Each iterator opens one os.scandir() per folder. A scandir iterator keeps an
OS level directory handle open until it is closed, and consumers commonly stop
early (break / takewhile / an outer generator that is being closed), so the
iterator has to close the handle itself instead of leaving it to the garbage
collector.
"""
import os

import pytest

from alasio.ext.path.iter import iter_entry, iter_filenames, iter_files, iter_foldernames, iter_folders
from alasio.testing.filesystem import fs  # noqa: F401


class WatchedScandir:
    """
    Stand-in of the iterator returned by os.scandir(), records the close
    """

    def __init__(self, entries):
        """
        Args:
            entries (Iterator): Entries of the real scandir iterator
        """
        self._entries = entries
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        if self.closed:
            raise StopIteration
        try:
            return next(self._entries)
        except StopIteration:
            # The real scandir iterator closes itself when exhausted
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def close(self):
        self.closed = True


@pytest.fixture
def watched(fs, monkeypatch):
    """
    Wrap os.scandir() to watch every iterator opened during the test

    Returns:
        list[WatchedScandir]: Opened iterators, in open order
    """
    opened = []
    scandir = os.scandir

    def watched_scandir(path):
        entries = WatchedScandir(scandir(path))
        opened.append(entries)
        return entries

    monkeypatch.setattr(os, 'scandir', watched_scandir)
    return opened


def assert_all_closed(watched):
    """
    Assert every opened scandir iterator is closed

    Args:
        watched (list[WatchedScandir]): Opened iterators
    """
    assert watched, 'no scandir iterator was opened'
    assert [entries.closed for entries in watched] == [True] * len(watched)


TREE = [
    '/root/a.txt',
    '/root/b.log',
    '/root/sub/c.txt',
]


@pytest.fixture
def tree(fs):
    """
    Create the folder tree used by the iterators

    /root/a.txt, /root/b.log, /root/sub/c.txt, /root/sub/deep/

    Returns:
        str: Normalized path of /root in the fake filesystem
    """
    for file in TREE:
        fs.create_file(file, contents='')
    fs.create_dir('/root/sub/deep')
    return fs.realpath('/root')


def subpath(paths, root):
    """
    Convert full paths under root to sorted paths relative to root

    Args:
        paths (Iterable[str]): Full paths
        root (str): Normalized path of the root folder

    Returns:
        list[str]: Sorted relative paths, "/" separated
    """
    prefix = root + '/'
    relative = []
    for path in paths:
        assert path.startswith(prefix), f'{path} is not under {root}'
        relative.append(path[len(prefix):])
    return sorted(relative)


class TestScandirClosed:
    """The scandir handle is closed whatever the consumer does"""

    @pytest.mark.parametrize("func, kwargs", [
        (iter_files, dict()),
        (iter_files, dict(ext='.txt')),
        (iter_files, dict(recursive=True)),
        (iter_files, dict(ext='.txt', recursive=True)),
        (iter_filenames, dict()),
        (iter_filenames, dict(ext='.txt')),
        (iter_folders, dict()),
        (iter_folders, dict(recursive=True)),
        (iter_foldernames, dict()),
        (iter_foldernames, dict(recursive=True)),
        (iter_entry, dict()),
        (iter_entry, dict(recursive=True)),
    ])
    def test_closed_after_early_stop(self, tree, watched, func, kwargs):
        """The consumer breaks out of the loop, the handle is already released."""
        iterator = func(tree, **kwargs)
        next(iterator)
        iterator.close()
        assert_all_closed(watched)

    @pytest.mark.parametrize("func, kwargs", [
        (iter_files, dict(ext='.txt', recursive=True)),
        (iter_filenames, dict(ext='.txt')),
        (iter_folders, dict(recursive=True)),
        (iter_foldernames, dict()),
        (iter_foldernames, dict(recursive=True)),
        (iter_entry, dict(recursive=True)),
    ])
    def test_closed_after_exhaustion(self, tree, watched, func, kwargs):
        """A fully consumed iterator closes the handle as well."""
        assert list(func(tree, **kwargs))
        assert_all_closed(watched)

    def test_closed_in_every_folder_when_recursive(self, tree, watched):
        """One scandir per folder, /root, /root/sub and the empty /root/sub/deep."""
        assert subpath(iter_files(tree, recursive=True), tree) == [
            'a.txt',
            'b.log',
            'sub/c.txt',
        ]
        assert len(watched) == 3
        assert_all_closed(watched)

    def test_missing_root(self, tree, watched):
        """A missing folder yields nothing and opens nothing."""
        assert list(iter_files(tree + '/missing', recursive=True)) == []
        assert list(iter_folders(tree + '/missing', recursive=True)) == []
        assert watched == []

    def test_root_is_a_file(self, tree, watched):
        """A root that is a file yields nothing and opens nothing."""
        assert list(iter_files(tree + '/a.txt')) == []
        assert list(iter_entry(tree + '/a.txt')) == []
        assert watched == []


class TestResults:
    """The iterators yield what their name says"""

    @pytest.mark.parametrize("ext, recursive, expected", [
        ('', False, ['a.txt', 'b.log']),
        ('', True, ['a.txt', 'b.log', 'sub/c.txt']),
        ('.txt', False, ['a.txt']),
        ('.txt', True, ['a.txt', 'sub/c.txt']),
        ('.log', True, ['b.log']),
        ('.img', True, []),
    ])
    def test_iter_files(self, tree, ext, recursive, expected):
        assert subpath(iter_files(tree, ext=ext, recursive=recursive), tree) == expected

    @pytest.mark.parametrize("ext, expected", [
        ('', ['a.txt', 'b.log']),
        ('.txt', ['a.txt']),
        ('.img', []),
    ])
    def test_iter_filenames(self, tree, ext, expected):
        assert sorted(iter_filenames(tree, ext=ext)) == expected

    @pytest.mark.parametrize("recursive, expected", [
        (False, ['sub']),
        (True, ['sub', 'sub/deep']),
    ])
    def test_iter_folders(self, tree, recursive, expected):
        assert subpath(iter_folders(tree, recursive=recursive), tree) == expected

    @pytest.mark.parametrize("recursive, expected", [
        (False, ['sub']),
        (True, ['deep', 'sub']),
    ])
    def test_iter_foldernames(self, tree, recursive, expected):
        assert sorted(iter_foldernames(tree, recursive=recursive)) == expected

    def test_iter_foldernames_recursive_yields_names_only(self, tree):
        """
        The recursion yields the names of the subdirectories, not their paths

        A path separator in a result means iter_foldernames() recursed with
        iter_folders(), which yields full paths instead of names.
        """
        names = list(iter_foldernames(tree, recursive=True))
        assert [name for name in names if '/' in name or '\\' in name] == []

    @pytest.mark.parametrize("recursive, expected", [
        (False, ['a.txt', 'b.log', 'sub']),
        (True, ['a.txt', 'b.log', 'c.txt', 'deep', 'sub']),
    ])
    def test_iter_entry(self, tree, recursive, expected):
        entries = list(iter_entry(tree, recursive=recursive))
        assert sorted(entry.name for entry in entries) == expected
