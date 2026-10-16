"""
Tests for path validation in the pack encoder.

The encoder must reject unsafe paths before they are packed, so a pack
never carries a path the decoder at the other end refuses, see
validate_filepath and the shared INVALID_PATHS table of conftest. The
decoder (PackDecodeBase) rejects the same paths of the shared table
before they touch the filesystem, its side of the tests lives in
test_path_validate.py of the deploy pack tests.

The encoder additionally rejects pack area paths nested deeper than one
level (validate_pack_area, see TestEncodePackAreaValidation): the pack
area .pack of a tree keeps its pack files directly under it, a nested one
would be ambiguous with the ledger folder of a named deploy target.
"""
import pytest

from alasio.deploy_dev.pack.pack_full import PackFull
from alasio.deploy_dev.pack.pack_update import PackUpdate
from alasio.git.mock.mock_repo import MockGitRepo
from tests.deploy_dev.pack.conftest import INVALID_PATHS


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


class TestEncodePackAreaValidation:
    """The encoder must reject pack area paths nested deeper than one level.

    The pack area .pack of a tree holds the pack files of the version
    directly under it (.pack/index.pack, .pack/history.pack, ...); a
    nested path like .pack/httpx/index.pack would be ambiguous with the
    ledger folder of a named deploy target, the client maps every .pack
    path into that folder.
    """

    @pytest.mark.parametrize('path', [
        '.pack/index.pack/x',
        '.pack/httpx/index.pack',
        '.pack/a/b.pack',
        '.pack/workspace/job.pack',
    ])
    def test_nested_pack_area_rejected(self, path):
        """A pack with an ambiguous pack area path must fail to encode."""
        repo = MockGitRepo()
        repo.register_file('c1', path, b'x')
        repo.register_commit('c1', author_name='Author', message='')
        pack = PackFull(repo, commit='c1')
        with pytest.raises(ValueError, match='pack area'):
            b''.join(pack.iter_pack_data())

    def test_pack_area_itself_rejected(self):
        """A record path equal to .pack is rejected: the pack area must be
        a folder, never a file."""
        repo = MockGitRepo()
        repo.register_file('c1', '.pack', b'x')
        repo.register_commit('c1', author_name='Author', message='')
        pack = PackFull(repo, commit='c1')
        with pytest.raises(ValueError, match='pack area itself'):
            b''.join(pack.iter_pack_data())

    def test_direct_pack_area_files_encoded(self):
        """Files directly under .pack must encode without a validation error."""
        repo = MockGitRepo()
        repo.register_file('c1', '.pack/extra.pack', b'x')
        repo.register_file('c1', '.pack/notes.txt', b'y')
        repo.register_commit('c1', author_name='Author', message='')
        data = b''.join(PackFull(repo, commit='c1').iter_pack_data())
        assert data[:4] == b'PACK'

    def test_nested_pack_area_rejected_in_update_pack(self):
        """An update pack must reject an ambiguous pack area record too."""
        repo = MockGitRepo()
        repo.register_commit('old', author_name='Author', message='')
        repo.register_file('old', 'a.py', b'x')
        repo.register_commit('new', author_name='Author', message='')
        repo.register_file('new', 'a.py', b'x')
        repo.register_file('new', '.pack/nested/x.pack', b'y')
        update = PackUpdate(PackFull(repo, commit='new'), 'old')
        with pytest.raises(ValueError, match='pack area'):
            b''.join(update.iter_pack_data())
