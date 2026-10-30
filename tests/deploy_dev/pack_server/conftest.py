"""
Fixtures shared by the tests of the pack server flow.

The flow of a repo (repo.Repo) writes one workspace in the run directory, and
its steps read the git repo of the config: every test needs a run directory in
the fake filesystem, with the builtin .gitattributes of the library next to it
(the pack encoders resolve the eol of the files with it), and the in-memory
pypi mirror of the fetch tests.
"""
import pytest

from alasio.deploy_dev.pack_server.repo_base import RepoBase
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack_server.test_fetch_wheel import MockMirror

# the builtin .gitattributes of the library, copied into the fake filesystem
# by the run_dir fixture, see GitAttributes
BUILTIN_GITATTRIBUTES = env.ALASIO_ROOT.joinpath('.gitattributes').atomic_read_bytes()


@pytest.fixture
def run_dir(fs, monkeypatch):
    """
    A run directory of the pack server in the fake filesystem.

    env.ALASIO_ROOT points at a folder of its own with the builtin
    .gitattributes of the library, so the pack encoders resolve the eol of the
    files in the fake filesystem.

    Returns:
        PathStr: Absolute path of the run directory
    """
    root = PathStr.new(fs.root_dir.path)
    library = root.joinpath('alasio')
    run = root.joinpath('pack_server')
    fs.create_dir(library)
    fs.create_dir(run)
    fs.create_file(library.joinpath('.gitattributes'), contents=BUILTIN_GITATTRIBUTES)
    monkeypatch.setattr(env, 'ALASIO_ROOT', library)
    monkeypatch.setattr(env, 'PROJECT_ROOT', run)
    return run


@pytest.fixture
def mirror():
    """
    The in-memory PyPI mirror the wheels are fetched from.

    Returns:
        MockMirror: The mock mirror of the fetch tests
    """
    return MockMirror()


def join_path(root, *parts):
    """
    Join path parts one by one, PathStr.joinpath takes one part only.

    Args:
        root (str): Root path
        *parts (str): Parts to join

    Returns:
        PathStr: Joined path
    """
    path = PathStr.new(root)
    for part in parts:
        path = path.joinpath(part)
    return path


def workspace_of(repo, config):
    """
    The workspace folder of a repo of the tests.

    Args:
        repo (MockGitRepo): Repo of the config
        config (PackRepoModel): Config of the repo

    Returns:
        PathStr: {run directory}/pack/{Author}_{Repo}_{Branch}
    """
    return RepoBase(repo, config).workspace
