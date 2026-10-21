"""
Build the shared libraries of the accelerators.

Every accelerator of the package is a C source next to it, compiled into a
shared library that is loaded with ctypes. The build is cached until the
source changes, and a machine without a C compiler skips it: the
accelerators are optional, the pure Python implementations of alasio stay
the reference when they are missing.

The compiler is the native one of the platform: `cl.exe` of visual studio
on windows, `gcc` or `clang` elsewhere. Both command line families are
spoken, see toolchain(); the library is plain C, so either toolchain
produces a shared object ctypes can load.

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
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))

# how a compiler is called, see toolchain()
TOOLCHAIN_MSVC = 'msvc'
TOOLCHAIN_GNU = 'gnu'

# names that speak the msvc command line, clang-cl included
MSVC_COMPILERS = ('cl', 'cl.exe', 'clang-cl', 'clang-cl.exe')

# mingw64 gcc of the local pack server, the last candidate: it exists on the
# machine it was set up on and nowhere else
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
    stem = source[:-2]
    suffix = library_suffix()
    if sys.platform == 'win32':
        return stem + suffix
    # outside windows python imports ``pathcomb.so`` as an extension module
    # before it ever looks at ``pathcomb.py``, so the library of a module
    # carries the unix ``lib`` prefix: ``libpathcomb.so`` shadows no module
    # and is still what ctypes loads
    directory, sep, name = stem.replace('\\', '/').rpartition('/')
    return f'{directory}{sep}lib{name}{suffix}'


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


def toolchain(cc):
    """
    Command line family of a compiler.

    Args:
        cc (str): Path or name of the compiler

    Returns:
        str: TOOLCHAIN_MSVC for the compilers that take /flags,
            TOOLCHAIN_GNU for the gcc style command line of everything else
    """
    name = cc.replace('\\', '/').rpartition('/')[2].lower()
    if name in MSVC_COMPILERS:
        return TOOLCHAIN_MSVC
    return TOOLCHAIN_GNU


def compiler_candidates(compiler=''):
    """
    Candidate compilers of this platform, most preferred first.

    Args:
        compiler (str): Explicit compiler, takes priority over the environment

    Returns:
        list[str]: Compiler paths or names to try, the empty ones removed
    """
    candidates = [compiler, os.environ.get('BIT2_CC', ''), os.environ.get('CC', '')]
    if sys.platform == 'win32':
        # cl.exe is the toolchain of windows and of python itself, and the
        # one a developer prompt puts on PATH: a gcc that happens to be
        # installed beside it (mingw, strawberry perl) is not the native
        # choice, and it may even build for another architecture
        candidates += ['cl', 'clang-cl', 'gcc', 'cc']
    else:
        candidates += ['cc', 'gcc', 'clang']
    candidates.append(MINGW64_GCC)
    return [candidate for candidate in candidates if candidate]


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
    candidates = compiler_candidates(compiler)
    for candidate in candidates:
        if '/' in candidate or '\\' in candidate:
            if is_file(candidate):
                return candidate
            continue
        found = shutil.which(candidate)
        if found:
            return found
    raise BuildError(
        f'No C compiler available, tried: {candidates}, '
        'set BIT2_CC to the compiler path'
    )


def compiler_flags(cc, debug=False, platform=None):
    """
    Args:
        cc (str): Compiler to build the flags of, see toolchain()
        debug (bool): Build with debug symbols and no optimization
        platform (str): Value of sys.platform to build for, defaults to the
            platform the build runs on

    Returns:
        list[str]: Compiler flags to build a shared library
    """
    platform = sys.platform if platform is None else platform
    if toolchain(cc) == TOOLCHAIN_MSVC:
        # /LD builds a DLL, /MT links the runtime statically so the library
        # does not depend on a redistributable being installed, /nologo
        # keeps the banner out of the log
        flags = ['/nologo', '/LD', '/MT', '/W3']
        flags += ['/Od', '/Zi'] if debug else ['/O2']
        return flags
    flags = ['-O0', '-g'] if debug else ['-O2']
    flags += ['-Wall', '-Wextra', '-shared']
    if platform == 'win32':
        # link libgcc statically, the DLL is loaded by python and must not
        # depend on the compiler bin folder being on PATH
        flags += ['-static-libgcc']
    else:
        flags += ['-fPIC']
    return flags


def compile_command(cc, source, output, workdir=None, debug=False):
    """
    Command line that builds one source into one shared library.

    Args:
        cc (str): Compiler
        source (str): C source
        output (str): Path of the library to write
        workdir (str): Directory the intermediate files go to, the msvc
            toolchain writes its object file there, the gnu toolchains
            ignore it
        debug (bool): Build with debug symbols and no optimization

    Returns:
        list[str]: The command line, the compiler first
    """
    flags = compiler_flags(cc, debug=debug)
    if toolchain(cc) == TOOLCHAIN_MSVC:
        # /Fe names the library, /Fo the directory of the object file; the
        # import library and the export file land in the working directory
        if workdir is None:
            raise BuildError(f'The msvc toolchain needs a work directory, no /Fo for {source}')
        return [cc] + flags + ['/Fe:' + output, '/Fo:' + workdir + os.sep, source]
    return [cc] + flags + ['-o', output, source]


def invoke(cmd, cwd=None):
    """
    Run the compiler, raise BuildError when it does not succeed.

    Args:
        cmd (list[str]): Command line, the compiler first
        cwd (str): Working directory of the compiler, None for the current one

    Raises:
        BuildError: If the compiler cannot be run or returns a non zero code
    """
    cc = cmd[0]
    try:
        result = subprocess.run(
            cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise BuildError(f'Failed to run the compiler {cc}: {e}')
    if result.returncode == 0:
        return
    # cl reports its diagnostics on stdout, gcc on stderr, keep both
    output = (result.stdout + result.stderr).decode('utf-8', 'replace').strip()
    message = f'Compiler {cc} failed with code {result.returncode}: {" ".join(cmd)}'
    if output:
        message += f'\n{output}'
    if toolchain(cc) == TOOLCHAIN_MSVC and not os.environ.get('INCLUDE'):
        message += (
            '\ncl.exe needs the visual studio environment: run the build from a '
            'developer prompt (vcvars64.bat) so INCLUDE and LIB are set'
        )
    raise BuildError(message)


def run_compiler(cc, source, output, debug=False):
    """
    Build one source into one shared library.

    Args:
        cc (str): Compiler
        source (str): C source
        output (str): Path of the library to write
        debug (bool): Build with debug symbols and no optimization

    Raises:
        BuildError: If the compiler fails
    """
    if toolchain(cc) == TOOLCHAIN_MSVC:
        # cl writes the object file, the import library and the export file
        # where it runs, keep them out of the package directory
        with tempfile.TemporaryDirectory(prefix='alasio_speedup-build-') as scratch:
            invoke(compile_command(cc, source, output, scratch, debug=debug), cwd=scratch)
        return
    invoke(compile_command(cc, source, output, debug=debug))


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

    # build next to the target and replace it at once, a running process
    # may be holding the library and no half written file is ever loaded
    tmp = target + '.tmp'
    run_compiler(cc, source, tmp, debug=debug)

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
        help='Path of the C compiler, defaults to BIT2_CC / CC / cl / gcc / cc, '
             'see compiler_candidates()',
    )
    parser.add_argument(
        '--debug', action='store_true', help='Build without optimization and with debug info',
    )
    parser.add_argument('--force', action='store_true', help='Rebuild even when up to date')
    args = parser.parse_args()

    if args.library:
        sources = [source_file(name) for name in args.library]
    else:
        sources = source_files()
    if not sources:
        print(f'No C source in {ROOT}')
        return 1

    # resolve the compiler once, so the log names the toolchain and a machine
    # without any compiler says so before the first build
    try:
        cc = find_compiler(args.compiler)
    except BuildError as e:
        print(e)
        return 1
    print(f'Compiler: {cc} ({toolchain(cc)})')

    for source in sources:
        print('Building:', source)
        try:
            path = build_library(source, compiler=cc, debug=args.debug, force=args.force)
        except BuildError as e:
            print(e)
            return 1
        print(f'Built: {path} ({os.stat(path).st_size} bytes)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
