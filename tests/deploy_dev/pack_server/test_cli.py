"""
The exit code of the command line entry of the pack server.

A scheduler reads three outcomes: 0 for a run that published everything, 1 for
a run a later attempt may succeed at (a network failure, a busy channel), and 2
for a run that has to be fixed by editing a file (the run directory, a config).
A configuration error is never collected into a failed run: it stops the run at
once, see PackRun.run.

`check_run_dir()` runs once per process, which would make the second test of the
file see a no-op. The tests therefore swap it for its undecorated form, the
seam the gate documents (`check_run_dir.__wrapped__`), so every case is checked
for real.
"""

import sys

import pytest

import alasio.deploy_dev.pack_server.main as main_module
from alasio.deploy_dev.pack_server.gate import RunDirError, check_run_dir
from alasio.deploy_dev.pack_server.main import PackRunError
from alasio.deploy_dev.pack_server.model import ConfigError
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.testing.filesystem import fs  # noqa: F401

pytestmark = [
    pytest.mark.parallel,
]


@pytest.fixture
def root(fs, monkeypatch):
    """
    A run directory of the fake filesystem, and a real run-directory check.

    Returns:
        PathStr: Absolute path of the run directory
    """
    root = PathStr.new(fs.root_dir.path).joinpath('pack_server')
    root.makedirs(exist_ok=True)
    monkeypatch.setattr(env, 'PROJECT_ROOT', root)
    # the undecorated check: the decorator keeps the first result of the
    # process, and every test of this file needs its own
    monkeypatch.setattr(main_module, 'check_run_dir', check_run_dir.__wrapped__)
    return root


def run_cli(monkeypatch, argv):
    """
    Run `main()` with these arguments.

    Args:
        monkeypatch (pytest.MonkeyPatch): The fixture of the test
        argv (list[str]): Arguments, without the program name

    Returns:
        int: The exit code, None when main() returned normally
    """
    monkeypatch.setattr(sys, 'argv', ['main'] + argv)
    try:
        main_module.main()
    except SystemExit as e:
        return e.code
    return None


def stub_server(monkeypatch, error=None):
    """
    Replace the run of the configs with one that returns or raises.

    Args:
        monkeypatch (pytest.MonkeyPatch): The fixture of the test
        error (Exception): Raised by the run, None to return
    """
    class FakeServer:
        def run(self):
            if error is not None:
                raise error

    monkeypatch.setattr(main_module, 'PackServer', FakeServer)


class TestRoot:
    """The run directory is checked before the first log line."""

    def test_a_missing_root_is_a_usage_error(self, root, monkeypatch):
        """A mistyped root is refused and nothing is created on the disk.

        Before the check was moved in front of the first log line, opening the
        log file created {root}/log: the root then existed, the check read it as
        a run directory without a config, and the run exited 0.
        """
        missing = PathStr.new(root).joinpath('typo')
        stub_server(monkeypatch)
        assert run_cli(monkeypatch, ['--root', str(missing)]) == 2
        assert not missing.exists()

    def test_the_library_is_refused(self, root, monkeypatch):
        """The library itself is not a run directory."""
        stub_server(monkeypatch)
        assert run_cli(monkeypatch, ['--root', str(env.ALASIO_ROOT)]) == 2

    def test_a_run_directory_is_accepted(self, root, monkeypatch):
        """A usable root reaches the run of the configs."""
        stub_server(monkeypatch)
        assert run_cli(monkeypatch, ['--root', str(root)]) is None


class TestExitCode:
    """The code tells a fixable config from a run worth retrying."""

    def test_a_successful_run_exits_zero(self, root, monkeypatch):
        """Every config published: 0."""
        stub_server(monkeypatch)
        assert run_cli(monkeypatch, []) is None

    def test_a_config_error_exits_two(self, root, monkeypatch):
        """A value the operator has to edit: 2."""
        stub_server(monkeypatch, error=ConfigError('Empty Author'))
        assert run_cli(monkeypatch, []) == 2

    def test_a_retryable_failure_exits_one(self, root, monkeypatch):
        """A failure a later run may not see: 1."""
        stub_server(monkeypatch, error=PackRunError([('a.yaml', 'connection reset')]))
        assert run_cli(monkeypatch, []) == 1

    def test_a_root_that_vanished_exits_two(self, root, monkeypatch):
        """A root that stops being usable during the run is not a retry."""
        stub_server(monkeypatch, error=RunDirError('Run directory was removed'))
        assert run_cli(monkeypatch, []) == 2
