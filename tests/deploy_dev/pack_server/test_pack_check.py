"""
Tests for PackChecksum: read the identity of a pack file.

The pack server reads the identity of a kept pack file to decide whether it is
the pack of the version pair, see kept_pack: the format version,
the versions and the index pack checksum are read from the index section of
the file, the data section is never read.
"""
import pytest

from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.deploy_dev.pack.pack_full import PackFull
from alasio.deploy_dev.pack.pack_update import PackUpdate
from alasio.deploy_dev.pack_server.pack_check import PackChecksum, kept_pack
from alasio.ext import env
from alasio.ext.algorithm.vint import encode_vint
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_write, file_read_bytes
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.testing.filesystem import fs  # noqa: F401

# the builtin .gitattributes of the library, the pack encoders read it from
# env.ALASIO_ROOT to resolve the eol of the files
BUILTIN_GITATTRIBUTES = env.ALASIO_ROOT.joinpath('.gitattributes').atomic_read_bytes()


@pytest.fixture
def repo(fs, monkeypatch):
    """
    A mock repo of two commits, with the builtin .gitattributes in the fake filesystem.

    The mock repo and the pack encoders read the builtin .gitattributes of the
    library, which the fake filesystem hides, so it is copied into a folder of
    its own and env.ALASIO_ROOT is pointed at it, like the run_dir fixture of
    test_pack_gen.

    Returns:
        MockGitRepo: Repo, the head of the master branch is c2
    """
    root = PathStr.new(fs.root_dir.path)
    library = root.joinpath('alasio')
    fs.create_dir(library)
    fs.create_file(library.joinpath('.gitattributes'), contents=BUILTIN_GITATTRIBUTES)
    monkeypatch.setattr(env, 'ALASIO_ROOT', library)
    monkeypatch.setattr(env, 'PROJECT_ROOT', root)

    repo = MockGitRepo()
    repo.register_file('c1', 'backend/main.py', b'print("hello")\n')
    repo.register_file('c1', 'docs/notes.txt', b'line 1\r\n')
    repo.register_commit('c1', author_name='Author', author_time=0, message='commit c1')
    repo.register_file('c2', 'backend/main.py', b'print("hello")\nprint("world")\n')
    repo.register_file('c2', 'docs/notes.txt', b'line 1\r\n')
    repo.register_file('c2', 'assets/logo.png', bytes(range(256)) * 4)
    repo.register_commit('c2', parents=['c1'], author_name='Author', author_time=1, message='commit c2')
    repo.register_branch('master', 'c2')
    repo.register_head('c2')
    return repo


@pytest.fixture
def full_pack_file(fs, repo):
    """The full pack of the latest version, written to a file."""
    file = PathStr.new(fs.root_dir.path).joinpath('full_c2.pack')
    atomic_write(file, b''.join(PackFull(repo, 'c2').iter_pack_data()))
    return file


@pytest.fixture
def update_pack_file(fs, repo):
    """The update pack from c1 to c2, written to a file."""
    file = PathStr.new(fs.root_dir.path).joinpath('update_c1.pack')
    update = PackUpdate(PackFull(repo, 'c2'), 'c1')
    atomic_write(file, b''.join(update.iter_pack_data()))
    return file


class TestPackChecksum:
    """Read the format version, the versions and the checksum of a pack file."""

    def test_full_pack(self, full_pack_file):
        """A full pack carries the packed version and no old version."""
        checksum = PackChecksum.from_file(full_pack_file)
        assert checksum.pack_version == 0
        assert checksum.current_version == 'c2'
        assert checksum.old_version == ''
        # the checksum is the trailing digest of the index section, the bytes
        # latest.pack appends to the version
        decoder = PackDecodeBase(file_read_bytes(full_pack_file))
        assert checksum.index_checksum == bytes(decoder.extract_index_pack())[-20:]
        assert checksum.index_checksum.hex() == decoder.index_checksum

    def test_update_pack(self, update_pack_file):
        """An update pack carries the version it updates from."""
        checksum = PackChecksum.from_file(update_pack_file)
        assert checksum.pack_version == 0
        assert checksum.current_version == 'c2'
        assert checksum.old_version == 'c1'
        decoder = PackDecodeBase(file_read_bytes(update_pack_file))
        assert checksum.index_checksum == bytes(decoder.extract_index_pack())[-20:]

    def test_not_a_pack(self, fs):
        """A file that is not a pack is refused."""
        file = PathStr.new(fs.root_dir.path).joinpath('nothing.pack')
        atomic_write(file, b'NOPE' + b'\x00' * 60)
        with pytest.raises(PackDecodeError, match='not a pack file'):
            PackChecksum.from_file(file)

    def test_shorter_than_a_header(self, fs):
        """A file shorter than a pack header is refused."""
        file = PathStr.new(fs.root_dir.path).joinpath('nothing.pack')
        atomic_write(file, b'PA')
        with pytest.raises(PackDecodeError, match='not a pack file'):
            PackChecksum.from_file(file)

    def test_short_index_section(self, fs):
        """An index section shorter than the checksum it carries is refused."""
        file = PathStr.new(fs.root_dir.path).joinpath('nothing.pack')
        # the pack header and a 5 bytes index section, the checksum can not fit
        atomic_write(file, b'PACK\x00' + encode_vint(5) + b'\x00' * 5)
        with pytest.raises(PackDecodeError, match='index section of'):
            PackChecksum.from_file(file)

    def test_truncated_pack(self, full_pack_file):
        """A pack that ends inside its index section is refused."""
        data = file_read_bytes(full_pack_file)
        decoder = PackDecodeBase(data)
        # the file ends one byte before the checksum of the index section
        atomic_write(full_pack_file, data[:5 + len(decoder.index_section) - 1])
        with pytest.raises(PackDecodeError, match='ends before its index section'):
            PackChecksum.from_file(full_pack_file)

    def test_version_part_out_of_range(self, fs):
        """An index section that ends inside a version part is refused."""
        file = PathStr.new(fs.root_dir.path).joinpath('nothing.pack')
        # the index section is 26 bytes, its current version claims 50 bytes
        section = encode_vint(26) + encode_vint(50) + b'\x00' * 24
        atomic_write(file, b'PACK\x00' + encode_vint(26) + section)
        with pytest.raises(PackDecodeError, match='version part out of range'):
            PackChecksum.from_file(file)


class TestKeptPack:
    """The keep decision of a pack server flow over the identity of a file."""

    def test_missing_file(self, fs):
        """A pack that does not exist is written by this run."""
        file = PathStr.new(fs.root_dir.path).joinpath('nothing.pack')
        assert kept_pack(file, 0, 'c2', '') is None

    def test_kept(self, full_pack_file):
        """The file of the version pair, encoded with the format, is kept."""
        checksum = kept_pack(full_pack_file, 0, 'c2', '')
        assert checksum.pack_version == 0
        assert checksum.current_version == 'c2'
        assert checksum.old_version == ''
        decoder = PackDecodeBase(file_read_bytes(full_pack_file))
        assert checksum.index_checksum == bytes(decoder.extract_index_pack())[-20:]

    def test_another_format(self, fs, full_pack_file):
        """A pack of another format is written again."""
        assert kept_pack(full_pack_file, 1, 'c2', '') is None

    def test_another_version(self, fs, full_pack_file):
        """A pack of another version pair is written again."""
        assert kept_pack(full_pack_file, 0, 'c2', 'c1') is None
        assert kept_pack(full_pack_file, 0, 'c1', '') is None

    def test_not_a_pack(self, fs):
        """A file that cannot be read as a pack is written again."""
        file = PathStr.new(fs.root_dir.path).joinpath('nothing.pack')
        atomic_write(file, b'NOPE' + b'\x00' * 60)
        assert kept_pack(file, 0, 'c2', '') is None

    def test_not_a_file(self, fs):
        """A folder at the path of a pack is refused, not overwritten."""
        folder = PathStr.new(fs.root_dir.path).joinpath('full.pack')
        fs.create_dir(folder)
        with pytest.raises(ValueError, match='not a file'):
            kept_pack(folder, 0, 'c2', '')
