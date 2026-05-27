import os
import site
import sys
import sysconfig
import warnings

from alasio.backport.strenum import StrEnum
from alasio.ext.cache import cached_property
from alasio.ext.singleton import SingletonOptionalNamed


# Module type classification Enum
class ModuleType(StrEnum):
    STANDARD_LIBRARY = "standard_library"
    THIRD_PARTY = "third_party"
    LOCAL_PROJECT = "local_project"
    UNKNOWN = "unknown"


# ==========================================
# 2. Environment Library Scanner and Classifier
# ==========================================
class EnvLibraryScanner(metaclass=SingletonOptionalNamed):
    def __init__(self, project_root=None):
        """
        Initialize the library scanner

        Args:
            project_root (str): Project root directory. Defaults to None.
        """
        # Determine project root directory (absolute path)
        self.project_root = os.path.realpath(project_root or os.getcwd())

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

    def _get_path_category(self, path):
        """
        Determine the classification category of a directory path

        Args:
            path (str): Real path of the directory

        Returns:
            ModuleType: Classification category
        """
        if self._is_subpath(path, self.site_paths):
            return ModuleType.THIRD_PARTY
        if self._is_subpath(path, self.stdlib_paths):
            return ModuleType.STANDARD_LIBRARY
        if self._is_subpath(path, [self.project_root]):
            return ModuleType.LOCAL_PROJECT
        return ModuleType.UNKNOWN

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

        import importlib.util
        try:
            spec = importlib.util.find_spec(name)
            if spec is None:
                return ModuleType.UNKNOWN
        except Exception:
            return ModuleType.UNKNOWN

        # 2. Extract actual physical path
        if spec.origin is None:
            if spec.submodule_search_locations:
                origin_path = os.path.realpath(list(spec.submodule_search_locations)[0])
            else:
                return ModuleType.UNKNOWN
        else:
            if spec.origin in ('built-in', 'frozen'):
                return ModuleType.STANDARD_LIBRARY
            origin_path = os.path.realpath(spec.origin)

        # 3. Classify by path range
        # Prioritize third-party check to avoid misclassification when the project has an embedded virtual environment
        if self._is_subpath(origin_path, self.site_paths):
            return ModuleType.THIRD_PARTY

        if self._is_subpath(origin_path, self.stdlib_paths):
            return ModuleType.STANDARD_LIBRARY

        if self._is_subpath(origin_path, [self.project_root]):
            return ModuleType.LOCAL_PROJECT

        return ModuleType.UNKNOWN

    # ==========================================
    # 3. Core Cached Properties
    # ==========================================

    @cached_property
    def _scanned_metadata(self):
        """
        Scan and classify all available modules

        Returns:
            dict[str, ModuleType]: Mapping of module name to its type
        """
        classified = {}

        # 1. Built-in C modules (highest precedence, no physical path)
        for name in sys.builtin_module_names:
            classified[name] = ModuleType.STANDARD_LIBRARY

        # Use warnings context to block UserWarning from setuptools
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                category=UserWarning,
                message=".*Setuptools is replacing distutils.*"
            )

            # 2. Scan and classify modules folder by folder in sys.path
            import pkgutil
            for path in sys.path:
                if not path:
                    real_path = self.project_root
                else:
                    real_path = os.path.realpath(path)

                if not os.path.exists(real_path):
                    continue

                # Pre-determine the category of this path
                path_category = self._get_path_category(real_path)

                try:
                    # Fast directory listing using pkgutil.iter_modules
                    for module_info in pkgutil.iter_modules([real_path]):
                        name = module_info.name
                        # Shadowing: first module found in sys.path takes precedence
                        if name not in classified:
                            classified[name] = path_category
                except Exception:
                    # Gracefully skip inaccessible paths or broken zip files in sys.path
                    continue

        return classified

    @cached_property
    def all_importable(self):
        """
        Return a set of all scanned, directly importable top-level names

        Returns:
            set[str]: Set of module names
        """
        return set(self._scanned_metadata.keys())

    @cached_property
    def standard_library(self):
        """
        Return a set of scanned standard library names

        Returns:
            set[str]: Set of standard library module names
        """
        return {
            name for name, mtype in self._scanned_metadata.items()
            if mtype == ModuleType.STANDARD_LIBRARY
        }

    @cached_property
    def third_party(self):
        """
        Return a set of installed third-party library import names

        Returns:
            set[str]: Set of third-party module names
        """
        return {
            name for name, mtype in self._scanned_metadata.items()
            if mtype == ModuleType.THIRD_PARTY
        }
