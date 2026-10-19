import pytest

from alasio.git.mock.mock_repo import MockGitRepo


@pytest.fixture
def make_repo():
    """
    Return a factory of MockGitRepo from {path: content}.

    Returns:
        Callable: factory(files, commit='c1', time=0) -> MockGitRepo
    """
    def factory(files, commit='c1', time=0):
        repo = MockGitRepo()
        for path, content in files.items():
            repo.register_file(commit, path, content)
        # the author time is also the default committer time
        repo.register_commit(commit, author_name='Author', author_time=time, message='commit')
        repo.register_head(commit)
        return repo
    return factory
