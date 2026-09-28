"""
Build and load the shared libraries of the accelerators.

Every accelerator declares one library: the C source next to it, the shared
library built from it by build.py, and the version of the C interface it
speaks. The library is built on first use and cached until the source
changes; a library that does not declare the expected interface version is
refused, so a stale or foreign library is never called with the wrong
signature (the caller falls back to the Python implementation instead).
"""
import ctypes

from alasio_speedup.build import BuildError, build_library, is_file, library_for, source_file

# name of the export every library of the package declares its interface
# version with, see bit2_encode.c
ABI_EXPORT = 'abi_version'


class AcceleratorLibrary:
    """
    One shared library of the accelerator package.

    Args:
        name (str): Stem of the C source and of the built library, e.g. 'bit2_encode'
        abi_version (int): Interface version this build of the package speaks,
            the library declares its own with the abi_version() export

    Attributes:
        name (str): Stem of the library
        source (str): Path of the C source
        path (str): Path of the built shared library
        abi_version (int): Interface version this module needs
    """

    def __init__(self, name, abi_version):
        self.name = name
        self.source = source_file(name)
        self.path = library_for(self.source)
        self.abi_version = abi_version
        self._cdll = None

    def build(self, compiler='', debug=False, force=False):
        """
        Build the library, skipped when it is up to date.

        Args:
            compiler (str): Explicit compiler path
            debug (bool): Build with debug symbols and no optimization
            force (bool): Rebuild even when the library is up to date

        Returns:
            str: Path of the built shared library

        Raises:
            BuildError: If no compiler is available or the build fails
        """
        return build_library(self.source, compiler=compiler, debug=debug, force=force)

    def load(self):
        """
        Load the library, building it on first use and caching it.

        Returns:
            ctypes.CDLL: The loaded library, its interface version verified

        Raises:
            BuildError: If the library is missing, cannot be built, or does
                not speak the expected interface version
        """
        if self._cdll is None:
            self._cdll = self._load()
        return self._cdll

    def _load(self):
        """
        Returns:
            ctypes.CDLL: The loaded library
        """
        try:
            path = self.build()
        except BuildError:
            # the build is not possible here (no compiler, read only install),
            # an already built library is still worth loading
            path = self.path
            if not is_file(path):
                raise

        lib = ctypes.CDLL(path)
        check_abi(lib, path, self.abi_version)
        return lib


def check_abi(lib, path, abi_version):
    """
    Refuse a library that does not speak the expected interface version.

    Args:
        lib (ctypes.CDLL): Loaded library
        path (str): Path the library was loaded from, for the message
        abi_version (int): Interface version this module needs

    Raises:
        BuildError: If the library does not export the version, or exports
            another one
    """
    if not hasattr(lib, ABI_EXPORT):
        raise BuildError(
            f'{path} does not export {ABI_EXPORT}(), it is not the library of this version'
        )
    version_function = getattr(lib, ABI_EXPORT)
    version_function.restype = ctypes.c_int64
    version_function.argtypes = []
    version = version_function()
    if version != abi_version:
        raise BuildError(f'{path} is ABI {version}, this module needs ABI {abi_version}')
