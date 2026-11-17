"""
Tests of the warmup process entry, see alasio/backend/pyc_warm/entry.py.
"""
import ast
import sys

import msgspec
import pytest

from alasio.backend.pyc_warm import entry as entry_module
from alasio.ext import env
from alasio.logger import logger
from alasio.logger.writer import PseudoStream

pytestmark = [
    pytest.mark.parallel,
]

# The report the stand-in run answers
REPORT = {'results': [], 'compiled': 2, 'failed': 0, 'stale': 2, 'seconds': 0.25}

# Modules of the standard library of this interpreter, by top level name
STDLIB = set(sys.stdlib_module_names) if hasattr(sys, 'stdlib_module_names') else None


class FakeConn:
    """
    The parent end of the pipe, as the entry uses it.

    Attributes:
        sent (list[bytes]): The reports the entry sent
        closed (bool): Whether the entry closed it
    """

    def __init__(self):
        self.sent = []
        self.closed = False

    def send_bytes(self, data):
        """
        Args:
            data (bytes): The report being sent
        """
        self.sent.append(data)

    def close(self):
        """Close the connection."""
        self.closed = True


class FakeConnLost(FakeConn):
    """
    A pipe whose parent is gone: every send raises.
    """

    def send_bytes(self, data):
        """
        Args:
            data (bytes): Ignored

        Raises:
            EOFError: Always, the parent is gone
        """
        raise EOFError()


@pytest.fixture
def restore():
    """
    Restore the process state the entry sets.

    Yields:
        None: The block of the test
    """
    root = env.PROJECT_ROOT
    yield
    env.set_project_root(root)
    logger.mute_clear()


class TestProcessBoundary:
    """
    Tests of the import contract of the entry module.
    """

    def test_module_level_imports_are_stdlib_only(self):
        """The backend imports this module to spawn, it must stay light."""
        source = open(entry_module.__file__, encoding='utf-8').read()
        tree = ast.parse(source)
        imported = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                imported.update(alias.name.partition('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.partition('.')[0])
        assert imported <= {'sys'}, imported


def call_entry(conn, project_root='/the/root'):
    """
    Run the warmup entry the way the manager spawns it.

    Args:
        conn (FakeConn): The parent end of the pipe
        project_root (str): Project root the manager passes
    """
    return entry_module.pyc_warm_entry('pyc_warm', 'pyc_warm', conn, project_root)


class TestEntry:
    """
    Tests of pyc_warm_entry().
    """

    @pytest.fixture(autouse=True)
    def _stand_in(self, monkeypatch):
        """
        Replace the run: the entry must not compile anything in a test.

        Args:
            monkeypatch: The pytest monkeypatch fixture
        """
        from alasio.deploy.pyc_warm import runner, scope

        ran = []
        self_ = self

        class FakeScope:
            def __repr__(self):
                return '<FakeScope>'

        class FakeReport:
            def to_dict(self):
                return REPORT

        def fake_warm(scope_obj, dry_run=False):
            ran.append(scope_obj)
            return FakeReport()

        monkeypatch.setattr(runner, 'warm', fake_warm)
        monkeypatch.setattr(scope, 'Scope', FakeScope)
        self_.ran = ran

    def test_sets_the_project_root(self, restore):
        """The mods of the run are the ones of the root it is given."""
        conn = FakeConn()
        call_entry(conn, '/the/root')
        assert env.PROJECT_ROOT == '/the/root'
        assert len(self.ran) == 1

    def test_without_a_root_the_process_keeps_its_own(self, restore):
        """An entry spawned without a root does not reset it to the cwd."""
        env.set_project_root('/a/root')
        conn = FakeConn()
        call_entry(conn, '')
        assert env.PROJECT_ROOT == '/a/root'

    def test_sends_the_report(self, restore):
        """The report is a msgpack line on the pipe, not a print."""
        conn = FakeConn()
        call_entry(conn, '/the/root')
        assert conn.closed is True
        assert msgspec.msgpack.decode(conn.sent[0]) == REPORT

    def test_mutes_the_logger(self, monkeypatch, restore):
        """The warmup never opens the log file of the release."""
        calls = []
        real_mute = logger.mute

        def counting_mute(**kwargs):
            calls.append(kwargs)
            real_mute(**kwargs)

        monkeypatch.setattr(logger, 'mute', counting_mute)
        conn = FakeConn()
        call_entry(conn, '/the/root')
        # stdout, the log file and the backend, before the first line of the
        # run: a spawn child inherits argv[0] from the supervisor, so the file
        # it would derive is the one gui.py is writing at that same moment
        assert calls == [{'all': True}]
        # The writer really is muted, not only told to be
        assert isinstance(logger._writer.fd, PseudoStream)

    def test_a_crashed_run_is_reported(self, monkeypatch, restore):
        """A run that raises answers an error rather than an exit code."""
        from alasio.deploy.pyc_warm import runner

        def raising(scope_obj, dry_run=False):
            raise RuntimeError('boom')

        monkeypatch.setattr(runner, 'warm', raising)
        conn = FakeConn()
        call_entry(conn, '/the/root')
        report = msgspec.msgpack.decode(conn.sent[0])
        assert report == {'error': 'RuntimeError: boom'}
        assert conn.closed is True

    def test_a_lost_parent_is_not_an_error(self, restore):
        """A backend that died during the run leaves nobody to report to."""
        conn = FakeConnLost()
        call_entry(conn, '/the/root')
        assert conn.closed is True
