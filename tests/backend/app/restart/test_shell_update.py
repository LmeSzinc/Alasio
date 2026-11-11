"""
Tests for the shell update: the install context, the staged archive and the
updater process (shell_update.py)

The file system is the in-memory one and the process start is a stand-in: what
these tests pin is the contract with the updater (the environment it reads, the
paths it works on) and the judgement this module makes before anything is
touched.
"""
import hashlib
import json
import os
import subprocess
import sys

import pytest

from alasio.backend.app.restart import shell_update as shell_module
from alasio.backend.app.restart.shell_update import ShellTarget, ShellUpdate
from alasio.ext.path import PathStr
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401

pytestmark = [
    pytest.mark.parallel,
]

# The install context is only read from absolute paths, and os.path.isabs()
# stopped counting a rooted path without a drive ("\root") as absolute on
# windows in python 3.13: the root of the fake file system is the drive of the
# real working directory, so the paths of the test are absolute everywhere
WORK = os.path.abspath(os.sep)
ROOT = os.path.join(WORK, 'root')
ASAR = os.path.join(ROOT, 'resources', 'app.asar')
SRC = os.path.join(ROOT, 'resources', 'new-app.asar')
EXE = os.path.join(ROOT, 'Alasio.exe')
BUNDLED = os.path.join(ROOT, 'alasio', 'deploy_data', 'webapp', 'app.asar')
UPDATER = os.path.join(ROOT, 'alasio', 'deploy', 'updater.py')

OLD = b'old archive'
NEW = b'new archive'
# One read of the module's own staging code, so the test measures what the
# implementation does instead of repeating a literal
CHUNK_SIZE = shell_module.CHUNK_SIZE


def digest_of(content):
    """
    Args:
        content (bytes): File content

    Returns:
        str: Its sha256, lowercase hex
    """
    return hashlib.sha256(content).hexdigest()


def make_environ(**overrides):
    """
    Build the environment of a backend running inside a packaged electron.

    Args:
        overrides ("dict[str, str]"): Values replacing the defaults, None
            removes the key

    Returns:
        "dict[str, str]": The environment
    """
    environ = {
        'ALASIO_APP_ASAR': ASAR,
        'ALASIO_APP_EXE': EXE,
        'ALASIO_APP_CWD': ROOT,
        'ALASIO_APP_ARGV': json.dumps(['--foo', 'bar baz']),
        'ALASIO_APP_ELECTRON_PID': '4242',
    }
    for key, value in overrides.items():
        if value is None:
            environ.pop(key, None)
        else:
            environ[key] = value
    return environ


@pytest.fixture
def make_shell(fs, monkeypatch):
    """
    Factory of a shell update configured over the fake file system.

    The package root and the project root are pointed into the fake file
    system, so the digest comparison and the staging write nothing real. The
    start marker of the host is a stand-in: the pid of the context is 4242 and
    every marker is pid * 10, so no test depends on a real process.

    Returns:
        Callable: (content_installed=OLD, content_bundled=NEW) -> ShellUpdate
    """
    monkeypatch.setattr(shell_module.env, 'ALASIO_ROOT', PathStr.new(ROOT))
    monkeypatch.setattr(shell_module.env, 'PROJECT_ROOT', PathStr.new(ROOT))
    monkeypatch.setattr(shell_module, 'start_time', lambda pid: pid * 10)

    def build(content_installed=OLD, content_bundled=NEW):
        """
        Args:
            content_installed (bytes): The installed archive, None to leave it
                out
            content_bundled (bytes): The archive of the package, None to leave
                it out

        Returns:
            ShellUpdate: The configured instance
        """
        if content_installed is not None:
            fs.create_file(ASAR, contents=content_installed)
        if content_bundled is not None:
            fs.create_file(BUNDLED, contents=content_bundled)
        shell = ShellUpdate()
        shell.configure(make_environ())
        return shell

    return build


@pytest.fixture
def make_detached_shell(fs, monkeypatch):
    """
    Returns:
        Callable: a ShellUpdate of a process with no install context
    """
    monkeypatch.setattr(shell_module.env, 'ALASIO_ROOT', PathStr.new(ROOT))
    monkeypatch.setattr(shell_module.env, 'PROJECT_ROOT', PathStr.new(ROOT))

    def build(content_bundled=NEW):
        """
        Args:
            content_bundled (bytes): The archive of the package

        Returns:
            ShellUpdate: The instance, never configured
        """
        fs.create_file(BUNDLED, contents=content_bundled)
        return ShellUpdate()

    return build


class TestShellTarget:
    """The install context, read from the environment of the chain."""

    def test_valid(self):
        """Every value is read and the staged path is derived from the target."""
        target = ShellTarget.from_environ(make_environ())
        assert target.asar == ASAR
        assert target.exe == EXE
        assert target.cwd == ROOT
        assert target.argv == ['--foo', 'bar baz']
        assert target.electron_pid == 4242
        assert target.src == SRC

    def test_the_old_mode_flag_is_gone(self):
        """The host pid is the switch; ELECTRON is not read at all."""
        assert ShellTarget.from_environ(make_environ(ELECTRON=None)) is not None
        assert ShellTarget.from_environ(make_environ(ELECTRON='0')) is not None

    def test_the_host_is_not_resolved_at_parse_time(self, monkeypatch):
        """The pid is a switch first: a dead one still yields the context."""
        def gone(pid):
            raise ProcessLookupError(pid)

        monkeypatch.setattr(shell_module, 'start_time', gone)
        target = ShellTarget.from_environ(make_environ())
        assert target is not None
        assert target.electron_pid == 4242

    def test_host_start_of_a_live_process(self, monkeypatch):
        """The marker is read on demand, when the archive has to be replaced."""
        monkeypatch.setattr(shell_module, 'start_time', lambda pid: pid * 10)
        assert ShellTarget.from_environ(make_environ()).host_start() == 42420

    @pytest.mark.parametrize('error', [
        ProcessLookupError(4242),
        PermissionError(5, 'denied'),
        NotImplementedError(4242),
    ])
    def test_host_start_of_an_unreachable_process(self, monkeypatch, error):
        """A host that cannot be described is reported as missing."""
        def raise_error(pid):
            raise error

        monkeypatch.setattr(shell_module, 'start_time', raise_error)
        assert ShellTarget.from_environ(make_environ()).host_start() is None

    def test_a_real_dead_process_has_no_start(self):
        """The check runs against the kernel, not against a parsed number."""
        process = subprocess.Popen([sys.executable, '-c', 'pass'])
        process.wait(timeout=60)
        target = ShellTarget.from_environ(make_environ(**{
            'ALASIO_APP_ELECTRON_PID': str(process.pid)}))
        assert target is not None
        assert target.host_start() is None

    def test_a_real_live_process_has_a_start(self):
        """A running pid this user owns is a host, whoever started it."""
        process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        try:
            target = ShellTarget.from_environ(make_environ(**{
                'ALASIO_APP_ELECTRON_PID': str(process.pid)}))
            marker = target.host_start()
        finally:
            process.kill()
            process.wait(timeout=60)
        assert target is not None
        assert target.electron_pid == process.pid
        assert isinstance(marker, int)

    @pytest.mark.parametrize('name', [
        'ALASIO_APP_ASAR',
        'ALASIO_APP_EXE',
        'ALASIO_APP_CWD',
        'ALASIO_APP_ARGV',
        'ALASIO_APP_ELECTRON_PID',
    ])
    def test_half_a_context_is_no_context(self, name):
        """Every variable is required: half of them would start the wrong app."""
        assert ShellTarget.from_environ(make_environ(**{name: None})) is None

    @pytest.mark.parametrize('name, value', [
        ('ALASIO_APP_ASAR', 'resources/app.asar'),
        ('ALASIO_APP_EXE', 'Alasio.exe'),
        ('ALASIO_APP_CWD', '.'),
        ('ALASIO_APP_ASAR', os.path.join(ROOT, 'resources', 'other.bin')),
        ('ALASIO_APP_ARGV', 'not json'),
        ('ALASIO_APP_ARGV', json.dumps({'a': 1})),
        ('ALASIO_APP_ARGV', json.dumps([1, 2])),
        ('ALASIO_APP_ELECTRON_PID', 'zero'),
        ('ALASIO_APP_ELECTRON_PID', '0'),
        ('ALASIO_APP_ELECTRON_PID', '-3'),
    ])
    def test_invalid(self, name, value):
        """A value that cannot be used makes the whole context unusable."""
        assert ShellTarget.from_environ(make_environ(**{name: value})) is None

    def test_configure_reports_the_context(self, make_detached_shell):
        """configure() reads once and answers whether the process has one."""
        shell = make_detached_shell()
        assert shell.configure(make_environ()) is True
        assert shell.available is True
        assert shell.configure({}) is False
        assert shell.available is False


class TestConfigure:
    """Reading the context once per process, and saying why it is missing."""

    def test_no_context_is_a_normal_situation(self):
        """A development checkout has no host: one line, no complaint."""
        with logger.mock_capture_writer() as capture:
            assert ShellUpdate().configure({}) is False
        assert capture.concat() == """\
INFO | [Shell] No install context: the electron archive is not managed by this process
"""

    def test_a_partial_context_is_reported(self):
        """Some of the archive variables are here: a broken chain, not a dev one."""
        with logger.mock_capture_writer() as capture:
            assert ShellUpdate().configure(make_environ(ALASIO_APP_ASAR=None)) is False
        assert capture.concat() == """\
WARNING | [Shell] The install context is incomplete or malformed: no shell update
"""

    def test_a_development_checkout_is_not_a_complaint(self):
        """Only the host pid: electron in dev mode, nothing to manage."""
        with logger.mock_capture_writer() as capture:
            assert ShellUpdate().configure(
                make_environ(
                    ALASIO_APP_ASAR=None, ALASIO_APP_EXE=None,
                    ALASIO_APP_CWD=None, ALASIO_APP_ARGV=None,
                )) is False
        assert capture.concat() == """\
INFO | [Shell] No install context: the electron archive is not managed by this process
"""

    def test_the_host_is_not_probed_at_startup(self, monkeypatch):
        """The pid is a switch here: the process behind it is asked later."""
        def boom(pid):
            raise AssertionError('the host must not be resolved at startup')

        monkeypatch.setattr(shell_module, 'start_time', boom)
        with logger.mock_capture_writer() as capture:
            assert ShellUpdate().configure(make_environ()) is True
        assert capture.concat() == f"""\
INFO | [Shell] Install context: {ASAR} (exe={EXE}, host pid=4242)
"""


class TestPending:
    """The read-only question the flows ask before they decide."""

    def test_pending_when_the_package_carries_another_archive(self, make_shell):
        """The two files differ: a relaunch would install something."""
        assert make_shell(content_installed=OLD, content_bundled=NEW).pending() is True

    def test_not_pending_when_the_same(self, make_shell):
        """The installed archive is already the one of the package."""
        assert make_shell(content_installed=NEW, content_bundled=NEW).pending() is False

    def test_pending_when_the_installation_has_no_archive(self, make_shell):
        """A missing installed file is a difference, not an error."""
        assert make_shell(content_installed=None, content_bundled=NEW).pending() is True

    def test_not_pending_without_a_package_archive(self, make_shell):
        """A build without the webapp maintains no archive."""
        assert make_shell(content_installed=OLD, content_bundled=None).pending() is False

    def test_not_pending_when_the_host_is_gone(self, make_shell, monkeypatch):
        """A pending archive with no host to wait for is not installed."""
        shell = make_shell(content_installed=OLD, content_bundled=NEW)

        def gone(pid):
            raise ProcessLookupError(pid)

        monkeypatch.setattr(shell_module, 'start_time', gone)
        with logger.mock_capture_writer() as capture:
            assert shell.pending() is False
        assert capture.concat() == """\
WARNING | [Shell] The electron process of pid 4242 does not exist: the archive cannot be replaced, keeping the installed one
"""

    def test_not_pending_without_a_context(self, make_detached_shell):
        """A development checkout has nothing to compare and nothing to do."""
        assert make_detached_shell().pending() is False


class TestStage:
    """Writing the package archive next to the installed one."""

    def test_stages_the_package_archive(self, make_shell):
        """The installed file is never touched, the staged one carries the bytes."""
        shell = make_shell(content_installed=OLD, content_bundled=NEW)
        staged = shell.stage()
        assert staged.src == SRC
        assert staged.dst == ASAR
        assert staged.sha256 == digest_of(NEW)
        with open(SRC, 'rb') as f:
            assert f.read() == NEW
        with open(ASAR, 'rb') as f:
            assert f.read() == OLD

    def test_nothing_to_do(self, make_shell):
        """An installation that is current stages nothing."""
        assert make_shell(content_installed=NEW, content_bundled=NEW).stage() is None

    def test_without_a_context(self, make_detached_shell):
        """A process that may not install anything writes nothing."""
        assert make_detached_shell().stage() is None

    def test_without_a_package_archive(self, make_shell):
        """Nothing to install, and nothing to complain about."""
        assert make_shell(content_installed=OLD, content_bundled=None).stage() is None

    def test_without_a_host(self, make_shell, monkeypatch):
        """Nothing is staged for a chain that can never come down."""
        shell = make_shell(content_installed=OLD, content_bundled=NEW)

        def gone(pid):
            raise ProcessLookupError(pid)

        monkeypatch.setattr(shell_module, 'start_time', gone)
        assert shell.stage() is None
        assert not os.path.exists(SRC)

    def test_the_staged_archive_is_reused(self, make_shell, monkeypatch):
        """The second flow that asks does not rewrite the same bytes."""
        shell = make_shell(content_installed=OLD, content_bundled=NEW)
        writes = []
        original = shell_module.atomic_write_stream

        def counting(file, chunks):
            writes.append(str(file))
            return original(file, chunks)

        monkeypatch.setattr(shell_module, 'atomic_write_stream', counting)
        first = shell.stage()
        second = shell.stage()
        assert (first.src, first.sha256) == (second.src, second.sha256)
        assert writes == [SRC]

    def test_the_archive_is_never_loaded_into_memory(self, make_shell, monkeypatch):
        """Both the digest and the copy read the file one chunk at a time."""
        content = bytes(range(256)) * (CHUNK_SIZE // 256 + 1)
        shell = make_shell(content_installed=OLD, content_bundled=content)
        reads = []
        real_open = open

        class Reader:
            """A file that records how much the caller asked for at once."""

            def __init__(self, handle):
                self._handle = handle

            def read(self, size=-1):
                reads.append(size)
                return self._handle.read(size)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self._handle.close()

        def measuring_open(path, mode='r', *args, **kwargs):
            handle = real_open(path, mode, *args, **kwargs)
            if 'b' in mode and str(path) == str(shell.bundled):
                return Reader(handle)
            return handle

        # the module looks the name up as a global before the builtin one
        monkeypatch.setattr(shell_module, 'open', measuring_open, raising=False)
        staged = shell.stage()
        assert staged is not None
        assert reads
        assert max(reads) == CHUNK_SIZE
        assert all(size == CHUNK_SIZE for size in reads)


class TestDigestCache:
    """The digests are memoized, tied to the file they were read from."""

    @staticmethod
    def counting_digest(monkeypatch, shell):
        """
        Count the reads of the archive bodies by ``ShellUpdate.digest``.

        Args:
            monkeypatch (pytest.MonkeyPatch): Fixture of the test
            shell (ShellUpdate): The configured shell update under test

        Returns:
            list: Paths that were digested, in order
        """
        reads = []
        real_digest = shell_module.ShellUpdate.digest

        def counting(file):
            reads.append(str(file))
            return real_digest(file)

        monkeypatch.setattr(shell_module.ShellUpdate, 'digest', staticmethod(counting))
        return reads

    def test_two_questions_read_each_archive_once(self, make_shell, monkeypatch):
        """pending() twice costs one read of each file, not two."""
        shell = make_shell(content_installed=OLD, content_bundled=NEW)
        reads = self.counting_digest(monkeypatch, shell)
        assert shell.pending() is True
        assert shell.pending() is True
        assert reads == [str(shell.bundled), str(ASAR)]

    def test_stage_reuses_the_digests_of_the_question(self, make_shell, monkeypatch):
        """The handover does not read the same two files again."""
        shell = make_shell(content_installed=OLD, content_bundled=NEW)
        reads = self.counting_digest(monkeypatch, shell)
        assert shell.pending() is True
        assert shell.stage() is not None
        # bundled, installed, and the staged file the stage() itself checks
        assert reads == [str(shell.bundled), str(ASAR), str(SRC)]

    def test_a_rewritten_package_archive_is_read_again(self, make_shell, monkeypatch):
        """An update flow can rewrite the package tree while the backend runs."""
        from types import SimpleNamespace

        shell = make_shell(content_installed=OLD, content_bundled=NEW)
        reads = self.counting_digest(monkeypatch, shell)
        state = {'mtime': 1}
        real_stat = shell_module.os.stat

        # os.stat is replaced for the whole process, so the stand-in takes the
        # same arguments as the original: other callers (pathlib in pytest, the
        # modules of the fake file system) keep using their own keywords
        def fake_stat(path, *args, **kwargs):
            if str(path) == str(shell.bundled):
                return SimpleNamespace(st_mtime_ns=state['mtime'], st_size=len(NEW))
            return real_stat(path, *args, **kwargs)

        monkeypatch.setattr(shell_module.os, 'stat', fake_stat)
        assert shell.pending() is True
        assert shell.pending() is True
        assert reads.count(str(shell.bundled)) == 1
        # the file was replaced: the memo no longer describes it
        state['mtime'] = 2
        assert shell.pending() is True
        assert reads.count(str(shell.bundled)) == 2

    def test_the_installed_archive_is_read_once_per_process(self, make_shell, monkeypatch):
        """Only the updater replaces it, and only after this process is gone."""
        shell = make_shell(content_installed=OLD, content_bundled=NEW)
        reads = self.counting_digest(monkeypatch, shell)
        assert shell.pending() is True
        assert shell.stage() is not None
        assert shell.pending() is True
        assert reads.count(str(ASAR)) == 1

    def test_both_digests_are_cached_properties(self, make_shell):
        """The installed digest is a string, the bundled one a three-tuple."""
        shell = make_shell(content_installed=OLD, content_bundled=NEW)
        assert shell.pending() is True
        installed = shell.__dict__['installed_digest']
        bundled = shell.__dict__['bundled_digest']
        assert installed == digest_of(OLD)
        assert isinstance(bundled, tuple) and len(bundled) == 3
        mtime_ns, size, digest = bundled
        assert (mtime_ns, size) == (
            os.stat(shell.bundled).st_mtime_ns, os.stat(shell.bundled).st_size)
        assert digest == digest_of(NEW)

    def test_reset_drops_the_cached_digests(self, make_shell):
        """A fresh process has a fresh view of both archives."""
        shell = make_shell(content_installed=OLD, content_bundled=NEW)
        assert shell.pending() is True
        shell.reset()
        assert 'installed_digest' not in shell.__dict__
        assert 'bundled_digest' not in shell.__dict__


class TestAsync:
    """The async entry points the flows use (the file work off the loop)."""

    @pytest.mark.trio
    async def test_pending_async_answers_the_same_question(self, make_shell):
        """The thread hop does not change the answer."""
        shell = make_shell(content_installed=OLD, content_bundled=NEW)
        assert await shell.pending_async() is True

    @pytest.mark.trio
    async def test_pending_async_reports_a_current_installation(self, make_shell):
        """The same archives: nothing to install, through the async entry too."""
        shell = make_shell(content_installed=NEW, content_bundled=NEW)
        assert await shell.pending_async() is False

    @pytest.mark.trio
    async def test_stage_async_returns_what_stage_returns(self, make_shell):
        """The handover gets the same staged archive through the async call."""
        shell = make_shell(content_installed=OLD, content_bundled=NEW)
        staged = await shell.stage_async()
        assert staged.src == SRC
        assert staged.sha256 == digest_of(NEW)

    @pytest.mark.trio
    async def test_stage_async_swallows_a_write_failure(self, make_shell, monkeypatch):
        """A read only installation is a restart of the backend only, not an error."""
        shell = make_shell(content_installed=OLD, content_bundled=NEW)
        error = OSError(28, 'no space left on device')

        def broken(src, dst):
            raise error

        monkeypatch.setattr(shell_module.ShellUpdate, 'copy_with_digest', staticmethod(broken))
        with logger.mock_capture_writer() as capture:
            assert await shell.stage_async() is None
        assert capture.concat() == """\
WARNING | [Shell] Cannot stage the electron archive, restarting the backend only: [Errno 28] no space left on device
"""


class TestTargets:
    """The three processes the updater waits for."""

    def test_identities(self, make_shell, monkeypatch):
        """Pids and kernel markers of this chain, the host included."""
        monkeypatch.setattr(shell_module, 'start_time', lambda pid: pid * 10)
        shell = make_shell()
        assert shell.targets() == {
            'backend': {'pid': os.getpid(), 'start': os.getpid() * 10},
            'supervisor': {'pid': os.getppid(), 'start': os.getppid() * 10},
            'electron': {'pid': 4242, 'start': 42420},
        }

    def test_an_unreadable_marker_keeps_the_entry_alive(self, monkeypatch):
        """A member of the chain that cannot be described waits on its pid."""
        def denied(pid):
            raise PermissionError(5, 'denied')

        monkeypatch.setattr(shell_module, 'start_time', denied)
        shell = ShellUpdate()
        shell.target = ShellTarget(ASAR, EXE, ROOT, [], 4242)
        targets = shell.targets()
        assert targets['backend'] == {'pid': os.getpid()}
        assert targets['supervisor'] == {'pid': os.getppid()}
        # the host could not be described either: its entry waits on the pid
        assert targets['electron'] == {'pid': 4242}

    def test_a_platform_without_a_probe_keeps_the_entry_alive(self, monkeypatch):
        """The updater may not know the platform: the pid alone still counts."""
        def unsupported(pid):
            raise NotImplementedError(pid)
        monkeypatch.setattr(shell_module, 'start_time', unsupported)
        shell = ShellUpdate()
        shell.target = ShellTarget(ASAR, EXE, ROOT, [], 4242)
        assert shell.targets()['electron'] == {'pid': 4242}


class TestSpawnUpdater:
    """The process that outlives this chain."""

    @staticmethod
    def spawn(monkeypatch, shell):
        """
        Start the updater against a recorded Popen.

        Args:
            monkeypatch (pytest.MonkeyPatch): Fixture of the test
            shell (ShellUpdate): Configured instance

        Returns:
            tuple: (the recorded calls, the returned stand-in process)
        """
        calls = []

        class FakeProcess:
            pid = 777

        def popen(argv, **kwargs):
            calls.append((argv, kwargs))
            return FakeProcess()

        monkeypatch.setattr(shell_module.subprocess, 'Popen', popen)
        staged = shell.stage()
        process = shell.spawn_updater(staged, 'token-checksum', '/log/resume/x.json')
        return calls, process

    def test_the_environment_carries_the_whole_input(self, make_shell, monkeypatch):
        """Nothing is passed on the command line: paths would be parsed back."""
        monkeypatch.setattr(shell_module, 'start_time', lambda pid: pid)
        shell = make_shell()
        calls, process = self.spawn(monkeypatch, shell)
        argv, kwargs = calls[0]
        assert argv == [shell_module.sys.executable, str(shell.updater_script())]
        assert process.pid == 777
        environ = kwargs['env']
        assert environ['ALASIO_RELAUNCH_SHA256'] == digest_of(NEW)
        assert environ['ALASIO_RELAUNCH_TIMEOUT'] == str(shell_module.RELAUNCH_TIMEOUT)
        assert environ['ALASIO_RESUME_FILE'] == '/log/resume/x.json'
        assert environ['ALASIO_RESUME_TOKEN'] == 'token-checksum'
        assert json.loads(environ['ALASIO_RELAUNCH_TARGETS']) == {
            'backend': {'pid': os.getpid(), 'start': os.getpid()},
            'supervisor': {'pid': os.getppid(), 'start': os.getppid()},
            'electron': {'pid': 4242, 'start': 4242},
        }
        # the install context of this chain travels to the updater, which drops
        # it again before it starts the new one
        assert environ['ALASIO_APP_ASAR'] == ASAR
        assert environ['ALASIO_APP_EXE'] == EXE
        assert environ['ALASIO_APP_CWD'] == ROOT
        assert json.loads(environ['ALASIO_APP_ARGV']) == ['--foo', 'bar baz']
        assert environ['ALASIO_APP_ELECTRON_PID'] == '4242'
        # the marker is not an environment channel any more: the updater reads
        # it from the targets it was given
        assert 'ALASIO_APP_ELECTRON_START' not in environ

    def test_the_descriptors_of_a_dead_chain_are_not_inherited(self, make_shell, monkeypatch):
        """The process outlives its starter: a pipe of the old chain would block."""
        shell = make_shell()
        calls, _ = self.spawn(monkeypatch, shell)
        _, kwargs = calls[0]
        assert kwargs['stdin'] is shell_module.subprocess.DEVNULL
        assert kwargs['stdout'] is shell_module.subprocess.DEVNULL
        assert kwargs['stderr'] is shell_module.subprocess.DEVNULL
        assert kwargs['close_fds'] is True
        assert kwargs['cwd'] == str(shell_module.env.PROJECT_ROOT)
        if shell_module.sys.platform == 'win32':
            assert kwargs['creationflags'] == (
                shell_module.subprocess.DETACHED_PROCESS
                | shell_module.subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            assert kwargs['start_new_session'] is True

    def test_a_process_that_cannot_start_raises(self, make_shell, monkeypatch):
        """The handover turns the error into its own failure."""
        shell = make_shell()

        def popen(argv, **kwargs):
            raise OSError(2, 'no such file')

        monkeypatch.setattr(shell_module.subprocess, 'Popen', popen)
        with pytest.raises(OSError):
            shell.spawn_updater(shell.stage(), 'token-checksum', '/log/resume/x.json')
