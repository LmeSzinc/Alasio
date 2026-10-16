"""
Tests for path validation in the pack decoder.

The decoder must reject unsafe paths before they touch the filesystem, so
a malicious pack can never write outside env.PROJECT_ROOT or carry files
that cannot be unpacked on some platform, see validate_filepath and the
shared INVALID_PATHS table of conftest. The encoder (PackEncodeBase)
rejects the same paths of the shared table before they are packed, its
side of the tests lives in test_path_validate.py of the deploy_dev pack
tests.
"""
import pytest

from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.deploy_dev.pack.pack_full import PackFull
from alasio.git.mock.mock_repo import MockGitRepo
from tests.deploy_dev.pack.conftest import INVALID_PATHS


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

    def test_traversal_rejected_in_full_pack(self, cache, monkeypatch):
        """A pack containing a traversal path must fail to decode."""
        # the encoder validates paths too, bypass it to build a
        # malicious pack, the decoder must still reject it. The bypass
        # also lets the path into the valid_path set of the cache, the
        # test takes a cache of its own: in the shared process cache the
        # path would stay valid for the encoder tests run after this one
        import alasio.deploy_dev.pack._pack_cache as module
        monkeypatch.setattr(module, 'validate_filepath', lambda path: None)
        repo = MockGitRepo()
        repo.register_file('c1', '../evil.txt', b'x')
        repo.register_commit('c1', author_name='Author', message='')
        data = b''.join(PackFull(repo, commit='c1').iter_pack_data())
        decoder = PackDecodeBase(data)
        with pytest.raises(PackDecodeError, match='Failed to decode paths'):
            _ = decoder.idx_info
