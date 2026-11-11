"""
Fixtures of the git fetch tests.

The repository under test is one of the machine, served by `git daemon` on a
loopback port (alasio.testing.git_server), so no test of this folder leaves the
network and the same tests run on a developer machine and on a runner of the CI.

The content of the repository is chosen by the size assertions of the
negotiation tests: the first commit holds one large file, the second one keeps
it and adds a small file, so a full pack is much larger than an incremental
one, whatever the compression does.
"""
import secrets

import pytest

from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.testing import git_server

# Size of the file of the first commit, the bulk of a full pack
FIRST_FILE_SIZE = 256 * 1024
# Size of the file the second commit adds, the bulk of an incremental pack
SECOND_FILE_SIZE = 8 * 1024


class ServedRepository:
    """
    A repository with the two commits the tests fetch.

    Attributes:
        url (str): Url of the repository, the `repo_url` of the transports
        work (PathStr): Empty folder of the test, for the packs it downloads
        sha_v051 (str): Sha1 of the commit tagged v0.5.1
        sha_v052 (str): Sha1 of the commit tagged v0.5.2
        old_content (bytes): Content of the file the first commit holds
        new_content (bytes): Content of the file the second commit adds
    """

    def __init__(self, url, work, sha_v051, sha_v052, old_content, new_content):
        self.url = url
        self.work = work
        self.sha_v051 = sha_v051
        self.sha_v052 = sha_v052
        self.old_content = old_content
        self.new_content = new_content


@pytest.fixture
def served_repo():
    """
    A git:// server of a repository with two tagged commits.

    Yields:
        ServedRepository: The server, and the commits it serves
    """
    root = PathStr.new(env.ALASIO_TEMP).joinpath(f'git_fetch_{secrets.token_hex(8)}')
    root.makedirs(exist_ok=True)
    try:
        repo = git_server.GitRepository.build(root)
        # Incompressible content: the size of the packs is decided here, not by
        # the compressor, so the assertions on the savings hold on every git
        old_content = secrets.token_bytes(FIRST_FILE_SIZE)
        new_content = secrets.token_bytes(SECOND_FILE_SIZE)
        sha_v051 = repo.commit({'old.bin': old_content}, message='v0.5.1')
        repo.update_ref('refs/tags/v0.5.1', sha_v051)
        sha_v052 = repo.commit({'old.bin': old_content, 'new.bin': new_content}, message='v0.5.2', parent=sha_v051)
        repo.update_ref('refs/tags/v0.5.2', sha_v052)
        # A branch of its own: the transport keys the advertisement by sha1, so
        # two references of the same commit would collapse into one entry
        sha_master = repo.commit({'old.bin': old_content, 'new.bin': new_content}, message='master', parent=sha_v052)
        repo.update_ref('refs/heads/master', sha_master)

        work = root.joinpath('work')
        work.makedirs(exist_ok=True)
        with git_server.serve_repository(repo.path) as server:
            yield ServedRepository(
                url=server.url,
                work=work,
                sha_v051=sha_v051,
                sha_v052=sha_v052,
                old_content=old_content,
                new_content=new_content,
            )
    finally:
        git_server.remove_folder(root)
