"""
Tests for the run directory guard of the pack server.

The pack server clones the repos to pack and writes the packs into its run
directory, so it must run in a directory of its own: not the alasio library
itself, not a mod (a game script).
"""
import pytest

from alasio.deploy_dev.pack_server.gate import RunDirError, check_run_dir, is_library_dir, is_mod_dir
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.testing.filesystem import fs  # noqa: F401


@pytest.fixture
def root(fs, monkeypatch):
    """
    A run directory and the library root in the fake filesystem.

    Returns:
        PathStr: Absolute path of the run directory
    """
    library = PathStr.new(fs.root_dir.path).joinpath('alasio')
    run = PathStr.new(fs.root_dir.path).joinpath('pack_server')
    fs.create_dir(library)
    fs.create_dir(run)
    monkeypatch.setattr(env, 'ALASIO_ROOT', library)
    return run


class TestCheckRunDir:
    """Check the run directory of the pack server."""

    def test_ok(self, fs, root):
        """A directory of its own is accepted."""
        assert check_run_dir(root) is None

    def test_default_root(self, fs, root, monkeypatch):
        """The default run directory is env.PROJECT_ROOT."""
        monkeypatch.setattr(env, 'PROJECT_ROOT', root)
        assert check_run_dir() is None

    def test_not_set(self, fs, root, monkeypatch):
        """An unset run directory is refused."""
        monkeypatch.setattr(env, 'PROJECT_ROOT', PathStr.new(''))
        with pytest.raises(RunDirError) as e:
            check_run_dir()
        assert 'Run directory is not set' in str(e.value)

    def test_missing(self, fs, root):
        """A run directory that does not exist is refused."""
        with pytest.raises(RunDirError) as e:
            check_run_dir(root.joinpath('missing'))
        assert 'Run directory does not exist' in str(e.value)

    def test_not_a_folder(self, fs, root):
        """A file as the run directory is refused."""
        fs.create_file(root.joinpath('file'))
        with pytest.raises(RunDirError) as e:
            check_run_dir(root.joinpath('file'))
        assert 'Run directory is not a folder' in str(e.value)

    def test_library_itself(self, fs, root):
        """The alasio library itself is refused."""
        with pytest.raises(RunDirError) as e:
            check_run_dir(env.ALASIO_ROOT)
        assert 'Cannot run in the alasio library' in str(e.value)

    def test_inside_library(self, fs, root):
        """A folder inside the alasio library is refused."""
        inside = env.ALASIO_ROOT.joinpath('pack_server')
        fs.create_dir(inside)
        with pytest.raises(RunDirError) as e:
            check_run_dir(inside)
        assert 'Cannot run in the alasio library' in str(e.value)

    @pytest.mark.parametrize('marker', [
        'module/config/_index/config.index.json',
        'module/main.py',
        'module',
        'assets',
    ])
    def test_mod(self, fs, root, marker):
        """A mod (a game script) is refused, the marker is named."""
        fs.create_file(root.joinpath(marker), contents='')
        with pytest.raises(RunDirError) as e:
            check_run_dir(root)
        assert 'Cannot run in a mod' in str(e.value)
        assert marker in str(e.value)


class TestIsModDir:
    """Check whether a folder is the library or a mod."""

    def test_run_dir(self, fs, root):
        """A directory of its own is neither the library nor a mod."""
        assert is_library_dir(root) is False
        assert is_mod_dir(root) == ''

    def test_marker_priority(self, fs, root):
        """The marker found is returned, the strongest one first."""
        fs.create_file(root.joinpath('module/main.py'), contents='')
        fs.create_file(root.joinpath('module/config/_index/config.index.json'), contents='')
        assert is_mod_dir(root) == 'module/config/_index/config.index.json'

    def test_library_dir(self, fs, root):
        """The library root and the folders inside it are the library."""
        assert is_library_dir(env.ALASIO_ROOT) is True
        assert is_library_dir(env.ALASIO_ROOT.joinpath('deploy_dev')) is True
        # a folder with the same name prefix is not inside the library
        assert is_library_dir(env.ALASIO_ROOT + '_backup') is False
