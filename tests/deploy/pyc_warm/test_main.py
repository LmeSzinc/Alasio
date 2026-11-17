"""
Tests of the command line of the warmup, see alasio/deploy/pyc_warm/__main__.py.
"""
import json
import re

import pytest

from alasio.deploy.pyc_warm.__main__ import main
from alasio.deploy.pyc_warm.runner import Report, ResultReport
from alasio.deploy.pyc_warm.scope import TIER_MOD, ScopeResult
from alasio.ext import env
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401


def without_time(text):
    """
    Mask the durations of the report lines, they differ at every run.

    Args:
        text (str): Log text, e.g. "pack/trio: 1 sources, ..., 0.00s"

    Returns:
        str: The text with every duration replaced by "<time>"
    """
    return re.sub(r'\d+\.\d\ds', '<time>', text)


def fake_report(stale, compiled):
    """
    A warm() stand-in: one mod unit, no compilation.

    Args:
        stale (int): Sources the unit answers stale
        compiled (int): pyc files the unit answers written

    Returns:
        callable: The stand-in, which logs like warm() and answers the report
    """
    def fake_warm(scope, dry_run=False):
        entry = ResultReport(TIER_MOD, 'mod')
        entry.sources = stale
        entry.stale = stale
        entry.compiled = compiled
        entry.seconds = 0.0
        report = Report()
        report.results.append(entry)
        report.seconds = 1.25
        logger.info(entry.line())
        return report

    return fake_warm


class TestMain:
    """
    Tests for main().
    """

    @pytest.fixture(autouse=True)
    def _restore(self, monkeypatch):
        """
        Restore the global state the command line sets.

        main() resolves the project root of the process and a --json run
        mutes the logger of the process: a test that leaves either behind
        changes every test after it.
        """
        monkeypatch.setattr(env, 'PROJECT_ROOT', env.PROJECT_ROOT)
        yield
        logger.mute_clear()

    def test_missing_root(self, fs, capsys):
        """A root that does not exist is the only fatal answer."""
        assert main(['--root', '/missing']) == 1
        assert capsys.readouterr().err == (
            '[pyc_warm] root does not exist: /missing\n'
        )

    def test_project_root(self, fs, monkeypatch):
        """The root of the command line is the project root of the run."""
        monkeypatch.setattr('alasio.deploy.pyc_warm.runner.warm', fake_report(0, 0))
        fs.create_dir('/mod')
        assert main(['--root', '/mod', '--dry-run']) == 0
        # The scope reads the mods of the project root, so the root has to
        # be in force before the scope is built
        assert env.PROJECT_ROOT == '/mod'

    def test_json(self, fs, capsys, monkeypatch):
        """The JSON line is the only line of a run the backend starts."""
        monkeypatch.setattr('alasio.deploy.pyc_warm.runner.warm', fake_report(3, 2))
        fs.create_dir('/mod')
        assert main(['--root', '/mod', '--json']) == 0

        out = capsys.readouterr().out
        # The report is logged, and the logger is muted on all of its
        # outputs: the line the parent parses stands alone on stdout
        assert out.count('\n') == 1
        data = json.loads(out)
        assert data['results'] == [{
            'tier': TIER_MOD,
            'target': 'mod',
            'sources': 3,
            'stale': 3,
            'compiled': 2,
            'failed': 0,
            'seconds': 0.0,
            'errors': [],
        }]
        assert (data['unresolved'], data['compiled'], data['failed'], data['stale']) == ([], 2, 0, 3)
        assert data['seconds'] == 1.25

    def test_json_mutes_every_output(self, fs, monkeypatch):
        """A run the backend starts writes no log line anywhere."""
        calls = []
        real_mute = logger.mute

        def counting_mute(**kwargs):
            calls.append(kwargs)
            real_mute(**kwargs)

        monkeypatch.setattr('alasio.deploy.pyc_warm.runner.warm', fake_report(0, 0))
        monkeypatch.setattr(logger, 'mute', counting_mute)
        fs.create_dir('/mod')
        assert main(['--root', '/mod', '--json']) == 0
        # stdout, the log file and the backend, in one call
        assert calls == [{'all': True}]

    def test_without_json(self, fs, capsys, monkeypatch):
        """A run started by hand logs its lines and prints nothing."""
        monkeypatch.setattr('alasio.deploy.pyc_warm.runner.warm', fake_report(2, 2))
        fs.create_dir('/mod')
        with logger.mock_capture_writer() as capture:
            assert main(['--root', '/mod', '--dry-run']) == 0

        assert without_time(capture.concat()) == """\
INFO | mod/mod: 2 sources, 2 stale, 2 compiled, 0 failed, <time>
INFO | total   : <time>
"""
        # Nothing on stdout, the report went to the logger
        assert capsys.readouterr().out == ''

    def test_the_scope_of_the_run(self, fs, monkeypatch):
        """The run warms the scope of the project root, nothing else."""
        built = []

        class FakeScope:
            def __init__(self):
                built.append(True)
                self.results = [ScopeResult(TIER_MOD, 'mod', ['/mod/main.py'])]

            def tiers(self):
                yield from self.results

        monkeypatch.setattr('alasio.deploy.pyc_warm.scope.Scope', FakeScope)
        fs.create_dir('/mod')
        assert main(['--root', '/mod', '--dry-run', '--json']) == 0
        assert built == [True]
