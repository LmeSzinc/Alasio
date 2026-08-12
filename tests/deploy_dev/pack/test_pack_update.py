"""
Tests for PackUpdate: defensive branches of the update pack generator.

The round-trip behavior of PackUpdate is covered by test_unpack_update.py
(the update pack is applied by UpdateJob), these tests cover the
defensive branches of PackUpdate.fileinfo(): a diff record whose source
is missing from refinfo and fileinfo must be rejected.
"""
import pytest

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy_dev.pack.pack_diff import UpdateInfo
from alasio.deploy_dev.pack.pack_repo import PackFull
from alasio.deploy_dev.pack.pack_update import PackUpdate
from alasio.git.mock.mock_repo import MockGitRepo


def _make_pack(files, commit):
    """
    Build a full pack decoder of a version.

    Args:
        files (dict[str, bytes]): {path: content}
        commit (str): Version of the pack

    Returns:
        PackDecodeBase: Decoder of the full pack
    """
    repo = MockGitRepo()
    for path, content in files.items():
        repo.register_file(commit, path, content)
    return PackDecodeBase(b''.join(PackFull(repo, commit=commit).iter_pack_data()))


# module level singletons, built before the fake filesystem is active
OLD = _make_pack({'old.txt': b'old'}, 'old')
NEW = _make_pack({'old.txt': b'old', 'new.txt': b'new'}, 'new')


class _FakeDiff:
    """
    Stub of PackDiff with fixed diff_info / refinfo.
    """

    def __init__(self, diff_info, refinfo):
        """
        Args:
            diff_info (dict[str, UpdateInfo]): Fixed diff records
            refinfo (dict[str, RefInfo]): Fixed ref records
        """
        self.diff_info = diff_info
        self.refinfo = refinfo


class TestPackUpdateFileinfoDefensive:
    """Defensive branches of PackUpdate.fileinfo()."""

    @staticmethod
    def _make_update(diff_info, refinfo):
        """
        A PackUpdate whose diff is replaced by fixed records.

        Args:
            diff_info (dict[str, UpdateInfo]): Diff records
            refinfo (dict[str, RefInfo]): Ref records

        Returns:
            PackUpdate:
        """
        update = PackUpdate(OLD, NEW)
        update._diff = _FakeDiff(diff_info=diff_info, refinfo=refinfo)
        return update

    def test_copied_source_not_found_raises(self):
        """A copied record whose source is missing must be rejected."""
        diff_info = {
            'a.txt': UpdateInfo(path='a.txt', edit=0, source_path='missing.txt', eol=0, mode=0),
        }
        update = self._make_update(diff_info, refinfo={})
        with pytest.raises(ValueError, match='source of a.txt not found'):
            _ = update.fileinfo

    def test_modified_source_not_found_raises(self):
        """An M record whose source is missing must be rejected."""
        diff_info = {
            'a.txt': UpdateInfo(path='a.txt', edit=1, source_path='missing.txt', eol=0, mode=0),
        }
        update = self._make_update(diff_info, refinfo={})
        with pytest.raises(ValueError, match='source of a.txt not found'):
            _ = update.fileinfo
