import os
import site
import sys
import sysconfig

from alasio.backport.strenum import StrEnum
from alasio.codegen.ruff.stdlist import STD_SET
from alasio.ext.singleton import SingletonNamed


# Module type classification Enum
class ModuleType(StrEnum):
    STANDARD_LIBRARY = "standard_library"
    THIRD_PARTY = "third_party"
    LOCAL_PROJECT = "local_project"
    UNKNOWN = "unknown"


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
        # interpreter can not tell them reliably, see alasio.codegen.stdlist
        if name in STD_SET:
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
