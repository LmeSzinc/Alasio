import os
import sys
from importlib.util import cache_from_source
from stat import S_IWRITE

import pytest

from alasio.deploy.simple_pip import cleanup_pyc
from alasio.deploy.simple_pip.cleanup_pyc import CleanupPyc
from alasio.ext.path.atomic import IS_WINDOWS
from alasio.ext.path.calc import to_posix
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401

# Root of the tests, a site-packages look-alike
SITE = '/env/Lib/site-packages'


def pyc_path(source, optimization=None):
    """
    Path of the pyc file python compiles for a source file.

    Args:
        source (str): Path of the .py file, relative to SITE
        optimization (str): Optimization level of the manual optimization
            names, e.g. "1" gives "core.cpython-38.opt-1.pyc". Defaults to None.

    Returns:
        str: Relative posix path of the .pyc file
    """
    return to_posix(cache_from_source(source, optimization=optimization))


def create_source(fake, path):
    """
    Create a source file.

    Args:
        fake (FakeFilesystem): Active fake filesystem
        path (str): Path of the .py file, relative to SITE
    """
    fake.create_file(f'{SITE}/{path}', contents=b'')


def create_pyc(fake, path, content=b'\x00' * 16):
    """
    Create a pyc file.

    Args:
        fake (FakeFilesystem): Active fake filesystem
        path (str): Path of the .pyc file, relative to SITE
        content (bytes): Content of the .pyc file
    """
    fake.create_file(f'{SITE}/{path}', contents=content)


def pyc_name(path):
    """
    File name of a pyc path.

    Args:
        path (str): Relative posix path of the .pyc file

    Returns:
        str: File name of the .pyc file
    """
    return path.rpartition('/')[2]


def run_cleanup(root):
    """
    Run a cleanup and return its counts.

    Args:
        root (str): Root folder to clean

    Returns:
        tuple[int, int]: Number of pyc files removed and failed
    """
    cleaner = CleanupPyc(root)
    cleaner.cleanup()
    return cleaner.removed, cleaner.failed


class TestInit:
    def test_normalize_root(self):
        """The root is normalized: a trailing separator is stripped."""
        assert CleanupPyc(SITE).root == SITE
        assert CleanupPyc(f'{SITE}/').root == SITE

    @pytest.mark.skipif(not IS_WINDOWS, reason='A Windows path is normalized on Windows only')
    def test_normalize_root_windows(self):
        """The backslashes of a Windows path are normalized to "/"."""
        assert CleanupPyc(SITE.replace('/', '\\')).root == SITE

    def test_counters_start_at_zero(self):
        """The counters are readable before the first cleanup."""
        cleaner = CleanupPyc(SITE)
        assert (cleaner.removed, cleaner.failed) == (0, 0)

    def test_cleanup_returns_none(self, fs):
        """The counts live in the instance, cleanup() has no return value."""
        create_pyc(fs, pyc_path('demo/core.py'))
        cleaner = CleanupPyc(SITE)
        assert cleaner.cleanup() is None
        assert (cleaner.removed, cleaner.failed) == (1, 0)


class TestOrphanPyc:
    def test_removes_orphan_pyc(self, fs):
        """A pyc file whose source is gone is an orphan, the emptied __pycache__ is removed."""
        pyc = pyc_path('demo/core.py')
        create_pyc(fs, pyc)
        assert run_cleanup(SITE) == (1, 0)
        assert not os.path.exists(f'{SITE}/{pyc}')
        assert not os.path.exists(f'{SITE}/demo/__pycache__')
        assert os.path.exists(f'{SITE}/demo')

    def test_keeps_pyc_of_existing_source(self, fs):
        """A pyc file whose source exists is kept, removing it only recompiles it."""
        pyc = pyc_path('demo/core.py')
        create_source(fs, 'demo/core.py')
        create_pyc(fs, pyc)
        assert run_cleanup(SITE) == (0, 0)
        assert os.path.exists(f'{SITE}/{pyc}')
        assert os.path.exists(f'{SITE}/demo/__pycache__')

    def test_removes_orphan_optimized_pyc(self, fs):
        """The manual optimization pyc files (opt-1, opt-2) are checked too."""
        create_pyc(fs, pyc_path('demo/core.py', optimization='1'))
        create_pyc(fs, pyc_path('demo/core.py', optimization='2'))
        assert run_cleanup(SITE) == (2, 0)
        assert not os.path.exists(f'{SITE}/demo/__pycache__')

    def test_keeps_optimized_pyc_of_existing_source(self, fs):
        """The source of an optimized pyc file is the same as the normal one."""
        pyc = pyc_path('demo/core.py', optimization='1')
        create_source(fs, 'demo/core.py')
        create_pyc(fs, pyc)
        assert run_cleanup(SITE) == (0, 0)
        assert os.path.exists(f'{SITE}/{pyc}')

    def test_removes_orphan_pyc_of_other_tag(self, fs):
        """The pyc files of an other interpreter or tag are checked too."""
        create_pyc(fs, 'demo/__pycache__/core.cpython-37.pyc')
        create_pyc(fs, 'demo/__pycache__/core.pypy38.pyc')
        assert run_cleanup(SITE) == (2, 0)
        assert not os.path.exists(f'{SITE}/demo/__pycache__')

    def test_keeps_pyc_of_other_tag_when_source_exists(self, fs):
        """The tag of a pyc name does not change the source file it belongs to."""
        create_source(fs, 'demo/core.py')
        create_pyc(fs, 'demo/__pycache__/core.cpython-37.pyc')
        assert run_cleanup(SITE) == (0, 0)
        assert os.path.exists(f'{SITE}/demo/__pycache__/core.cpython-37.pyc')

    def test_removes_orphan_pyc_of_deleted_module(self, fs):
        """A module removed by an update leaves the pyc of its siblings valid."""
        create_source(fs, 'demo/keep.py')
        create_pyc(fs, pyc_path('demo/keep.py'))
        create_pyc(fs, pyc_path('demo/gone.py'))
        assert run_cleanup(SITE) == (1, 0)
        assert os.path.exists(f'{SITE}/{pyc_path("demo/keep.py")}')
        assert not os.path.exists(f'{SITE}/{pyc_path("demo/gone.py")}')

    def test_idempotent(self, fs):
        """A second run finds nothing, the counters report the run they belong to."""
        create_pyc(fs, pyc_path('demo/core.py'))
        cleaner = CleanupPyc(SITE)
        cleaner.cleanup()
        assert (cleaner.removed, cleaner.failed) == (1, 0)
        cleaner.cleanup()
        assert (cleaner.removed, cleaner.failed) == (0, 0)


class TestNameCandidates:
    """
    A pyc name holds the module name, the cache tag and the optional
    optimization, all separated by dots: every dotted prefix of the name
    is a candidate module name and the pyc file is an orphan only when
    no candidate "<prefix>.py" is a source of the folder.
    """

    def test_removes_orphan_pytest_tag_pyc(self, fs):
        """pytest compiles the modules it rewrites with an own cache tag."""
        create_pyc(fs, f'demo/__pycache__/core.{sys.implementation.cache_tag}-pytest-8.3.5.pyc')
        assert run_cleanup(SITE) == (1, 0)
        assert not os.path.exists(f'{SITE}/demo/__pycache__')

    def test_keeps_pytest_tag_pyc_of_existing_source(self, fs):
        """The first dotted prefix is the module name of the pyc."""
        create_source(fs, 'demo/core.py')
        create_pyc(fs, f'demo/__pycache__/core.{sys.implementation.cache_tag}-pytest-8.3.5.pyc')
        assert run_cleanup(SITE) == (0, 0)
        assert os.path.exists(f'{SITE}/demo/__pycache__/core.{sys.implementation.cache_tag}-pytest-8.3.5.pyc')

    def test_removes_orphan_dotted_name_pyc(self, fs):
        """A pyc of a module whose file name holds a dot: "pkg.mod.py"."""
        create_pyc(fs, f'demo/__pycache__/pkg.mod.{sys.implementation.cache_tag}.pyc')
        assert run_cleanup(SITE) == (1, 0)
        assert not os.path.exists(f'{SITE}/demo/__pycache__')

    def test_keeps_dotted_name_pyc_of_existing_source(self, fs):
        """A deeper prefix is checked too: "pkg.mod" gives "pkg.mod.py", the "pkg.py" of the first prefix is not needed."""
        create_source(fs, 'demo/pkg.mod.py')
        create_pyc(fs, f'demo/__pycache__/pkg.mod.{sys.implementation.cache_tag}.pyc')
        assert run_cleanup(SITE) == (0, 0)
        assert os.path.exists(f'{SITE}/demo/__pycache__/pkg.mod.{sys.implementation.cache_tag}.pyc')

    def test_leaves_pyc_without_module_name(self, fs):
        """A name without any module part, e.g. a file named ".pyc", is left rather than guessed."""
        create_pyc(fs, 'demo/__pycache__/.pyc')
        assert run_cleanup(SITE) == (0, 0)
        assert os.path.exists(f'{SITE}/demo/__pycache__/.pyc')


class TestWindowsPaths:
    @pytest.mark.skipif(not IS_WINDOWS, reason='The paths of Windows are not case sensitive')
    def test_keeps_pyc_of_source_with_other_case(self, fs):
        """On Windows the name of a source is matched without case."""
        create_source(fs, 'demo/core.py')
        create_pyc(fs, 'demo/__pycache__/Core.cpython-38.pyc')
        assert run_cleanup(SITE) == (0, 0)
        assert os.path.exists(f'{SITE}/demo/__pycache__/Core.cpython-38.pyc')


class TestScope:
    def test_removes_recursively(self, fs):
        """The subfolders are walked, the folders still holding a valid pyc are kept."""
        create_pyc(fs, pyc_path('demo/core.py'))
        create_pyc(fs, pyc_path('demo/sub/deep/core.py'))
        create_source(fs, 'demo/sub/deep/keep.py')
        create_pyc(fs, pyc_path('demo/sub/deep/keep.py'))
        assert run_cleanup(SITE) == (2, 0)
        assert not os.path.exists(f'{SITE}/demo/__pycache__')
        assert os.path.exists(f'{SITE}/{pyc_path("demo/sub/deep/keep.py")}')
        assert os.path.exists(f'{SITE}/demo/sub/deep/__pycache__')

    def test_missing_root(self, fs):
        """A root folder that does not exist has nothing to clean."""
        assert run_cleanup(f'{SITE}/missing') == (0, 0)

    def test_legacy_pyc_not_touched(self, fs):
        """A legacy pyc file placed next to its source is never touched."""
        create_source(fs, 'demo/core.py')
        create_pyc(fs, 'demo/core.pyc')
        assert run_cleanup(SITE) == (0, 0)
        assert os.path.exists(f'{SITE}/demo/core.pyc')

    def test_non_pyc_file_kept(self, fs):
        """A file that is not a pyc is left, it keeps its __pycache__ from being removed."""
        fs.create_file(f'{SITE}/demo/__pycache__/notes.txt', contents=b'keep me')
        assert run_cleanup(SITE) == (0, 0)
        assert os.path.exists(f'{SITE}/demo/__pycache__/notes.txt')
        assert os.path.exists(f'{SITE}/demo/__pycache__')

    def test_removes_empty_pycache(self, fs):
        """A __pycache__ folder without any pyc file is a residue too."""
        fs.create_dir(f'{SITE}/demo/__pycache__')
        assert run_cleanup(SITE) == (0, 0)
        assert not os.path.exists(f'{SITE}/demo/__pycache__')


class TestOneListingPerFolder:
    def test_reads_every_folder_once(self, fs, monkeypatch):
        """A folder is listed once: the sources of a __pycache__ come from the listing of its own folder."""
        create_source(fs, 'demo/core.py')
        create_pyc(fs, pyc_path('demo/core.py'))
        create_pyc(fs, pyc_path('demo/gone.py'))
        scandir = os.scandir
        calls = []

        def scandir_record(path):
            calls.append(path)
            return scandir(path)

        monkeypatch.setattr(os, 'scandir', scandir_record)
        assert run_cleanup(SITE) == (1, 0)
        # the folder and its __pycache__: no listing of a parent folder
        # and no per file source lookup
        assert calls == [SITE, f'{SITE}/demo', f'{SITE}/demo/__pycache__']

    def test_no_source_lookup(self, fs, monkeypatch):
        """The sources are checked against the listing of the folder, no path lookup is made."""
        create_source(fs, 'demo/core.py')
        create_pyc(fs, pyc_path('demo/core.py'))
        create_pyc(fs, pyc_path('demo/gone.py'))
        exists = os.path.exists
        lookups = []

        def exists_record(path):
            if isinstance(path, str) and path.startswith(SITE):
                lookups.append(path)
            return exists(path)

        monkeypatch.setattr(os.path, 'exists', exists_record)
        assert run_cleanup(SITE) == (1, 0)
        assert lookups == []


class TestSymlinks:
    def test_symlinked_pycache_not_followed(self, fs):
        """A __pycache__ folder that is a symbolic link is not walked."""
        fs.create_file('/env/other/__pycache__/core.cpython-38.pyc', contents=b'\x00' * 16)
        fs.create_dir(f'{SITE}/demo')
        fs.create_symlink(f'{SITE}/demo/__pycache__', '/env/other/__pycache__')
        assert run_cleanup(SITE) == (0, 0)
        assert os.path.exists('/env/other/__pycache__/core.cpython-38.pyc')

    def test_symlinked_pyc_not_removed(self, fs):
        """A pyc file that is a symbolic link is not followed nor removed."""
        fs.create_file('/env/other/core.cpython-38.pyc', contents=b'\x00' * 16)
        fs.create_dir(f'{SITE}/demo/__pycache__')
        fs.create_symlink(f'{SITE}/demo/__pycache__/core.cpython-38.pyc', '/env/other/core.cpython-38.pyc')
        assert run_cleanup(SITE) == (0, 0)
        assert os.path.lexists(f'{SITE}/demo/__pycache__/core.cpython-38.pyc')
        assert os.path.exists('/env/other/core.cpython-38.pyc')
        assert os.path.exists(f'{SITE}/demo/__pycache__')


class TestLogging:
    def test_silent_on_success(self, fs):
        """A cleanup that goes well logs nothing."""
        create_source(fs, 'demo/core.py')
        create_pyc(fs, pyc_path('demo/core.py'))
        create_pyc(fs, pyc_path('demo/gone.py'))
        with logger.mock_capture_writer() as capture:
            assert run_cleanup(SITE) == (1, 0)
        assert capture.fd.logs == []
        assert capture.backend.logs == []

    def test_warns_on_failure(self, fs, monkeypatch):
        """A file that can not be removed is warned about, the run goes on."""
        locked = pyc_path('demo/locked.py')
        removed = pyc_path('demo/core.py')
        create_pyc(fs, locked)
        create_pyc(fs, removed)
        unlink = os.unlink

        def unlink_locked(path):
            if path.endswith(pyc_name(locked)):
                raise PermissionError(13, 'Permission denied', path)
            return unlink(path)

        monkeypatch.setattr(os, 'unlink', unlink_locked)
        cleaner = CleanupPyc(SITE)
        with logger.mock_capture_writer() as capture:
            cleaner.cleanup()
        assert (cleaner.removed, cleaner.failed) == (1, 1)
        assert os.path.exists(f'{SITE}/{locked}')
        assert not os.path.exists(f'{SITE}/{removed}')
        assert capture.fd.any_contains(f'Failed to remove orphan pyc file: {SITE}/{locked}')
        # the __pycache__ folder still holds the locked pyc file
        assert os.path.exists(f'{SITE}/demo/__pycache__')


class TestFailure:
    @pytest.mark.skipif(not IS_WINDOWS, reason='A read-only file is refused on Windows only')
    def test_readonly_pyc_made_writable(self, fs, monkeypatch):
        """On Windows a read-only file is made writable, then removed."""
        pyc = pyc_path('demo/core.py')
        create_pyc(fs, pyc)
        pyc_full = f'{SITE}/{pyc}'
        unlink = os.unlink
        chmod = os.chmod
        attempts = []
        chmods = []

        def unlink_once(path):
            if path.endswith(pyc_name(pyc)) and not attempts:
                attempts.append(path)
                raise PermissionError(13, 'Permission denied', path)
            return unlink(path)

        def chmod_record(path, mode):
            chmods.append((path, mode))
            return chmod(path, mode)

        monkeypatch.setattr(os, 'unlink', unlink_once)
        monkeypatch.setattr(os, 'chmod', chmod_record)
        assert run_cleanup(SITE) == (1, 0)
        assert attempts == [pyc_full]
        assert chmods == [(pyc_full, S_IWRITE)]
        assert not os.path.exists(f'{SITE}/{pyc}')


class TestDependencies:
    def test_no_importlib(self):
        """The pyc names are resolved with plain string work, importlib is not imported."""
        assert 'importlib' not in vars(cleanup_pyc)
        assert 'source_from_cache' not in vars(cleanup_pyc)
