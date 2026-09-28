"""
Build the shared libraries of the accelerators.

Every accelerator of the package is a C source next to it, compiled into a
shared library that is loaded with ctypes. The build is cached until the
source changes, and a machine without a C compiler skips it: the
accelerators are optional, the pure Python implementations of alasio stay
the reference when they are missing.

Usage:
    python -m alasio_speedup.build
    python -m alasio_speedup.build bit2_encode --force
    python -m alasio_speedup.build --compiler "D:/mingw64/bin/gcc.exe"
"""
import argparse
import os
import shutil
import stat
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

# mingw64 gcc of the local pack server, the compiler used when nothing is
# given on the command line or in the environment
MINGW64_GCC = r'E:\ProgramData\Pycharm\py38deps\mingw64\bin\gcc.exe'


class BuildError(RuntimeError):
    """Raised when a shared library cannot be built or loaded."""


def library_suffix():
    """
    Returns:
        str: File suffix of a shared library on this platform
    """
    if sys.platform == 'win32':
        return '.dll'
    elif sys.platform == 'darwin':
        return '.dylib'
    else:
        return '.so'


def source_files():
    """
    Returns:
        list[str]: C sources of the package, one per accelerator
    """
    return sorted(
        os.path.join(ROOT, name) for name in os.listdir(ROOT) if name.endswith('.c')
    )


def source_file(name):
    """
    Args:
        name (str): Stem of the library, e.g. 'bit2_encode'

    Returns:
        str: Path of the C source of the library
    """
    return os.path.join(ROOT, name + '.c')


def library_for(source):
    """
    Args:
        source (str): Path of the C source

    Returns:
        str: Path of the shared library built from it
    """
    return source[:-2] + library_suffix()


def is_file(path):
    """
    Args:
        path (str): File path to check

    Returns:
        bool: True if path is an existing regular file
    """
    try:
        return stat.S_ISREG(os.stat(path).st_mode)
    except OSError:
        return False


def find_compiler(compiler=''):
    """
    Find the C compiler to build the accelerators.

    Args:
        compiler (str): Explicit compiler, takes priority over the environment

    Returns:
        str: Path of the compiler

    Raises:
        BuildError: If no compiler is available
    """
    candidates = [
        compiler,
        os.environ.get('BIT2_CC', ''),
        os.environ.get('CC', ''),
        MINGW64_GCC,
        'gcc',
        'cc',
    ]
    for candidate in candidates:
        if not candidate:
            continue
        if '/' in candidate or '\\' in candidate:
            if is_file(candidate):
                return candidate
            continue
        found = shutil.which(candidate)
        if found:
            return found
    raise BuildError(
        f'No C compiler available, tried: {[c for c in candidates if c]}, '
        'set BIT2_CC to the compiler path'
    )


def compiler_flags(debug=False):
    """
    Args:
        debug (bool): Build with debug symbols and no optimization

    Returns:
        list[str]: Compiler flags to build a shared library
    """
    flags = ['-O0', '-g'] if debug else ['-O2']
    flags += ['-Wall', '-Wextra', '-shared']
    if sys.platform == 'win32':
        # link libgcc statically, the DLL is loaded by python and must not
        # depend on the compiler bin folder being on PATH
        flags += ['-static-libgcc']
    else:
        flags += ['-fPIC']
    return flags


def out_of_date(target, source):
    """
    Args:
        target (str): Built library
        source (str): C source file

    Returns:
        bool: True if the library is missing or older than the source
    """
    try:
        target_time = os.stat(target).st_mtime
        source_time = os.stat(source).st_mtime
    except FileNotFoundError:
        return True
    return source_time > target_time


def build_library(source, compiler='', debug=False, force=False):
    """
    Build one library, skip the build when it is newer than its source.

    Args:
        source (str): Path of the C source
        compiler (str): Explicit compiler path
        debug (bool): Build with debug symbols and no optimization
        force (bool): Rebuild even when the library is up to date

    Returns:
        str: Path of the built shared library

    Raises:
        BuildError: If no compiler is available or the build fails
    """
    target = library_for(source)
    if not force and not out_of_date(target, source):
        return target

    cc = find_compiler(compiler)
    flags = compiler_flags(debug)

    # build next to the target and replace it at once, a running process
    # may be holding the library and no half written file is ever loaded
    tmp = target + '.tmp'
    cmd = [cc] + flags + ['-o', tmp, source]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
    except (OSError, subprocess.SubprocessError) as e:
        raise BuildError(f'Failed to run the compiler {cc}: {e}')
    if result.returncode != 0:
        stderr = result.stderr.decode('utf-8', 'replace')
        raise BuildError(f'Compiler {cc} failed with code {result.returncode}:\n{stderr}')

    try:
        os.replace(tmp, target)
    except PermissionError:
        raise BuildError(
            f'Cannot replace {target}, a running process still has the library loaded'
        )
    return target


def main():
    parser = argparse.ArgumentParser(description='Build the shared libraries of alasio_speedup')
    parser.add_argument(
        'library', nargs='*',
        help='Stem of the libraries to build, defaults to every C source of the package',
    )
    parser.add_argument(
        '--compiler', default='',
        help='Path of the C compiler, defaults to BIT2_CC / CC / the mingw64 gcc of py38deps / gcc / cc',
    )
    parser.add_argument('--debug', action='store_true', help='Build with -O0 -g')
    parser.add_argument('--force', action='store_true', help='Rebuild even when up to date')
    args = parser.parse_args()

    if args.library:
        sources = [source_file(name) for name in args.library]
    else:
        sources = source_files()
    if not sources:
        print(f'No C source in {ROOT}')
        return 1

    for source in sources:
        print('Building:', source)
        path = build_library(
            source, compiler=args.compiler, debug=args.debug, force=args.force,
        )
        print(f'Built: {path} ({os.stat(path).st_size} bytes)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
