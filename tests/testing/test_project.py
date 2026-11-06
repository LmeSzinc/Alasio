"""
Tests for alasio.testing.project, the project root and the collection folder.

A project root is the nearest ancestor of a file declaring a mod entry
(`module/config/_index/mod_entry.yaml`), and it is the folder the golden
screenshots of the mod are collected under. The pytest plugin of the screenshot
tests builds on it, and so does the golden test review page of the backend, so
the rule lives in a module that imports neither.
"""
import os
import subprocess
import sys

import pytest

from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.testing import project as project_module
from alasio.testing.project import PROJECT_ROOT_MANAGER, ProjectRootError, ProjectRootManager

# The example mod of the repository, and a test file of it. These are the real
# paths of the repository: resolving a test file to the project root of its mod
# is the very behavior under test.
EXAMPLE_MOD = env.ALASIO_ROOT.joinpath('ExampleMod')
TEST_FILE = EXAMPLE_MOD.joinpath('tests/map/test_fleet_bar.py')


class TestImport:
    def test_import_does_not_load_pytest_or_the_image_stack(self):
        """
        The backend imports this module at runtime, so it must not drag in the
        test runner or the image stack of the screenshot harness.
        """
        code = (
            "import sys\n"
            "import alasio.testing.project\n"
            "assert 'pytest' not in sys.modules, 'pytest imported at module load'\n"
            "assert 'numpy' not in sys.modules, 'numpy imported at module load'\n"
            "assert 'cv2' not in sys.modules, 'cv2 imported at module load'\n"
        )
        process_env = os.environ.copy()
        process_env['PYTHONPATH'] = str(env.ALASIO_ROOT) + os.pathsep + process_env.get('PYTHONPATH', '')
        result = subprocess.run(
            [sys.executable, '-c', code],
            cwd=str(env.ALASIO_ROOT),
            env=process_env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


class TestProjectRootManager:
    """
    The project root of a file is the nearest ancestor of the file declaring a
    mod entry, `module/config/_index/mod_entry.yaml`
    """

    def test_project_root_of_a_test_inside_a_mod(self):
        """A test inside ExampleMod resolves to ExampleMod, not to the repository."""
        assert PROJECT_ROOT_MANAGER.get_project_root(TEST_FILE) == EXAMPLE_MOD

    def test_project_root_of_a_mod_that_is_the_project_root(self):
        """A mod that is the repository itself: its root is the repository root."""
        assert PROJECT_ROOT_MANAGER.get_project_root(EXAMPLE_MOD.joinpath('tests/test_x.py')) == EXAMPLE_MOD

    def test_no_project_root(self):
        """A file outside of any mod has no project root, and no collection folder."""
        with pytest.raises(ProjectRootError):
            PROJECT_ROOT_MANAGER.get_project_root(env.ALASIO_ROOT.joinpath('alasio/testing/project.py'))

    def test_the_walk_runs_once_per_mod(self, monkeypatch):
        """
        Many test files of one mod share one walk of the folders: the root of a
        mod is discovered once, not once per test file.
        """
        manager = ProjectRootManager()
        # A step of the walk is one existence check of the entry file of a mod
        calls = []
        original = project_module.ModEntry.is_mod

        def counting_is_mod(root):
            calls.append(root)
            return original(root)

        monkeypatch.setattr(project_module.ModEntry, 'is_mod', counting_is_mod)

        manager.get_project_root(TEST_FILE)
        walk = len(calls)
        # Other test files of the same mod, in the same and in other folders
        manager.get_project_root(EXAMPLE_MOD.joinpath('tests/map/test_other.py'))
        manager.get_project_root(EXAMPLE_MOD.joinpath('tests/island/handler/test_order.py'))
        manager.get_project_root(EXAMPLE_MOD.joinpath('tests/test_entry.py'))

        assert walk > 0
        assert len(calls) == walk

    def test_known_root_prefers_the_longest_match(self):
        """A mod nested in another mod resolves to the inner one, without IO."""
        manager = ProjectRootManager()
        manager.roots[:] = [PathStr.new('/a/mod'), PathStr.new('/a/mod/inner')]

        assert manager.get_known_root('/a/mod/inner/tests/test_x.py') == PathStr.new('/a/mod/inner')
        assert manager.get_known_root('/a/mod/tests/test_x.py') == PathStr.new('/a/mod')
        # A prefix of the folder name is not a folder of the mod
        assert manager.get_known_root('/a/modular/tests/test_x.py') is None

    def test_the_manager_resolves_and_caches_a_root(self, monkeypatch):
        """The root of a mod is discovered once for the whole process."""
        manager = ProjectRootManager()

        assert manager.get_project_root(TEST_FILE) == EXAMPLE_MOD
        # No walk: the root is already known, so is_mod() is never called
        monkeypatch.setattr(
            project_module.ModEntry, 'is_mod',
            lambda root: pytest.fail(f'unexpected walk to {root}')
        )
        assert manager.get_project_root(EXAMPLE_MOD.joinpath('tests/test_entry.py')) == EXAMPLE_MOD
