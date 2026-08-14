"""
Tests for the command line entry of cli.py.

A real GitRepo is built on the in-memory filesystem with a chain of
3 commits, then the gen command is verified to write the commit
history into .pack/history.pack.
"""
import hashlib
import sys
import zlib

import pytest

from alasio.deploy.history.decode_history import HistoryObj, decode_history
from alasio.deploy_dev.history.cli import main
from alasio.ext.path.atomic import file_read_bytes
from alasio.testing.filesystem import fs  # noqa: F401


def make_loose_object(fs, repo, objtype, content):
    """
    Create a loose object in the in-memory filesystem.

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture
        repo (str): Repository path
        objtype (str): Object type, e.g. "commit", "blob"
        content (bytes): Object content

    Returns:
        str: sha1 of the object
    """
    header = f'{objtype} {len(content)}\x00'.encode()
    data = header + content
    sha1 = hashlib.sha1(data).hexdigest()
    fs.create_file(f'{repo}/.git/objects/{sha1[:2]}/{sha1[2:]}', contents=zlib.compress(data))
    return sha1


def make_commit(fs, repo, parents, author_name, author_time, message):
    """
    Create a loose commit object in the in-memory filesystem.

    The timezone is +0000, so the author time is stored as-is.

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture
        repo (str): Repository path
        parents (list[str]): Parent commit sha1s, empty for the initial commit
        author_name (str): Author name
        author_time (int): Author time
        message (str): Commit message

    Returns:
        str: sha1 of the commit
    """
    content = b'tree ' + b'tree' * 5 + b'\n'
    for parent in parents:
        content += f'parent {parent}\n'.encode()
    content += (
        f'author {author_name} <{author_name}@example.com> {author_time} +0000\n'
        f'committer {author_name} <{author_name}@example.com> {author_time} +0000\n'
        f'\n'
        f'{message}'
    ).encode()
    return make_loose_object(fs, repo, 'commit', content)


@pytest.fixture
def git_repo(fs):
    """
    Create a repository with a chain of 3 commits, head is c3.

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture

    Returns:
        tuple[str, dict[str, str]]: Repository path and commit sha1s
    """
    root = fs.root_dir.path.rstrip('/\\')
    repo = f'{root}/repo'
    fs.create_dir(f'{repo}/.git/objects')
    sha1_1 = make_commit(fs, repo, [], 'Author1', 1000, 'Title1\nBody1')
    sha1_2 = make_commit(fs, repo, [sha1_1], 'Author2', 2000, 'Title2')
    sha1_3 = make_commit(fs, repo, [sha1_2], 'Author3', 3000, 'Title3')
    fs.create_file(f'{repo}/.git/HEAD', contents=sha1_3.encode())
    return repo, {'c1': sha1_1, 'c2': sha1_2, 'c3': sha1_3}


class TestGenCommand:
    """The gen command of the history cli."""

    def test_gen_with_cwd(self, git_repo, monkeypatch):
        """gen writes the commit history into .pack/history.pack."""
        repo, sha1s = git_repo
        monkeypatch.setattr(sys, 'argv', ['cli', 'gen', '-c', repo])
        main()
        data = file_read_bytes(f'{repo}/.pack/history.pack')
        assert decode_history(data) == [
            HistoryObj(
                version=sha1s['c3'], author='Author3', time=3000, title='Title3', detail=''
            ),
            HistoryObj(
                version=sha1s['c2'], author='Author2', time=2000, title='Title2', detail=''
            ),
            HistoryObj(
                version=sha1s['c1'], author='Author1', time=1000, title='Title1', detail='Body1'
            ),
        ]

    def test_gen_default_cwd(self, git_repo, fs, monkeypatch):
        """gen reads the current directory when -c is not given."""
        repo, sha1s = git_repo
        fs.chdir(repo)
        monkeypatch.setattr(sys, 'argv', ['cli', 'gen'])
        main()
        history = decode_history(file_read_bytes(f'{repo}/.pack/history.pack'))
        assert history[0].version == sha1s['c3']
        assert len(history) == 3

    def test_missing_command(self, monkeypatch):
        """A missing command raises SystemExit."""
        monkeypatch.setattr(sys, 'argv', ['cli'])
        with pytest.raises(SystemExit):
            main()

    def test_no_head(self, fs, monkeypatch):
        """A repository without a head raises SystemExit."""
        root = fs.root_dir.path.rstrip('/\\')
        repo = f'{root}/repo'
        fs.create_dir(f'{repo}/.git/objects')
        make_commit(fs, repo, [], 'Author1', 1000, 'Title1')
        monkeypatch.setattr(sys, 'argv', ['cli', 'gen', '-c', repo])
        with pytest.raises(SystemExit):
            main()

    def test_not_a_repo(self, fs, monkeypatch):
        """A directory without .git raises SystemExit."""
        root = fs.root_dir.path.rstrip('/\\')
        repo = f'{root}/repo'
        fs.create_dir(repo)
        monkeypatch.setattr(sys, 'argv', ['cli', 'gen', '-c', repo])
        with pytest.raises(SystemExit):
            main()
