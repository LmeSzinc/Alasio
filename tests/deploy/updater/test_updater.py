"""
Tests for the standalone updater: input, waiting, replacing, relaunching.

The module is a single script started by path, so the tests keep the same
shape as its callers: the environment is the whole input, and the file system
is the in-memory fake one.
"""
import ast
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
from datetime import date

import pytest

from alasio.deploy import updater
from alasio.testing.filesystem import fs  # noqa: F401

pytestmark = [
    pytest.mark.parallel,
]


HEX_A = 'a' * 64
HEX_B = 'b' * 64

# Absolute paths of the in-memory files, outside the repository on posix and on
# the current drive on windows: the fake filesystem never touches the disk
WORK = os.sep
DST = os.path.join(WORK, 'resources', 'app.asar')
SRC = os.path.join(os.path.dirname(DST), 'new-app.asar')
EXE = os.path.join(WORK, 'Alasio.exe')
RESUME = os.path.join(WORK, 'log', 'resume.json')
LOG = os.path.join(WORK, 'log', 'updater.log')

# Every logged line starts with the local time; a test that let the real clock
# through could only assert a suffix of the line
STAMP = '2000-01-01 00:00:00.000'


def freeze_clock(monkeypatch):
    """
    Give every logged line the same timestamp.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture of the test
    """
    monkeypatch.setattr(updater.Log, 'stamp', staticmethod(lambda: STAMP))


def read_text(path):
    """
    Read a file of the fake file system.

    Args:
        path (str): File to read

    Returns:
        str: Its content, utf-8
    """
    with open(path, encoding='utf-8') as f:
        return f.read()


class FakeProcess:
    """A process whose poll() answers from a prepared sequence."""

    def __init__(self, codes=()):
        """
        Args:
            codes (list): Answers of the successive poll() calls, None for a
                process that is still running. An exhausted sequence keeps
                answering None. Defaults to ().
        """
        self.codes = list(codes)
        self.polls = 0

    def poll(self):
        """
        Returns:
            int: Exit code when the process has exited, None while it runs
        """
        self.polls += 1
        if not self.codes:
            return None
        return self.codes.pop(0)


def make_environ(**overrides):
    """
    Build a valid updater environment.

    Args:
        overrides ("dict[str, str]"): Values replacing the defaults, a value
            of None removes the key

    Returns:
        "dict[str, str]": Complete environment
    """
    environ = {
        'ALASIO_APP_ASAR': DST,
        'ALASIO_APP_EXE': EXE,
        'ALASIO_APP_CWD': WORK,
        'ALASIO_APP_ARGV': json.dumps(['--foo', 'bar baz']),
        'ALASIO_RELAUNCH_TARGETS': json.dumps({
            'backend': {'pid': 10, 'start': 100},
            'supervisor': {'pid': 20, 'start': 200},
            'electron': {'pid': 30, 'start': 300},
        }),
        'ALASIO_RELAUNCH_SHA256': HEX_A,
        'ALASIO_RESUME_FILE': RESUME,
        'ALASIO_RESUME_TOKEN': 'token-abc',
    }
    for key, value in overrides.items():
        if value is None:
            environ.pop(key, None)
        else:
            environ[key] = value
    return environ


def make_updater(log=None, **overrides):
    """
    Build an updater from a valid environment.

    Args:
        log (updater.Log): Sink of the messages, the default log of the
            updater when omitted. Defaults to None.
        overrides ("dict[str, str]"): Values replacing the defaults

    Returns:
        "updater.ElectronUpdater": The updater
    """
    return updater.ElectronUpdater(make_environ(**overrides), log=log)


class TestTarget:
    """One waited process: its identity and its state."""

    def test_gone(self, monkeypatch):
        """A process that does not exist any more is GONE."""
        def raise_nsp(pid):
            raise ProcessLookupError(pid)
        monkeypatch.setattr(updater, 'start_time', raise_nsp)
        assert updater.Target('backend', 1234).probe() == updater.GONE

    @pytest.mark.parametrize('error', [PermissionError(5, 'denied'), OSError(22, 'bad')])
    def test_unknown(self, monkeypatch, error):
        """An unreadable process is UNKNOWN, which never lets the updater act early."""
        def raise_error(pid):
            raise error
        monkeypatch.setattr(updater, 'start_time', raise_error)
        assert updater.Target('backend', 1234).probe() == updater.UNKNOWN

    def test_alive_without_marker(self, monkeypatch):
        """Without a marker the probe only asks whether the pid exists."""
        monkeypatch.setattr(updater, 'start_time', lambda pid: 777)
        assert updater.Target('backend', 1234).probe() == updater.ALIVE

    def test_alive_with_matching_marker(self, monkeypatch):
        """The same marker means the very same process."""
        monkeypatch.setattr(updater, 'start_time', lambda pid: 777)
        assert updater.Target('backend', 1234, 777).probe() == updater.ALIVE

    def test_reused(self, monkeypatch):
        """Another marker means the pid was recycled by another process."""
        monkeypatch.setattr(updater, 'start_time', lambda pid: 888)
        assert updater.Target('backend', 1234, 777).probe() == updater.REUSED

    def test_unsupported_platform_is_unknown(self, monkeypatch):
        """A platform without a probe cannot claim the process is done."""
        def raise_unsupported(pid):
            raise NotImplementedError(pid)
        monkeypatch.setattr(updater, 'start_time', raise_unsupported)
        assert updater.Target('backend', 1234, 777).probe() == updater.UNKNOWN


class TestParseTargets:
    """The target list of ALASIO_RELAUNCH_TARGETS, a json object."""

    def test_reads_three_targets(self):
        """Pid and marker are read, the result follows the declared order."""
        targets = updater.ElectronUpdater.parse_targets(json.dumps({
            'backend': {'pid': 10, 'start': 100},
            'supervisor': {'pid': 20, 'start': 200},
            'electron': {'pid': 30, 'start': 300},
        }))
        assert [(t.name, t.pid, t.start) for t in targets] == [
            ('backend', 10, 100),
            ('supervisor', 20, 200),
            ('electron', 30, 300),
        ]

    def test_order_does_not_matter(self):
        """The names decide the order, not the order of the keys."""
        targets = updater.ElectronUpdater.parse_targets(json.dumps({
            'electron': {'pid': 30, 'start': 300},
            'backend': {'pid': 10, 'start': 100},
            'supervisor': {'pid': 20, 'start': 200},
        }))
        assert [t.name for t in targets] == ['backend', 'supervisor', 'electron']

    @pytest.mark.parametrize('entry', [{'pid': 10}, {'pid': 10, 'start': None}])
    def test_start_is_optional(self, entry):
        """Without a marker only the existence of the pid is asked about."""
        targets = updater.ElectronUpdater.parse_targets(json.dumps({
            'backend': entry,
            'supervisor': {'pid': 20, 'start': 200},
            'electron': {'pid': 30, 'start': 300},
        }))
        assert targets[0].start is None

    @pytest.mark.parametrize(
        'text',
        [
            '',
            'not json',
            '[]',
            '"backend"',
            json.dumps({'backend': {'pid': 10, 'start': 100},
                        'supervisor': {'pid': 20, 'start': 200}}),
            json.dumps({'backend': {'pid': 10, 'start': 100},
                        'supervisor': {'pid': 20, 'start': 200},
                        'electron': {'pid': 30, 'start': 300},
                        'ghost': {'pid': 40, 'start': 400}}),
            json.dumps({'backend': 10, 'supervisor': {'pid': 20}, 'electron': {'pid': 30}}),
            json.dumps({'backend': {'pid': 'ten'}, 'supervisor': {'pid': 20}, 'electron': {'pid': 30}}),
            json.dumps({'backend': {'pid': 0}, 'supervisor': {'pid': 20}, 'electron': {'pid': 30}}),
            json.dumps({'backend': {'pid': -5}, 'supervisor': {'pid': 20}, 'electron': {'pid': 30}}),
            json.dumps({'backend': {'pid': True}, 'supervisor': {'pid': 20}, 'electron': {'pid': 30}}),
            json.dumps({'backend': {'pid': 10, 'start': 'soon'},
                        'supervisor': {'pid': 20}, 'electron': {'pid': 30}}),
            json.dumps({'backend': {'start': 100}, 'supervisor': {'pid': 20}, 'electron': {'pid': 30}}),
        ],
    )
    def test_malformed(self, text):
        """Every deviation from the schema is a preflight failure."""
        with pytest.raises(updater.PreflightError):
            updater.ElectronUpdater.parse_targets(text)


class TestElectronUpdater:
    """Validation of the environment, the whole input of the process."""

    def test_valid(self):
        """Every field is read and the staged path is derived from the target."""
        instance = make_updater()
        assert instance.dst == DST
        assert instance.src == os.path.join(os.path.dirname(DST), 'new-app.asar')
        assert instance.argv == ['--foo', 'bar baz']
        assert [t.name for t in instance.targets] == ['backend', 'supervisor', 'electron']
        assert instance.sha256 == HEX_A
        assert instance.token == 'token-abc'
        assert instance.timeout == 120.0
        assert instance.log.path == updater.ElectronUpdater.log_path()

    def test_reads_os_environ_by_default(self, monkeypatch):
        """Without an argument the process environment is read."""
        monkeypatch.setattr(os, 'environ', make_environ())
        assert updater.ElectronUpdater().token == 'token-abc'

    def test_optional_value(self):
        """The timeout is optional."""
        instance = make_updater(ALASIO_RELAUNCH_TIMEOUT='30.5')
        assert instance.timeout == 30.5

    def test_log_path_is_not_an_input(self):
        """No variable redirects the log away from today's file."""
        instance = make_updater(ALASIO_RELAUNCH_LOG=LOG)
        assert instance.log.path == updater.ElectronUpdater.log_path()

    @pytest.mark.parametrize(
        'name',
        [
            'ALASIO_APP_ASAR',
            'ALASIO_APP_EXE',
            'ALASIO_APP_CWD',
            'ALASIO_APP_ARGV',
            'ALASIO_RELAUNCH_TARGETS',
            'ALASIO_RELAUNCH_SHA256',
            'ALASIO_RESUME_FILE',
            'ALASIO_RESUME_TOKEN',
        ],
    )
    def test_missing_required(self, name):
        """A missing required variable is a preflight failure."""
        with pytest.raises(updater.PreflightError, match=name):
            make_updater(**{name: None})

    @pytest.mark.parametrize(
        'name, value',
        [
            ('ALASIO_APP_ASAR', 'resources/app.asar'),
            ('ALASIO_APP_EXE', 'Alasio.exe'),
            ('ALASIO_RESUME_FILE', 'resume.json'),
            ('ALASIO_RELAUNCH_SHA256', 'xyz'),
            ('ALASIO_RELAUNCH_SHA256', 'a' * 63),
            ('ALASIO_RELAUNCH_SHA256', 'g' * 64),
            ('ALASIO_RELAUNCH_TARGETS', 'not json'),
            ('ALASIO_RELAUNCH_TARGETS', json.dumps({'backend': {'pid': 1}})),
            ('ALASIO_RELAUNCH_TIMEOUT', 'soon'),
            ('ALASIO_RELAUNCH_TIMEOUT', '0'),
            ('ALASIO_RELAUNCH_TIMEOUT', '-5'),
            ('ALASIO_APP_ARGV', 'not json'),
            ('ALASIO_APP_ARGV', '{"a": 1}'),
            ('ALASIO_APP_ARGV', '[1, 2]'),
        ],
    )
    def test_invalid_value(self, name, value):
        """A malformed value is a preflight failure, never a silent default."""
        with pytest.raises(updater.PreflightError):
            make_updater(**{name: value})

    def test_digest_is_normalised(self):
        """An uppercase digest is accepted and stored lowercase."""
        assert make_updater(ALASIO_RELAUNCH_SHA256=HEX_A.upper()).sha256 == HEX_A

    def test_reads_nothing_at_import(self):
        """The backend imports this module, so the module level must be inert."""
        tree = ast.parse(updater_source())
        for node in _module_level_nodes(tree):
            if isinstance(node, ast.Attribute) and node.attr == 'environ':
                raise AssertionError('os.environ is read at module level')


def _module_level_nodes(node):
    """
    Walk the statements of a module, without entering functions or classes.

    Args:
        node ("ast.AST"): Module or block to walk

    Yields:
        "ast.AST": Every node reachable without entering a scope
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield child
        yield from _module_level_nodes(child)


class TestSha256File:
    """The digest helper."""

    def test_known_content(self, fs):
        """The digest is the sha256 of the bytes on disk."""
        fs.create_file('/digest.bin', contents=b'abc')
        assert updater.ElectronUpdater.sha256_file('/digest.bin') == \
            hashlib.sha256(b'abc').hexdigest()

    def test_missing_file(self):
        """A missing file raises, it is not reported as a mismatch."""
        with pytest.raises(OSError):
            updater.ElectronUpdater.sha256_file('/does/not/exist.bin')


class TestWaitTargets:
    """The polling loop, with a budget given by the input."""

    def test_all_done_at_once(self, monkeypatch):
        """Nothing to wait for returns after the first round."""
        def raise_nsp(pid):
            raise ProcessLookupError(pid)
        monkeypatch.setattr(updater, 'start_time', raise_nsp)
        instance = make_updater(ALASIO_RELAUNCH_TIMEOUT='1')
        assert instance.wait_targets(interval=0.01) == []

    def test_waits_until_gone(self, monkeypatch):
        """A live target is polled until it disappears, then the loop returns."""
        calls = {'count': 0}

        def start_time(pid):
            calls['count'] += 1
            if calls['count'] <= 3:
                return 111
            raise ProcessLookupError(pid)
        monkeypatch.setattr(updater, 'start_time', start_time)
        instance = make_updater(ALASIO_RELAUNCH_TIMEOUT='5')
        instance.targets = [updater.Target('backend', 1234, 111)]
        assert instance.wait_targets(interval=0.01) == []
        assert calls['count'] == 4

    def test_timeout_reports_pending(self, monkeypatch):
        """A target that never goes away is reported by name at the deadline."""
        monkeypatch.setattr(updater, 'start_time', lambda pid: 111)
        instance = make_updater(ALASIO_RELAUNCH_TIMEOUT='0.15')
        instance.targets = [
            updater.Target('backend', 1, 111),
            updater.Target('electron', 2, 111),
        ]
        started = time.monotonic()
        pending = instance.wait_targets(interval=0.02)
        elapsed = time.monotonic() - started
        assert pending == ['backend', 'electron']
        assert elapsed >= 0.15


class TestReplace:
    """The installation of the staged archive, in two steps."""

    def staged(self, fs, content=b'new'):
        """
        Create the installed archive and a staged one, and stage the copy.

        Args:
            fs (FakeFilesystem): Fixture
            content (bytes): Content of the staged archive

        Returns:
            "updater.ElectronUpdater": An updater whose temporary file is ready
        """
        fs.create_file(DST, contents=b'old')
        fs.create_file(SRC, contents=content)
        instance = make_updater(ALASIO_RELAUNCH_SHA256=hashlib.sha256(content).hexdigest())
        temp = instance.stage()
        assert temp == instance.temp
        return instance

    def test_stage_copies_and_hashes(self, fs):
        """The temporary file holds the content and lives next to the target."""
        instance = self.staged(fs)
        temp = instance.temp
        assert temp.startswith(DST)
        assert temp != instance.src
        with open(temp, 'rb') as f:
            assert f.read() == b'new'

    def test_stage_rejects_changed_content(self, fs):
        """A source that does not match the digest is not copied into place."""
        fs.create_file(DST, contents=b'old')
        fs.create_file(SRC, contents=b'tampered')
        instance = make_updater(ALASIO_RELAUNCH_SHA256=hashlib.sha256(b'new').hexdigest())
        with pytest.raises(updater.PreflightError):
            instance.stage()
        assert not os.path.exists(instance.temp)

    def test_stage_of_a_consumed_source(self, fs):
        """A source that is already gone reports that somebody installed it."""
        fs.create_file(DST, contents=b'old')
        assert make_updater().stage() is None

    def test_installs(self, fs):
        """The target holds the new content, the temporary and staged files are gone."""
        instance = self.staged(fs)
        instance.replace(instance.temp)
        with open(DST, 'rb') as f:
            assert f.read() == b'new'
        assert not os.path.exists(instance.temp)
        # the staged archive is consumed, a stale copy would be compared by
        # the next preflight
        assert not os.path.exists(SRC)
        assert not os.path.exists(DST + '.old')

    def test_installs_into_an_empty_target(self, fs):
        """A target that does not exist yet is installed."""
        fs.create_file(SRC, contents=b'new')
        instance = make_updater(ALASIO_RELAUNCH_SHA256=hashlib.sha256(b'new').hexdigest())
        temp = instance.stage()
        assert temp == instance.temp
        instance.replace(instance.temp)
        with open(DST, 'rb') as f:
            assert f.read() == b'new'

    def test_retries_only_the_swap(self, fs, monkeypatch):
        """A failing swap is retried, and the copy is not repeated."""
        instance = self.staged(fs)
        calls = []
        real_replace = os.replace

        def flaky_replace(src, dst):
            calls.append((src, dst))
            if len(calls) < 3:
                raise PermissionError(13, 'in use', src)
            return real_replace(src, dst)
        copies = []
        real_open = open

        def counting_open(path, mode='r', *args, **kwargs):
            if isinstance(path, str) and path == instance.src and 'r' in mode:
                copies.append(path)
            return real_open(path, mode, *args, **kwargs)
        monkeypatch.setattr(os, 'replace', flaky_replace)
        monkeypatch.setattr('builtins.open', counting_open)
        instance.replace(instance.temp, attempts=5)
        assert len(calls) == 3
        # the source was read once, by stage(), never again by the retries
        assert len(copies) == 0
        with open(DST, 'rb') as f:
            assert f.read() == b'new'

    def test_reports_after_every_attempt(self, fs, monkeypatch):
        """A swap that never succeeds raises its last error."""
        instance = self.staged(fs)
        calls = []

        def broken_replace(src, dst):
            calls.append((src, dst))
            raise PermissionError(13, 'in use', src)
        monkeypatch.setattr(os, 'replace', broken_replace)
        with pytest.raises(OSError):
            instance.replace(instance.temp, attempts=3)
        assert len(calls) == 3
        with open(DST, 'rb') as f:
            assert f.read() == b'old'


class TestRelaunchEnv:
    """The environment handed to the new chain, built with the input."""

    def test_drops_private_keys_and_keeps_the_token(self):
        """The keys of the updater are dropped, the credential survives."""
        environ = dict(make_environ(), PYTHONUNBUFFERED='1')
        env = updater.ElectronUpdater(environ).env
        assert env['ALASIO_RESUME_TOKEN'] == 'token-abc'
        assert env['PYTHONUNBUFFERED'] == '1'
        assert not any(name.startswith('ALASIO_RELAUNCH_') for name in env)
        assert not any(name.startswith('ALASIO_APP_') for name in env)
        assert 'ALASIO_RESUME_FILE' not in env
        assert 'ELECTRON' not in env

    def test_does_not_mutate_the_input(self):
        """Reading the environment leaves the passed mapping alone."""
        environ = make_environ()
        before = dict(environ)
        updater.ElectronUpdater(environ)
        assert environ == before


class TestRelaunch:
    """Starting the application again, with a polled window."""

    def fake_popen(self, monkeypatch, processes):
        """
        Replace Popen with a source of prepared processes.

        Args:
            monkeypatch (pytest.MonkeyPatch): Fixture
            processes (list): Objects returned by the successive calls

        Returns:
            list: (argv, kwargs) of every spawn
        """
        spawned = []
        pending = list(processes)

        def popen(argv, **kwargs):
            spawned.append((argv, kwargs))
            return pending.pop(0)
        monkeypatch.setattr(subprocess, 'Popen', popen)
        return spawned

    def test_alive_at_the_end_of_the_window_is_started(self, monkeypatch):
        """A child that survives the whole window has started."""
        spawned = self.fake_popen(monkeypatch, [FakeProcess()])
        instance = make_updater()
        started = time.monotonic()
        instance.relaunch(window=0.05, interval=0.01)
        assert time.monotonic() - started >= 0.05
        assert len(spawned) == 1
        assert spawned[0][0] == [EXE, '--foo', 'bar baz']
        assert spawned[0][1]['cwd'] == WORK
        assert spawned[0][1]['env']['ALASIO_RESUME_TOKEN'] == 'token-abc'
        assert 'ALASIO_RELAUNCH_TARGETS' not in spawned[0][1]['env']

    def test_fast_exit_is_noticed_at_once(self, monkeypatch):
        """An early exit ends the watch at the next poll, not at the deadline."""
        spawned = self.fake_popen(monkeypatch, [FakeProcess([0]), FakeProcess([0])])
        instance = make_updater()
        started = time.monotonic()
        with pytest.raises(RuntimeError):
            instance.relaunch(attempts=2, window=5.0, interval=0.01)
        # the two attempts exit within a poll each, far below the window
        assert time.monotonic() - started < 1.0
        assert len(spawned) == 2

    def test_zero_exit_is_retried_until_started(self, monkeypatch):
        """A second instance swallowed by the single instance lock is retried."""
        spawned = self.fake_popen(monkeypatch, [FakeProcess([0]), FakeProcess()])
        make_updater().relaunch(attempts=3, window=0.02, interval=0.01)
        assert len(spawned) == 2

    def test_crash_is_not_retried(self, monkeypatch):
        """A non zero exit is a failure of the application, not of the wait."""
        spawned = self.fake_popen(monkeypatch, [FakeProcess([3])])
        with pytest.raises(RuntimeError, match='code 3'):
            make_updater().relaunch(attempts=3, window=0.05, interval=0.01)
        assert len(spawned) == 1

    def test_exec_failure_is_retried(self, monkeypatch):
        """A program that cannot be started is retried and then reported."""
        calls = []

        def popen(argv, **kwargs):
            calls.append(argv)
            raise FileNotFoundError(2, 'no such program', argv[0])
        monkeypatch.setattr(subprocess, 'Popen', popen)
        with pytest.raises(OSError):
            make_updater().relaunch(attempts=2, interval=0.01)
        assert len(calls) == 2


class TestRun:
    """The flow of the updater, from the preflight check to the new process."""

    def digest_of(self, content):
        """
        Args:
            content (bytes): File content

        Returns:
            str: Its sha256, lowercase hex
        """
        return hashlib.sha256(content).hexdigest()

    def make_log(self, monkeypatch, path=LOG):
        """
        Open a log file of the fake file system at a frozen time.

        Args:
            monkeypatch (pytest.MonkeyPatch): Fixture of the test
            path (str): Log file. Defaults to LOG.

        Returns:
            "updater.Log": The opened log
        """
        freeze_clock(monkeypatch)
        log = updater.Log(path)
        log.open()
        return log

    def make_instance(self, tmp_digest=HEX_A, log=None):
        """
        Build an updater configured for the fake file system.

        Args:
            tmp_digest (str): Digest the staged archive is expected to have
            log (updater.Log): Sink of the messages. Defaults to None.

        Returns:
            "updater.ElectronUpdater": The updater
        """
        return make_updater(
            log=log,
            ALASIO_RELAUNCH_SHA256=tmp_digest,
            ALASIO_RELAUNCH_TIMEOUT='1',
        )

    def test_success(self, fs, monkeypatch):
        """The staged archive is installed, the credential reaches the new chain."""
        fs.create_file(DST, contents=b'old')
        fs.create_file(SRC, contents=b'new')
        fs.create_file(RESUME, contents=b'{}')
        launched = []
        monkeypatch.setattr(updater.ElectronUpdater, 'wait_targets', lambda self, interval=0.1: [])
        monkeypatch.setattr(updater.ElectronUpdater, 'relaunch',
                            lambda self, **kwargs: launched.append(self.env))
        log = self.make_log(monkeypatch)
        instance = self.make_instance(tmp_digest=self.digest_of(b'new'), log=log)
        assert instance.run() == updater.EXIT_OK
        assert read_text(LOG) == f"""\
{STAMP} | INFO | preflight: {SRC} -> {DST}
{STAMP} | INFO | wait: the old chain is gone
{STAMP} | INFO | replace: installed {DST}
{STAMP} | INFO | relaunch: started {EXE}
"""
        with open(DST, 'rb') as f:
            assert f.read() == b'new'
        assert not os.path.exists(instance.temp)
        assert not os.path.exists(SRC)
        assert not os.path.exists(DST + '.old')
        assert len(launched) == 1
        assert launched[0]['ALASIO_RESUME_TOKEN'] == 'token-abc'

    def test_preflight_digest_mismatch(self, fs, monkeypatch):
        """A staged file that does not match its digest stops before the wait."""
        fs.create_file(DST, contents=b'old')
        fs.create_file(SRC, contents=b'tampered')
        monkeypatch.setattr(updater.ElectronUpdater, 'wait_targets', lambda self, interval=0.1: [])
        log = self.make_log(monkeypatch)
        instance = self.make_instance(tmp_digest=self.digest_of(b'new'), log=log)
        assert instance.run() == updater.EXIT_PREFLIGHT
        assert read_text(LOG) == f"""\
{STAMP} | INFO | preflight: {SRC} -> {DST}
{STAMP} | ERROR | preflight: {SRC} is {self.digest_of(b'tampered')}, expected {self.digest_of(b'new')}, not staging
"""
        with open(DST, 'rb') as f:
            assert f.read() == b'old'

    def test_preflight_missing_source(self, fs, monkeypatch):
        """A staged archive that never existed stops before the wait."""
        fs.create_file(DST, contents=b'old')
        monkeypatch.setattr(updater.ElectronUpdater, 'wait_targets', lambda self, interval=0.1: [])
        try:
            with open(SRC, 'rb'):
                pass
        except OSError as e:
            missing = e
        else:
            raise AssertionError(f'{SRC} must not exist')
        log = self.make_log(monkeypatch)
        assert self.make_instance(log=log).run() == updater.EXIT_PREFLIGHT
        assert read_text(LOG) == f"""\
{STAMP} | INFO | preflight: {SRC} -> {DST}
{STAMP} | ERROR | preflight: cannot read {SRC}: {missing}
"""

    def test_timeout_keeps_the_installation(self, fs, monkeypatch):
        """A chain that does not come down leaves the files alone."""
        fs.create_file(DST, contents=b'old')
        fs.create_file(SRC, contents=b'new')
        fs.create_file(RESUME, contents=b'{}')
        monkeypatch.setattr(updater.ElectronUpdater, 'wait_targets',
                            lambda self, interval=0.1: ['electron'])
        launched = []
        monkeypatch.setattr(updater.ElectronUpdater, 'relaunch',
                            lambda self, **kwargs: launched.append(self.env))
        log = self.make_log(monkeypatch)
        instance = self.make_instance(tmp_digest=self.digest_of(b'new'), log=log)
        assert instance.run() == updater.EXIT_TIMEOUT
        assert read_text(LOG) == f"""\
{STAMP} | INFO | preflight: {SRC} -> {DST}
{STAMP} | ERROR | wait: still running after 1.0s: electron
"""
        assert not launched
        with open(DST, 'rb') as f:
            assert f.read() == b'old'

    def test_withdrawn_relaunch_installs_nothing(self, fs, monkeypatch):
        """Without the resume file the relaunch was cancelled, nothing is touched."""
        fs.create_file(DST, contents=b'old')
        fs.create_file(SRC, contents=b'new')
        monkeypatch.setattr(updater.ElectronUpdater, 'wait_targets', lambda self, interval=0.1: [])
        launched = []
        monkeypatch.setattr(updater.ElectronUpdater, 'relaunch',
                            lambda self, **kwargs: launched.append(self.env))
        log = self.make_log(monkeypatch)
        instance = self.make_instance(tmp_digest=self.digest_of(b'new'), log=log)
        assert instance.run() == updater.EXIT_NO_GO
        assert read_text(LOG) == f"""\
{STAMP} | INFO | preflight: {SRC} -> {DST}
{STAMP} | INFO | wait: the old chain is gone
{STAMP} | INFO | go: {RESUME} is gone, the relaunch was withdrawn
"""
        assert not launched
        with open(DST, 'rb') as f:
            assert f.read() == b'old'
        assert os.path.exists(SRC)

    def test_unreadable_resume_file_installs_nothing(self, fs, monkeypatch):
        """An unreadable resume file is treated like a withdrawn relaunch."""
        fs.create_file(DST, contents=b'old')
        fs.create_file(SRC, contents=b'new')
        fs.create_file(RESUME, contents=b'{}')
        monkeypatch.setattr(updater.ElectronUpdater, 'wait_targets', lambda self, interval=0.1: [])
        error = PermissionError(13, 'permission denied')

        # The whole signature of os.stat, keywords included: pytest stats the
        # source file of every frame while it formats a failure, and a stub
        # that takes one positional argument raises TypeError there instead,
        # which kills the worker and takes the whole run down with it
        def denied(*args, **kwargs):
            raise error
        monkeypatch.setattr(os, 'stat', denied)
        log = self.make_log(monkeypatch)
        launched = []
        monkeypatch.setattr(updater.ElectronUpdater, 'relaunch',
                            lambda self, **kwargs: launched.append(self.env))
        instance = self.make_instance(tmp_digest=self.digest_of(b'new'), log=log)
        assert instance.run() == updater.EXIT_NO_GO
        assert read_text(LOG) == f"""\
{STAMP} | INFO | preflight: {SRC} -> {DST}
{STAMP} | INFO | wait: the old chain is gone
{STAMP} | WARNING | go: cannot read {RESUME}: {error}
"""
        assert not launched

    def test_tampered_after_the_wait_relaunches_the_old_version(self, fs, monkeypatch):
        """A staged file that changes during the wait is not installed."""
        fs.create_file(DST, contents=b'old')
        fs.create_file(SRC, contents=b'new')
        fs.create_file(RESUME, contents=b'{}')

        def tamper(instance, interval=0.1):
            # the preflight read the correct content, the window between it
            # and the installation is what this test is about
            with open(SRC, 'wb') as f:
                f.write(b'tampered')
            return []
        monkeypatch.setattr(updater.ElectronUpdater, 'wait_targets', tamper)
        launched = []
        monkeypatch.setattr(updater.ElectronUpdater, 'relaunch',
                            lambda self, **kwargs: launched.append(self.env))
        log = self.make_log(monkeypatch)
        instance = self.make_instance(tmp_digest=self.digest_of(b'new'), log=log)
        assert instance.run() == updater.EXIT_REPLACE
        assert read_text(LOG) == f"""\
{STAMP} | INFO | preflight: {SRC} -> {DST}
{STAMP} | INFO | wait: the old chain is gone
{STAMP} | WARNING | replace: {SRC} is {self.digest_of(b'tampered')}, expected {self.digest_of(b'new')}, keeping the installed one
{STAMP} | INFO | relaunch: started {EXE}
"""
        assert len(launched) == 1
        with open(DST, 'rb') as f:
            assert f.read() == b'old'

    def test_consumed_staged_archive_is_not_an_error(self, fs, monkeypatch):
        """Another updater installed the same content: nothing left to do."""
        fs.create_file(DST, contents=b'new')
        fs.create_file(SRC, contents=b'new')
        fs.create_file(RESUME, contents=b'{}')

        def consume(instance, interval=0.1):
            # the preflight saw the staged archive, the other updater consumed
            # it while this one was waiting
            os.remove(SRC)
            return []
        monkeypatch.setattr(updater.ElectronUpdater, 'wait_targets', consume)
        launched = []
        monkeypatch.setattr(updater.ElectronUpdater, 'relaunch',
                            lambda self, **kwargs: launched.append(self.env))
        log = self.make_log(monkeypatch)
        instance = self.make_instance(tmp_digest=self.digest_of(b'new'), log=log)
        assert instance.run() == updater.EXIT_OK
        assert read_text(LOG) == f"""\
{STAMP} | INFO | preflight: {SRC} -> {DST}
{STAMP} | INFO | wait: the old chain is gone
{STAMP} | INFO | replace: {SRC} already consumed, leaving {DST} alone
{STAMP} | INFO | relaunch: started {EXE}
"""
        assert len(launched) == 1

    def test_staging_failure_keeps_the_installation(self, fs, monkeypatch):
        """A temporary file that cannot be written leaves the old version."""
        fs.create_file(DST, contents=b'old')
        fs.create_file(SRC, contents=b'new')
        fs.create_file(RESUME, contents=b'{}')
        monkeypatch.setattr(updater.ElectronUpdater, 'wait_targets', lambda self, interval=0.1: [])
        error = OSError(28, 'no space left on device')

        def broken(self):
            raise error
        monkeypatch.setattr(updater.ElectronUpdater, 'stage', broken)
        launched = []
        monkeypatch.setattr(updater.ElectronUpdater, 'relaunch',
                            lambda self, **kwargs: launched.append(self.env))
        log = self.make_log(monkeypatch)
        instance = self.make_instance(tmp_digest=self.digest_of(b'new'), log=log)
        assert instance.run() == updater.EXIT_REPLACE
        assert read_text(LOG) == f"""\
{STAMP} | INFO | preflight: {SRC} -> {DST}
{STAMP} | INFO | wait: the old chain is gone
{STAMP} | WARNING | replace: cannot stage {SRC}: {error}
{STAMP} | INFO | relaunch: started {EXE}
"""
        assert len(launched) == 1
        with open(DST, 'rb') as f:
            assert f.read() == b'old'

    def test_swap_failure_keeps_the_installation(self, fs, monkeypatch):
        """A file that cannot be swapped in leaves the old version."""
        fs.create_file(DST, contents=b'old')
        fs.create_file(SRC, contents=b'new')
        fs.create_file(RESUME, contents=b'{}')
        monkeypatch.setattr(updater.ElectronUpdater, 'wait_targets', lambda self, interval=0.1: [])
        error = PermissionError(13, 'the file is in use')

        def broken(self, temp, attempts=5):
            raise error
        monkeypatch.setattr(updater.ElectronUpdater, 'replace', broken)
        launched = []
        monkeypatch.setattr(updater.ElectronUpdater, 'relaunch',
                            lambda self, **kwargs: launched.append(self.env))
        log = self.make_log(monkeypatch)
        instance = self.make_instance(tmp_digest=self.digest_of(b'new'), log=log)
        assert instance.run() == updater.EXIT_REPLACE
        assert read_text(LOG) == f"""\
{STAMP} | INFO | preflight: {SRC} -> {DST}
{STAMP} | INFO | wait: the old chain is gone
{STAMP} | WARNING | replace: {instance.temp} -> {DST} failed: {error}
{STAMP} | INFO | relaunch: started {EXE}
"""
        assert len(launched) == 1
        with open(DST, 'rb') as f:
            assert f.read() == b'old'

    def test_relaunch_failure(self, fs, monkeypatch):
        """A chain that cannot be started is reported with its own code."""
        fs.create_file(DST, contents=b'old')
        fs.create_file(SRC, contents=b'new')
        fs.create_file(RESUME, contents=b'{}')
        monkeypatch.setattr(updater.ElectronUpdater, 'wait_targets', lambda self, interval=0.1: [])

        def broken(self, **kwargs):
            raise OSError(2, 'no such program')
        monkeypatch.setattr(updater.ElectronUpdater, 'relaunch', broken)
        log = self.make_log(monkeypatch)
        instance = self.make_instance(tmp_digest=self.digest_of(b'new'), log=log)
        assert instance.run() == updater.EXIT_RELAUNCH
        assert read_text(LOG) == f"""\
{STAMP} | INFO | preflight: {SRC} -> {DST}
{STAMP} | INFO | wait: the old chain is gone
{STAMP} | INFO | replace: installed {DST}
{STAMP} | ERROR | relaunch: {EXE} failed: [Errno 2] no such program
"""


class TestLog:
    """The log file, the only output of the process."""

    def test_open_creates_the_directory(self, fs, monkeypatch):
        """log/ does not exist on a fresh installation."""
        freeze_clock(monkeypatch)
        path = os.path.join(os.sep, 'log', 'fresh', 'updater.log')
        log = updater.Log(path)
        log.open()
        log.info('hello')
        log.close()
        assert read_text(path) == f'{STAMP} | INFO | hello\n'

    def test_levels_use_the_layout_of_the_application_logger(self, fs, monkeypatch):
        """`stamp | LEVEL | message`, the lines of log/{date}_{name}.txt."""
        freeze_clock(monkeypatch)
        log = updater.Log(LOG)
        log.open()
        log.info('one')
        log.warning('two')
        log.error('three')
        log.close()
        assert read_text(LOG) == f"""\
{STAMP} | INFO | one
{STAMP} | WARNING | two
{STAMP} | ERROR | three
"""

    def test_error_appends_the_traceback_after_the_line(self, fs, monkeypatch):
        """The stack follows the line, verbatim, like the application logger."""
        freeze_clock(monkeypatch)
        log = updater.Log(LOG)
        log.open()
        try:
            raise ValueError('bad value')
        except ValueError:
            log.error('boom', exc_info=True)
        log.close()
        lines = read_text(LOG).splitlines()
        assert lines[0] == f'{STAMP} | ERROR | boom'
        assert lines[1] == 'Traceback (most recent call last):'
        assert lines[-1] == 'ValueError: bad value'
        # the frame that raised is named; the source lines are absent because
        # the fake filesystem shadows the file the code came from
        assert any('in test_error_appends_the_traceback_after_the_line' in line for line in lines)

    def test_error_without_exc_info_writes_one_line(self, fs, monkeypatch):
        """An expected failure needs no stack, only its reason."""
        freeze_clock(monkeypatch)
        log = updater.Log(LOG)
        log.open()
        log.error('boom')
        log.close()
        assert read_text(LOG) == f'{STAMP} | ERROR | boom\n'

    def test_stamp_carries_milliseconds(self):
        """The prefix is the timestamp the application logger writes."""
        assert re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}', updater.Log.stamp())

    def test_appends_across_runs(self, fs, monkeypatch):
        """One file per day, so every run of that day adds to the same file."""
        freeze_clock(monkeypatch)
        for word in ('first', 'second'):
            log = updater.Log(LOG)
            log.open()
            log.info(word)
            log.close()
        assert read_text(LOG) == f'{STAMP} | INFO | first\n{STAMP} | INFO | second\n'

    def test_silent_without_path(self):
        """An instance built without a path writes nothing, anywhere."""
        log = updater.Log('')
        log.open()
        log.info('hello')
        log.close()
        assert log.file is None


class TestLogPath:
    """The log file is fixed, dated and lives in the installation root."""

    def test_directory_is_under_the_working_directory(self, monkeypatch):
        """The backend starts the updater in the project root."""
        root = os.path.join(os.sep, 'root')
        monkeypatch.setattr(os, 'getcwd', lambda: root)
        name = f'{date.today()}_ElectronUpdater.txt'
        assert updater.ElectronUpdater.log_path() == os.path.join(root, 'log', name)

    def test_not_inside_the_package(self, monkeypatch):
        """The library location is not the installation location."""
        monkeypatch.setattr(os, 'getcwd', lambda: os.path.join(os.sep, 'somewhere', 'else'))
        assert 'alasio' not in updater.ElectronUpdater.log_path().split(os.sep)

    def test_name_is_dated_like_every_other_log(self, monkeypatch):
        """One file per day, in the form the application logger uses."""
        monkeypatch.setattr(os, 'getcwd', lambda: os.path.join(os.sep, 'root'))
        name = os.path.basename(updater.ElectronUpdater.log_path())
        assert name == f'{date.today()}_ElectronUpdater.txt'


class TestMain:
    """The entry point."""

    def test_preflight_failure_is_written_to_the_log(self, fs, monkeypatch):
        """An unusable environment still leaves its reason in today's log."""
        freeze_clock(monkeypatch)
        monkeypatch.setattr(os, 'environ', {})
        monkeypatch.setattr(updater.ElectronUpdater, 'log_path', staticmethod(lambda: LOG))
        stdout = io.StringIO()
        stderr = io.StringIO()
        monkeypatch.setattr(sys, 'stdout', stdout)
        monkeypatch.setattr(sys, 'stderr', stderr)
        assert updater.ElectronUpdater.main() == updater.EXIT_PREFLIGHT
        assert read_text(LOG) == f'{STAMP} | ERROR | preflight: ALASIO_APP_ASAR is not set\n'
        assert stdout.getvalue() == ''
        assert stderr.getvalue() == ''

    def test_an_unexpected_failure_is_written_with_its_stack(self, fs, monkeypatch):
        """A bug of the updater itself leaves the frame that raised it."""
        freeze_clock(monkeypatch)
        monkeypatch.setattr(updater.ElectronUpdater, 'log_path', staticmethod(lambda: LOG))
        monkeypatch.setattr(os, 'environ', make_environ())

        def broken(self):
            raise RuntimeError('boom')

        monkeypatch.setattr(updater.ElectronUpdater, 'run', broken)
        assert updater.ElectronUpdater.main() == updater.EXIT_ERROR
        content = read_text(LOG)
        lines = content.splitlines()
        assert lines[0] == f'{STAMP} | ERROR | error: RuntimeError: boom'
        assert lines[1] == 'Traceback (most recent call last):'
        assert lines[-1] == 'RuntimeError: boom'
        # both frames are named: the entry point and the patched step
        assert 'in main' in content
        assert 'in broken' in content
        # the credential of the new chain never reaches the log
        assert 'token-abc' not in content


def updater_source():
    """
    Read the updater source.

    Returns:
        str: Content of alasio/deploy/updater.py
    """
    with open(updater.__file__, encoding='utf-8') as f:
        return f.read()


class TestStandaloneConstraints:
    """The updater stays a single file with no third party imports."""

    ALLOWED = {
        'ctypes',
        'datetime',
        'errno',
        'hashlib',
        'json',
        'os',
        'socket',
        'subprocess',
        'sys',
        'time',
        'traceback',
    }

    def test_imports_are_stdlib_only(self):
        """Every imported module belongs to the allowed allowlist."""
        imported = set()
        for node in ast.walk(ast.parse(updater_source())):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    imported.add('.' * node.level + (node.module or ''))
                else:
                    imported.add((node.module or '').split('.')[0])
        assert imported <= self.ALLOWED, imported - self.ALLOWED

    def test_no_project_imports(self):
        """The file runs by path, so no project module is importable anyway."""
        source = updater_source()
        assert 'from alasio' not in source
        assert 'import alasio' not in source
        assert 'import psutil' not in source
        assert 'from psutil' not in source
        assert 'codegen' not in source

    def test_no_command_line(self):
        """The environment is the whole input, the command line carries nothing."""
        source = updater_source()
        assert 'argparse' not in source
        assert 'sys.argv' not in source

    def test_no_stream_writes(self):
        """The pipes inherited from the dead chain have no reader left."""
        source = updater_source()
        assert 'sys.stdout' not in source
        assert 'sys.stderr' not in source
        assert 'print(' not in source

    def test_exposed_helpers(self):
        """The entry points other modules and tests rely on."""
        assert callable(updater.start_time)
        assert callable(updater.port_available)
        assert callable(updater.ElectronUpdater.main)
        assert callable(updater.Target.probe)
