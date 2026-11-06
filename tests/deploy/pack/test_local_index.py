"""
Tests for the local index pack of a deploy target, see
alasio.deploy.pack.local_index.

The index pack is the record a pack flow commits: it names the version of the
target and the files of it, and one read answers both. The parse of the
records is deferred to fileinfo(), which is what a check of a target does not
pay for.
"""

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job_base import DeployTarget
from alasio.deploy.pack.local_index import LocalIndex
from alasio.deploy_dev.pack.pack_full import PackFull
from alasio.ext.path import PathStr
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack.conftest import app_folder  # noqa: F401


def build_packs(files, commit='c1'):
    """
    The packs of a version.

    Args:
        files (dict[str, bytes]): Files of the version
        commit (str): Version of the pack. Defaults to 'c1'

    Returns:
        tuple[bytes, bytes]: Index pack and full pack data
    """
    repo = MockGitRepo()
    for path, content in files.items():
        repo.register_file(commit, path, content)
    repo.register_commit(commit, author_name='A', message='')
    full = PackFull(repo, commit=commit)
    return b''.join(full.iter_packidx_data()), b''.join(full.iter_pack_data())


# module level, built before the fake filesystem is active: MockGitRepo reads
# the real .gitattributes file, which the fake filesystem does not provide
LEDGER_INDEX, LEDGER_FULL = build_packs({'a.py': b'x = 1\n'})


class TestLocalIndex:
    """The records of the index pack of a target."""

    @staticmethod
    def target(name='demo'):
        return DeployTarget(root=PathStr.new('/app'), name=name)

    @staticmethod
    def decoder():
        return PackDecodeBase(LEDGER_FULL)

    def test_version_from_the_header(self):
        """The version of the pack is read without parsing its records."""
        index = LocalIndex(self.target(), decoder=self.decoder())
        assert index.version == 'c1'
        assert index.exists is True
        # the records are not parsed yet: a check of a target pays nothing
        assert index._fileinfo is None

    def test_fileinfo_maps_the_pack_area(self):
        """The records of the pack area are mapped into the ledger folder."""
        fileinfo = LocalIndex(self.target(), decoder=self.decoder()).fileinfo()
        assert 'a.py' in fileinfo
        assert any(path.startswith('.pack/demo/') for path in fileinfo)
        assert not any(path == '.pack/history.pack' for path in fileinfo)

    def test_fileinfo_is_built_once(self):
        """The records are validated, mapped and parsed once per flow."""
        index = LocalIndex(self.target(), decoder=self.decoder())
        first = index.fileinfo()
        assert index.fileinfo() is first

    def test_deleted_markers_are_excluded(self):
        """A record that says a file must not exist is not a file of the
        old version, so it is not a deletion base."""
        index = LocalIndex(self.target(), decoder=self.decoder())
        assert all(info.edit != 2 for info in index.fileinfo().values())

    def test_identity_target_keeps_the_canonical_paths(self):
        """A target without a ledger key maps nothing."""
        index = LocalIndex(self.target(name=''), decoder=self.decoder())
        assert '.pack/history.pack' in index.fileinfo()

    def test_a_corrupt_pack_is_no_deletion_base(self):
        """A pack that fails its checksum describes no file: nothing is
        deleted on the strength of it."""
        # the last byte of the index pack is the last byte of its checksum:
        # the structure still decodes, the checksum does not match
        data = LEDGER_INDEX[:-1] + bytes([LEDGER_INDEX[-1] ^ 0xFF])
        index = LocalIndex(self.target(), decoder=PackDecodeBase(data))
        with logger.mock_capture_writer() as capture:
            assert index.fileinfo() == {}
        assert capture.fd.any_contains('Failed to read the old index pack')

    def test_a_missing_pack_is_not_an_error(self, fs, app_folder):
        """A target a pack flow never wrote has no index pack, which is the
        state of every fresh target and of every pip installation."""
        index = LocalIndex.read(self.target())
        assert index.exists is False
        assert index.missing is True
        assert index.version == ''
        assert 'No such file' in index.reason

    def test_a_corrupt_pack_is_not_missing(self, fs, app_folder):
        """A ledger that can not be decoded is a target pack flows wrote: it
        is not the same state as a target they never touched."""
        target = self.target()
        fs.create_file(target.index_file, contents=b'not a pack')
        index = LocalIndex.read(target)
        assert index.exists is False
        assert index.missing is False
        assert index.reason

    def test_read_once_per_flow(self, fs, app_folder):
        """The version and the deletion base of a flow come from one read:
        the second member of the same object reads nothing."""
        target = self.target()
        fs.create_file(target.index_file, contents=LEDGER_INDEX)
        index = LocalIndex.read(target)
        assert index.version == 'c1'
        # the file is gone, the records still answer: they were read already
        fs.remove(target.index_file)
        assert 'a.py' in index.fileinfo()
