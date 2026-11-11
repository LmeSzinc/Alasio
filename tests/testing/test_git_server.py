"""
Tests for the git server of the test suite, see alasio.testing.git_server.
"""
import os
import secrets
import socket
import subprocess
import time

import pytest
import trio

from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.git.fetch.argument import Arguments
from alasio.git.fetch.transport_git import GitTransport
from alasio.testing import git_server

pytestmark = pytest.mark.parallel

# A command of the machine is fast, anything slower is a hang
TIMEOUT = 60


@pytest.fixture
def root():
    """
    A folder of the disposable folder of the repository.

    Yields:
        PathStr: The folder, removed with the repository it may hold
    """
    folder = PathStr.new(env.ALASIO_TEMP).joinpath(f'git_server_{secrets.token_hex(8)}')
    folder.makedirs(exist_ok=True)
    try:
        yield folder
    finally:
        git_server.remove_folder(folder)


class TestGitRepository:
    """Tests for building a repository with the plumbing commands."""

    def test_commit_holds_the_files(self, root):
        """A file of a commit is readable under the name it was committed with."""
        repo = git_server.GitRepository.build(root)
        sha = repo.commit({'old.bin': b'old content'}, message='first')
        tree = git_server.git('-C', repo.path, 'ls-tree', '-r', '--name-only', sha)
        assert tree == b'old.bin\n'
        blob = git_server.git('-C', repo.path, 'rev-parse', f'{sha}:old.bin').strip().decode()
        assert repo.read_blob(blob) == b'old content'

    def test_second_commit_has_a_parent(self, root):
        """A commit built with a parent is a child of that commit."""
        repo = git_server.GitRepository.build(root)
        first = repo.commit({'old.bin': b'old'}, message='first')
        second = repo.commit({'old.bin': b'old', 'new.bin': b'new'}, message='second', parent=first)
        assert git_server.git('-C', repo.path, 'rev-parse', f'{second}^').strip().decode() == first

    def test_a_reference_points_at_its_commit(self, root):
        """A reference of the repository resolves to the commit it was set to."""
        repo = git_server.GitRepository.build(root)
        sha = repo.commit({'old.bin': b'old'}, message='first')
        repo.update_ref('refs/tags/v0.5.1', sha)
        assert git_server.git('-C', repo.path, 'rev-parse', 'refs/tags/v0.5.1').strip().decode() == sha

    def test_the_repository_carries_its_own_identity(self, root):
        """A commit needs no identity of the machine, the repository has one."""
        repo = git_server.GitRepository.build(root)
        sha = repo.commit({'old.bin': b'old'}, message='first')
        author = git_server.git('-C', repo.path, 'cat-file', 'commit', sha)
        assert b'author alasio <alasio@example.com>' in author


class TestServeRepository:
    """Tests for serving a repository with git daemon."""

    def build(self, root, name='server.git'):
        """
        Build a repository with one tagged commit.

        Args:
            root (PathStr): Folder of the repository
            name (str): Name of the repository folder. Defaults to 'server.git'.

        Returns:
            tuple: The repository and the sha1 of its commit
        """
        repo = git_server.GitRepository.build(root, name=name)
        sha = repo.commit({'old.bin': b'old'}, message='first')
        repo.update_ref('refs/tags/v0.5.1', sha)
        return repo, sha

    def test_the_real_git_client_reads_the_refs(self, root):
        """The daemon answers the real client, so the transport talks to git."""
        repo, sha = self.build(root)
        with git_server.serve_repository(repo.path) as server:
            result = subprocess.run(
                ('git', 'ls-remote', server.url), capture_output=True, timeout=TIMEOUT,
            )
        assert result.returncode == 0, result.stderr.decode(errors='replace')
        assert result.stdout.decode().split() == [sha, 'refs/tags/v0.5.1']

    @pytest.mark.trio
    async def test_the_transport_reads_the_refs(self, root):
        """The transport gets the advertisement of the served repository."""
        repo, sha = self.build(root)
        with git_server.serve_repository(repo.path) as server:
            transport = GitTransport(Arguments(repo_path=root, repo_url=server.url))
            with trio.fail_after(TIMEOUT):
                refs = await transport.fetch_refs()
        assert refs == {sha.encode(): b'refs/tags/v0.5.1'}

    @pytest.mark.trio
    async def test_another_repository_is_not_served(self, root):
        """The daemon does not serve a repository of the folder it was not asked for."""
        repo, _ = self.build(root)
        with git_server.serve_repository(repo.path) as server:
            url = f'git://127.0.0.1:{server.port}/other.git'
            result = subprocess.run(
                ('git', 'ls-remote', url), capture_output=True, timeout=TIMEOUT,
            )
        assert result.returncode != 0

    def test_two_servers_share_no_port(self, root):
        """Every server owns its port, the workers of a parallel run never meet."""
        first, _ = self.build(root, name='first.git')
        second, _ = self.build(root, name='second.git')
        with git_server.serve_repository(first.path) as server_a:
            with git_server.serve_repository(second.path) as server_b:
                assert server_a.port != server_b.port
                assert server_a.url != server_b.url

    def test_the_port_is_released(self, root):
        """The daemon and its children are stopped when the context exits."""
        repo, _ = self.build(root)
        with git_server.serve_repository(repo.path) as server:
            port = server.port
        # A daemon is a tree of processes, the port is released with the last
        # of them, which the operating system does a moment after the kill
        deadline = time.monotonic() + 5
        while True:
            try:
                socket.create_connection(('127.0.0.1', port), timeout=0.5).close()
            except OSError:
                break
            assert time.monotonic() < deadline, f'port {port} is still open'
            time.sleep(0.02)


class TestRemoveFolder:
    """Tests for removing the folder of a repository."""

    def test_a_repository_with_read_only_objects(self, root):
        """The loose objects of git are removed, read-only ones included."""
        repo = git_server.GitRepository.build(root)
        repo.commit({'old.bin': b'old'}, message='first')
        git_server.remove_folder(repo.path)
        assert not os.path.exists(repo.path)
