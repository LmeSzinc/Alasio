import importlib.util
import os

from alasio.codegen.python.libscan import EnvLibraryScanner, ModuleType

# Alasio repo root, used as the scanner's classification root.
# Current file is tests/codegen/python/test_libscan.py
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..'))


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
