"""
Tests for path validation in pack encode and decode.

The encode side (PackEncodeBase) must reject unsafe paths before they
are packed, the decode side (PackDecodeBase) must reject them before
they touch the filesystem, so a malicious pack can never write outside
env.PROJECT_ROOT or carry files that cannot be unpacked on some
platform. Both sides use validate_filepath.
"""
import pytest

from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.deploy_dev.pack.pack_full import PackFull
from alasio.git.mock.mock_repo import MockGitRepo

# unsafe paths: traversal, absolute, reserved system names, illegal
# characters, trailing dot / space, too long
INVALID_PATHS = [
    '../evil.txt',
    'a/../../evil.txt',
    '/etc/passwd',
    '\\evil.txt',
    'CON',
    'CON.txt',
    'a/CON',
    'LPT1',
    'a/*.txt',
    'a/b?.txt',
    'a/b<c.txt',
    'a/b.txt.',
    'a/b.txt ',
    'a' * 300,
]


class TestEncodePathValidation:
    """The encoder must reject unsafe paths."""

    @pytest.mark.parametrize('path', INVALID_PATHS)
    def test_invalid_path_rejected(self, path):
        """A pack with an unsafe path must fail to encode."""
        repo = MockGitRepo()
        repo.register_file('c1', path, b'x')
        repo.register_commit('c1', author_name='Author', message='')
        pack = PackFull(repo, commit='c1')
        with pytest.raises(ValueError):
            b''.join(pack.iter_pack_data())

    def test_valid_paths_encoded(self):
        """Normal repo paths must encode without a validation error."""
        repo = MockGitRepo()
        repo.register_file('c1', 'a/b.txt', b'x')
        repo.register_file('c1', '.gitattributes', b'y')
        repo.register_commit('c1', author_name='Author', message='')
        data = b''.join(PackFull(repo, commit='c1').iter_pack_data())
        assert data[:4] == b'PACK'


class TestDecodePathValidation:
    """The decoder must reject unsafe paths."""

    @staticmethod
    def _decode_paths(path):
        """
        Decode a single path through PackDecodeBase._decode_paths.

        Args:
            path (str): Path to decode

        Returns:
            list[str]: Decoded paths
        """
        data = path.encode()
        return PackDecodeBase._decode_paths(data, [0], [len(data)], [0], [0])

    @pytest.mark.parametrize('path', INVALID_PATHS)
    def test_invalid_path_rejected(self, path):
        """An unsafe decoded path must raise PackDecodeError."""
        with pytest.raises(PackDecodeError, match='Failed to decode paths'):
            self._decode_paths(path)

    def test_empty_path_rejected(self):
        """An empty decoded path must raise PackDecodeError."""
        with pytest.raises(PackDecodeError, match='Failed to decode paths'):
            self._decode_paths('')

    def test_valid_paths_accepted(self):
        """Normal paths decode without a validation error."""
        for path in ('a/b.txt', '.gitattributes', '.pack/index.pack',
                     'frontend/src/+page.svelte', '中文/文件.txt'):
            assert self._decode_paths(path) == [path]

    def test_traversal_rejected_in_full_pack(self, monkeypatch):
        """A pack containing a traversal path must fail to decode."""
        # the encoder validates paths too, bypass it to build a
        # malicious pack, the decoder must still reject it
        import alasio.deploy_dev.pack.encode_base as module
        monkeypatch.setattr(module, 'validate_filepath', lambda path: None)
        repo = MockGitRepo()
        repo.register_file('c1', '../evil.txt', b'x')
        repo.register_commit('c1', author_name='Author', message='')
        data = b''.join(PackFull(repo, commit='c1').iter_pack_data())
        decoder = PackDecodeBase(data)
        with pytest.raises(PackDecodeError, match='Failed to decode paths'):
            _ = decoder.idx_info
