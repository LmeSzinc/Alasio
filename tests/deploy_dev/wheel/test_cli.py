"""Tests for the command line entry of the wheel builder."""
import pytest

from alasio.deploy_dev.wheel import cli
from alasio.deploy_dev.wheel.verify import BUILD_INPUTS, collect_artifacts
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.wheel.test_verify import make_sdist, make_wheel


class TestVerifyCommand:
    def test_verify_file(self, fs):
        """A valid wheel passes, the exit code is 0."""
        file = make_wheel()
        with logger.mock_capture_writer() as capture:
            code = cli.main(['verify', file])
        assert code == 0
        assert capture.concat() == f"""\
INFO | Verified wheel demo==1.0.0 (8 files, frontend 2 files, asar 8 entries): {file}
"""

    def test_verify_directory(self, fs):
        """A directory verifies every artifact it holds."""
        make_wheel()
        make_sdist()
        files, _ = collect_artifacts(['/repo/dist'])
        assert len(files) == 2
        with logger.mock_capture_writer() as capture:
            code = cli.main(['verify', '/repo/dist'])
        assert code == 0
        assert capture.concat() == f"""\
INFO | Verified wheel demo==1.0.0 (8 files, frontend 2 files, asar 8 entries): {files[0]}
INFO | Verified sdist demo==1.0.0 (3 files): {files[1]}
"""

    def test_verify_default_path(self, fs):
        """The default path is dist of the current directory."""
        make_wheel()
        fs.chdir('/repo')
        with logger.mock_capture_writer():
            code = cli.main(['verify'])
        assert code == 0

    def test_verify_failure(self, fs):
        """A broken artifact fails, the exit code is 1."""
        file = make_wheel(asar=b'broken')
        with logger.mock_capture_writer() as capture:
            code = cli.main(['verify', file])
        assert code == 1
        assert capture.concat() == f"""\
ERROR | Failed to verify {file}
ERROR |   app.asar is not a valid archive: archive is truncated, expected 16 bytes but got 6
"""

    def test_verify_no_artifact(self, fs):
        """A directory without an artifact fails."""
        fs.create_dir('/repo/dist')
        with logger.mock_capture_writer() as capture:
            code = cli.main(['verify', '/repo/dist'])
        assert code == 1
        assert capture.concat() == 'ERROR | No wheel or sdist to verify\n'

    def test_verify_missing_path(self, fs):
        """A path that does not exist fails."""
        with logger.mock_capture_writer() as capture:
            code = cli.main(['verify', '/repo/dist'])
        assert code == 1
        assert capture.concat() == """\
ERROR | Not a wheel, an sdist, or a directory that holds them: /repo/dist
ERROR | No wheel or sdist to verify
"""

    def test_verify_folder(self, fs):
        """The folder names the library tree inside the wheel."""
        file = make_wheel()
        with logger.mock_capture_writer() as capture:
            code = cli.main(['verify', file, '--folder', 'other'])
        assert code == 1
        assert capture.concat() == f"""\
ERROR | Failed to verify {file}
ERROR |   Missing required member: other/deploy_data/frontend/index.html
ERROR |   Missing required member: other/deploy_data/frontend-manifest.pack
ERROR |   Missing required member: other/deploy_data/webapp/app.asar
"""


class TestBuildCommand:
    def test_missing_command(self, fs):
        """A missing command is a command line error."""
        with pytest.raises(SystemExit) as e:
            cli.main([])
        assert e.value.code == 2

    def test_require_static_missing(self, fs):
        """The build stops before git when a build input is missing."""
        with logger.mock_capture_writer() as capture:
            code = cli.main(['build', '--root', '/repo', '--require-static'])
        assert code == 1
        assert capture.concat() == """\
ERROR | Missing build input: frontend/build/index.html
ERROR | Missing build input: frontend/build/_app/version.json
ERROR | Missing build input: webapp/dist/main/index.js
ERROR | Missing build input: webapp/dist/preload/index.js
ERROR | Missing build input: webapp/dist/renderer/index.html
ERROR | Missing build input: webapp/package.json
ERROR | Build inputs are incomplete, the wheel would carry no frontend / no webapp
"""

    def test_require_static_complete(self, fs, monkeypatch):
        """The build runs when every build input is there."""
        for path in BUILD_INPUTS:
            fs.create_file(f'/repo/{path}', contents=b'')
        monkeypatch.setattr(cli, 'BuildWheel', fake_build_wheel(True))
        with logger.mock_capture_writer():
            code = cli.main(['build', '--root', '/repo', '--require-static'])
        assert code == 0

    def test_build_arguments(self, fs, monkeypatch):
        """The root and the folder of the command line reach the builder."""
        created = {}
        monkeypatch.setattr(cli, 'BuildWheel', fake_build_wheel(True, created))
        with logger.mock_capture_writer():
            code = cli.main(['build', '--root', '/repo', '--folder', 'demo'])
        assert code == 0
        assert created == {'root': '/repo', 'folder': 'demo'}

    def test_build_failure(self, fs, monkeypatch):
        """A build that reports a failure exits with 1."""
        monkeypatch.setattr(cli, 'BuildWheel', fake_build_wheel(False))
        with logger.mock_capture_writer():
            code = cli.main(['build', '--root', '/repo'])
        assert code == 1

    def test_build_error(self, fs, monkeypatch):
        """An exception of the build exits with 1 instead of a traceback."""
        monkeypatch.setattr(cli, 'BuildWheel', fake_build_wheel(error=RuntimeError('boom')))
        with logger.mock_capture_writer():
            code = cli.main(['build', '--root', '/repo'])
        assert code == 1


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
