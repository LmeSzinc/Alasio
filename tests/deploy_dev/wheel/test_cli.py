"""Tests for the command line entry of the wheel builder."""
import pytest

from alasio.deploy_dev.wheel import cli
from alasio.deploy_dev.wheel.verify import BUILD_INPUTS
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.wheel.test_verify import make_sdist, make_wheel


class TestLogCapture:
    def test_logs_are_captured(self, fs, capture_logs):
        """Every log of the folder is captured in memory, never written to the log file."""
        logger.info('captured in memory')
        logger.error('captured error')
        assert capture_logs.fd.any_contains('captured in memory')
        assert capture_logs.fd.any_contains('captured error')
        assert capture_logs.backend.any_contains('captured error')


class TestVerifyCommand:
    def test_verify_file(self, fs, capture_logs):
        """A valid wheel passes, the exit code is 0."""
        assert cli.main(['verify', make_wheel()]) == 0
        assert capture_logs.fd.any_contains('Verified wheel demo==1.0.0')

    def test_verify_directory(self, fs, capture_logs):
        """A directory verifies every artifact it holds."""
        make_wheel()
        make_sdist()
        assert cli.main(['verify', '/repo/dist']) == 0
        assert capture_logs.fd.any_contains('Verified wheel')
        assert capture_logs.fd.any_contains('Verified sdist')

    def test_verify_default_path(self, fs):
        """The default path is dist of the current directory."""
        make_wheel()
        fs.chdir('/repo')
        assert cli.main(['verify']) == 0

    def test_verify_failure(self, fs, capture_logs):
        """A broken artifact fails, the exit code is 1."""
        assert cli.main(['verify', make_wheel(asar=b'broken')]) == 1
        assert capture_logs.fd.any_contains('Failed to verify')
        assert capture_logs.fd.any_contains('app.asar is not a valid archive')

    def test_verify_no_artifact(self, fs, capture_logs):
        """A directory without an artifact fails."""
        fs.create_dir('/repo/dist')
        assert cli.main(['verify', '/repo/dist']) == 1
        assert capture_logs.fd.any_contains('No wheel or sdist to verify')

    def test_verify_missing_path(self, fs, capture_logs):
        """A path that does not exist fails."""
        assert cli.main(['verify', '/repo/dist']) == 1
        assert capture_logs.fd.any_contains('Not a wheel, an sdist, or a directory')

    def test_verify_folder(self, fs, capture_logs):
        """The folder names the library tree inside the wheel."""
        assert cli.main(['verify', make_wheel(), '--folder', 'other']) == 1
        assert capture_logs.fd.any_contains('Missing required member')


class TestBuildCommand:
    def test_missing_command(self, fs):
        """A missing command is a command line error."""
        with pytest.raises(SystemExit) as e:
            cli.main([])
        assert e.value.code == 2

    def test_require_static_missing(self, fs, capture_logs):
        """The build stops before git when a build input is missing."""
        assert cli.main(['build', '--root', '/repo', '--require-static']) == 1
        assert capture_logs.fd.any_contains('Missing build input: frontend/build/index.html')

    def test_require_static_complete(self, fs, monkeypatch):
        """The build runs when every build input is there."""
        for path in BUILD_INPUTS:
            fs.create_file(f'/repo/{path}', contents=b'')
        monkeypatch.setattr(cli, 'BuildWheel', fake_build_wheel(True))
        assert cli.main(['build', '--root', '/repo', '--require-static']) == 0

    def test_build_arguments(self, fs, monkeypatch):
        """The root and the folder of the command line reach the builder."""
        created = {}
        monkeypatch.setattr(cli, 'BuildWheel', fake_build_wheel(True, created))
        assert cli.main(['build', '--root', '/repo', '--folder', 'demo']) == 0
        assert created == {'root': '/repo', 'folder': 'demo'}

    def test_build_failure(self, fs, monkeypatch):
        """A build that reports a failure exits with 1."""
        monkeypatch.setattr(cli, 'BuildWheel', fake_build_wheel(False))
        assert cli.main(['build', '--root', '/repo']) == 1

    def test_build_error(self, fs, monkeypatch, capture_logs):
        """An exception of the build exits with 1 instead of a traceback."""
        monkeypatch.setattr(cli, 'BuildWheel', fake_build_wheel(error=RuntimeError('boom')))
        assert cli.main(['build', '--root', '/repo']) == 1
        assert capture_logs.fd.any_contains('Build failed: boom')
        assert capture_logs.fd.any_contains('RuntimeError: boom')


def fake_build_wheel(result=False, created=None, error=None):
    """
    Build a fake BuildWheel class for the command line tests.

    Args:
        result (bool): Value build() returns
        created (dict): Filled with the arguments of the constructor
        error (Exception): Raised by build() instead of returning a result

    Returns:
        type: Fake class
    """
    class FakeBuildWheel:
        def __init__(self, root, folder):
            if created is not None:
                created.update(root=root, folder=folder)

        def build(self):
            if error is not None:
                raise error
            return result

    return FakeBuildWheel
