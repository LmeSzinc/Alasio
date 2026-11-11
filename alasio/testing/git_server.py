"""
A git:// server of the test suite, built on the git executable of the machine.

The git transport of the project is a client of the git protocol over a plain
tcp connection, so a test of it needs something that answers that connection
with a repository the test controls. `git daemon` is that server: the real
implementation of the protocol, shipped with git itself, so the advertisement,
the negotiation and the pack are the ones a real server sends, and no second
server has to be written and trusted here.

The daemon is started on a free port of the loopback interface and stopped with
the context manager, so the servers of two workers of the parallel test suite
never meet, and a test never talks to anything but its own machine.

One behaviour of the daemon matters to the client and is covered by the tests
of the transport: it may end the answer with a reset instead of a clean close
(it does on windows), after every byte of the pack has been sent.

The repository itself is built by the plumbing commands of git (`hash-object`,
`mktree`, `commit-tree`), so the object ids, the pack and the negotiation of a
test are the ones of a repository built by `git commit`, without a worktree and
without a user identity of the machine.
"""
import contextlib
import dataclasses
import os
import socket
import stat
import subprocess
import time

from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import folder_rmtree

# The server is never exposed to the network, a test talks to its own machine
LOOPBACK = '127.0.0.1'
# Seconds to wait until the listening socket of a daemon accepts a connection
START_TIMEOUT = 10
# A port picked by free_port() is free until something else takes it, the
# daemon is started again on another one when that happens
START_ATTEMPTS = 5
# Seconds between two probes of the listening socket
PROBE_INTERVAL = 0.02


def git(*args, input=b''):
    """
    Run a git command and return what it wrote on stdout.

    Args:
        *args (str): Arguments of the command
        input (bytes): Content of the standard input

    Returns:
        bytes: Content of the standard output

    Raises:
        RuntimeError: When git exits with a non zero code, with its stderr
    """
    arguments = ('git',) + tuple(str(arg) for arg in args)
    result = subprocess.run(arguments, input=input, capture_output=True)
    if result.returncode:
        stderr = result.stderr.decode(errors='replace').strip()
        raise RuntimeError(f'{" ".join(arguments)} exited with {result.returncode}: {stderr}')
    return result.stdout


class GitRepository:
    """
    A bare repository built by the plumbing commands of git.

    A commit is written the way git writes it: one blob per file, a tree, and a
    commit object.
    """

    def __init__(self, path):
        """
        Args:
            path (str): Folder of the repository, created by build()
        """
        self.path = PathStr.new(path)

    @classmethod
    def build(cls, folder, name='server.git'):
        """
        Create an empty bare repository in a folder.

        Args:
            folder (str): Folder that holds the repository
            name (str): Name of the repository folder. Defaults to 'server.git'.

        Returns:
            GitRepository: The repository
        """
        repo = cls(PathStr.new(folder).joinpath(name))
        git('init', '--quiet', '--bare', repo.path)
        # The machine of a developer or of a runner may have no identity
        # configured, and a commit cannot be written without one
        git('-C', repo.path, 'config', 'user.name', 'alasio')
        git('-C', repo.path, 'config', 'user.email', 'alasio@example.com')
        return repo

    def commit(self, files, message='commit', parent=None):
        """
        Write a commit holding a mapping of file name to content.

        Args:
            files (dict[str, bytes]): Content of the files in the root of the tree
            message (str): Commit message
            parent (str): Sha1 of the parent commit, none for the first commit

        Returns:
            str: Sha1 of the commit
        """
        entries = ''
        for name, content in files.items():
            sha = git('-C', self.path, 'hash-object', '-w', '--stdin', input=content).strip().decode()
            entries += f'100644 blob {sha}\t{name}\n'
        tree = git('-C', self.path, 'mktree', input=entries.encode()).strip().decode()
        arguments = ['-C', self.path, 'commit-tree', tree, '-m', message]
        if parent is not None:
            arguments += ['-p', parent]
        return git(*arguments).strip().decode()

    def update_ref(self, ref, sha):
        """
        Point a reference at a commit.

        Args:
            ref (str): Name of the reference, e.g. refs/tags/v0.5.1
            sha (str): Sha1 of a commit
        """
        git('-C', self.path, 'update-ref', ref, sha)

    def read_blob(self, sha):
        """
        Read a blob back, to check what a commit holds.

        Args:
            sha (str): Sha1 of the blob

        Returns:
            bytes: Content of the blob
        """
        return git('-C', self.path, 'cat-file', 'blob', sha)


@dataclasses.dataclass(frozen=True)
class GitServer:
    """
    The address of a repository being served.

    Attributes:
        url (str): Url of the repository, e.g. git://127.0.0.1:51235/server.git
        port (int): Port the server listens on
        repo (PathStr): Folder of the served repository
    """

    url: str
    port: int
    repo: PathStr


@contextlib.contextmanager
def serve_repository(repo):
    """
    Serve one repository with `git daemon` until the context exits.

    Args:
        repo (str): Folder of the bare repository to serve

    Yields:
        GitServer: The address of the repository

    Raises:
        RuntimeError: When the daemon cannot be started, with the reason
    """
    repo = PathStr.new(repo)
    folder = repo.uppath(1)
    reason = 'the daemon did not start'
    for _ in range(START_ATTEMPTS):
        port = free_port()
        process = subprocess.Popen(
            (
                'git', 'daemon',
                '--reuseaddr',
                '--export-all',
                f'--base-path={folder}',
                f'--listen={LOOPBACK}',
                f'--port={port}',
                # The daemon has nothing to say that the test wants to read
                '--log-destination=none',
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if wait_until_listening(process, port):
            try:
                yield GitServer(url=f'git://{LOOPBACK}:{port}/{repo.name}', port=port, repo=repo)
            finally:
                stop(process)
            return
        # The daemon is either gone or unable to listen, and a daemon that is
        # still running would keep its port for the rest of the run
        if process.poll() is not None:
            reason = f'the daemon exited with {process.returncode}'
        else:
            reason = 'the daemon did not listen'
        stop(process)
    raise RuntimeError(f'cannot serve {repo}: {reason}')


def free_port():
    """
    Ask the system for a port nothing listens on, then release it.

    Returns:
        int: The port
    """
    with socket.socket() as probe:
        probe.bind((LOOPBACK, 0))
        return probe.getsockname()[1]


def wait_until_listening(process, port):
    """
    Wait until the daemon of a port accepts a connection.

    Args:
        process (subprocess.Popen): The daemon
        port (int): Port it was asked to listen on

    Returns:
        bool: True when the port answers, False when the daemon exited instead
    """
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with socket.create_connection((LOOPBACK, port), timeout=0.2):
                return True
        except OSError:
            time.sleep(PROBE_INTERVAL)
    return False


def stop(process):
    """
    Stop a daemon and the children it started.

    `git daemon` re-executes itself: the process behind the handle listens on
    nothing, the listening socket belongs to a process it started (on windows
    the fork of git is a re-exec), so the whole tree has to be killed. Killing
    only the handle leaves a daemon behind, and the port of that test stays
    bound for the rest of the run.

    Args:
        process (subprocess.Popen): The daemon
    """
    if env.WINDOWS:
        # The tree is what holds the socket, and taskkill reports a pid that is
        # already gone as an error instead of failing the test
        subprocess.run(
            ('taskkill', '/F', '/T', '/PID', str(process.pid)),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    elif process.poll() is None:
        process.kill()
    process.wait()


def remove_folder(folder):
    """
    Remove the folder of a repository.

    Git marks the objects of a repository read-only on windows, so a plain
    rmtree leaves them behind and the folder of a test stays in temp forever.

    Args:
        folder (str): Folder to remove
    """
    for root, _, files in os.walk(folder):
        for name in files:
            try:
                os.chmod(os.path.join(root, name), stat.S_IWRITE)
            except OSError:
                pass
    folder_rmtree(folder)
