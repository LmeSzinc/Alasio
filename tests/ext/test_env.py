import os

import pytest

from alasio.ext import env
from alasio.ext.path import PathStr, calc


class TestSetProjectRoot:
    """set_project_root() stores the root as an absolute path."""

    @pytest.fixture(autouse=True)
    def restore_global(self, monkeypatch):
        """Keep the project root of the running test process unchanged."""
        monkeypatch.setattr(env, 'PROJECT_ROOT', env.PROJECT_ROOT)

    @pytest.fixture(autouse=True)
    def fake_windows(self, monkeypatch):
        """Force the Windows branch and a fixed cwd to run on every platform."""
        monkeypatch.setattr(calc, 'WINDOWS_SEP', True)
        monkeypatch.setattr(os, 'getcwd', lambda: 'C:\\fake\\cwd')

    @pytest.mark.parametrize("root, up, expected", [
        # A relative root is resolved against the cwd before walking up,
        # this is the __file__ of `python module/main.py`
        ('alasio/assets_dev/extract.py', 3, 'C:/fake/cwd'),
        ('alasio/assets_dev/extract.py', 2, 'C:/fake/cwd/alasio'),
        ('module\\main.py', 1, 'C:/fake/cwd/module'),
        ('main.py', 0, 'C:/fake/cwd/main.py'),
        # An absolute root is kept, up is applied to it as usual
        ('E:/ProgramData/Pycharm/Alasio/alasio/ext/env.py', 3, 'E:/ProgramData/Pycharm/Alasio'),
        ('E:\\ProgramData\\Pycharm\\Alasio\\alasio\\ext\\env.py', 3, 'E:/ProgramData/Pycharm/Alasio'),
    ])
    def test_set_project_root(self, root, up, expected):
        """A relative root should not collapse to the empty unset root."""
        env.set_project_root(root, up=up)
        assert env.PROJECT_ROOT == expected

    def test_project_root_is_a_pathstr(self):
        """The stored root should stay a PathStr."""
        env.set_project_root('main.py')
        assert isinstance(env.PROJECT_ROOT, PathStr)


class TestSetProjectRootWhenPosix:
    """set_project_root() on the POSIX branch, the cwd is a root."""

    @pytest.fixture(autouse=True)
    def restore_global(self, monkeypatch):
        """Keep the project root of the running test process unchanged."""
        monkeypatch.setattr(env, 'PROJECT_ROOT', env.PROJECT_ROOT)

    @pytest.fixture(autouse=True)
    def fake_posix(self, monkeypatch):
        """Force the POSIX branch and a fixed cwd to run on every platform."""
        monkeypatch.setattr(calc, 'WINDOWS_SEP', False)
        monkeypatch.setattr(os, 'getcwd', lambda: '/fake/cwd')

    @pytest.mark.parametrize("root, up, expected", [
        ('alasio/assets_dev/extract.py', 3, '/fake/cwd'),
        ('alasio/assets_dev/extract.py', 1, '/fake/cwd/alasio/assets_dev'),
        ('main.py', 0, '/fake/cwd/main.py'),
        ('/srv/alasio/module/main.py', 2, '/srv/alasio'),
    ])
    def test_set_project_root(self, root, up, expected):
        """Absolute and relative roots are both stored as absolute paths."""
        env.set_project_root(root, up=up)
        assert env.PROJECT_ROOT == expected
