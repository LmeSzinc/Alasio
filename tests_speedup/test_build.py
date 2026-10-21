"""
Tests for ``alasio_speedup.build``, the compiler driving of the package.

The command lines of both toolchains are pure functions, so almost nothing
here needs a compiler: the accelerator tests of this directory compile the
libraries with the toolchain of the runner, this file pins down what the
other toolchain would be handed.

The module is imported by path, not through the package: the accelerator
loader of alasio replaces ``sys.modules['alasio_speedup']`` with the
installed copy on first use, so a plain import would test whichever copy
the collection order left behind (an installed copy older than the
checkout, on a developer machine).
"""
import importlib.util
import os
import subprocess
import sys

import pytest


def load_build():
    """
    Import the build module of the checkout.

    Returns:
        module: The ``alasio_speedup.build`` module of this repository
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, 'alasio_speedup', 'build.py')
    spec = importlib.util.spec_from_file_location('alasio_speedup_build', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = load_build()


def forbidden(*args, **kwargs):
    """Stand in for a function the case expects not to be called."""
    raise AssertionError(f'must not be called: {args} {kwargs}')


def completed(returncode, stdout=b'', stderr=b''):
    """A finished compiler run."""
    return subprocess.CompletedProcess(['cc'], returncode, stdout, stderr)


def missing_path():
    """Path of a file that does not exist."""
    return os.path.join(os.path.dirname(sys.executable), 'no-such-compiler-alas')


class TestToolchain:
    """The command line family of a compiler."""

    @pytest.mark.parametrize("cc, expected", [
        # the msvc command line
        ('cl', build.TOOLCHAIN_MSVC),
        ('cl.exe', build.TOOLCHAIN_MSVC),
        ('CL.EXE', build.TOOLCHAIN_MSVC),
        ('clang-cl', build.TOOLCHAIN_MSVC),
        ('clang-cl.exe', build.TOOLCHAIN_MSVC),
        (r'C:\Program Files\Microsoft Visual Studio\2022\Enterprise\VC\Tools\MSVC'
         r'\14.44.35207\bin\Hostx64\x64\cl.exe', build.TOOLCHAIN_MSVC),
        # the gcc style command line
        ('gcc', build.TOOLCHAIN_GNU),
        ('gcc.exe', build.TOOLCHAIN_GNU),
        ('cc', build.TOOLCHAIN_GNU),
        ('clang', build.TOOLCHAIN_GNU),
        ('clang-clang', build.TOOLCHAIN_GNU),
        ('/usr/bin/gcc-14', build.TOOLCHAIN_GNU),
        (r'C:\msys64\mingw64\bin\gcc.exe', build.TOOLCHAIN_GNU),
        ('', build.TOOLCHAIN_GNU),
    ])
    def test_family(self, cc, expected):
        """The family is decided by the file name of the compiler."""
        assert build.toolchain(cc) == expected


class TestCompilerFlags:
    """The flags of the two toolchains."""

    def test_gnu_windows(self):
        """The gcc style build links libgcc statically on windows."""
        flags = build.compiler_flags('gcc', platform='win32')
        assert flags == ['-O2', '-Wall', '-Wextra', '-shared', '-static-libgcc']

    def test_gnu_posix(self):
        """The gcc style build is position independent outside windows."""
        flags = build.compiler_flags('gcc', platform='linux')
        assert flags == ['-O2', '-Wall', '-Wextra', '-shared', '-fPIC']

    def test_gnu_debug(self):
        """The debug build keeps the warnings and drops the optimization."""
        flags = build.compiler_flags('gcc', debug=True, platform='darwin')
        assert flags == ['-O0', '-g', '-Wall', '-Wextra', '-shared', '-fPIC']

    def test_msvc(self):
        """The msvc build is a DLL with a static runtime."""
        flags = build.compiler_flags('cl', platform='win32')
        assert flags == ['/nologo', '/LD', '/MT', '/W3', '/O2']

    def test_msvc_debug(self):
        """The msvc debug build has no optimization and debug info."""
        flags = build.compiler_flags('cl.exe', debug=True, platform='win32')
        assert flags == ['/nologo', '/LD', '/MT', '/W3', '/Od', '/Zi']

    def test_msvc_ignores_the_platform(self):
        """The msvc flags do not depend on the platform of the build."""
        assert build.compiler_flags('cl', platform='linux') == build.compiler_flags(
            'cl', platform='win32'
        )

    def test_platform_defaults_to_the_running_one(self):
        """Without a platform the flags are those of the runner."""
        flags = build.compiler_flags('gcc')
        assert ('-fPIC' in flags) is (sys.platform != 'win32')


class TestCompileCommand:
    """The command line that builds one library."""

    def test_gnu(self):
        """The gcc style command line names the output with -o."""
        cmd = build.compile_command('/usr/bin/gcc', 'a.c', 'a.so')
        assert cmd[0] == '/usr/bin/gcc'
        assert cmd[-3:] == ['-o', 'a.so', 'a.c']
        assert not any(arg.startswith('/Fe:') for arg in cmd)

    def test_msvc(self):
        """The msvc command line names the library with /Fe and the object
        directory with /Fo, and has no -o."""
        cmd = build.compile_command('C:/VS/cl.exe', 'a.c', 'a.dll.tmp', workdir='C:/scratch')
        assert cmd[0] == 'C:/VS/cl.exe'
        assert '/nologo' in cmd
        assert '/LD' in cmd
        assert '/MT' in cmd
        assert '/Fe:a.dll.tmp' in cmd
        assert '/Fo:C:/scratch' + os.sep in cmd
        assert cmd[-1] == 'a.c'
        assert '-o' not in cmd

    def test_msvc_without_a_work_directory(self):
        """The msvc toolchain refuses to build without a place for its objects."""
        with pytest.raises(build.BuildError):
            build.compile_command('cl', 'a.c', 'a.dll')


class TestCompilerCandidates:
    """The compilers the build prefers, in order."""

    def test_explicit_comes_first(self, monkeypatch):
        """The compiler of the call takes priority over the environment."""
        monkeypatch.setenv('BIT2_CC', '/env/gcc')
        candidates = build.compiler_candidates('/explicit/gcc')
        assert candidates[0] == '/explicit/gcc'
        assert candidates[1] == '/env/gcc'

    def test_windows_prefers_msvc(self, monkeypatch):
        """On windows cl.exe is tried before any gcc that happens to be on PATH."""
        monkeypatch.setattr(sys, 'platform', 'win32')
        for name in ('BIT2_CC', 'CC'):
            monkeypatch.delenv(name, raising=False)
        candidates = build.compiler_candidates()
        assert candidates[0] == 'cl'
        assert candidates.index('cl') < candidates.index('gcc')
        assert candidates.index('clang-cl') < candidates.index('gcc')

    def test_posix_has_no_msvc(self, monkeypatch):
        """The msvc names are not candidates outside windows."""
        monkeypatch.setattr(sys, 'platform', 'linux')
        for name in ('BIT2_CC', 'CC'):
            monkeypatch.delenv(name, raising=False)
        candidates = build.compiler_candidates()
        assert 'cl' not in candidates
        assert 'clang-cl' not in candidates
        assert candidates[0] == 'cc'

    def test_empty_entries_are_dropped(self, monkeypatch):
        """Unset environment variables and an empty compiler are not candidates."""
        for name in ('BIT2_CC', 'CC'):
            monkeypatch.delenv(name, raising=False)
        assert all(build.compiler_candidates())
        assert all(build.compiler_candidates(''))


class TestFindCompiler:
    """The compiler the build runs."""

    def test_explicit_file_wins(self, monkeypatch):
        """An existing explicit compiler is used even when the environment names another."""
        monkeypatch.setenv('BIT2_CC', os.__file__)
        assert build.find_compiler(sys.executable) == sys.executable

    def test_environment_is_used(self, monkeypatch):
        """Without an explicit compiler BIT2_CC decides."""
        monkeypatch.setenv('BIT2_CC', sys.executable)
        assert build.find_compiler() == sys.executable

    def test_missing_explicit_path_is_skipped(self, monkeypatch):
        """An explicit path that does not exist falls through to the next candidate."""
        monkeypatch.setenv('BIT2_CC', sys.executable)
        assert build.find_compiler(missing_path()) == sys.executable

    def test_no_compiler_at_all(self, monkeypatch):
        """A machine without any compiler says what it tried."""
        for name in ('BIT2_CC', 'CC'):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(build, 'MINGW64_GCC', missing_path())
        monkeypatch.setattr(build.shutil, 'which', lambda name: None)
        with pytest.raises(build.BuildError) as info:
            build.find_compiler()
        message = str(info.value)
        assert 'No C compiler available' in message
        assert 'BIT2_CC' in message


class TestInvoke:
    """The result of a compiler run, as the build reports it."""

    def test_success_is_silent(self, monkeypatch):
        """A run that returns zero raises nothing."""
        monkeypatch.setattr(build.subprocess, 'run', lambda *args, **kwargs: completed(0))
        build.invoke(['gcc', '-o', 'a.so', 'a.c'])

    def test_gnu_failure_reports_stderr(self, monkeypatch):
        """The diagnostics of a gcc style compiler are on stderr."""
        monkeypatch.setattr(
            build.subprocess, 'run',
            lambda *args, **kwargs: completed(1, stderr=b'a.c:3:5: error: boom'),
        )
        with pytest.raises(build.BuildError) as info:
            build.invoke(['gcc', '-o', 'a.so', 'a.c'])
        message = str(info.value)
        assert 'failed with code 1' in message
        assert 'gcc -o a.so a.c' in message
        assert 'a.c:3:5: error: boom' in message

    def test_msvc_failure_reports_stdout(self, monkeypatch):
        """cl reports its diagnostics on stdout, they must not be lost."""
        monkeypatch.setattr(
            build.subprocess, 'run',
            lambda *args, **kwargs: completed(2, stdout=b'a.c\r\nerror C2065: undeclared'),
        )
        with pytest.raises(build.BuildError) as info:
            build.invoke(['cl', '/LD', 'a.c'])
        message = str(info.value)
        assert 'failed with code 2' in message
        assert 'cl /LD a.c' in message
        assert 'error C2065: undeclared' in message

    def test_msvc_hint_without_the_environment(self, monkeypatch):
        """A cl without INCLUDE gets the developer prompt hint."""
        monkeypatch.delenv('INCLUDE', raising=False)
        monkeypatch.setattr(build.subprocess, 'run', lambda *args, **kwargs: completed(2))
        with pytest.raises(build.BuildError) as info:
            build.invoke(['cl', '/LD', 'a.c'])
        assert 'vcvars64.bat' in str(info.value)

    def test_msvc_hint_with_the_environment(self, monkeypatch):
        """A cl inside a developer prompt needs no hint."""
        monkeypatch.setenv('INCLUDE', r'C:\VS\include')
        monkeypatch.setattr(build.subprocess, 'run', lambda *args, **kwargs: completed(2))
        with pytest.raises(build.BuildError) as info:
            build.invoke(['cl', '/LD', 'a.c'])
        assert 'vcvars64.bat' not in str(info.value)

    def test_gnu_has_no_msvc_hint(self, monkeypatch):
        """The hint belongs to the msvc toolchain only."""
        monkeypatch.delenv('INCLUDE', raising=False)
        monkeypatch.setattr(build.subprocess, 'run', lambda *args, **kwargs: completed(1))
        with pytest.raises(build.BuildError) as info:
            build.invoke(['gcc', 'a.c'])
        assert 'vcvars64.bat' not in str(info.value)


class TestRunCompiler:
    """The command and the working directory handed to the compiler."""

    def test_msvc_builds_in_a_scratch_directory(self, monkeypatch):
        """cl runs where its object file, import library and export file land."""
        calls = []
        monkeypatch.setattr(build, 'invoke', lambda cmd, cwd=None: calls.append((cmd, cwd)))
        build.run_compiler('C:/VS/cl.exe', 'a.c', 'a.dll.tmp')
        cmd, cwd = calls[0]
        assert cwd, 'cl must run in a scratch directory'
        assert '/Fo:' + cwd + os.sep in cmd
        assert '/Fe:a.dll.tmp' in cmd
        assert cmd[-1] == 'a.c'

    def test_gnu_builds_in_place(self, monkeypatch):
        """The gcc style toolchain writes nothing but the library."""
        calls = []
        monkeypatch.setattr(build, 'invoke', lambda cmd, cwd=None: calls.append((cmd, cwd)))
        build.run_compiler('/usr/bin/gcc', 'a.c', 'a.so')
        cmd, cwd = calls[0]
        assert cwd is None
        assert cmd[-3:] == ['-o', 'a.so', 'a.c']


class TestBuildLibrary:
    """The incremental rule of the build."""

    def test_up_to_date_library_skips_the_compiler(self, monkeypatch):
        """A library newer than its source is not rebuilt, and no compiler is looked up."""
        monkeypatch.setattr(build, 'out_of_date', lambda target, source: False)
        monkeypatch.setattr(build, 'find_compiler', forbidden)
        assert build.build_library('a.c') == build.library_for('a.c')

    def test_stale_library_is_replaced_atomically(self, monkeypatch):
        """A stale library is built next to the target and moved over it."""
        target = build.library_for('a.c')
        calls = []
        monkeypatch.setattr(build, 'out_of_date', lambda target, source: True)
        monkeypatch.setattr(build, 'find_compiler', lambda compiler='': '/usr/bin/gcc')
        monkeypatch.setattr(
            build, 'run_compiler',
            lambda cc, source, output, debug=False: calls.append((cc, source, output)),
        )
        monkeypatch.setattr(build.os, 'replace', lambda source, output: calls.append((source, output)))
        assert build.build_library('a.c') == target
        assert calls == [
            ('/usr/bin/gcc', 'a.c', target + '.tmp'),
            (target + '.tmp', target),
        ]
