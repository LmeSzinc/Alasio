"""
The warmup meets paths and contents that are the wrong thing to read.

A warmup never fails a release: the pyc files are an optimization, python
compiles a source on the next import anyway. Every shape here is therefore
answered rather than raised -- a path that is the wrong kind of file, and a
file whose content is not the format it claims -- and the run reports what
it could not do and warms the rest.
"""
from types import SimpleNamespace

import pytest

from alasio.deploy.pyc_warm import fresh as fresh_module, runner
from alasio.deploy.pyc_warm.fresh import plan, plan_folder, stale_sources
from alasio.deploy.pyc_warm.runner import warm
from alasio.deploy.pyc_warm.scope import TIER_MOD, Scope, ScopeResult, index_sources, record_sources
from alasio.deploy.simple_pip.pyc import pyc_path
from alasio.ext import env
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy.pyc_warm.test_fresh import patch_stat_spy

# The folders of the tests
SITE = '/site'
MOD = '/mod'
# A mtime in the past, the clock of the test run is later than the file
PAST = 1700000000


@pytest.fixture(autouse=True)
def capture():
    """
    Collect the lines of a run: a test must not write the log of the repo.

    Yields:
        CaptureWriter: The writer that holds the lines
    """
    with logger.mock_capture_writer() as capture:
        yield capture


class RecordingCompiler:
    """
    A compile_file() stand-in that records what it was asked to compile.

    Attributes:
        called (list[str]): Sources the warmup tried to compile
    """

    def __init__(self):
        self.called = []

    def __call__(self, file):
        """
        Args:
            file (str): Path of the source

        Returns:
            str: Empty, the compile answered as a written pyc file
        """
        self.called.append(file)
        return ''


class FakeScope:
    """
    A scope of the tests: the units of a run, in the order given.
    """

    def __init__(self, results):
        self.results = results

    def tiers(self):
        yield from self.results


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


def make_mod(root, requirements_file='requirements.txt'):
    """
    A mod as the scope reads one.

    Args:
        root (str): Posix path of the mod tree
        requirements_file (str): Declared location of the pins

    Returns:
        SimpleNamespace: The mod
    """
    return SimpleNamespace(
        name='mod', root=root,
        entry=SimpleNamespace(requirements_file=requirements_file),
    )


class TestWrongKindOfPath:
    """
    A path of the scope that is not the kind of file it is named after.
    """

    @pytest.fixture(autouse=True)
    def _windows_listing(self, monkeypatch):
        """
        Run the listing that stats its entries, the one of Windows.

        Args:
            monkeypatch: The pytest monkeypatch fixture
        """
        monkeypatch.setattr(fresh_module, 'DIRENT_STAT_IS_FREE', True)

    def test_a_cache_folder_that_is_a_file_warms_nothing(self, fs, monkeypatch):
        """A folder whose __pycache__ is a file has nothing to warm."""
        write(fs, f'{MOD}/module/a.py', b'VALUE = 1\n')
        write(fs, f'{MOD}/module/__pycache__', b'not a folder')
        compiler = RecordingCompiler()
        monkeypatch.setattr(runner, 'compile_file', compiler)

        report = warm(FakeScope([ScopeResult(TIER_MOD, 'mod', [f'{MOD}/module/a.py'])]))
        # Not one compilation: the write of the pyc could only fail, and the
        # source stays stale until the layout is repaired
        assert compiler.called == []
        assert report.compiled == 0
        assert report.failed == 0

    def test_a_cache_folder_that_is_a_file_warms_nothing_posix(self, fs, monkeypatch):
        """The same, on the listing that stats nothing and defers to the worker."""
        monkeypatch.setattr(fresh_module, 'DIRENT_STAT_IS_FREE', False)
        write(fs, f'{MOD}/module/a.py', b'VALUE = 1\n')
        write(fs, f'{MOD}/module/__pycache__', b'not a folder')
        compiler = RecordingCompiler()
        monkeypatch.setattr(runner, 'compile_file', compiler)

        report = warm(FakeScope([ScopeResult(TIER_MOD, 'mod', [f'{MOD}/module/a.py'])]))
        assert compiler.called == []
        assert (report.compiled, report.failed) == (0, 0)

    def test_a_folder_named_like_a_source_is_not_a_source(self, fs):
        """A folder named "b.py" is never planned, and never compiled."""
        write(fs, f'{MOD}/module/a.py', b'VALUE = 1\n')
        fs.create_dir(f'{MOD}/module/b.py')

        items = plan_folder(f'{MOD}/module', {'a.py', 'b.py'}, PAST)
        assert [item[0] for item in items] == [f'{MOD}/module/a.py']

    def test_a_folder_named_like_a_source_is_not_a_source_posix(self, fs, monkeypatch):
        """The listing that stats nothing answers the same, without a stat."""
        monkeypatch.setattr(fresh_module, 'DIRENT_STAT_IS_FREE', False)
        write(fs, f'{MOD}/module/a.py', b'VALUE = 1\n')
        fs.create_dir(f'{MOD}/module/b.py')
        stats = []
        patch_stat_spy(monkeypatch, stats)

        stale = stale_sources([f'{MOD}/module/a.py', f'{MOD}/module/b.py'])
        assert stale == [f'{MOD}/module/a.py']
        # The type of an entry is free on every platform (readdir answers
        # it), so filtering a folder costs no stat on the one thread of the
        # run that is not parallel, see DIRENT_STAT_IS_FREE
        assert stats == []

    def test_a_pyc_that_is_a_folder_is_stale(self, fs):
        """A folder where a pyc file belongs is answered stale, not skipped."""
        write(fs, f'{MOD}/module/a.py', b'VALUE = 1\n')
        fs.create_dir(pyc_path(f'{MOD}/module/a.py'))
        assert stale_sources([f'{MOD}/module/a.py']) == [f'{MOD}/module/a.py']

    def test_a_source_folder_that_is_a_file_holds_no_source(self, fs):
        """A file where a folder of the scope belongs holds nothing to warm."""
        write(fs, f'{MOD}/module', b'not a folder')
        assert list(plan([f'{MOD}/module/a.py'])) == []
        assert stale_sources([f'{MOD}/module/a.py']) == []

    def test_a_gone_source_is_not_a_source(self, fs):
        """A source removed between the scope and the listing is skipped."""
        fs.create_dir(f'{MOD}/module')
        assert stale_sources([f'{MOD}/module/gone.py']) == []


class TestMalformedRecord:
    """
    A RECORD that is not the file list it claims to be.
    """

    def test_a_nul_byte(self, fs):
        """A NUL byte is no character of a path, a RECORD holding one is no list."""
        write(fs, f'{SITE}/demo-1.0.dist-info/RECORD', b'demo/a\x00b.py,sha256=x,1\n')
        assert record_sources(SITE, f'{SITE}/demo-1.0.dist-info') == []

    def test_not_utf8(self, fs):
        """A RECORD saved in the local code page is not a list either."""
        write(fs, f'{SITE}/demo-1.0.dist-info/RECORD', b'demo/\xd6\xd0.py,sha256=x,1\n')
        assert record_sources(SITE, f'{SITE}/demo-1.0.dist-info') == []

    def test_that_is_a_folder(self, fs):
        """A folder named RECORD is not a file list."""
        fs.create_dir(f'{SITE}/demo-1.0.dist-info/RECORD')
        assert record_sources(SITE, f'{SITE}/demo-1.0.dist-info') == []

    def test_a_row_that_walks_out(self, fs):
        """A row that climbs out of site-packages is not a file of it."""
        write(
            fs, f'{SITE}/demo-1.0.dist-info/RECORD',
            b'demo/../../outside/secret.py,sha256=x,1\n',
        )
        assert record_sources(SITE, f'{SITE}/demo-1.0.dist-info') == []

    def test_a_row_of_a_script(self, fs):
        """The scripts pip installs outside site-packages are skipped."""
        write(
            fs, f'{SITE}/demo-1.0.dist-info/RECORD',
            b'../../Scripts/demo.py,sha256=x,1\ndemo/main.py,sha256=y,1\n',
        )
        assert record_sources(SITE, f'{SITE}/demo-1.0.dist-info') == [f'{SITE}/demo/main.py']

    def test_a_row_that_is_a_folder(self, fs):
        """A row naming a folder is listed, and the write is what refuses it."""
        write(fs, f'{SITE}/demo-1.0.dist-info/RECORD', b'demo/a.py,sha256=x,1\n')
        fs.create_dir(f'{SITE}/demo/a.py')
        # The row is a path of the installation, the type of it is the
        # business of the compile, which counts the refusal and moves on
        assert record_sources(SITE, f'{SITE}/demo-1.0.dist-info') == [f'{SITE}/demo/a.py']


class TestMalformedRequirements:
    """
    A requirements file that can not be read as pins.
    """

    def test_not_utf8(self, fs):
        """A file saved in the local code page answers no pin, and no error."""
        write(fs, f'{MOD}/requirements.txt', b'trio==0.22.0  # \xd6\xd0\xce\xc4\n')
        results = list(
            Scope(site_packages=[SITE], mods={}).lock_tier(
                SITE, {}, make_mod(MOD), set(),
            )
        )
        assert [(result.target, result.sources) for result in results] == [
            (f'{MOD}/requirements.txt', []),
        ]

    def test_that_is_a_folder(self, fs):
        """A folder named requirements.txt answers no pin, and no error."""
        fs.create_dir(f'{MOD}/requirements.txt')
        results = list(
            Scope(site_packages=[SITE], mods={}).lock_tier(SITE, {}, make_mod(MOD), set())
        )
        assert [(result.target, result.sources) for result in results] == [
            (f'{MOD}/requirements.txt', []),
        ]

    def test_a_nul_byte(self, fs):
        """A NUL byte is a line the parser skips, not a crash."""
        write(fs, f'{MOD}/requirements.txt', b'trio==0.22.0\x00\ntrio==0.22.0\n')
        results = list(
            Scope(site_packages=[SITE], mods={}).lock_tier(SITE, {}, make_mod(MOD), set())
        )
        assert [result.target for result in results] == ['trio']


class TestMalformedLedger:
    """
    An index pack that is not the file list it claims to be.
    """

    @pytest.mark.parametrize('content', [
        b'',
        b'\x01\x02\x03',
        b'{"a": 1}',
        bytes(range(256)) * 4,
    ])
    def test_garbage(self, fs, content):
        """An index pack that can not be decoded is no file list."""
        write(fs, f'{MOD}/.pack/index.pack', content)
        assert index_sources(MOD) is None

    def test_that_is_a_folder(self, fs):
        """A folder named index.pack is no file list."""
        fs.create_dir(f'{MOD}/.pack/index.pack')
        assert index_sources(MOD) is None


class TestMalformedDeclaration:
    """
    A mod declaration that can not be read.
    """

    def test_a_mod_entry_that_is_not_yaml(self, fs, monkeypatch):
        """A broken declaration of the root leaves the environment warmable."""
        write(fs, f'{MOD}/module/config/_index/mod_entry.yaml', b':\n\t- [\x00')
        monkeypatch.setattr(env, 'PROJECT_ROOT', MOD)
        scope = Scope(site_packages=[])
        # The backend reports the declaration at its own startup: the warmup
        # has no mod to warm and does not become a second place to fail
        assert dict(scope.mods) == {}
        assert list(scope.mod_tier()) == []


class TestOneBrokenUnitDoesNotStopTheRun:
    """
    The property the cases above exist for: a run reports and moves on.
    """

    def test_a_broken_distribution_and_a_good_one(self, fs, monkeypatch):
        """One distribution that can not be read does not stop the others."""
        write(fs, f'{SITE}/demo-1.0.dist-info/RECORD', b'demo/a\x00b.py,x,1\n')
        write(fs, f'{SITE}/good-1.0.dist-info/RECORD', b'good/main.py,sha256=y,1\n')
        write(fs, f'{SITE}/good/main.py', b'VALUE = 1\n')
        compiler = RecordingCompiler()
        monkeypatch.setattr(runner, 'compile_file', compiler)

        report = warm(FakeScope([
            ScopeResult('common', 'demo', []),
            ScopeResult('common', 'good', [f'{SITE}/good/main.py']),
        ]))
        assert compiler.called == [f'{SITE}/good/main.py']
        assert report.compiled == 1
        assert report.unresolved == ['demo']

    def test_every_case_of_this_module_answers_a_report(self, fs, monkeypatch, capture):
        """A run over a tree of wrong kinds of files still answers a report."""
        write(fs, f'{MOD}/module/a.py', b'VALUE = 1\n')
        write(fs, f'{MOD}/module/__pycache__', b'not a folder')
        fs.create_dir(f'{MOD}/module/b.py')
        write(fs, f'{MOD}/other/c.py', b'VALUE = 2\n')
        monkeypatch.setattr(runner, 'compile_file', RecordingCompiler())

        report = warm(FakeScope([ScopeResult(TIER_MOD, 'mod', [
            f'{MOD}/module/a.py', f'{MOD}/module/b.py', f'{MOD}/other/c.py',
        ])]))
        assert len(report.results) == 1
        assert report.seconds >= 0
        assert capture.concat().count('INFO |') == 1
