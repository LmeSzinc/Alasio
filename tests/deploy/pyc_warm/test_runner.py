"""
Tests of the warmup run, see alasio/deploy/pyc_warm/runner.py.
"""
import os
from importlib.util import MAGIC_NUMBER

import pytest

from alasio.deploy.pyc_warm import runner
from alasio.deploy.pyc_warm.fresh import stale_sources
from alasio.deploy.pyc_warm.runner import warm
from alasio.deploy.simple_pip.pyc import pyc_path
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401

# The folders of the tests
SITE = '/site'
MOD = '/mod'
# Content of the sources of the tests, 10 bytes
CONTENT = b'VALUE = 1\n'
# A mtime in the past, the clock of the test run is later than the file
PAST = 1700000000


@pytest.fixture(autouse=True)
def capture():
    """
    Collect the lines of a run.

    Every test of the module takes it: the report of a run is logged, and a
    test must neither write the log file of the repository nor read it.

    Yields:
        CaptureWriter: The writer that holds the lines of the run
    """
    with logger.mock_capture_writer() as capture:
        yield capture


def write(fs, path, content=b''):
    """
    Create a file of the fake filesystem.

    Args:
        fs (FakeFilesystem): The fake filesystem
        path (str): Path of the file
        content (bytes | str): Content of the file

    Returns:
        str: Path of the file
    """
    fs.create_file(path, contents=content)
    return path


def make_header(mtime, size):
    """
    Build the header of a timestamp based pyc file.

    Args:
        mtime (float): mtime of the source to record
        size (int): Size of the source to record

    Returns:
        bytes: The 16 bytes header
    """
    return b''.join((
        MAGIC_NUMBER,
        (0).to_bytes(4, 'little'),
        int(mtime).to_bytes(4, 'little'),
        size.to_bytes(4, 'little'),
    ))


def write_pyc(fs, py_file, header, mtime=None):
    """
    Create the pyc file of a source of the fake filesystem.

    Args:
        fs (FakeFilesystem): The fake filesystem
        py_file (str): Path of the source file
        header (bytes): Header of the pyc file
        mtime (float): Modification time of the pyc file. Defaults to
            None, the time of the write

    Returns:
        str: Path of the pyc file
    """
    pyc = pyc_path(py_file)
    os.makedirs(os.path.dirname(pyc), exist_ok=True)
    write(fs, pyc, header)
    if mtime is not None:
        os.utime(pyc, (mtime, mtime))
    return pyc


def make_dist(fs, name, paths, version='1.0'):
    """
    Create the .dist-info folder of a distribution, with a RECORD.

    Args:
        fs (FakeFilesystem): The fake filesystem
        name (str): Name of the distribution
        paths (list[str]): Paths of the RECORD rows
        version (str): Version of the distribution

    Returns:
        str: Posix path of the .dist-info folder
    """
    dist_info = f'{SITE}/{name}-{version}.dist-info'
    write(fs, f'{dist_info}/RECORD', ''.join(f'{path},sha256=x,1\n' for path in paths).encode())
    return dist_info


class FakeCompiler:
    """
    A compile_file() stand-in: records the sources and writes a valid cache.

    Attributes:
        compiled (list[str]): Sources it wrote a cache for
        fail (set[str]): Sources it answers an error for
    """

    def __init__(self):
        self.compiled = []
        self.fail = set()

    def __call__(self, file):
        """
        Args:
            file (str): Path of the source

        Returns:
            str: Empty when the cache was written, an error otherwise
        """
        if file in self.fail:
            return f'OSError: cannot write {file}'
        self.compiled.append(file)
        st = os.stat(file)
        pyc = pyc_path(file)
        os.makedirs(os.path.dirname(pyc), exist_ok=True)
        with open(pyc, 'wb') as f:
            f.write(make_header(st.st_mtime, st.st_size))
        # A real compilation replaces the pyc file with a freshly written
        # one, see importlib._bootstrap_external._write_atomic: the new
        # file dates from the write. The fake filesystem does not touch the
        # mtime of a file written in place, so the write states it here,
        # otherwise a second run would still see a pyc older than its source
        os.utime(pyc, None)
        return ''


def make_site(fs):
    """
    A site-packages with one distribution per tier.

    Args:
        fs (FakeFilesystem): The fake filesystem
    """
    # A pack distribution, its ledger has no index pack: the RECORD is read
    fs.create_dir(f'{SITE}/.pack/trio')
    make_dist(fs, 'trio', ['trio/_abc.py'])
    write(fs, f'{SITE}/trio/_abc.py', b'VALUE = 1\n')
    # A common distribution
    make_dist(fs, 'msgspec', ['msgspec/__init__.py'])
    write(fs, f'{SITE}/msgspec/__init__.py', b'VALUE = 2\n')
    # A pin of the requirements file only
    make_dist(fs, 'sympy', ['sympy/__init__.py'])
    write(fs, f'{SITE}/sympy/__init__.py', b'VALUE = 3\n')
    write(fs, f'{MOD}/requirements.txt', 'trio==0.22.0\nmsgspec==0.18.0\nsympy==1.12\n')


def make_mixed_tree(fs, root, folders=6, each=12):
    """
    A tree whose sources are in every state the scan can meet.

    Every fourth source has no pyc file, every fourth has one older than
    itself, every fourth has one that still needs its header read whatever
    the answer, and the last quarter is fresh only if the header is read.

    Args:
        fs (FakeFilesystem): The fake filesystem
        root (str): Posix path of the tree
        folders (int): Folders of the tree
        each (int): Sources of a folder

    Returns:
        list[str]: Posix paths of the sources
    """
    files = []
    for folder in range(folders):
        for index in range(each):
            file = f'{root}/mod{folder}/src{index}.py'
            write(fs, file, CONTENT)
            # A source dated before everything, so the mtime gate lets the
            # pyc file through and the header decides
            os.utime(file, (PAST, PAST))
            files.append(file)
            state = index % 4
            pyc = pyc_path(file)
            if state == 0:
                # No pyc file: stale at the listing
                continue
            if state == 1:
                # A pyc file older than its source: stale at the listing
                write(fs, pyc, make_header(PAST, len(CONTENT)))
                os.utime(pyc, (PAST - 100, PAST - 100))
                continue
            if state == 2:
                # A header of another revision: stale once read
                write(fs, pyc, make_header(PAST, len(CONTENT) + 10))
                continue
            # The header of the source: fresh once read
            write(fs, pyc, make_header(PAST, len(CONTENT)))
    return files


class TestThreadedScan:
    """
    Tests of the scan that runs in a pool of threads.
    """

    def test_agrees_with_the_serial_scan(self, fs, monkeypatch):
        """The pool finds exactly what one thread finds, in every state."""
        files = make_mixed_tree(fs, MOD)
        expected = stale_sources(files)
        # Three quarters are stale: no pyc file, a pyc file older than its
        # source, or a header of another revision; the fourth quarter is
        # fresh, and only its header tells so
        assert len(expected) == len(files) * 3 // 4

        compiler = FakeCompiler()
        monkeypatch.setattr(runner, 'compile_file', compiler)
        report = warm(site_packages=[], mod_roots=[MOD])
        assert report.tiers[0].sources == len(files)
        assert report.tiers[0].stale == len(expected)
        assert sorted(compiler.compiled) == sorted(expected)
        assert (report.compiled, report.failed) == (len(expected), 0)

    def test_second_run_compiles_nothing(self, fs, monkeypatch):
        """The pool leaves a tree that a second run finds warm."""
        make_mixed_tree(fs, MOD)
        monkeypatch.setattr(runner, 'compile_file', FakeCompiler())
        first = warm(site_packages=[], mod_roots=[MOD])
        second = warm(site_packages=[], mod_roots=[MOD])
        assert first.tiers[0].stale == first.tiers[0].sources * 3 // 4
        assert second.tiers[0].stale == 0
        assert second.compiled == 0

    def test_every_source_reaches_a_task(self, fs, monkeypatch):
        """A batch boundary never drops a source."""
        # More than one batch of COMPILE_BATCH sources, all stale
        count = runner.COMPILE_BATCH * 2 + 5
        files = [f'{MOD}/module/mod{index:03d}.py' for index in range(count)]
        for file in files:
            write(fs, file, CONTENT)
        compiler = FakeCompiler()
        monkeypatch.setattr(runner, 'compile_file', compiler)

        report = warm(site_packages=[], mod_roots=[MOD])
        assert report.tiers[0].stale == count
        assert sorted(compiler.compiled) == sorted(files)

    def test_errors_of_every_batch_are_capped(self, fs, monkeypatch):
        """Failures of several batches keep the samples of the tier only."""
        count = runner.COMPILE_BATCH * 3
        files = [f'{MOD}/module/mod{index:03d}.py' for index in range(count)]
        for file in files:
            write(fs, file, CONTENT)
        compiler = FakeCompiler()
        compiler.fail = set(files)
        monkeypatch.setattr(runner, 'compile_file', compiler)

        report = warm(site_packages=[], mod_roots=[MOD])
        assert report.failed == count
        assert len(report.tiers[0].errors) == runner.ERROR_SAMPLES


class TestWarm:
    """
    Tests for warm().
    """

    def test_site_packages(self, fs, monkeypatch):
        """The three tiers of an environment compile their own sources."""
        compiler = FakeCompiler()
        monkeypatch.setattr(runner, 'compile_file', compiler)
        make_site(fs)

        report = warm(site_packages=[SITE], requirements_file=f'{MOD}/requirements.txt')
        assert [tier.name for tier in report.tiers] == ['pack', 'common', 'lock']
        assert [tier.sources for tier in report.tiers] == [1, 1, 1]
        # The tiers share one pool, so the order the batches reach it is
        # not the order of the tiers: every source is compiled once
        assert sorted(compiler.compiled) == [
            f'{SITE}/msgspec/__init__.py',
            f'{SITE}/sympy/__init__.py',
            f'{SITE}/trio/_abc.py',
        ]
        assert (report.compiled, report.failed, report.stale) == (3, 0, 3)

    def test_mods(self, fs, monkeypatch):
        """A mod tree compiles its own sources, after the environment."""
        compiler = FakeCompiler()
        monkeypatch.setattr(runner, 'compile_file', compiler)
        write(fs, f'{MOD}/module/main.py', b'VALUE = 1\n')
        write(fs, f'{MOD}/module/data.json', b'{}\n')

        report = warm(site_packages=[], mod_roots=[MOD])
        assert [tier.name for tier in report.tiers] == ['mod mod']
        assert [tier.sources for tier in report.tiers] == [1]
        assert compiler.compiled == [f'{MOD}/module/main.py']

    def test_second_run_compiles_nothing(self, fs, monkeypatch):
        """A warmed environment is only scanned again."""
        compiler = FakeCompiler()
        monkeypatch.setattr(runner, 'compile_file', compiler)
        make_site(fs)

        first = warm(site_packages=[SITE], requirements_file=f'{MOD}/requirements.txt')
        second = warm(site_packages=[SITE], requirements_file=f'{MOD}/requirements.txt')
        assert (first.compiled, second.compiled, second.stale) == (3, 0, 0)
        assert [tier.sources for tier in second.tiers] == [1, 1, 1]

    def test_dry_run(self, fs, monkeypatch, capture):
        """A dry run lists the sources and compiles nothing."""
        compiler = FakeCompiler()
        monkeypatch.setattr(runner, 'compile_file', compiler)
        make_site(fs)

        report = warm(
            site_packages=[SITE], requirements_file=f'{MOD}/requirements.txt',
            dry_run=True,
        )
        assert compiler.compiled == []
        assert report.stale == 3
        assert report.compiled == 0
        assert capture.concat() == f"""\
INFO |   {SITE}/trio/_abc.py
INFO | pack    : 1 sources, 1 stale, 0 compiled, 0 failed, {report.tiers[0].seconds:.2f}s
INFO |   {SITE}/msgspec/__init__.py
INFO | common  : 1 sources, 1 stale, 0 compiled, 0 failed, {report.tiers[1].seconds:.2f}s
INFO |   {SITE}/sympy/__init__.py
INFO | lock    : 1 sources, 1 stale, 0 compiled, 0 failed, {report.tiers[2].seconds:.2f}s
"""

    def test_a_warm_run_logs_one_line_per_tier(self, fs, monkeypatch, capture):
        """A run that compiles nothing still reports every tier."""
        monkeypatch.setattr(runner, 'compile_file', FakeCompiler())
        make_site(fs)

        report = warm(site_packages=[SITE], requirements_file=f'{MOD}/requirements.txt')
        assert capture.concat() == f"""\
INFO | pack    : 1 sources, 1 stale, 1 compiled, 0 failed, {report.tiers[0].seconds:.2f}s
INFO | common  : 1 sources, 1 stale, 1 compiled, 0 failed, {report.tiers[1].seconds:.2f}s
INFO | lock    : 1 sources, 1 stale, 1 compiled, 0 failed, {report.tiers[2].seconds:.2f}s
"""

    def test_failure(self, fs, monkeypatch):
        """A source that can not be compiled is counted, never raised."""
        compiler = FakeCompiler()
        compiler.fail = {f'{SITE}/msgspec/__init__.py'}
        monkeypatch.setattr(runner, 'compile_file', compiler)
        make_site(fs)

        report = warm(site_packages=[SITE], requirements_file=f'{MOD}/requirements.txt')
        assert (report.compiled, report.failed) == (2, 1)
        assert report.tiers[1].errors == [
            f'{SITE}/msgspec/__init__.py: OSError: cannot write {SITE}/msgspec/__init__.py',
        ]

    def test_errors_capped(self, fs, monkeypatch):
        """Only a few failure messages are kept, the count is exact."""
        files = [f'{MOD}/module/mod{index}.py' for index in range(5)]
        for file in files:
            write(fs, file, b'VALUE = 1\n')
        compiler = FakeCompiler()
        compiler.fail = set(files)
        monkeypatch.setattr(runner, 'compile_file', compiler)

        report = warm(site_packages=[], mod_roots=[MOD])
        assert report.failed == 5
        assert len(report.tiers[0].errors) == runner.ERROR_SAMPLES

    def test_unresolved(self, fs, monkeypatch):
        """A name the scope can not resolve is reported, not fatal."""
        monkeypatch.setattr(runner, 'compile_file', FakeCompiler())
        make_site(fs)

        report = warm(site_packages=[SITE], requirements_file=f'{MOD}/requirements.txt')
        assert 'numpy' in report.unresolved
        assert 'trio' not in report.unresolved

    def test_to_dict(self, fs, monkeypatch):
        """The report serializes to the JSON form of the parent."""
        monkeypatch.setattr(runner, 'compile_file', FakeCompiler())
        write(fs, f'{MOD}/module/main.py', b'VALUE = 1\n')

        data = warm(site_packages=[], mod_roots=[MOD]).to_dict()
        for tier in data['tiers']:
            tier.pop('seconds')
        assert data == {
            'tiers': [{
                'name': 'mod mod',
                'folder': MOD,
                'sources': 1,
                'stale': 1,
                'compiled': 1,
                'failed': 0,
                'errors': [],
            }],
            'unresolved': [],
            'compiled': 1,
            'failed': 0,
            'stale': 1,
            'seconds': data['seconds'],
        }


class TestWork:
    """
    Tests for _work(): what a batch of one task does.
    """

    def test_mixed_batch(self, fs, monkeypatch):
        """A batch holds both kinds of item, and both are handled."""
        compiler = FakeCompiler()
        monkeypatch.setattr(runner, 'compile_file', compiler)
        # One source the listing settled stale, one whose pyc file is fresh,
        # and one whose pyc file records another revision
        stale = write(fs, '/mod/mod/stale.py', b'VALUE = 1\n')
        fresh = write(fs, '/mod/mod/fresh.py', b'VALUE = 2\n')
        edited = write(fs, '/mod/mod/edited.py', b'VALUE = 3\n')
        for file, size in ((fresh, _stat_of(fresh)[1]), (edited, 99)):
            st = os.stat(file)
            write_pyc(fs, file, make_header(st.st_mtime, size))

        batch = (
            (stale, None, None, PAST),
            (fresh, pyc_path(fresh), _stat_of(fresh), PAST),
            (edited, pyc_path(edited), _stat_of(edited), PAST),
        )
        answered, compiled, failed, errors = runner._work(batch)
        assert (answered, compiled, failed, errors) == (2, 2, 0, [])
        assert sorted(compiler.compiled) == [edited, stale]

    def test_nothing_stale(self, fs, monkeypatch):
        """A batch of fresh sources compiles nothing."""
        compiler = FakeCompiler()
        monkeypatch.setattr(runner, 'compile_file', compiler)
        file = write(fs, '/mod/mod/fresh.py', b'VALUE = 1\n')
        st = os.stat(file)
        write_pyc(fs, file, make_header(st.st_mtime, st.st_size))
        batch = ((file, pyc_path(file), (st.st_mtime, st.st_size), PAST),)
        assert runner._work(batch) == (0, 0, 0, [])
        assert compiler.compiled == []

    def test_empty_batch(self):
        """An empty batch does nothing."""
        assert runner._work(()) == (0, 0, 0, [])

    def test_failure_of_one_item(self, fs, monkeypatch):
        """A source that can not be compiled is counted, never raised."""
        file = write(fs, '/mod/mod/bad.py', b'VALUE = 1\n')
        compiler = FakeCompiler()
        compiler.fail = {file}
        monkeypatch.setattr(runner, 'compile_file', compiler)
        answered, compiled, failed, errors = runner._work(((file, None, None, PAST),))
        assert (answered, compiled, failed) == (1, 0, 1)
        assert errors == [f'{file}: OSError: cannot write {file}']


def _stat_of(file):
    """
    The stat of a source, as the pair the work item carries.

    Args:
        file (str): Path of the source

    Returns:
        tuple[float, int]: mtime and size
    """
    st = os.stat(file)
    return st.st_mtime, st.st_size


class TestOnePool:
    """
    Tests of the pool of a run: one for every tier.
    """

    def test_one_pool_for_the_run(self, fs, monkeypatch):
        """Every tier is served by the same pool, created once."""
        make_site(fs)
        monkeypatch.setattr(runner, 'compile_file', FakeCompiler())
        made = []
        real = runner.ThreadPool

        def counting(pool_size=8):
            pool = real(pool_size=pool_size)
            made.append(pool)
            return pool

        monkeypatch.setattr(runner, 'ThreadPool', counting)
        report = warm(site_packages=[SITE], requirements_file=f'{MOD}/requirements.txt')
        assert len(made) == 1
        assert [tier.name for tier in report.tiers] == ['pack', 'common', 'lock']

    def test_no_pool_on_a_dry_run(self, fs, monkeypatch):
        """A dry run compiles nothing, so it creates no pool."""
        monkeypatch.setattr(
            runner, 'ThreadPool',
            lambda pool_size=8: pytest.fail('a dry run created a pool'),
        )
        write(fs, f'{MOD}/module/main.py', b'VALUE = 1\n')
        report = warm(site_packages=[], mod_roots=[MOD], dry_run=True)
        assert report.compiled == 0
        assert report.stale == 1
