"""
Tests for the process start marker of the standalone updater.

The marker is what tells the updater whether a pid still belongs to the
process the backend handed over, so every read is checked against real
processes of the machine. Only the /proc/<pid>/stat parser is fed with
synthetic content, so the field arithmetic is covered on the whole CI matrix
instead of on Linux alone.

Marked parallel: every test spawns its own short lived process and reads the
kernel counters of this machine, nothing is shared with the other tests.
"""
import ast
import os
import subprocess
import sys
import time

import pytest

from alasio.deploy import updater

pytestmark = [
    pytest.mark.parallel,
]


def make_stat(starttime, comm=b'bash', tail=b'20 21 22'):
    """
    Build a /proc/<pid>/stat content with a given comm and starttime.

    Args:
        starttime (int): Value of the starttime field
        comm (bytes): Process name, without the surrounding parentheses
        tail (bytes): Fields after starttime, they are not read back

    Returns:
        bytes: Synthetic stat content
    """
    fields = [b'S'] + [b'%d' % i for i in range(1, 19)]
    fields.append(b'%d' % starttime)
    return b'1234 (' + comm + b') ' + b' '.join(fields) + b' ' + tail


def spawn_idle():
    """
    Start a python process that does nothing until killed.

    Returns:
        "subprocess.Popen": The running process
    """
    return subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])


def kill_all(procs):
    """
    Kill every process and wait for it.

    Args:
        procs ("list[subprocess.Popen]"): Processes to kill
    """
    for proc in procs:
        proc.kill()
    for proc in procs:
        proc.wait()
    # give the kernel a moment to release the pid on slow CI machines
    time.sleep(0.1)


def updater_source():
    """
    Read the updater source.

    Returns:
        str: Content of alasio/deploy/updater.py
    """
    with open(updater.__file__, encoding='utf-8') as f:
        return f.read()


def identity(pid):
    """
    Build the pair that identifies a process over time.

    Args:
        pid (int): Target pid

    Returns:
        "tuple[int, int]": Pid and its start marker
    """
    return pid, updater.start_time(pid)


class TestParseStat:
    """The /proc/<pid>/stat parser."""

    @pytest.mark.parametrize('starttime', [0, 1, 987654, 4294967296])
    def test_reads_field_19_after_comm(self, starttime):
        """starttime is field 22 of "man proc", index 19 after pid/comm/state."""
        assert updater._parse_stat(make_stat(starttime))[1] == starttime

    @pytest.mark.parametrize(
        'comm',
        [
            b'bash',
            b'a b',
            b'a)',
            b'(a)',
            b'a (b) c',
            b') (',
            b'\xe4\xbd\xa0\xe5\xa5\xbd',  # UTF-8 name, bytes must not be decoded
        ],
    )
    def test_comm_with_spaces_and_parentheses(self, comm):
        """The comm may contain spaces and parentheses, fields start at the last ')'."""
        assert updater._parse_stat(make_stat(4242, comm=comm))[1] == 4242

    def test_parser_ignores_trailing_content(self):
        """Fields after starttime are not needed, so a short tail is enough."""
        data = b'1 (x) S ' + b' '.join(b'%d' % i for i in range(1, 19)) + b' 777'
        assert updater._parse_stat(data)[1] == 777

    def test_reports_the_state_letter(self):
        """The state comes before starttime; linux reports a zombie as gone."""
        data = b'1 (x) Z ' + b' '.join(b'%d' % i for i in range(1, 19)) + b' 777'
        assert updater._parse_stat(data) == (b'Z', 777)

    @pytest.mark.parametrize(
        'data',
        [
            b'',
            b'1234 bash S 1 2 3',
            b'1234 (bash S 1 2 3',
            b'1234 (bash) S 1 2 3',
            b'1234 (bash) S ' + b' '.join(b'%d' % i for i in range(1, 19)),
            b'1234 (bash) S ' + b' '.join(b'%d' % i for i in range(1, 19)) + b' notanumber',
        ],
    )
    def test_malformed_content_is_not_reported_as_gone(self, data):
        """A malformed content raises OSError, never ProcessLookupError."""
        with pytest.raises(OSError) as excinfo:
            updater._parse_stat(data)
        assert not isinstance(excinfo.value, ProcessLookupError)


class TestStartTime:
    """start_time() against real processes."""

    def test_self_is_stable(self):
        """Two reads of the same live process return the same marker."""
        first = updater.start_time(os.getpid())
        second = updater.start_time(os.getpid())
        assert isinstance(first, int)
        assert first == second

    def test_children_identities_are_distinct(self):
        """Two processes started back to back have different identities.

        The marker alone is not unique: linux counts it in jiffies (10ms by
        default), so two processes of the same tick report the same value.
        Identity is the pair, and the pids always differ.
        """
        procs = [spawn_idle() for _ in range(2)]
        try:
            identities = [identity(proc.pid) for proc in procs]
        finally:
            kill_all(procs)
        assert all(isinstance(marker, int) for _, marker in identities)
        assert identities[0] != identities[1]

    def test_child_differs_from_self(self):
        """A child is a different process, so its identity differs from ours."""
        proc = spawn_idle()
        try:
            assert identity(proc.pid) != identity(os.getpid())
        finally:
            kill_all([proc])

    def test_dead_process(self):
        """A process that has been reaped raises ProcessLookupError."""
        proc = subprocess.Popen([sys.executable, '-c', 'pass'])
        proc.wait()
        with pytest.raises(ProcessLookupError):
            updater.start_time(proc.pid)

    def test_unused_pid(self):
        """A pid that is gone raises ProcessLookupError."""
        proc = spawn_idle()
        kill_all([proc])
        with pytest.raises(ProcessLookupError):
            updater.start_time(proc.pid)

    @pytest.mark.skipif(sys.platform != 'win32', reason='pid 0 is Windows only')
    def test_windows_idle_process_is_refused(self):
        """pid 0 cannot be opened, psutil reports it as AccessDenied."""
        with pytest.raises(PermissionError):
            updater.start_time(0)


class TestStandaloneModule:
    """The updater stays a single file with no third party imports."""

    ALLOWED = {
        'argparse',
        'ctypes',
        'datetime',
        'errno',
        'hashlib',
        'json',
        'logging',
        'os',
        'shutil',
        'socket',
        'subprocess',
        'sys',
        'time',
        'traceback',
    }

    def test_imports_are_stdlib_only(self):
        """Every imported module belongs to the allowed allowlist."""
        tree = ast.parse(updater_source())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    imported.add('.' * node.level + (node.module or ''))
                else:
                    imported.add((node.module or '').split('.')[0])
        assert imported <= self.ALLOWED, imported - self.ALLOWED

    def test_no_package_imports(self):
        """The file never imports the project itself, it runs as a plain script."""
        source = updater_source()
        assert 'from alasio' not in source
        assert 'import alasio' not in source
        assert 'import psutil' not in source
        assert 'from psutil' not in source

    def test_exposed_helpers(self):
        """The module exposes the two helpers the updater needs at this stage."""
        assert callable(updater.start_time)
        assert callable(updater.port_available)
        assert callable(updater._parse_stat)
