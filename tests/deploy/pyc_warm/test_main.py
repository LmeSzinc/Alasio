"""
Tests of the command line of the warmup, see alasio/deploy/pyc_warm/__main__.py.
"""
import json
import re

import pytest

from alasio.deploy.pyc_warm.__main__ import main
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401


def without_time(text):
    """
    Mask the durations of the report lines, they differ at every run.

    Args:
        text (str): Log text, e.g. "pack    : 1 sources, ..., 0.00s"

    Returns:
        str: The text with every duration replaced by "<time>"
    """
    return re.sub(r'\d+\.\d\ds', '<time>', text)


class TestMain:
    """
    Tests for main().
    """

    @pytest.fixture(autouse=True)
    def _unmute(self):
        """A run of the backend mutes the logger of the process, restore it."""
        yield
        logger.mute_clear()

    def test_missing_root(self, fs, capsys):
        """A root that does not exist is the only fatal answer."""
        assert main(['--root', '/missing']) == 1
        assert capsys.readouterr().err == (
            '[pyc_warm] root does not exist: /missing\n'
        )

    def test_dry_run_json(self, fs, capsys):
        """The JSON line is the only line of a run the backend starts."""
        fs.create_file('/mod/module/main.py', contents=b'VALUE = 1\n')
        assert main(['--root', '/mod', '--dry-run', '--json']) == 0

        out = capsys.readouterr().out
        # The report is logged, and the logger is muted on all of its
        # outputs: the line the parent parses stands alone on stdout
        assert out.count('\n') == 1
        data = json.loads(out)
        assert data['tiers'] == [{
            'name': 'mod mod',
            'folder': '/mod',
            'sources': 1,
            'stale': 1,
            'compiled': 0,
            'failed': 0,
            'seconds': data['tiers'][0]['seconds'],
            'errors': [],
        }]
        assert (data['unresolved'], data['compiled'], data['failed'], data['stale']) == ([], 0, 0, 1)

    def test_json_mutes_every_output(self, fs, monkeypatch):
        """A run the backend starts writes no log line anywhere."""
        calls = []
        real_mute = logger.mute

        def counting_mute(**kwargs):
            calls.append(kwargs)
            real_mute(**kwargs)

        monkeypatch.setattr(logger, 'mute', counting_mute)
        fs.create_dir('/mod')
        assert main(['--root', '/mod', '--json']) == 0
        # stdout, the log file and the backend, in one call
        assert calls == [{'all': True}]

    def test_dry_run_without_json(self, fs, capsys):
        """A run started by hand logs its lines and prints nothing."""
        fs.create_file('/mod/module/main.py', contents=b'VALUE = 1\n')
        with logger.mock_capture_writer() as capture:
            assert main(['--root', '/mod', '--dry-run']) == 0

        assert without_time(capture.concat()) == """\
INFO |   /mod/module/main.py
INFO | mod mod : 1 sources, 1 stale, 0 compiled, 0 failed, <time>
INFO | total   : <time>
"""
        # Nothing on stdout, the report went to the logger
        assert capsys.readouterr().out == ''

    def test_mod_roots(self, fs, capsys):
        """--mod is repeatable, the trees are warmed in the order given."""
        for name in ('self', 'other'):
            fs.create_file(f'/mods/{name}/module/main.py', contents=b'VALUE = 1\n')
        assert main([
            '--root', '/mods/self',
            '--mod', '/mods/self',
            '--mod', '/mods/other',
            '--dry-run', '--json',
        ]) == 0

        data = json.loads(capsys.readouterr().out)
        assert [(tier['name'], tier['folder']) for tier in data['tiers']] == [
            ('mod self', '/mods/self'),
            ('mod other', '/mods/other'),
        ]
        assert data['stale'] == 2
