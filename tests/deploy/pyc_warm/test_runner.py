"""
Tests of the warmup run, see alasio/deploy/pyc_warm/runner.py.
"""
import os
from importlib.util import MAGIC_NUMBER
from types import SimpleNamespace

import pytest

from alasio.deploy.pyc_warm import runner, scope as scope_module
from alasio.deploy.pyc_warm.fresh import stale_sources
from alasio.deploy.pyc_warm.runner import warm
from alasio.deploy.pyc_warm.scope import TIER_COMMON, TIER_LOCK, TIER_MOD, TIER_PACK, Scope, ScopeResult
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


def fake_mod(name='mod', root=MOD, requirements_file='requirements.txt'):
    """
    A mod as the scope reads one.

    Args:
        name (str): Name of the mod
        root (str): Posix path of the mod tree
        requirements_file (str): Declared location of the pins of the mod

    Returns:
        SimpleNamespace: The mod
    """
    return SimpleNamespace(
        name=name,
        root=root,
        entry=SimpleNamespace(requirements_file=requirements_file),
    )


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
    A site-packages with one distribution per tier and one mod.

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


def site_scope(fs, monkeypatch, common=('msgspec',)):
    """
    The scope of the site-packages of the tests, with one mod.

    The common tier is the list of the scope module, one unit per name: the
    tests narrow it, so the report of a run stays readable.

    Args:
        fs (FakeFilesystem): The fake filesystem
        monkeypatch: The pytest monkeypatch fixture
        common (tuple[str]): Names of the common tier

    Returns:
        Scope: The scope
    """
    make_site(fs)
    # The scope reads the list at call time, so the tier of a test is the
    # tier of its own names
    monkeypatch.setattr(scope_module, 'COMMON_DISTS', common)
    return Scope(site_packages=[SITE], mods={'mod': fake_mod()})


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


class FakeScope:
    """
    A scope of the tests: the results of a run, in the order given.

    Attributes:
        results (list[ScopeResult]): The units of the run
    """

    def __init__(self, results):
        """
        Args:
            results (list[ScopeResult]): The units of the run
        """
        self.results = results

    def tiers(self):
        """
        Yields:
            ScopeResult: The units of the run
        """
        yield from self.results


def sources_of(fs, count, folder=f'{MOD}/module'):
    """
    Create `count` sources of a folder and answer their paths.

    Args:
        fs (FakeFilesystem): The fake filesystem
        count (int): Number of sources
        folder (str): Folder of the sources

    Returns:
        list[str]: Posix paths of the sources
    """
    return [
        write(fs, f'{folder}/mod{index:03d}.py', CONTENT)
        for index in range(count)
    ]


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
        report = warm(FakeScope([ScopeResult(TIER_MOD, 'mod', files)]))
        assert report.results[0].sources == len(files)
        assert report.results[0].stale == len(expected)
        assert sorted(compiler.compiled) == sorted(expected)
        assert (report.compiled, report.failed) == (len(expected), 0)

    def test_second_run_compiles_nothing(self, fs, monkeypatch):
        """The pool leaves a tree that a second run finds warm."""
        files = make_mixed_tree(fs, MOD)
        monkeypatch.setattr(runner, 'compile_file', FakeCompiler())
        scope = FakeScope([ScopeResult(TIER_MOD, 'mod', files)])
        first = warm(scope)
        second = warm(scope)
        assert first.results[0].stale == first.results[0].sources * 3 // 4
        assert second.results[0].stale == 0
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

        report = warm(FakeScope([ScopeResult(TIER_MOD, 'mod', files)]))
        assert report.results[0].stale == count
        assert sorted(compiler.compiled) == sorted(files)

    def test_errors_of_every_batch_are_capped(self, fs, monkeypatch):
        """Failures of several batches keep the samples of the unit only."""
        count = runner.COMPILE_BATCH * 3
        files = [f'{MOD}/module/mod{index:03d}.py' for index in range(count)]
        for file in files:
            write(fs, file, CONTENT)
        compiler = FakeCompiler()
        compiler.fail = set(files)
        monkeypatch.setattr(runner, 'compile_file', compiler)

        report = warm(FakeScope([ScopeResult(TIER_MOD, 'mod', files)]))
        assert report.failed == count
        assert len(report.results[0].errors) == runner.ERROR_SAMPLES


class TestUnits:
    """
    Tests of the units of a run: one ScopeResult is one line of the report.
    """

    def test_one_line_per_unit(self, fs, monkeypatch, capture):
        """The scope is answered unit by unit, each unit as one line."""
        one = sources_of(fs, 2, f'{MOD}/one')
        two = sources_of(fs, 3, f'{MOD}/two')
        monkeypatch.setattr(runner, 'compile_file', FakeCompiler())
        report = warm(FakeScope([
            ScopeResult(TIER_PACK, 'trio', one),
            # A unit the scope asked for and did not find
            ScopeResult(TIER_PACK, 'gone', []),
            ScopeResult(TIER_MOD, 'mod', two),
        ]))

        assert [(result.tier, result.target) for result in report.results] == [
            (TIER_PACK, 'trio'), (TIER_PACK, 'gone'), (TIER_MOD, 'mod'),
        ]
        assert [result.sources for result in report.results] == [2, 0, 3]
        assert capture.concat() == ''.join(
            f'INFO | {result.line()}\n' for result in report.results
        )
        assert capture.concat() == f"""\
INFO | pack/trio: 2 sources, 2 stale, 2 compiled, 0 failed, {report.results[0].seconds:.2f}s
INFO | pack/gone: 0 sources, 0 stale, 0 compiled, 0 failed, {report.results[1].seconds:.2f}s
INFO | mod/mod: 3 sources, 3 stale, 3 compiled, 0 failed, {report.results[2].seconds:.2f}s
"""

    def test_an_empty_unit_compiles_nothing(self, fs, monkeypatch):
        """A unit without sources is reported, never compiled."""
        monkeypatch.setattr(
            runner, 'ensure',
            lambda item: pytest.fail('an empty unit planned a source'),
        )
        report = warm(FakeScope([ScopeResult(TIER_COMMON, 'numpy', [])]))
        assert len(report.results) == 1
        assert report.results[0].sources == 0
        assert report.compiled == 0

    def test_one_pool_for_the_whole_run(self, fs, monkeypatch):
        """Every unit is served by the same pool, created once."""
        monkeypatch.setattr(runner, 'compile_file', FakeCompiler())
        made = []
        real = runner.ThreadPool

        def counting(pool_size=8):
            pool = real(pool_size=pool_size)
            made.append(pool)
            return pool

        monkeypatch.setattr(runner, 'ThreadPool', counting)
        warm(FakeScope([
            ScopeResult(TIER_PACK, 'one', sources_of(fs, 2, f'{MOD}/one')),
            ScopeResult(TIER_PACK, 'two', sources_of(fs, 2, f'{MOD}/two')),
        ]))
        assert len(made) == 1

    def test_no_pool_on_a_dry_run(self, fs, monkeypatch):
        """A dry run compiles nothing, so it creates no pool."""
        monkeypatch.setattr(
            runner, 'ThreadPool',
            lambda pool_size=8: pytest.fail('a dry run created a pool'),
        )
        files = sources_of(fs, 3)
        report = warm(FakeScope([ScopeResult(TIER_MOD, 'mod', files)]), dry_run=True)
        assert report.compiled == 0
        assert report.stale == 3

    def test_a_batch_is_cut_from_the_unit(self, fs, monkeypatch):
        """The batches of a unit do not carry the sources of another."""
        one = sources_of(fs, 20, f'{MOD}/one')
        two = sources_of(fs, 20, f'{MOD}/two')
        batches = []
        real = runner._work

        def recording(batch):
            batches.append([item[0] for item in batch])
            return real(batch)

        monkeypatch.setattr(runner, '_work', recording)
        monkeypatch.setattr(runner, 'compile_file', FakeCompiler())
        warm(FakeScope([
            ScopeResult(TIER_COMMON, 'one', one),
            ScopeResult(TIER_COMMON, 'two', two),
        ]))
        # A unit of 20 sources is one batch, and the second unit is another
        assert [sorted(batch) for batch in batches] == [sorted(one), sorted(two)]

    def test_the_pool_is_fed_while_the_scope_still_reads(self, fs, monkeypatch):
        """The batches of a unit are submitted before the next unit is read."""
        count = runner.COMPILE_BATCH * 2
        files = [f'{MOD}/module/mod{index:03d}.py' for index in range(count)]
        for file in files:
            write(fs, file, CONTENT)
        pools = []

        class FakeJob:
            def __init__(self, value):
                self.value = value

            def get(self):
                return self.value

        class FakePool:
            """A pool that answers a batch where it was submitted."""

            def __init__(self, pool_size=4):
                self.submitted = 0
                pools.append(self)

            def start_thread_soon(self, fn, *args, **kwargs):
                self.submitted += 1
                return FakeJob(fn(*args, **kwargs))

        monkeypatch.setattr(runner, 'ThreadPool', FakePool)
        monkeypatch.setattr(runner, 'compile_file', FakeCompiler())
        submitted = []

        class Scope:
            """A scope that looks at the pool before it answers a unit."""

            def tiers(self):
                yield ScopeResult(TIER_COMMON, 'one', files)
                # A run that reads the whole scope before it dispatches has
                # submitted nothing by the time the next unit is read
                submitted.append(pools[0].submitted)
                yield ScopeResult(TIER_COMMON, 'two', [])

        report = warm(Scope())
        # Both batches of the 64 sources went out while the scope was still
        # between its two units
        assert submitted == [2]
        assert report.results[0].sources == count
        assert report.unresolved == ['two']


class TestWarm:
    """
    Tests for warm().
    """

    def test_site_packages(self, fs, monkeypatch):
        """The tiers of an environment compile their own sources."""
        compiler = FakeCompiler()
        monkeypatch.setattr(runner, 'compile_file', compiler)

        report = warm(site_scope(fs, monkeypatch))
        # Every source is compiled once, by the unit that owns it
        assert sorted(compiler.compiled) == [
            f'{SITE}/msgspec/__init__.py',
            f'{SITE}/sympy/__init__.py',
            f'{SITE}/trio/_abc.py',
        ]
        assert (report.compiled, report.failed, report.stale) == (3, 0, 3)

    def test_every_unit_of_the_scope_is_reported(self, fs, monkeypatch):
        """The report follows the scope, the units that found nothing included."""
        monkeypatch.setattr(runner, 'compile_file', FakeCompiler())
        report = warm(site_scope(fs, monkeypatch))
        resolved = [result.target for result in report.results if result.sources]
        # trio is a pack distribution and a pin, msgspec a common name and a
        # pin, sympy a pin only: a name is reported by the unit that found it
        assert resolved == ['trio', 'msgspec', 'sympy']
        assert [result.tier for result in report.results if result.sources] == [
            TIER_PACK, TIER_COMMON, TIER_LOCK,
        ]

    def test_mods(self, fs, monkeypatch):
        """A mod tree compiles its own sources, after the environment."""
        compiler = FakeCompiler()
        monkeypatch.setattr(runner, 'compile_file', compiler)
        write(fs, f'{MOD}/module/main.py', b'VALUE = 1\n')
        write(fs, f'{MOD}/module/data.json', b'{}\n')

        report = warm(FakeScope([ScopeResult(TIER_MOD, 'mod', [f'{MOD}/module/main.py'])]))

        assert [(result.tier, result.sources) for result in report.results] == [(TIER_MOD, 1)]
        assert compiler.compiled == [f'{MOD}/module/main.py']

    def test_second_run_compiles_nothing(self, fs, monkeypatch):
        """A warmed environment is only scanned again."""
        compiler = FakeCompiler()
        monkeypatch.setattr(runner, 'compile_file', compiler)
        scope = site_scope(fs, monkeypatch)

        first = warm(scope)
        second = warm(scope)
        assert (first.compiled, second.compiled, second.stale) == (3, 0, 0)
        assert [result.sources for result in second.results if result.sources] == [1, 1, 1]

    def test_dry_run(self, fs, monkeypatch, capture):
        """A dry run lists the sources and compiles nothing."""
        compiler = FakeCompiler()
        monkeypatch.setattr(runner, 'compile_file', compiler)

        report = warm(site_scope(fs, monkeypatch), dry_run=True)
        assert compiler.compiled == []
        assert report.stale == 3
        assert report.compiled == 0
        assert capture.concat() == f"""\
INFO |   {SITE}/trio/_abc.py
INFO | pack/trio: 1 sources, 1 stale, 0 compiled, 0 failed, {report.results[0].seconds:.2f}s
INFO |   {SITE}/msgspec/__init__.py
INFO | common/msgspec: 1 sources, 1 stale, 0 compiled, 0 failed, {report.results[1].seconds:.2f}s
INFO |   {SITE}/sympy/__init__.py
INFO | lock/sympy: 1 sources, 1 stale, 0 compiled, 0 failed, {report.results[2].seconds:.2f}s
INFO | mod/mod: 0 sources, 0 stale, 0 compiled, 0 failed, {report.results[3].seconds:.2f}s
"""

    def test_failure(self, fs, monkeypatch):
        """A source that can not be compiled is counted, never raised."""
        compiler = FakeCompiler()
        compiler.fail = {f'{SITE}/msgspec/__init__.py'}
        monkeypatch.setattr(runner, 'compile_file', compiler)

        report = warm(site_scope(fs, monkeypatch))
        assert (report.compiled, report.failed) == (2, 1)
        failed = [result for result in report.results if result.failed]
        assert len(failed) == 1
        assert failed[0].target == 'msgspec'
        assert failed[0].errors == [
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

        report = warm(FakeScope([ScopeResult(TIER_MOD, 'mod', files)]))
        assert report.failed == 5
        assert len(report.results[0].errors) == runner.ERROR_SAMPLES

    def test_unresolved(self, fs, monkeypatch):
        """A unit the scope could not fill is reported, not fatal."""
        monkeypatch.setattr(runner, 'compile_file', FakeCompiler())
        report = warm(site_scope(fs, monkeypatch))

        # The mod has no ledger, the pins that are not installed are empty
        assert report.unresolved == ['mod']
        assert all(result.sources == 0 for result in report.results if result.target == 'mod')

    def test_to_dict(self, fs, monkeypatch):
        """The report serializes to the JSON form of the parent."""
        monkeypatch.setattr(runner, 'compile_file', FakeCompiler())
        write(fs, f'{MOD}/module/main.py', b'VALUE = 1\n')

        report = warm(FakeScope([ScopeResult(TIER_MOD, 'mod', [f'{MOD}/module/main.py'])]))
        data = report.to_dict()
        for result in data['results']:
            result.pop('seconds')
        assert data == {
            'results': [{
                'tier': TIER_MOD,
                'target': 'mod',
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
