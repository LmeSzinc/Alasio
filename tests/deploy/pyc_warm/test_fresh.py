"""
Tests of the freshness of a pyc file, see alasio/deploy/pyc_warm/fresh.py.
"""
import os
import time
from importlib.util import MAGIC_NUMBER

import pytest

from alasio.deploy.pyc_warm import fresh as fresh_module
from alasio.deploy.pyc_warm.fresh import HEADER_SIZE, ensure, parse_header, plan, plan_folder, stale_sources
from alasio.deploy.simple_pip.pyc import pyc_path
from alasio.testing.filesystem import fs  # noqa: F401

# A mtime in the past, the clock of the test run is later than the file
PAST = 1700000000
# The content of the sources of the tests, 11 bytes
CONTENT = b'VALUE = 42\n'


def make_header(mtime=PAST, size=len(CONTENT), magic=MAGIC_NUMBER, flags=0):
    """
    Build the header of a timestamp based pyc file.

    Args:
        mtime (float): mtime of the source to record
        size (int): Size of the source to record
        magic (bytes): Magic number to record
        flags (int): Flags to record

    Returns:
        bytes: The 16 bytes header
    """
    return b''.join((
        magic,
        flags.to_bytes(4, 'little'),
        int(mtime).to_bytes(4, 'little'),
        size.to_bytes(4, 'little'),
    ))


def write_file(fs, path, data, mtime=None):
    """
    Create or replace a file of the fake filesystem.

    Args:
        fs (FakeFilesystem): The fake filesystem
        path (str): Path of the file
        data (bytes): Content of the file
        mtime (float): Modification time to set. Defaults to None, the
            time of the write

    Returns:
        str: Path of the file
    """
    try:
        fs.remove(path)
    except FileNotFoundError:
        pass
    fs.create_file(path, contents=data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def write_source(fs, path, content=CONTENT, mtime=PAST):
    """
    Create a source file of the fake filesystem.

    Args:
        fs (FakeFilesystem): The fake filesystem
        path (str): Path of the source file
        content (bytes): Content of the source file
        mtime (float): Modification time to set

    Returns:
        os.stat_result: Stat of the source file
    """
    write_file(fs, path, content, mtime)
    return os.stat(path)


def write_pyc(fs, py_file, header, body=b'', mtime=None):
    """
    Create the pyc file of a source of the fake filesystem.

    Args:
        fs (FakeFilesystem): The fake filesystem
        py_file (str): Path of the source file
        header (bytes): Header of the pyc file
        body (bytes): Bytes after the header
        mtime (float): Modification time of the pyc file. Defaults to
            None, the time of the write

    Returns:
        str: Path of the pyc file
    """
    return write_file(fs, pyc_path(py_file), header + body, mtime)


def _is_stale_per_file(file):
    """
    Check one source the naive way, a stat and an open per file.

    The oracle of the scan: it says what stale_sources() must answer,
    without grouping the folders or reading a line of directory entry
    twice.

    Args:
        file (str): Path of the source file

    Returns:
        bool: True if the source has to be compiled
    """
    st = os.stat(file)
    pyc = pyc_path(file)
    try:
        pyc_mtime = os.stat(pyc).st_mtime
    except OSError:
        return True
    if st.st_mtime > pyc_mtime:
        return True
    with open(pyc, 'rb') as f:
        header = f.read(HEADER_SIZE)
    return parse_header(header) != (int(st.st_mtime), st.st_size)


def check_of(file, st, now=PAST):
    """
    Build the work item of one source whose pyc file is to be looked at.

    Args:
        file (str): Path of the source
        st (os.stat_result): Stat of the source
        now (float): Current time of the scan

    Returns:
        tuple: The item of ensure()
    """
    return (file, pyc_path(file), (st.st_mtime, st.st_size), now)


def deferred_check(file, now=PAST):
    """
    Build the item of one source whose stat is read where it is ensured.

    Args:
        file (str): Path of the source
        now (float): Current time of the scan

    Returns:
        tuple: The item of ensure()
    """
    return (file, pyc_path(file), None, now)


def stale_item(file, now=PAST):
    """
    Build the item of one source the listing already settled stale.

    Args:
        file (str): Path of the source
        now (float): Current time of the scan

    Returns:
        tuple: The item of ensure()
    """
    return (file, None, None, now)


class TestParseHeader:
    """
    Tests for parse_header().
    """

    @pytest.mark.parametrize('header, expected', [
        # The header of the source
        (make_header(), (PAST, len(CONTENT))),
        # The bytecode follows the header, which is all that is parsed
        (make_header() + b'\x00' * 64, (PAST, len(CONTENT))),
        # The stat is answered as it is recorded, whatever the source
        # turns out to be
        (make_header(mtime=PAST + 1), (PAST + 1, len(CONTENT))),
        (make_header(size=len(CONTENT) + 1), (PAST, len(CONTENT) + 1)),
        # The check source flag of a timestamp based pyc file, python
        # accepts it and so do we
        (make_header(flags=0b10), (PAST, len(CONTENT))),
        # A pyc file of an other python version
        (make_header(magic=b'\x00\x00\x0d\x0a'), None),
        # An unknown flag, python raises ImportError on it
        (make_header(flags=0b100), None),
        # A hash based pyc file, the hash is not checked here
        (make_header(flags=0b1), None),
        # A truncated pyc file
        (make_header()[:HEADER_SIZE - 1], None),
        (make_header()[:4], None),
        (b'', None),
    ])
    def test_header(self, header, expected):
        """A header answers the stat it records, or None."""
        assert parse_header(header) == expected


class TestStaleSources:
    """
    Tests for stale_sources().
    """

    def test_empty(self, fs):
        """An empty scope compiles nothing."""
        assert stale_sources([]) == []

    def test_all_fresh(self, fs):
        """A warmed scope compiles nothing."""
        files = []
        for index in range(3):
            file = f'/pkg/mod{index}.py'
            st = write_source(fs, file)
            write_pyc(fs, file, make_header(st.st_mtime, st.st_size))
            files.append(file)
        assert stale_sources(files) == []

    def test_only_stale(self, fs):
        """Only the stale sources are returned, sorted by path."""
        write_source(fs, '/pkg/b.py', b'y = 2\n')
        st = os.stat('/pkg/b.py')
        write_pyc(fs, '/pkg/b.py', make_header(st.st_mtime, st.st_size))
        write_source(fs, '/pkg/a.py', b'x = 1\n' * 10)
        write_source(fs, '/pkg/c.py', b'z = 3\n' * 5)
        assert stale_sources(['/pkg/c.py', '/pkg/b.py', '/pkg/a.py']) == [
            '/pkg/a.py',
            '/pkg/c.py',
        ]

    def test_missing_pyc(self, fs):
        """A source without any pyc file, e.g. the first run, is stale."""
        write_source(fs, '/pkg/core.py')
        assert stale_sources(['/pkg/core.py']) == ['/pkg/core.py']

    def test_truncated_pyc(self, fs):
        """A pyc file shorter than a header is compiled again."""
        st = write_source(fs, '/pkg/core.py')
        write_file(fs, pyc_path('/pkg/core.py'), make_header(st.st_mtime, st.st_size)[:12])
        assert stale_sources(['/pkg/core.py']) == ['/pkg/core.py']

    def test_source_dated_in_the_future(self, fs):
        """A source whose clock is ahead still reuses its pyc file."""
        future = time.time() + 1000
        st = write_source(fs, '/pkg/core.py', mtime=future)
        write_pyc(fs, '/pkg/core.py', make_header(st.st_mtime, st.st_size))
        assert stale_sources(['/pkg/core.py']) == []

    def test_missing_source_is_skipped(self, fs):
        """A file of the scope that is not on the disk is skipped."""
        write_source(fs, '/pkg/core.py')
        assert stale_sources(['/pkg/gone.py']) == []

    def test_missing_folder(self, fs):
        """A folder that does not exist holds no source to compile."""
        assert stale_sources(['/missing/core.py']) == []

    def test_duplicates(self, fs):
        """A file listed twice is compiled once."""
        write_source(fs, '/pkg/core.py')
        assert stale_sources(['/pkg/core.py', '/pkg/core.py']) == ['/pkg/core.py']

    def test_pyc_of_an_other_interpreter(self, fs):
        """A pyc file of an other cache tag does not warm a source."""
        st = write_source(fs, '/pkg/core.py')
        write_file(
            fs, '/pkg/__pycache__/core.cpython-99.pyc',
            make_header(st.st_mtime, st.st_size),
        )
        assert stale_sources(['/pkg/core.py']) == ['/pkg/core.py']

    def test_not_a_python_source(self, fs):
        """A file of the scope that is not a .py source is skipped."""
        write_file(fs, '/pkg/core.pyi', b'value: int\n')
        assert stale_sources(['/pkg/core.pyi']) == []

    def test_several_folders(self, fs):
        """The files of every folder are found."""
        write_source(fs, '/pkg/core.py')
        write_source(fs, '/mod/module/config.py')
        assert stale_sources(['/mod/module/config.py', '/pkg/core.py']) == [
            '/mod/module/config.py',
            '/pkg/core.py',
        ]

    def test_agrees_with_a_per_file_check(self, fs):
        """The listing scan finds what a naive check of every file finds."""
        files = []
        for index in range(6):
            file = f'/pkg/mod{index}.py'
            st = write_source(fs, file)
            files.append(file)
            if index % 3:
                # A pyc file that records the source, or an out of date one
                mtime = st.st_mtime if index % 2 else st.st_mtime - 5
                write_pyc(fs, file, make_header(st.st_mtime, st.st_size), mtime=mtime)
        assert sorted(stale_sources(files)) == [
            file for file in files if _is_stale_per_file(file)
        ]

    def test_windows_separators(self, fs):
        """A path built with backslashes is normalized before the scan."""
        if os.name != 'nt':
            pytest.skip('windows path separators only')
        root = fs.root_dir.path
        write_source(fs, f'{root}/pkg/core.py')
        assert stale_sources([f'{root}\\pkg\\core.py']) == [f'{root}/pkg/core.py']


class OpenSpy:
    """
    A stand-in for atomic_open that records what the scan reads.

    Attributes:
        calls (list[tuple[str, str, dict]]): path, mode and kwargs of
            every open
    """

    def __init__(self):
        self.calls = []

    def __call__(self, file, mode='r', **kwargs):
        """
        Args:
            file (str): Path being opened
            mode (str): Open mode
            **kwargs: Open arguments

        Returns:
            object: The real open
        """
        self.calls.append((file, mode, kwargs))
        return open(file, mode=mode, **kwargs)

    @property
    def paths(self):
        """
        Returns:
            list[str]: The paths opened, in the order they were
        """
        return [call[0] for call in self.calls]


class TestScanOpens:
    """
    Tests of what the scan reads: a stale pyc file is never opened.
    """

    @pytest.fixture(autouse=True)
    def _free_stat(self, monkeypatch):
        """
        Run the listing that answers the stat of an entry, the one of Windows.

        The branch is the platform's, see DIRENT_STAT_IS_FREE, and the test
        pins it: what it asserts -- a pyc file older than its source is
        settled without opening it -- is a property of that listing. The
        listing that answers no stat is TestDeferredStat, which pins the
        other branch and asserts the same answers one layer later.

        Args:
            monkeypatch: The pytest monkeypatch fixture
        """
        monkeypatch.setattr(fresh_module, 'DIRENT_STAT_IS_FREE', True)

    def test_missing_pyc_never_opened(self, fs, monkeypatch):
        """A source without any pyc file is stale without an open."""
        spy = OpenSpy()
        monkeypatch.setattr(fresh_module, 'atomic_open', spy)
        write_source(fs, '/pkg/core.py')
        assert stale_sources(['/pkg/core.py']) == ['/pkg/core.py']
        assert spy.calls == []

    def test_older_pyc_never_opened(self, fs, monkeypatch):
        """A pyc file older than its source is stale without an open."""
        spy = OpenSpy()
        monkeypatch.setattr(fresh_module, 'atomic_open', spy)
        st = write_source(fs, '/pkg/core.py')
        write_pyc(fs, '/pkg/core.py', make_header(st.st_mtime, st.st_size), mtime=PAST - 100)
        assert stale_sources(['/pkg/core.py']) == ['/pkg/core.py']
        assert spy.calls == []

    def test_fresh_pyc_opened_for_the_header(self, fs, monkeypatch):
        """A pyc file that can still be fresh is opened for 16 bytes."""
        spy = OpenSpy()
        monkeypatch.setattr(fresh_module, 'atomic_open', spy)
        files = []
        for index in range(3):
            file = f'/pkg/mod{index}.py'
            st = write_source(fs, file)
            write_pyc(fs, file, make_header(st.st_mtime, st.st_size))
            files.append(file)
        assert stale_sources(files) == []
        # The order of a set of names is not defined, the paths are
        assert sorted(spy.paths) == sorted(pyc_path(file) for file in files)
        for _, mode, kwargs in spy.calls:
            assert mode == 'rb'
            # No buffered file object: python reads the header and nothing more
            assert kwargs == {'buffering': 0}

    def test_mtime_gate_reads_the_header_of_a_stale_pyc(self, fs, monkeypatch):
        """A pyc file newer than its source is opened, its header decides."""
        spy = OpenSpy()
        monkeypatch.setattr(fresh_module, 'atomic_open', spy)
        st = write_source(fs, '/pkg/core.py')
        # A pyc file newer than the source that records an other source
        write_pyc(fs, '/pkg/core.py', make_header(st.st_mtime + 5, st.st_size))
        assert stale_sources(['/pkg/core.py']) == ['/pkg/core.py']
        assert sorted(spy.paths) == [pyc_path('/pkg/core.py')]

    def test_unlisted_pyc_is_not_opened(self, fs, monkeypatch):
        """Only the pyc files of the sources asked for are opened."""
        spy = OpenSpy()
        monkeypatch.setattr(fresh_module, 'atomic_open', spy)
        st = write_source(fs, '/pkg/core.py')
        write_pyc(fs, '/pkg/core.py', make_header(st.st_mtime, st.st_size))
        for index in range(5):
            other = f'/pkg/other{index}.py'
            other_st = write_source(fs, other)
            write_pyc(fs, other, make_header(other_st.st_mtime, other_st.st_size))
        assert stale_sources(['/pkg/core.py']) == []
        assert sorted(spy.paths) == [pyc_path('/pkg/core.py')]


class ListingSpy:
    """
    A stand-in for os.scandir that records the folders it lists.

    The fake filesystem replaces os.scandir before the test runs, so the
    spy wraps the listing of the fixture rather than the one of the disk.

    Attributes:
        folders (list[str]): Path of every listed folder, in order
    """

    def __init__(self, monkeypatch):
        self.folders = []
        real = os.scandir

        def counting(path=0):
            self.folders.append(path)
            return real(path)

        monkeypatch.setattr(os, 'scandir', counting)

    def count(self, folder):
        """
        Args:
            folder (str): Path of a folder

        Returns:
            int: How many times the scan listed it
        """
        return self.folders.count(folder)


class TestPlan:
    """
    Tests for plan() and plan_folder(): the traversal that lists every
    folder once and yields one work item per source, opening no file.

    The listing that answers the stat of an entry is pinned, see
    TestScanOpens: the shapes of the items follow from which listing runs.
    """

    @pytest.fixture(autouse=True)
    def _free_stat(self, monkeypatch):
        """
        Args:
            monkeypatch: The pytest monkeypatch fixture
        """
        monkeypatch.setattr(fresh_module, 'DIRENT_STAT_IS_FREE', True)

    def test_stale_without_open(self, fs, monkeypatch):
        """A pyc file missing or older than its source is settled by the listing."""
        spy = OpenSpy()
        monkeypatch.setattr(fresh_module, 'atomic_open', spy)
        write_source(fs, '/pkg/missing.py')
        st = write_source(fs, '/pkg/older.py')
        write_pyc(fs, '/pkg/older.py', make_header(st.st_mtime, st.st_size), mtime=PAST - 100)
        items = list(plan(['/pkg/missing.py', '/pkg/older.py']))
        # The order of a set of names is not defined, the paths are
        assert sorted(item[0] for item in items) == ['/pkg/missing.py', '/pkg/older.py']
        # No pyc file to look at: ensure() compiles these as they are
        assert [item[1] for item in items] == [None, None]
        assert spy.calls == []

    def test_item_for_a_pyc_that_can_still_be_fresh(self, fs, monkeypatch):
        """A pyc file dated after its source travels to the worker."""
        spy = OpenSpy()
        monkeypatch.setattr(fresh_module, 'atomic_open', spy)
        st = write_source(fs, '/pkg/core.py')
        write_pyc(fs, '/pkg/core.py', make_header(st.st_mtime, st.st_size))
        items = list(plan(['/pkg/core.py']))
        assert len(items) == 1
        # The stat of the source travels with the item, the current time
        # too: the gate of a source dated in the future is decided where
        # the item is ensured
        assert items[0][:3] == check_of('/pkg/core.py', st)[:3]
        assert abs(items[0][3] - time.time()) < 60
        assert spy.calls == []

    def test_one_folder_listed_once(self, fs, monkeypatch):
        """The listing of a folder does not scale with its number of sources."""
        for index in range(20):
            write_source(fs, f'/pkg/mod{index}.py')
        write_source(fs, '/other/core.py')
        listing = ListingSpy(monkeypatch)

        items = list(plan([f'/pkg/mod{index}.py' for index in range(20)] + ['/other/core.py']))
        # Every source of the scope is planned, exactly once
        assert sorted(item[0] for item in items) == sorted(
            [f'/pkg/mod{index}.py' for index in range(20)] + ['/other/core.py']
        )
        # The folder and its __pycache__, however many sources it holds
        assert listing.count('/pkg') == 1
        assert listing.count('/pkg/__pycache__') == 1
        assert listing.count('/other') == 1
        assert len(listing.folders) == 4

    def test_unreadable_folder(self, fs):
        """A folder that does not exist holds no source to compile."""
        assert list(plan(['/missing/core.py'])) == []

    def test_not_a_python_source(self, fs):
        """A file that is not a .py source is never planned."""
        write_file(fs, '/pkg/core.pyi', b'value: int\n')
        assert list(plan(['/pkg/core.pyi'])) == []

    def test_duplicates(self, fs):
        """A file listed twice is planned once."""
        write_source(fs, '/pkg/core.py')
        items = list(plan(['/pkg/core.py', '/pkg/core.py']))
        assert [item[0] for item in items] == ['/pkg/core.py']

    def test_plan_folder(self, fs):
        """One folder can be planned on its own, without the generator."""
        st = write_source(fs, '/pkg/core.py')
        write_pyc(fs, '/pkg/core.py', make_header(st.st_mtime, st.st_size))
        now = time.time()
        assert plan_folder('/pkg', {'core.py'}, now) == [check_of('/pkg/core.py', st, now)]


class TestEnsure:
    """
    Tests for ensure(): the half of the scan that opens a file.
    """

    def test_settled_by_the_listing(self, fs):
        """An item without a pyc file is compiled as it is."""
        assert ensure(stale_item('/pkg/core.py')) == '/pkg/core.py'

    def test_fresh(self, fs):
        """A pyc file that records its source needs no compilation."""
        st = write_source(fs, '/pkg/core.py')
        write_pyc(fs, '/pkg/core.py', make_header(st.st_mtime, st.st_size))
        assert ensure(check_of('/pkg/core.py', st)) is None

    def test_other_source_in_the_header(self, fs):
        """The header decides when the mtime gate let the pyc file through."""
        st = write_source(fs, '/pkg/core.py')
        write_pyc(fs, '/pkg/core.py', make_header(st.st_mtime, st.st_size + 10))
        assert ensure(check_of('/pkg/core.py', st)) == '/pkg/core.py'

    @pytest.mark.parametrize('mtime, size, expected', [
        # The header records the stat of the source
        (PAST, len(CONTENT), None),
        # An other mtime, e.g. the source was edited
        (PAST + 1, len(CONTENT), '/pkg/core.py'),
        (PAST - 1, len(CONTENT), '/pkg/core.py'),
        # An other size, e.g. the source was edited to an other length
        (PAST, len(CONTENT) + 1, '/pkg/core.py'),
        (PAST, len(CONTENT) - 1, '/pkg/core.py'),
    ])
    def test_the_header_holds_the_stat_of_the_source(self, fs, mtime, size, expected):
        """The source is compiled unless the header records its stat."""
        st = write_source(fs, '/pkg/core.py')
        write_pyc(fs, '/pkg/core.py', make_header(mtime, size))
        assert ensure(check_of('/pkg/core.py', st)) == expected

    def test_the_mtime_of_the_header_is_truncated_to_seconds(self, fs):
        """A source modified within the second of its header is fresh."""
        st = write_source(fs, '/pkg/core.py', mtime=PAST + 0.937)
        # The fixture keeps the fraction, otherwise the test proves nothing
        assert int(st.st_mtime) == PAST and st.st_mtime > PAST
        write_pyc(fs, '/pkg/core.py', make_header(PAST, st.st_size))
        assert ensure(check_of('/pkg/core.py', st)) is None

    def test_broken_magic(self, fs):
        """A pyc file of an other interpreter is compiled again."""
        st = write_source(fs, '/pkg/core.py')
        header = make_header(st.st_mtime, st.st_size, magic=b'\x00\x00\x0d\x0a')
        write_pyc(fs, '/pkg/core.py', header)
        assert ensure(check_of('/pkg/core.py', st)) == '/pkg/core.py'

    def test_missing_pyc(self, fs):
        """A pyc file removed between the listing and the ensure is compiled."""
        st = write_source(fs, '/pkg/core.py')
        assert ensure(check_of('/pkg/core.py', st)) == '/pkg/core.py'

    def test_truncated_pyc(self, fs):
        """A pyc file shorter than a header is compiled again."""
        st = write_source(fs, '/pkg/core.py')
        write_file(fs, pyc_path('/pkg/core.py'), make_header(st.st_mtime, st.st_size)[:12])
        assert ensure(check_of('/pkg/core.py', st)) == '/pkg/core.py'

    def test_opens_the_pyc_of_the_item_only(self, fs, monkeypatch):
        """The ensure opens the pyc file it was given, for 16 bytes."""
        spy = OpenSpy()
        monkeypatch.setattr(fresh_module, 'atomic_open', spy)
        st = write_source(fs, '/pkg/core.py')
        write_pyc(fs, '/pkg/core.py', make_header(st.st_mtime, st.st_size))
        ensure(check_of('/pkg/core.py', st))
        assert spy.calls == [(pyc_path('/pkg/core.py'), 'rb', {'buffering': 0})]


def patch_stat_spy(monkeypatch, names):
    """
    Record the file name of every DirEntry whose stat is read.

    Args:
        monkeypatch: The pytest monkeypatch fixture
        names (list[str]): Where the names are appended
    """
    real_scandir = os.scandir

    class SpyEntry:
        def __init__(self, entry):
            self.entry = entry

        def __getattr__(self, name):
            return getattr(self.entry, name)

        def stat(self, *args, **kwargs):
            names.append(self.entry.name)
            return self.entry.stat(*args, **kwargs)

    class SpyCtx:
        def __init__(self, path):
            self.it = real_scandir(path)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            close = getattr(self.it, 'close', None)
            if close is not None:
                close()

        def __iter__(self):
            return (SpyEntry(entry) for entry in self.it)

    def counting(path=0):
        return SpyCtx(path)

    monkeypatch.setattr(os, 'scandir', counting)
    monkeypatch.setattr(fresh_module.os, 'scandir', counting)


class TestNoWastedStat:
    """
    Tests of what a listing stats: only the sources that need it.

    The name of the pyc file of a source is derived from the source name
    alone, so the __pycache__ folder is listed first and a source whose
    pyc file does not exist is answered stale without any stat: its own
    stat can only repeat what the listing of __pycache__ already told.
    """

    @pytest.fixture(autouse=True)
    def _free_stat(self, monkeypatch):
        """Run the Windows branch of the listing on every host."""
        monkeypatch.setattr(fresh_module, 'DIRENT_STAT_IS_FREE', True)

    def test_skips_the_sources_without_a_pyc_file(self, fs, monkeypatch):
        """A cold folder stats no source."""
        calls = []
        for index in range(20):
            write_source(fs, f'/pkg/mod{index}.py')
        patch_stat_spy(monkeypatch, calls)

        items = plan_folder('/pkg', {f'mod{index}.py' for index in range(20)}, PAST)
        assert len(items) == 20
        # No pyc file at all: every item is settled by the listing
        assert all(item[1] is None for item in items)
        # No pyc file exists, so no source was worth a stat
        assert calls == []

    def test_stats_the_sources_that_have_a_pyc_file(self, fs, monkeypatch):
        """A warm folder stats the sources the mtime gate compares."""
        calls = []
        for index in range(6):
            file = f'/pkg/mod{index}.py'
            st = write_source(fs, file)
            write_pyc(fs, file, make_header(st.st_mtime, st.st_size))
        patch_stat_spy(monkeypatch, calls)

        items = plan_folder('/pkg', {f'mod{index}.py' for index in range(6)}, PAST)
        assert len(items) == 6
        # A pyc file that can still be fresh travels to the worker
        assert all(item[1] is not None for item in items)
        # The 6 sources and their 6 pyc files, nothing else: the pyc file
        # of a source is the one thing the mtime gate compares with
        assert len(calls) == 12
        assert sorted(name for name in calls if name.endswith('.py')) == [
            f'mod{index}.py' for index in range(6)
        ]

    def test_a_source_without_a_pyc_file_is_answered_by_the_listing(self, fs):
        """A source of a cold folder is stale without a stat of its own."""
        write_source(fs, '/pkg/core.py')
        assert plan_folder('/pkg', {'core.py'}, PAST) == [stale_item('/pkg/core.py')]

    def test_the_pyc_folder_is_listed_first(self, fs, monkeypatch):
        """The listing of __pycache__ comes before the sources are read."""
        order = []
        real_scandir = os.scandir

        def counting(path=0):
            order.append(path)
            return real_scandir(path)

        monkeypatch.setattr(os, 'scandir', counting)
        monkeypatch.setattr(fresh_module.os, 'scandir', counting)
        write_source(fs, '/pkg/core.py')
        plan_folder('/pkg', {'core.py'}, PAST)
        assert order == ['/pkg/__pycache__', '/pkg']


class TestDeferredStat:
    """
    Tests of a listing that stats no entry, the POSIX path.

    os.scandir() answers the name and the type of an entry on POSIX, and
    the stat of one is an fstatat() syscall on the thread that lists the
    folders. The listing therefore takes no stat there and ensure() reads
    the answers from the descriptor of the pyc file it opens.
    """

    @pytest.fixture(autouse=True)
    def _deferred_stat(self, monkeypatch):
        """Run the POSIX branch of the listing on every host."""
        monkeypatch.setattr(fresh_module, 'DIRENT_STAT_IS_FREE', False)

    def test_no_check_when_the_pyc_file_is_missing(self, fs, monkeypatch):
        """A source without a pyc file is settled by the listing."""
        spy = OpenSpy()
        monkeypatch.setattr(fresh_module, 'atomic_open', spy)
        write_source(fs, '/pkg/core.py')
        items = list(plan(['/pkg/core.py']))
        assert len(items) == 1
        assert items[0][:3] == stale_item('/pkg/core.py')[:3]
        assert spy.calls == []

    def test_the_item_carries_no_stat(self, fs):
        """A pyc file that exists is settled by the worker."""
        st = write_source(fs, '/pkg/core.py')
        write_pyc(fs, '/pkg/core.py', make_header(st.st_mtime, st.st_size))
        assert plan_folder('/pkg', {'core.py'}, PAST) == [deferred_check('/pkg/core.py')]

    def test_missing_source_is_not_reported(self, fs):
        """A file of the scope that is not on the disk is skipped."""
        assert plan_folder('/pkg', {'gone.py'}, PAST) == []

    def test_agrees_with_the_stat_listing(self, fs, monkeypatch):
        """Both listings find the same sources, whatever the state."""
        files = []
        for index in range(12):
            file = f'/pkg/mod{index}.py'
            st = write_source(fs, file)
            files.append(file)
            state = index % 4
            if state == 0:
                # No pyc file: stale at either listing
                continue
            if state == 1:
                # An old pyc file: stale at either listing, from the mtime
                # of the entry on one platform and of the descriptor on the
                # other
                write_pyc(fs, file, make_header(st.st_mtime, st.st_size), mtime=PAST - 100)
                continue
            if state == 2:
                # A header of another revision: stale once read
                write_pyc(fs, file, make_header(st.st_mtime, st.st_size + 10))
                continue
            # The header of the source: fresh once read
            write_pyc(fs, file, make_header(st.st_mtime, st.st_size))

        expected = set(stale_sources(files))
        assert len(expected) == len(files) * 3 // 4
        for free in (True, False):
            monkeypatch.setattr(fresh_module, 'DIRENT_STAT_IS_FREE', free)
            found = [file for file in map(ensure, plan(files)) if file is not None]
            assert set(found) == expected, free

    def test_no_open_when_the_pyc_file_is_missing(self, fs, monkeypatch):
        """The cold tree of a POSIX listing opens no file at all."""
        spy = OpenSpy()
        monkeypatch.setattr(fresh_module, 'atomic_open', spy)
        write_source(fs, '/pkg/core.py')
        write_source(fs, '/pkg/other.py')
        stale = [file for file in map(ensure, plan(['/pkg/core.py', '/pkg/other.py'])) if file]
        assert len(stale) == 2
        assert spy.calls == []

    def test_the_mtime_gate_runs_in_the_ensure(self, fs):
        """A pyc file older than its source is stale, decided by the ensure."""
        st = write_source(fs, '/pkg/core.py')
        write_pyc(fs, '/pkg/core.py', make_header(st.st_mtime, st.st_size), mtime=PAST - 100)
        assert ensure(deferred_check('/pkg/core.py')) == '/pkg/core.py'

    def test_missing_pyc_in_the_ensure(self, fs):
        """A pyc file removed after the listing is stale."""
        write_source(fs, '/pkg/core.py')
        assert ensure(deferred_check('/pkg/core.py')) == '/pkg/core.py'

    def test_fresh_in_the_ensure(self, fs):
        """A pyc file that records its source needs no compilation."""
        st = write_source(fs, '/pkg/core.py')
        write_pyc(fs, '/pkg/core.py', make_header(st.st_mtime, st.st_size))
        assert ensure(deferred_check('/pkg/core.py')) is None

    def test_source_removed_after_the_listing(self, fs):
        """A source that is gone has nothing to compile."""
        assert ensure(deferred_check('/pkg/gone.py')) is None

    def test_opens_the_pyc_file_once(self, fs, monkeypatch):
        """The ensure opens the pyc file once and reads the stat of the fd."""
        spy = OpenSpy()
        monkeypatch.setattr(fresh_module, 'atomic_open', spy)
        st = write_source(fs, '/pkg/core.py')
        write_pyc(fs, '/pkg/core.py', make_header(st.st_mtime, st.st_size))
        assert ensure(deferred_check('/pkg/core.py')) is None
        # No fstatat() by path: one open, whose descriptor answers the stat
        assert spy.calls == [(pyc_path('/pkg/core.py'), 'rb', {'buffering': 0})]

    def test_source_dated_in_the_future(self, fs):
        """A source whose clock is ahead still reuses its pyc file."""
        future = time.time() + 1000
        st = write_source(fs, '/pkg/core.py', mtime=future)
        write_pyc(fs, '/pkg/core.py', make_header(st.st_mtime, st.st_size))
        assert ensure(deferred_check('/pkg/core.py', now=time.time())) is None

    def test_the_clock_is_read_before_the_descriptor(self, fs, monkeypatch):
        """A source dated in the future costs no fstat of its pyc file."""
        calls = []
        real_fstat = os.fstat

        def counting_fstat(fd):
            calls.append(fd)
            return real_fstat(fd)

        future = time.time() + 1000
        st = write_source(fs, '/pkg/core.py', mtime=future)
        write_pyc(fs, '/pkg/core.py', make_header(st.st_mtime, st.st_size))
        monkeypatch.setattr(os, 'fstat', counting_fstat)
        monkeypatch.setattr(fresh_module.os, 'fstat', counting_fstat)
        # The header alone decides, the gate can not apply to a source
        # dated after now, so the descriptor is never stat-ed
        assert ensure(deferred_check('/pkg/core.py', now=time.time())) is None
        assert calls == []

    def test_the_descriptor_is_stat_ed_when_the_clock_allows(self, fs, monkeypatch):
        """A source of the past is gated by the mtime of the pyc file."""
        calls = []
        real_fstat = os.fstat

        def counting_fstat(fd):
            calls.append(fd)
            return real_fstat(fd)

        st = write_source(fs, '/pkg/core.py')
        write_pyc(fs, '/pkg/core.py', make_header(st.st_mtime, st.st_size), mtime=PAST - 100)
        monkeypatch.setattr(os, 'fstat', counting_fstat)
        monkeypatch.setattr(fresh_module.os, 'fstat', counting_fstat)
        assert ensure(deferred_check('/pkg/core.py')) == '/pkg/core.py'
        assert len(calls) == 1

    def test_the_stat_of_the_source_is_read_once(self, fs, monkeypatch):
        """The gate reads the source by path, the pyc file by descriptor."""
        stats = []
        real_stat = os.stat

        def counting_stat(path, *args, **kwargs):
            stats.append(path)
            return real_stat(path, *args, **kwargs)

        st = write_source(fs, '/pkg/core.py')
        write_pyc(fs, '/pkg/core.py', make_header(st.st_mtime, st.st_size))
        monkeypatch.setattr(os, 'stat', counting_stat)
        monkeypatch.setattr(fresh_module.os, 'stat', counting_stat)
        assert ensure(deferred_check('/pkg/core.py')) is None
        # The source by path, and no fstatat() on the pyc file: its
        # descriptor answered, which is the point of opening it first
        assert stats == ['/pkg/core.py']

    def test_a_read_that_fails_compiles_the_source(self, fs, monkeypatch):
        """A pyc file that can not be read is stale, never an error."""
        st = write_source(fs, '/pkg/core.py')
        write_pyc(fs, '/pkg/core.py', make_header(st.st_mtime, st.st_size))

        class BrokenFile:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def fileno(self):
                raise OSError(13, 'Permission denied')

            def read(self, size):
                raise OSError(13, 'Permission denied')

        monkeypatch.setattr(fresh_module, 'atomic_open', lambda *a, **k: BrokenFile())
        assert ensure(deferred_check('/pkg/core.py')) == '/pkg/core.py'
