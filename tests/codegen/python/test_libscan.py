import pytest

from alasio.codegen.python.libscan import EnvLibraryScanner, ModuleType
from alasio.codegen.ruff.stdlist import STD_SET
from alasio.testing.filesystem import fs  # noqa: F401


class TestStdList:
    """The static table of standard library names shared with isort."""

    def test_covers_python_38_to_315(self):
        """Every version in the range contributes its names."""
        # 3.8
        assert 'typing' in STD_SET
        assert 'asynchat' in STD_SET
        assert 'distutils' in STD_SET
        # 3.9
        assert 'graphlib' in STD_SET
        assert 'zoneinfo' in STD_SET
        # 3.10
        assert 'xxlimited_35' in STD_SET
        # 3.11
        assert 'tomllib' in STD_SET
        # 3.12
        assert '_pydatetime' in STD_SET
        # 3.13
        assert '_pyrepl' in STD_SET
        # 3.14
        assert 'annotationlib' in STD_SET
        assert '_zstd' in STD_SET
        # 3.15
        assert 'profiling' in STD_SET

    def test_private_extension_modules(self):
        """The private C modules are standard library, isort's own table lacks them."""
        for name in ('_io', '_json', '_ssl', '_socket', '_sqlite3', '_ctypes', '_lzma'):
            assert name in STD_SET

    def test_third_party_are_absent(self):
        """Installed distributions and project modules are not in the table."""
        for name in ('pytest', 'msgspec', 'starlette', 'isort', 'ruff', 'alasio'):
            assert name not in STD_SET

    def test_is_a_set(self):
        """The table is a set, a module name is looked up without a scan."""
        assert isinstance(STD_SET, set)


class TestClassifySingleModule:
    """Classification of one module name."""

    def test_stdlib_by_name(self, fs):
        """A stdlib module is classified by name, without touching the disk.

        The fake filesystem serves nothing of the real interpreter, so a
        classification that consulted find_spec or the stdlib paths would not
        find `typing` here.
        """
        scanner = EnvLibraryScanner('/classify-stdlib-by-name')
        assert scanner.classify_single_module('typing') is ModuleType.STANDARD_LIBRARY

    def test_private_stdlib_by_name(self, fs):
        """A private C module of the table is standard library too."""
        scanner = EnvLibraryScanner('/classify-private-stdlib')
        assert scanner.classify_single_module('_io') is ModuleType.STANDARD_LIBRARY

    def test_third_party_by_path(self, fs):
        """A module of site-packages is classified third-party."""
        scanner = EnvLibraryScanner('/classify-third-party')
        assert scanner.classify_single_module('msgspec') is ModuleType.THIRD_PARTY

    def test_unknown(self, fs):
        """A name that is no standard library module and is not installed is unknown."""
        scanner = EnvLibraryScanner('/classify-unknown')
        assert scanner.classify_single_module('no_such_module_zzz') is ModuleType.UNKNOWN

    def test_cached_by_name(self, monkeypatch):
        """A name is classified once, however many times it is asked for."""
        scanner = EnvLibraryScanner('/classify-cached-by-name')
        calls = []

        def fake_classify(name):
            calls.append(name)
            return ModuleType.THIRD_PARTY

        monkeypatch.setattr(scanner, '_classify_single_module', fake_classify)
        assert scanner.classify_single_module('mod_a') is ModuleType.THIRD_PARTY
        assert scanner.classify_single_module('mod_a') is ModuleType.THIRD_PARTY
        assert scanner.classify_single_module('mod_b') is ModuleType.THIRD_PARTY
        assert scanner.classify_single_module('mod_b') is ModuleType.THIRD_PARTY
        assert calls == ['mod_a', 'mod_b']

    @pytest.mark.parametrize('name', sorted(STD_SET))
    def test_every_listed_name_is_standard_library(self, name):
        """No name of the table falls through to the path comparison."""
        scanner = EnvLibraryScanner('/classify-every-listed-name')
        assert scanner.classify_single_module(name) is ModuleType.STANDARD_LIBRARY
