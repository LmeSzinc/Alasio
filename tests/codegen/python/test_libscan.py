import importlib.util

import alasio.codegen.python.libscan as libscan
from alasio.codegen.python.libscan import EnvLibraryScanner, ModuleType
from alasio.ext import env
from alasio.testing.filesystem import fs  # noqa: F401

# Alasio repo root, used as the scanner's classification root.
PROJECT_ROOT = env.ALASIO_ROOT


class TestEnvLibraryScanner:
    def test_scanned_metadata(self):
        """
        Test that scanned_metadata is not empty and contains core modules
        """
        scanner = EnvLibraryScanner(PROJECT_ROOT)
        meta = scanner._scanned_metadata

        # Verify it's not empty
        assert len(meta) > 0

        # Verify core modules are identified as standard library
        assert meta.get('os') == ModuleType.STANDARD_LIBRARY
        assert meta.get('sys') == ModuleType.STANDARD_LIBRARY
        assert meta.get('json') == ModuleType.STANDARD_LIBRARY

    def test_all_importable(self):
        """
        Test that all_importable contains common top-level modules
        """
        scanner = EnvLibraryScanner(PROJECT_ROOT)
        all_modules = scanner.all_importable

        # Verify presence of core modules
        assert 'os' in all_modules
        assert 'sys' in all_modules
        assert 'math' in all_modules

    def test_scan_low_level_c_lib(self):
        """
        Test that low-level C libraries are correctly scanned as standard library
        """
        scanner = EnvLibraryScanner(PROJECT_ROOT)
        stdlib = scanner.standard_library

        # Specifically check _lzma as requested by user
        # Note: _lzma is a C extension usually located in DLLs/ on Windows or built-in
        assert '_lzma' in stdlib

        # Check other common low-level C modules
        assert '_ssl' in stdlib
        assert '_socket' in stdlib
        assert '_json' in stdlib

    def test_third_party(self):
        """
        Test that known third-party libraries are correctly classified
        """
        scanner = EnvLibraryScanner(PROJECT_ROOT)
        third_party = scanner.third_party

        # Verify project dependencies are classified as third-party
        assert 'pytest' in third_party
        assert 'rich' in third_party

        # Only check optional dependencies if they are installed
        for mod_name in ('msgspec', 'starlette'):
            if importlib.util.find_spec(mod_name) is not None:
                assert mod_name in third_party, \
                    f"Installed dependency '{mod_name}' should be classified as third_party"

    def test_no_overlap(self):
        """
        Test that standard_library and third_party sets have no intersection
        """
        scanner = EnvLibraryScanner(PROJECT_ROOT)
        stdlib = scanner.standard_library
        third_party = scanner.third_party

        overlap = stdlib.intersection(third_party)
        assert len(overlap) == 0, f"Overlap found between stdlib and third_party: {overlap}"

    def test_local_project(self):
        """
        Test that the local project itself is correctly classified
        """
        scanner = EnvLibraryScanner(PROJECT_ROOT)
        meta = scanner._scanned_metadata

        # 'alasio' should be classified as LOCAL_PROJECT
        # if it is found during scanning and is within the project root
        if 'alasio' in meta:
            assert meta['alasio'] == ModuleType.LOCAL_PROJECT


class TestClassifySingleModule:
    """Classification of one module name, without a full scan."""

    def test_stdlib_by_name(self, fs, monkeypatch):
        """A stdlib module is classified by name, not by the path of the interpreter.

        The path of a module of a symlinked interpreter does not share the
        prefix of sysconfig, and a fake filesystem does not resolve the
        symlinks of the real disk: with paths alone the module would fall back
        to the project root.
        """
        monkeypatch.setattr(libscan, 'STDLIB_MODULE_NAMES', frozenset({'typing'}))
        scanner = EnvLibraryScanner('/classify-stdlib-by-name')
        # the paths of sysconfig can not tell the stdlib of this interpreter
        scanner.stdlib_paths = {'/not/the/stdlib'}
        assert scanner._classify_single_module('typing') is ModuleType.STANDARD_LIBRARY

    def test_third_party_by_path(self, fs):
        """A module of site-packages is classified third-party."""
        scanner = EnvLibraryScanner('/classify-third-party')
        assert scanner._classify_single_module('msgspec') is ModuleType.THIRD_PARTY
