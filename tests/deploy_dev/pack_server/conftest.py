"""
Fixtures shared by the tests of the pack server flow.

The flow of a repo (repo.Repo) writes one workspace in the run directory, and
its steps read the git repo of the config: every test needs a run directory in
the fake filesystem, with the builtin .gitattributes of the library next to it
(the pack encoders resolve the eol of the files with it), the css sheet the flat
page inlines, and the in-memory pypi mirror of the fetch tests.
"""
import threading

import pytest

from alasio.codegen.pypisite.flat.gen import STYLE_FILE
from alasio.deploy_dev.pack_server.publish import lock as lock_module
from alasio.deploy_dev.pack_server.repo_base import RepoBase
from alasio.ext import env
from alasio.ext.file.filelock import FilelockTimeout
from alasio.ext.path import PathStr
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack_server.test_fetch_wheel import MockMirror

# the builtin .gitattributes of the library, copied into the fake filesystem
# by the run_dir fixture, see GitAttributes
BUILTIN_GITATTRIBUTES = env.ALASIO_ROOT.joinpath('.gitattributes').atomic_read_bytes()

# the sheet a flat page inlines into its head, read before the fake filesystem
# replaces the disk, see run_dir
PAGE_SHEET = PathStr.new(STYLE_FILE).atomic_read_text()


class MemoryLock:
    """
    In-memory stand-in of SQLiteFileLock for the tests of the fake filesystem.

    The lock of a channel is a SQLite database, opened in the C layer of the
    interpreter where the in-memory filesystem of the tests cannot serve it (the
    exclusion of the real lock is covered by test_lock.py on a real folder). One
    state per lock file, shared by the handles of the file the way
    SQLiteFileLock shares it: a second acquire of the same owner is a reentrancy
    error, an acquire of another owner times out.
    """

    # the owner of every locked file, keyed by the path of the lock file.
    # Replaced for every test, see use_memory_lock()
    states = {}

    def __init__(self, lock_file, timeout=-1):
        """
        Args:
            lock_file (str): Path of the lock file
            timeout (float): Default wait, kept for the SQLiteFileLock
                interface. Defaults to -1.
        """
        self.lock_file = str(lock_file)
        self.timeout = timeout

    @property
    def is_locked(self):
        """
        Returns:
            bool: True if the lock file is held
        """
        return self.lock_file in MemoryLock.states

    def acquire(self, timeout=None, owner=None):
        """
        Args:
            timeout (float): Ignored, the wait never succeeds here. Defaults to
                None, the timeout of the constructor
            owner (int): Identity of the owner, the calling thread when None.
                Defaults to None

        Returns:
            MemoryLock: self

        Raises:
            FilelockTimeout: If another owner holds the lock
            RuntimeError: If the owner already holds the lock
        """
        owner = threading.get_ident() if owner is None else owner
        holder = MemoryLock.states.get(self.lock_file)
        if holder == owner:
            raise RuntimeError(
                f'The lock "{self.lock_file}" is already held by this thread, '
                f'a reentrant acquire would wait for itself')
        if holder is not None:
            raise FilelockTimeout(self.lock_file)
        MemoryLock.states[self.lock_file] = owner
        return self

    def release(self, force=False):
        """
        Args:
            force (bool): True to release the lock whatever the owner.
                Defaults to False
        """
        if force:
            MemoryLock.states.pop(self.lock_file, None)
            return
        if MemoryLock.states.get(self.lock_file) == threading.get_ident():
            MemoryLock.states.pop(self.lock_file, None)


def use_memory_lock(monkeypatch):
    """
    Swap the lock of a channel for the in-memory lock of the tests.

    Args:
        monkeypatch (pytest.MonkeyPatch): The fixture of the test
    """
    monkeypatch.setattr(lock_module, 'SQLiteFileLock', MemoryLock)
    monkeypatch.setattr(MemoryLock, 'states', {})


@pytest.fixture
def run_dir(fs, monkeypatch):
    """
    A run directory of the pack server in the fake filesystem.

    env.ALASIO_ROOT points at a folder of its own with the builtin
    .gitattributes of the library, so the pack encoders resolve the eol of the
    files in the fake filesystem. The css sheet of the flat page is there too,
    the generator reads it from the folder of its module when a page is written.

    The lock of a channel is swapped for the in-memory lock of the tests, see
    MemoryLock.

    Returns:
        PathStr: Absolute path of the run directory
    """
    root = PathStr.new(fs.root_dir.path)
    library = root.joinpath('alasio')
    run = root.joinpath('pack_server')
    fs.create_dir(library)
    fs.create_dir(run)
    fs.create_file(library.joinpath('.gitattributes'), contents=BUILTIN_GITATTRIBUTES)
    fs.create_file(STYLE_FILE, contents=PAGE_SHEET)
    monkeypatch.setattr(env, 'ALASIO_ROOT', library)
    monkeypatch.setattr(env, 'PROJECT_ROOT', run)
    use_memory_lock(monkeypatch)
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
