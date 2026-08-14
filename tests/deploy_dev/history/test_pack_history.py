"""
Tests for PackHistory: pack the release history of a git repository.

A MockGitRepo is built with a chain of 4 commits (including a merge
commit), then iter_commit_history is verified to pack the latest
commits into a msgpack list of HistoryObj.
"""
import pytest

from alasio.deploy.history.decode_history import HistoryObj, decode_history
from alasio.deploy_dev.history.pack_history import PackHistory
from alasio.git.mock.mock_repo import MockGitRepo


def _make_repo():
    """
    Build a mock repo with a chain of 4 commits, the head is a merge.

    c1 -> c2 -> c3 -> merge(c3, c2)

    Returns:
        MockGitRepo:
    """
    repo = MockGitRepo()
    repo.register_commit('c1', author_name='Author1', author_time=1000, message='Title1\nBody1')
    repo.register_commit(
        'c2', parents=['c1'], author_name='Author2', author_time=2000, message='Title2'
    )
    repo.register_commit(
        'c3', parents=['c2'], author_name='Author3', author_time=3000, message='Title3\n\nBody3'
    )
    repo.register_commit(
        'merge', parents=['c3', 'c2'], author_name='Author4', author_time=4000,
        message='Merge\nBody4'
    )
    repo.register_head('merge')
    return repo


class TestPackHistoryInit:
    """PackHistory initialization."""

    def test_init_with_commit(self):
        """The latest commit is the given commit."""
        pack = PackHistory(_make_repo(), commit='c3')
        assert pack.latest_commit == 'c3'

    def test_init_head_get(self):
        """The latest commit is the repo head when commit is not given."""
        pack = PackHistory(_make_repo())
        assert pack.latest_commit == 'merge'

    def test_init_no_head(self):
        """No commit and no repo head must raise ValueError."""
        repo = MockGitRepo()
        repo.register_commit('c1', author_name='Author', message='')
        with pytest.raises(ValueError):
            PackHistory(repo)


class TestIterCommitHistory:
    """Pack the latest commits into a msgpack history."""

    def test_pack_all_commits(self):
        """The head and its parents are packed, merge takes the first parent."""
        pack = PackHistory(_make_repo(), commit='merge')
        data = b''.join(pack.iter_commit_history())
        assert decode_history(data) == [
            HistoryObj(
                version='merge', author='Author4', time=4000, title='Merge', detail='Body4'
            ),
            HistoryObj(
                version='c3', author='Author3', time=3000, title='Title3', detail='Body3'
            ),
            HistoryObj(version='c2', author='Author2', time=2000, title='Title2', detail=''),
            HistoryObj(
                version='c1', author='Author1', time=1000, title='Title1', detail='Body1'
            ),
        ]

    def test_pack_commit_chain(self):
        """A non-merge head packs its own chain."""
        pack = PackHistory(_make_repo(), commit='c3')
        data = b''.join(pack.iter_commit_history())
        assert decode_history(data) == [
            HistoryObj(
                version='c3', author='Author3', time=3000, title='Title3', detail='Body3'
            ),
            HistoryObj(version='c2', author='Author2', time=2000, title='Title2', detail=''),
            HistoryObj(
                version='c1', author='Author1', time=1000, title='Title1', detail='Body1'
            ),
        ]

    def test_lookback_limit(self):
        """lookback limits the number of packed commits, head is included."""
        pack = PackHistory(_make_repo(), commit='merge')
        data = b''.join(pack.iter_commit_history(lookback=2))
        assert decode_history(data) == [
            HistoryObj(
                version='merge', author='Author4', time=4000, title='Merge', detail='Body4'
            ),
            HistoryObj(
                version='c3', author='Author3', time=3000, title='Title3', detail='Body3'
            ),
        ]

    def test_lookback_zero_means_all(self):
        """lookback=0 packs all commits."""
        pack = PackHistory(_make_repo(), commit='merge')
        data = b''.join(pack.iter_commit_history(lookback=0))
        assert len(decode_history(data)) == 4

    def test_default_lookback(self):
        """The default lookback packs up to 20 commits."""
        pack = PackHistory(_make_repo(), commit='c1')
        data = b''.join(pack.iter_commit_history())
        assert len(decode_history(data)) == 1

    def test_returns_generator(self):
        """iter_commit_history returns a generator of bytes."""
        pack = PackHistory(_make_repo(), commit='c1')
        data = pack.iter_commit_history()
        assert iter(data) is data
        assert isinstance(b''.join(data), bytes)
