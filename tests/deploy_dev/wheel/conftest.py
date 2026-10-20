import pytest

from alasio.git.mock.mock_repo import MockGitRepo
from alasio.logger import logger


@pytest.fixture(autouse=True)
def capture_logs():
    """
    Capture the logs of every test of this folder in memory.

    ``logger.mock_capture_writer()`` swaps the whole writer for a capturing
    one, so no log line reaches the log file (log/{date}__main__.txt) or
    stdout. A test that checks a message takes this fixture and reads the
    capture.

    Returns:
        CaptureWriter: Logs of the test, see logger.mock_capture_writer()
    """
    with logger.mock_capture_writer() as capture:
        yield capture


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
