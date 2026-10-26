import os
import site
import sys
import sysconfig
from importlib.machinery import all_suffixes

from alasio.backport.strenum import StrEnum
from alasio.ext.cache import cached_property, cached_property_threadsafe
from alasio.ext.singleton import Singleton, SingletonNamed

# Suffixes of an importable file, source, bytecode and extension modules
MODULE_SUFFIXES = tuple(all_suffixes())
# Names of the file that makes a folder a package
PACKAGE_INIT_FILES = frozenset(f'__init__{suffix}' for suffix in MODULE_SUFFIXES)


# Module type classification Enum
class ModuleType(StrEnum):
    STANDARD_LIBRARY = "standard_library"
    THIRD_PARTY = "third_party"
    LOCAL_PROJECT = "local_project"
    UNKNOWN = "unknown"


def _iter_module_names(folder):
    """
    List the top level module names of a folder

    A module is a file with one of the suffixes of the interpreter, a package
    is a folder holding an `__init__` file of those suffixes. A name holding a
    dot is a sub module of a dotted package, it is not importable as a top
    level name.

    An absent folder yields nothing, so the folders of the other platforms can
    be named without checking them first.

    Args:
        folder (str): Folder to list

    Yields:
        str: Top level module names
    """
    try:
        entries = os.scandir(folder)
    except OSError:
        # the folder does not exist on this platform, or the path is no folder
        return
    with entries:
        for entry in entries:
            name = entry.name
            module = _module_name(name)
            if module is not None:
                yield module
                continue
            # a folder is a package only when it holds an __init__ file,
            # __pycache__ and the resource folders of a distribution are not
            try:
                if not entry.is_dir():
                    continue
                inner_names = os.listdir(entry.path)
            except OSError:
                continue
            if any(inner in PACKAGE_INIT_FILES for inner in inner_names):
                yield name


def _module_name(name):
    """
    Module name of a folder entry

    Args:
        name (str): File name

    Returns:
        str | None: Module name, None when the file name names no importable
            top level module (an `__init__` file and a sub module of a dotted
            package included)
    """
    for suffix in MODULE_SUFFIXES:
        if name.endswith(suffix):
            stem = name[:-len(suffix)]
            if not stem or stem == '__init__' or '.' in stem:
                return None
            return stem
    return None


def scan_stdlib_module_names():
    """
    Read the top level names of the standard library from the disk

    The directory of os.py is the anchor: it is the directory the running
    interpreter imports its modules from, while the paths of sysconfig do not
    always name the same directory as the interpreter (uv installs python under
    a symlink). The extension modules are not in that directory, they are in a
    sibling: DLLs next to Lib on Windows, lib-dynload inside or next to the
    standard library on POSIX.

    Returns:
        frozenset[str]: Top level names of the standard library, built-in
            modules included
    """
    stdlib = getattr(os, '__file__', None)
    if stdlib:
        stdlib = os.path.dirname(stdlib)
    else:
        # a frozen interpreter holds no os.py on the disk
        stdlib = sysconfig.get_path('stdlib')
    parent = os.path.dirname(stdlib)
    roots = (
        stdlib,
        os.path.join(parent, 'DLLs'),
        os.path.join(stdlib, 'lib-dynload'),
        os.path.join(parent, 'lib-dynload'),
    )
    names = set(sys.builtin_module_names)
    for root in roots:
        names.update(_iter_module_names(root))
    return frozenset(names)


class StdlibModuleNames(metaclass=Singleton):
    """
    Top level names of the standard library of the running interpreter

    The names are read at most once per process. python 3.10 reports its own
    standard library, the older interpreters read the folders of the
    interpreter on the disk from os.py.

    A name is classified by it before the physical path: the module path of a
    symlinked interpreter (uv installs python under a symlink) does not always
    share the prefix of sysconfig, and a fake filesystem does not resolve the
    symlinks of the real disk, so the path comparison would miss the stdlib and
    fall back to the project root.

    The disk is read, so the names have to be resolved before the fake
    filesystem of the tests replaces os.listdir() / os.scandir(). The suite
    warms the singleton up in the session fixture of tests/conftest.py, which is
    set up before the function scoped `fs` fixture of the first test.
    """

    @cached_property_threadsafe
    def STDLIB_MODULE_NAMES(self):
        """
        Top level names of the standard library

        Returns:
            frozenset[str]: Top level names, built-in modules included
        """
        names = getattr(sys, 'stdlib_module_names', None)
        if names is not None:
            # python 3.10+ reports the standard library itself
            return names
        return scan_stdlib_module_names()


# ==========================================
# 2. Environment Library Scanner and Classifier
# ==========================================
class EnvLibraryScanner(metaclass=SingletonNamed):
    """
    Named-singleton classifier of the modules of the current environment.

    Each project root gets its own scanner instance, since module
    classification depends on the project root.
    """

    def __init__(self, project_root):
        """
        Initialize the library scanner

        Args:
            project_root (str): Project root directory, used to classify
                project-local modules. Must be provided explicitly.
        """
        # Determine project root directory (absolute path)
        self.project_root = os.path.realpath(project_root)
        # Classification results by module name, the same name is classified
        # once however many imports of it are generated
        self._classified = {}

        # Pre-fetch standard library physical paths
        self.stdlib_paths = {
            os.path.realpath(sysconfig.get_path('stdlib')),
            os.path.realpath(sysconfig.get_path('platstdlib'))
        }
        # If stdlib is .../Lib, try to find the sibling DLLs directory
        stdlib_dir = sysconfig.get_path('stdlib')
        if stdlib_dir:
            parent_dir = os.path.dirname(os.path.realpath(stdlib_dir))
            sibling_dlls = os.path.join(parent_dir, 'DLLs')
            if os.path.isdir(sibling_dlls):
                self.stdlib_paths.add(sibling_dlls)

        # Pre-fetch third-party library physical paths (site-packages)
        self.site_paths = {
            os.path.realpath(sysconfig.get_path('purelib')),
            os.path.realpath(sysconfig.get_path('platlib'))
        }

        # Compatible with various virtual environment paths
        try:
            for path in site.getsitedirs():
                self.site_paths.add(os.path.realpath(path))
        except AttributeError:
            pass

        # Extra scan of sys.path for site-packages as a double guarantee
        # (Exclude paths that are already classified as standard library paths)
        for path in sys.path:
            if path:
                real_p = os.path.realpath(path)
                if real_p not in self.stdlib_paths:
                    if 'site-packages' in real_p or 'dist-packages' in real_p:
                        self.site_paths.add(real_p)

    @cached_property
    def stdlib_module_names(self):
        """
        Top level names of the standard library

        Cached on the scanner, so classifying many names does not go through
        the metaclass call of the singleton every time.

        Returns:
            frozenset[str]: Top level names of the standard library
        """
        return StdlibModuleNames().STDLIB_MODULE_NAMES

    def _is_subpath(self, path, base_paths):
        """
        Safely determine if a path is under a set of directory trees

        Args:
            path (str): Path to check
            base_paths (set[str] | list[str]): Base paths to check against

        Returns:
            bool: True if path is a subpath of any base_paths
        """
        for base in base_paths:
            try:
                if os.path.normcase(os.path.commonpath([path, base])) == os.path.normcase(base):
                    return True
            except ValueError:
                continue
        return False

    def classify_single_module(self, name):
        """
        Classify a single module name, the result is cached by name

        A name is classified at most once per project root: the generator asks
        for the same module for every import of it, in every generated file.

        Args:
            name (str): Module name

        Returns:
            ModuleType: Classification of the module
        """
        result = self._classified.get(name, None)
        if result is not None:
            return result
        result = self._classify_single_module(name)
        self._classified[name] = result
        return result

    def _classify_single_module(self, name):
        """
        Classify a single module name by its path

        Args:
            name (str): Module name

        Returns:
            ModuleType: Classification of the module
        """
        # Mark self as third-party
        if name == 'alasio':
            return ModuleType.THIRD_PARTY

        # 1. Built-in C modules are directly classified as standard library
        if name in sys.builtin_module_names:
            return ModuleType.STANDARD_LIBRARY

        # 2. Standard library modules are classified by name, the paths of the
        # interpreter can not tell them reliably, see StdlibModuleNames
        if name in self.stdlib_module_names:
            return ModuleType.STANDARD_LIBRARY

        import importlib.util
        try:
            spec = importlib.util.find_spec(name)
            if spec is None:
                return ModuleType.UNKNOWN
        except Exception:
            return ModuleType.UNKNOWN

        # 3. Extract actual physical path
        if spec.origin is None:
            if spec.submodule_search_locations:
                origin_path = os.path.realpath(list(spec.submodule_search_locations)[0])
            else:
                return ModuleType.UNKNOWN
        else:
            if spec.origin in ('built-in', 'frozen'):
                return ModuleType.STANDARD_LIBRARY
            origin_path = os.path.realpath(spec.origin)

        # 4. Classify by path range
        # Prioritize third-party check to avoid misclassification when the project has an embedded virtual environment
        if self._is_subpath(origin_path, self.site_paths):
            return ModuleType.THIRD_PARTY

        if self._is_subpath(origin_path, self.stdlib_paths):
            return ModuleType.STANDARD_LIBRARY

        if self._is_subpath(origin_path, [self.project_root]):
            return ModuleType.LOCAL_PROJECT

        return ModuleType.UNKNOWN
