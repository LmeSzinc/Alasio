"""
Tests for PackUpdate: defensive branches of the update pack generator.

The round-trip behavior of PackUpdate is covered by test_unpack_update.py
(the update pack is applied by UpdateJob), these tests cover the
defensive branches of PackUpdate.fileinfo(): a diff record whose source
is missing from refinfo and fileinfo must be rejected.
"""
import pytest

from alasio.deploy_dev.pack.pack_repo import PackFull
from alasio.deploy_dev.pack.pack_update import PackUpdate
from alasio.deploy_dev.pack.repo_diff import UpdateInfo
from tests.deploy_dev.pack.conftest import make_repo

# module level singletons, built before the fake filesystem is active
REPO = make_repo({
    'old': {'old.txt': b'old'},
    'new': {'old.txt': b'old', 'new.txt': b'new'},
})


class _FakeDiff:
    """
    Stub of RepoDiff with fixed diff_info / refinfo.
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
        update = PackUpdate(PackFull(REPO, commit='new'), 'old')
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
