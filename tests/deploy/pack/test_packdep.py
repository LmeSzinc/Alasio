"""Tests for the dependency snapshot of a packed tree."""
from types import SimpleNamespace

import pytest

from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.deploy.pack.packdep import PackDep
from alasio.deploy.pack.server_file import LatestInfo
from alasio.deploy_dev.pack.pack_full import PackFull
from alasio.ext.path.atomic import file_write
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401

CHECKSUM = bytes.fromhex('11' * 20)


def make_pack(dep_gen=None):
    """
    Build a tree pack carrying one dependency snapshot.

    Returns:
        bytes: Full pack data
    """
    repo = MockGitRepo()
    repo.register_file('c1', 'module/main.py', b'print("demo")\n')
    repo.register_commit('c1', author_name='Author', message='')
    if dep_gen is None:
        dep_gen = SimpleNamespace(published={'demo': ('1.0', CHECKSUM)})
    return b''.join(PackFull(repo, commit='c1', dep_gen=dep_gen).iter_pack_data())


# the packs are built before the fake filesystem is active: MockGitRepo reads
# the real .gitattributes file of the project
PACK = make_pack()
PLAIN_PACK = make_pack(dep_gen=SimpleNamespace(published={}))


@pytest.mark.trio
async def test_reads_the_snapshot_of_a_valid_tree(fs):
    """The snapshot is returned when its index record matches the file."""
    await UnpackJob(PACK, root='/tree').run()
    assert PackDep.read('/tree') == {
        'demo': LatestInfo(version='1.0', checksum=CHECKSUM.hex()),
    }


@pytest.mark.trio
async def test_a_tampered_file_is_not_used(fs):
    """A snapshot that does not match its index record is refused as a whole."""
    await UnpackJob(PACK, root='/tree').run()
    file_write('/tree/.pack/packdep.pack', b'PDEP\x00tampered')
    with logger.mock_capture_writer() as capture:
        assert PackDep.read('/tree') == {}
    assert capture.fd.any_contains('does not match its index record')


@pytest.mark.trio
async def test_a_missing_file_is_not_used(fs):
    """A tree whose index records the file but whose file is gone has no
    usable snapshot."""
    await UnpackJob(PACK, root='/tree').run()
    fs.remove('/tree/.pack/packdep.pack')
    with logger.mock_capture_writer() as capture:
        assert PackDep.read('/tree') == {}
    assert capture.fd.any_contains('file is missing')


@pytest.mark.trio
async def test_a_tree_without_packdep(fs):
    """A tree of an older pack has no snapshot and no warning."""
    await UnpackJob(PLAIN_PACK, root='/tree').run()
    with logger.mock_capture_writer() as capture:
        assert PackDep.read('/tree') == {}
    assert not capture.fd.any_contains('packdep')


@pytest.mark.trio
async def test_a_missing_index_is_not_used(fs):
    """A tree without an index has no snapshot and no warning."""
    with logger.mock_capture_writer() as capture:
        assert PackDep.read('/tree') == {}
    assert not capture.fd.any_contains('Failed to read the packdep')
