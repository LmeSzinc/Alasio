"""
Tests for alasio/ext/path/pathstr.py.

PathStr is the string based path object of the project: its path calculation
methods delegate to the functions of alasio.ext.path.calc (covered by
test_calc.py), the members implemented by PathStr itself are covered here.
"""
import os

import pytest

from alasio.ext.path import calc
from alasio.ext.path.pathstr import PathStr


class TestCwd:
    """Tests for PathStr.cwd()."""

    @pytest.fixture(autouse=True)
    def fake_env(self, monkeypatch):
        """Force the POSIX branch and a fixed cwd to run on every platform."""
        monkeypatch.setattr(calc, 'WINDOWS_SEP', False)
        monkeypatch.setattr(os, 'getcwd', lambda: '/fake/cwd')

    def test_cwd(self):
        """cwd should come back as a normalized PathStr."""
        cwd = PathStr.cwd()
        assert isinstance(cwd, PathStr)
        assert cwd == '/fake/cwd'

    def test_cwd_windows_sep(self, monkeypatch):
        """A backslash separated cwd should be normalized on the Windows branch."""
        monkeypatch.setattr(calc, 'WINDOWS_SEP', True)
        monkeypatch.setattr(os, 'getcwd', lambda: 'C:\\fake\\cwd')
        assert PathStr.cwd() == 'C:/fake/cwd'

    def test_cwd_is_root(self, monkeypatch):
        """A root cwd should stay "/" instead of becoming an empty string."""
        monkeypatch.setattr(os, 'getcwd', lambda: '/')
        assert PathStr.cwd() == '/'
