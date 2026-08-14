from typing import Union

from alasio.deploy_dev.history.encode_history import encode_commit_history
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.git.repo import GitRepo


class PackHistory:
    """
    Pack the release history of a git repository.

    The history is a msgpack encoded list of HistoryObj, packed from
    the latest commits of the repository.
    """

    def __init__(self, repo: Union[GitRepo, MockGitRepo], commit=''):
        """
        Args:
            repo (GitRepo): GitRepo object
            commit (str): commit sha1 in str, default to repo head
        """
        self.repo = repo
        self.latest_commit = commit
        if not self.latest_commit:
            self.latest_commit = repo.head_get()
        if not self.latest_commit:
            raise ValueError(f'Empty latest commit at repo {repo}')

    def iter_commit_history(self, lookback=20):
        """
        Pack the history of the latest commits.

        Args:
            lookback (int): Maximum number of commits to pack,
                0 for all commits. Defaults to 20.

        Returns:
            Iterator[bytes]: msgpack encoded history data
        """
        commits = self.repo.list_commit_have(self.latest_commit, have_lookback=lookback)
        yield encode_commit_history(commits)
