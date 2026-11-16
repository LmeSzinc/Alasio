"""
Tests of the single file compilation, see alasio/deploy/pyc_warm/compile.py.

The compilation itself is not tested here: py_compile writes its file with
the low level functions of importlib, which the in-memory filesystem does
not serve. The failure mapping is what these tests pin, the write of a real
pyc file is covered by tests/deploy/pyc_warm/test_pyc_format.py.
"""
import py_compile

from alasio.deploy.pyc_warm import compile as compile_module
from alasio.deploy.pyc_warm.compile import compile_file
from alasio.deploy.simple_pip.pyc import pyc_path
from alasio.testing.filesystem import fs  # noqa: F401


class TestCompileFile:
    """
    Tests for compile_file().
    """

    def test_written(self, fs, monkeypatch):
        """A source compiles into the cache of the running interpreter."""
        calls = []

        def fake_compile(file, cfile=None, doraise=False):
            calls.append((file, cfile, doraise))

        monkeypatch.setattr(compile_module.py_compile, 'compile', fake_compile)
        assert compile_file('/pkg/deep/core.py') == ''
        # The destination is the cache of the source, and the errors of the
        # compilation are asked for as exceptions rather than printed
        assert calls == [('/pkg/deep/core.py', pyc_path('/pkg/deep/core.py'), True)]

    def test_syntax_error(self, fs, monkeypatch):
        """A compilation error is reported rather than printed."""

        def fake_compile(file, cfile=None, doraise=False):
            raise py_compile.PyCompileError(ValueError, ValueError('boom'), file)

        monkeypatch.setattr(compile_module.py_compile, 'compile', fake_compile)
        # The class of the exception tells a source that does not compile
        # from a write that did not land, the message alone does not
        assert compile_file('/pkg/core.py') == 'PyCompileError: Sorry: ValueError: boom'

    def test_write_error(self, fs, monkeypatch):
        """A failed write is reported, e.g. a sharing violation on Windows."""

        def fake_compile(file, cfile=None, doraise=False):
            raise PermissionError(13, 'Permission denied')

        monkeypatch.setattr(compile_module.py_compile, 'compile', fake_compile)
        assert compile_file('/pkg/core.py') == 'PermissionError: [Errno 13] Permission denied'

    def test_other_error(self, fs, monkeypatch):
        """Any other error of the source is reported too."""

        def fake_compile(file, cfile=None, doraise=False):
            raise ValueError('source code string cannot contain null bytes')

        monkeypatch.setattr(compile_module.py_compile, 'compile', fake_compile)
        assert compile_file('/pkg/core.py') == 'ValueError: source code string cannot contain null bytes'

    def test_an_error_without_a_message(self, fs, monkeypatch):
        """An exception of an empty message is still named."""

        def fake_compile(file, cfile=None, doraise=False):
            raise MemoryError()

        monkeypatch.setattr(compile_module.py_compile, 'compile', fake_compile)
        assert compile_file('/pkg/core.py') == 'MemoryError'

    def test_not_a_source(self, fs, monkeypatch):
        """A path without a cache name is reported, never compiled."""
        calls = []
        monkeypatch.setattr(
            compile_module.py_compile, 'compile',
            lambda file, cfile=None, doraise=False: calls.append(file),
        )
        assert compile_file('/pkg/core.pyi') == (
            'ValueError: Not a python source file: "/pkg/core.pyi"'
        )
        assert calls == []
