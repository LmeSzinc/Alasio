import alasio.codegen.python.libscan as libscan
from alasio.codegen.python.libscan import EnvLibraryScanner, ModuleType
from alasio.testing.filesystem import fs  # noqa: F401


class TestClassifySingleModule:
    """Classification of one module name, without a full scan."""

    def test_stdlib_by_name(self, fs, monkeypatch):
        """A stdlib module is classified by name, not by the path of the interpreter.

        The path of a module of a symlinked interpreter does not share the
        prefix of sysconfig, and a fake filesystem does not resolve the
        symlinks of the real disk: with paths alone the module would fall back
        to the project root.
        """
        # the classification reads the name list of the singleton
        monkeypatch.setitem(
            libscan.StdlibModuleNames().__dict__, 'STDLIB_MODULE_NAMES', frozenset({'typing'})
        )
        scanner = EnvLibraryScanner('/classify-stdlib-by-name')
        # the paths of sysconfig can not tell the stdlib of this interpreter
        scanner.stdlib_paths = {'/not/the/stdlib'}
        assert scanner.classify_single_module('typing') is ModuleType.STANDARD_LIBRARY

    def test_third_party_by_path(self, fs):
        """A module of site-packages is classified third-party."""
        scanner = EnvLibraryScanner('/classify-third-party')
        assert scanner.classify_single_module('msgspec') is ModuleType.THIRD_PARTY

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


class TestIterModuleNames:
    """The folder listing that replaces pkgutil.iter_modules()."""

    def test_modules_packages_and_non_modules(self, fs):
        """Source modules and packages are listed, other entries are not."""
        fs.create_file('/mod.py', contents='')
        fs.create_file('/pkg/__init__.py', contents='')
        # a folder without __init__, and the bytecode folder of a module
        fs.create_file('/data/readme.txt', contents='')
        fs.create_file('/__pycache__/mod.cpython-38.pyc', contents='')
        # a sub module of a dotted package is not a top level name
        fs.create_file('/a.b.py', contents='')
        fs.create_file('/pkg/sub.py', contents='')
        # a distribution folder is not a package
        fs.create_file('/demo-1.0.dist-info/METADATA', contents='')

        names = set(libscan._iter_module_names('/'))
        assert names == {'mod', 'pkg'}

    def test_extension_module(self, fs):
        """A file with an extension suffix of the interpreter is a module."""
        suffix = libscan.MODULE_SUFFIXES[-1]
        fs.create_file(f'/ext_mod{suffix}', contents='')
        fs.create_file('/not_module.txt', contents='')

        assert set(libscan._iter_module_names('/')) == {'ext_mod'}

    def test_absent_and_non_folder_path(self, fs):
        """A missing path and a file path list nothing."""
        fs.create_file('/a_file.txt', contents='')
        assert list(libscan._iter_module_names('/missing')) == []
        assert list(libscan._iter_module_names('/a_file.txt')) == []

    def test_module_name(self):
        """Only a file name of a module at the top level has a module name."""
        suffix = libscan.MODULE_SUFFIXES[-1]
        assert libscan._module_name('mod.py') == 'mod'
        assert libscan._module_name(f'ext_mod{suffix}') == 'ext_mod'
        # the file that makes a folder a package
        assert libscan._module_name('__init__.py') is None
        # a sub module of a dotted package
        assert libscan._module_name('a.b.py') is None
        # a suffix alone, and a file that is no module
        assert libscan._module_name('.py') is None
        assert libscan._module_name('readme.txt') is None


class TestStdlibModuleNames:
    """The names of the standard library, read from the disk below python 3.10."""

    def test_scan_reads_the_interpreter(self):
        """The scan finds the modules the running interpreter imports."""
        names = libscan.scan_stdlib_module_names()
        assert 'os' in names
        assert 'typing' in names
        assert 'json' in names
        # a built-in module has no file on the disk
        assert 'sys' in names
        # third party modules live outside the standard library directory
        assert 'pytest' not in names
        assert 'msgspec' not in names

    def test_cached_once(self):
        """The names are read once per process and kept on the singleton."""
        names = libscan.StdlibModuleNames().STDLIB_MODULE_NAMES
        assert libscan.StdlibModuleNames().STDLIB_MODULE_NAMES is names
        # the property writes its result into the dict of the instance, so a
        # later access returns the cached value instead of scanning again
        assert libscan.StdlibModuleNames().__dict__['STDLIB_MODULE_NAMES'] is names

    def test_scanner_caches_the_names(self):
        """The scanner keeps the names, a classification does not call the singleton."""
        scanner = EnvLibraryScanner('/cached-stdlib-names')
        names = scanner.stdlib_module_names
        assert names is scanner.stdlib_module_names
        assert names is libscan.StdlibModuleNames().STDLIB_MODULE_NAMES

    def test_classified_in_the_fake_filesystem(self, fs):
        """A stdlib module is classified by name inside the fake filesystem.

        The fake filesystem serves the directory of os.py from memory, so a
        scan started here finds nothing but the built-in modules. The names
        come from the scan that tests/conftest.py warms up before the session,
        without it `typing` falls through to the path comparison and is not
        classified at all.
        """
        scanner = EnvLibraryScanner('/classify-in-fake-filesystem')
        # the paths of sysconfig can not tell the stdlib of this interpreter
        scanner.stdlib_paths = {'/not/the/stdlib'}
        assert scanner.classify_single_module('typing') is ModuleType.STANDARD_LIBRARY

    def test_scan_of_the_fake_filesystem_finds_no_stdlib(self, fs):
        """The reason of the warm up: the scan can not read the real disk here."""
        names = libscan.scan_stdlib_module_names()
        assert 'typing' not in names
        assert 'json' not in names
        # the built-in modules are a tuple of sys, they are always there
        assert 'sys' in names
