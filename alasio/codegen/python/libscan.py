from __future__ import annotations

import importlib.util
import os
import pkgutil
import site
import sys
import sysconfig
import warnings
from functools import cached_property

from alasio.ext.backport.strenum import StrEnum


# Module type classification Enum
class ModuleType(StrEnum):
    STANDARD_LIBRARY = "standard_library"
    THIRD_PARTY = "third_party"
    LOCAL_PROJECT = "local_project"
    UNKNOWN = "unknown"


# ==========================================
# 2. Environment Library Scanner and Classifier
# ==========================================
class EnvLibraryScanner:
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
        for path in sys.path:
            if path:
                real_p = os.path.realpath(path)
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
                if os.path.commonpath([path, base]) == base:
                    return True
            except ValueError:
                continue
        return False

    def _classify_single_module(self, name):
        """
        Classify a single module name by its path

        Args:
            name (str): Module name

        Returns:
            ModuleType: Classification of the module
        """
        # 1. Built-in C modules are directly classified as standard library
        if name in sys.builtin_module_names:
            return ModuleType.STANDARD_LIBRARY

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
        raw_names = set(sys.builtin_module_names)

        # Use warnings context to block UserWarning from setuptools
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                category=UserWarning,
                message=".*Setuptools is replacing distutils.*"
            )

            # 1. Accurately and explicitly scan standard library physical directories
            # (ensures capturing low-level C libraries like _lzma, _ssl)
            for path in self.stdlib_paths:
                if os.path.isdir(path):
                    # pkgutil.iter_modules([path]) scans only the specified directory,
                    # effectively finding missing C libraries
                    for module_info in pkgutil.iter_modules([path]):
                        raw_names.add(module_info.name)

            # 2. Explicitly scan third-party library physical directories
            for path in self.site_paths:
                if os.path.isdir(path):
                    for module_info in pkgutil.iter_modules([path]):
                        raw_names.add(module_info.name)

            # 3. Fallback scan of global modules
            for module_info in pkgutil.iter_modules():
                raw_names.add(module_info.name)

            # 4. Classify all scanned names
            for name in raw_names:
                classified[name] = self._classify_single_module(name)

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
