import pytest

from alasio.ext import env
from alasio.git.repo import GitRepo
from alasio.git.stage.gitreset import FileEntry

# ── Test data: commit sha1s from the alasio repository itself ────────────

HEAD_SHA = 'a9bdf4080475ac7e9f314df1fb2ba1262216c512'

# Case 1: add + modify + delete
# Commit c7ba305 "Add: FutureMixin"
#   M	alasio/config/_index/config_generated.py
#   M	alasio/config/_index/nav.order.yaml
#   D	alasio/config/alasio/alasio.tasks.yaml
#   M	alasio/config/alasio/alasio_model.py
#   M	alasio/config/alasio/group_export.py
#   A	alasio/config/alasio/mixin.args.yaml
#   A	alasio/config/alasio/mixin_model.py
#   M	alasio/config_dev/gen/gen_config.py
#   M	alasio/config_dev/gen/gen_config_generated.py
C1_OLD = '092962003d5817e45c33356d7cb4266587af331a'
C1_NEW = 'c7ba305125bd9f80c2d8b9aab29afa0ed12c33df'

# Case 2: file move (rename) only
# Commit b500bbf "Fix: Typo suppress"
#   M	alasio/assets_dev/extract_alas.py
#   M	alasio/assets_dev/extract_src.py
#   R100	alasio/ext/backport/subpress.py	alasio/ext/backport/suppress.py
#   R099	tests/ext/backport/test_subpress.py	tests/ext/backport/test_suppress.py
# Both renames are detected as delete + add in tree comparison.
C2_OLD = '696bb16258b73a48e6362e9d9933cefeff522673'
C2_NEW = 'b500bbfd28ab9a9ed4550f9e85efe60c43986b0d'

# Case 3: file move + content modification
# Commit e3b28b2 "Chore: Move inflect to alasio.ext.inflect"
#   M	alasio/base/scheduler/scheduler.py
#   R098	alasio/base/scheduler/inflect.py	alasio/ext/inflect.py
#   R084	tests/base/scheduler/test_inflect.py	tests/ext/test_inflect.py
C3_OLD = '41d3be6676e7c0b5d89b211704008676a611be01'
C3_NEW = 'e3b28b24f413d3f0afc773fe3fe4f56458286f8d'

# Case 4: delete-heavy
# Commit 7d9f32a "Dep: use abstracted msgspecerror"
#   D	alasio/ext/msgspec_error/__init__.py
#   D	alasio/ext/msgspec_error/const.py
#   D	alasio/ext/msgspec_error/parse_anno.py
#   D	alasio/ext/msgspec_error/parse_ctx.py
#   D	alasio/ext/msgspec_error/parse_error.py
#   D	alasio/ext/msgspec_error/parse_path.py
#   D	alasio/ext/msgspec_error/parse_struct.py
#   D	alasio/ext/msgspec_error/parse_type.py
#   D	alasio/ext/msgspec_error/repair.py
#   M	pyproject.toml
#   D	tests/ext/msgspec_error/test_anno.py
#   D	tests/ext/msgspec_error/test_error.py
#   D	tests/ext/msgspec_error/test_parse_ctx.py
#   D	tests/ext/msgspec_error/test_parse_error.py
#   D	tests/ext/msgspec_error/test_parse_path.py
#   D	tests/ext/msgspec_error/test_repair_json.py
#   D	tests/ext/msgspec_error/test_repair_json_unicode.py
#   D	tests/ext/msgspec_error/test_struct.py
#   D	tests/ext/msgspec_error/test_type.py
C4_OLD = '5d0be4835887af152b3efa748a719569e1c84426'
C4_NEW = '7d9f32ae5c577101e1ae4607cc78264a4838cce0'


@pytest.fixture(scope='module')
def repo():
    """Provide a lazily-loaded GitRepo for the alasio repository."""
    r = GitRepo(str(env.ALASIO_ROOT))
    r.read_lazy()
    return r


# ── Tests for list_files ─────────────────────────────────────────────────


class TestListFiles:
    """Tests for GitReset.list_files using the alasio repository."""

    def test_list_files_count(self, repo):
        """list_files should return many files for HEAD."""
        files = repo.list_files(HEAD_SHA)
        assert len(files) > 200

    def test_list_files_known_paths(self, repo):
        """list_files should include well-known files."""
        files = repo.list_files(HEAD_SHA)
        assert 'alasio/git/stage/gitreset.py' in files
        assert 'README.md' in files
        assert 'pyproject.toml' in files

    def test_list_files_entry_structure(self, repo):
        """Each entry should be a FileEntry with valid fields."""
        files = repo.list_files(HEAD_SHA)
        entry = files['alasio/git/stage/gitreset.py']
        assert isinstance(entry, FileEntry)
        assert entry.path == 'alasio/git/stage/gitreset.py'
        assert len(entry.sha1) == 40
        assert entry.mode in (b'100644', b'100755')

    def test_list_files_subdirectory(self, repo):
        """Files in nested directories should have correct paths."""
        files = repo.list_files(HEAD_SHA)
        assert 'alasio/ext/env.py' in files
        assert 'alasio/git/stage/gitref.py' in files
        entry = files['alasio/ext/env.py']
        assert entry.path == 'alasio/ext/env.py'

    def test_list_files_non_existent_commit(self, repo):
        """list_files should raise KeyError for an unknown sha1."""
        with pytest.raises(KeyError):
            repo.list_files('0' * 40)


# ── Tests for compare_commit ─────────────────────────────────────────────


class TestCompareCommit:
    """Tests for GitReset.compare_commit using real alasio commit pairs."""

    def test_compare_add_modify_delete(self, repo):
        """
        detect added, modified and deleted files simultaneously.

        c7ba305: 2 added, 6 modified, 1 deleted.
        """
        added, modified, deleted = repo.compare_commit(C1_OLD, C1_NEW)
        assert len(added) == 2
        assert len(modified) == 6
        assert len(deleted) == 1

        # Known added files
        assert 'alasio/config/alasio/mixin.args.yaml' in added
        assert 'alasio/config/alasio/mixin_model.py' in added

        # Known modified files
        assert 'alasio/config/_index/config_generated.py' in modified
        assert 'alasio/config/alasio/alasio_model.py' in modified

        # Known deleted file
        assert 'alasio/config/alasio/alasio.tasks.yaml' in deleted

    def test_compare_rename_only(self, repo):
        """
        detect renames (pure rename and rename+modify).

        b500bbf: R100 + R099 + 2 M.
        Both renames appear as delete + add in tree comparison.
        """
        added, modified, deleted = repo.compare_commit(C2_OLD, C2_NEW)
        # R100: subpress.py -> suppress.py (1 delete + 1 add)
        # R099: test_subpress -> test_suppress (1 delete + 1 add)
        # M:    2 files
        assert len(added) == 2
        assert len(modified) == 2
        assert len(deleted) == 2

        # Rename: old path deleted, new path added
        assert 'alasio/ext/backport/subpress.py' in deleted
        assert 'alasio/ext/backport/suppress.py' in added
        assert 'tests/ext/backport/test_subpress.py' in deleted
        assert 'tests/ext/backport/test_suppress.py' in added

    def test_compare_rename_with_modify(self, repo):
        """
        detect renames that also modify content.

        e3b28b2: R098 + R084 + 1 M.
        """
        added, modified, deleted = repo.compare_commit(C3_OLD, C3_NEW)
        # R098: inflect.py -> ext/inflect.py (1 delete + 1 add)
        # R084: test_inflect -> test_inflect (1 delete + 1 add)
        # M:    scheduler.py
        assert len(added) == 2
        assert len(modified) == 1
        assert len(deleted) == 2

        # Rename with modification: old path deleted, new path added
        assert 'alasio/base/scheduler/inflect.py' in deleted
        assert 'alasio/ext/inflect.py' in added
        assert 'tests/base/scheduler/test_inflect.py' in deleted
        assert 'tests/ext/test_inflect.py' in added

        assert 'alasio/base/scheduler/scheduler.py' in modified

    def test_compare_delete_heavy(self, repo):
        """
        detect bulk deletions.

        7d9f32a: 18 deleted, 1 modified, 0 added.
        """
        added, modified, deleted = repo.compare_commit(C4_OLD, C4_NEW)
        assert len(added) == 0
        assert len(modified) == 1
        assert len(deleted) == 18

        # All deleted files are under alasio/ext/msgspec_error/ or tests/
        for path in deleted:
            assert path.startswith('alasio/ext/msgspec_error/') or \
                   path.startswith('tests/ext/msgspec_error/')

        assert 'pyproject.toml' in modified

    def test_compare_identical_commits(self, repo):
        """comparing a commit to itself should yield three empty dicts."""
        added, modified, deleted = repo.compare_commit(HEAD_SHA, HEAD_SHA)
        assert len(added) == 0
        assert len(modified) == 0
        assert len(deleted) == 0

    def test_compare_swapped_order(self, repo):
        """
        swapping old and new inverts the result:
        additions become deletions and vice versa.
        """
        added_fwd, modified_fwd, deleted_fwd = repo.compare_commit(C1_OLD, C1_NEW)
        added_rev, modified_rev, deleted_rev = repo.compare_commit(C1_NEW, C1_OLD)

        assert len(added_fwd) == len(deleted_rev)
        assert len(deleted_fwd) == len(added_rev)
        assert len(modified_fwd) == len(modified_rev)

        # Specific path swaps
        assert 'alasio/config/alasio/mixin.args.yaml' in added_fwd
        assert 'alasio/config/alasio/mixin.args.yaml' not in deleted_fwd
        assert 'alasio/config/alasio/mixin.args.yaml' in deleted_rev
        assert 'alasio/config/alasio/mixin.args.yaml' not in added_rev

        assert 'alasio/config/alasio/alasio.tasks.yaml' in deleted_fwd
        assert 'alasio/config/alasio/alasio.tasks.yaml' not in added_fwd
        assert 'alasio/config/alasio/alasio.tasks.yaml' in added_rev
        assert 'alasio/config/alasio/alasio.tasks.yaml' not in deleted_rev

    def test_compare_file_entry_uses_new_tree(self, repo):
        """
        added and modified entries carry the sha1 from the new tree,
        while deleted entries carry the sha1 from the old tree.
        """
        added, modified, deleted = repo.compare_commit(C1_OLD, C1_NEW)

        # Added file
        entry = added['alasio/config/alasio/mixin.args.yaml']
        assert entry.path == 'alasio/config/alasio/mixin.args.yaml'
        assert len(entry.sha1) == 40
        assert entry.mode == b'100644'

        # Modified file — new sha1 differs from old
        mod_entry = modified['alasio/config/alasio/alasio_model.py']
        old_files = repo.list_files(C1_OLD)
        old_sha1 = old_files['alasio/config/alasio/alasio_model.py'].sha1
        assert mod_entry.sha1 != old_sha1

        # Deleted file — old sha1 is valid
        del_entry = deleted['alasio/config/alasio/alasio.tasks.yaml']
        assert len(del_entry.sha1) == 40
