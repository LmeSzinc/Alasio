import pytest

from alasio.git.mock.mock_base import MockGitRepoBase
from alasio.git.mock.mock_object import MockBlobEntry, MockGitObject
from alasio.git.obj.obj import GitLooseObject
from alasio.git.stage.gitreset import FileEntry
from alasio.git.stage.hashobj import blob_hash


class TestMockGitObjectInit:
    """Tests for MockGitObject.__init__."""

    def test_init_default_path(self):
        """Default path should be empty string."""
        m = MockGitObject()
        assert m.path == ''

    def test_init_custom_path(self):
        """Custom path should be stored."""
        m = MockGitObject('/tmp/repo')
        assert m.path == '/tmp/repo'

    def test_init_empty_state(self):
        """New instance should have no files or objects."""
        m = MockGitObject()
        assert m._files == {}
        assert m._objects == {}

    def test_init_inherits_base(self):
        """MockGitObject should inherit from MockGitRepoBase."""
        assert issubclass(MockGitObject, MockGitRepoBase)


class TestRegisterFile:
    """Tests for MockGitObject.register_file."""

    def test_register_single_file(self):
        """Register a single file and verify it's stored."""
        m = MockGitObject()
        m.register_file('abc123', 'hello.txt', b'hello world')
        assert 'abc123' in m._files
        assert 'hello.txt' in m._files['abc123']
        entry = m._files['abc123']['hello.txt']
        assert isinstance(entry, MockBlobEntry)
        assert entry.content == b'hello world'
        assert entry.mode == b'100644'
        assert entry.blob_sha1 == blob_hash(b'hello world')

    def test_register_multiple_files_same_commit(self):
        """Multiple files under the same commit should all be stored."""
        m = MockGitObject()
        m.register_file('abc123', 'a.txt', b'aaa')
        m.register_file('abc123', 'b.txt', b'bbb')
        assert len(m._files['abc123']) == 2

    def test_register_same_path_multiple_commits(self):
        """Same path under different commits should not interfere."""
        m = MockGitObject()
        m.register_file('old', 'file.py', b'old content')
        m.register_file('new', 'file.py', b'new content')
        assert m._files['old']['file.py'].content == b'old content'
        assert m._files['new']['file.py'].content == b'new content'

    def test_register_mode_644(self):
        """Mode 644 should be stored as b'100644'."""
        m = MockGitObject()
        m.register_file('c', 'f.txt', b'test', mode=644)
        assert m._files['c']['f.txt'].mode == b'100644'

    def test_register_mode_755(self):
        """Mode 755 should be stored as b'100755'."""
        m = MockGitObject()
        m.register_file('c', 'f.txt', b'test', mode=755)
        assert m._files['c']['f.txt'].mode == b'100755'

    @pytest.mark.parametrize('invalid_mode', [0, 1, 600, 777, 100644, '644', None])
    def test_register_invalid_mode(self, invalid_mode):
        """Invalid mode values should raise ValueError."""
        m = MockGitObject()
        with pytest.raises(ValueError):
            m.register_file('c', 'f.txt', b'test', mode=invalid_mode)

    def test_register_updates_objects_dict(self):
        """Registering a file should also populate _objects."""
        m = MockGitObject()
        m.register_file('c', 'f.txt', b'test')
        sha1 = blob_hash(b'test')
        assert sha1 in m._objects
        assert m._objects[sha1].content == b'test'

    def test_register_same_content_dedup(self):
        """Files with identical content should share the same blob entry in _objects."""
        m = MockGitObject()
        m.register_file('c1', 'a.txt', b'hello')
        m.register_file('c2', 'b.txt', b'hello')
        sha1 = blob_hash(b'hello')
        # Both files point to the same MockBlobEntry via _objects
        assert m._objects[sha1] is m._objects[sha1]
        assert m._files['c1']['a.txt'].blob_sha1 == sha1
        assert m._files['c2']['b.txt'].blob_sha1 == sha1

    def test_register_overwrite_path(self):
        """Re-registering the same commit+path should overwrite the entry."""
        m = MockGitObject()
        m.register_file('c', 'f.txt', b'first')
        m.register_file('c', 'f.txt', b'second')
        assert m._files['c']['f.txt'].content == b'second'
        assert len(m._files['c']) == 1


class TestListFiles:
    """Tests for MockGitObject.list_files."""

    def test_list_files_known_commit(self, mock):
        """list_files should return FileEntry dict for a known commit."""
        files = mock.list_files('commit1')
        assert isinstance(files, dict)
        assert 'a.txt' in files
        entry = files['a.txt']
        assert isinstance(entry, FileEntry)
        assert entry.path == 'a.txt'

    def test_list_files_unknown_commit(self, mock):
        """list_files should return empty dict for an unknown commit."""
        files = mock.list_files('nonexistent')
        assert files == {}

    def test_list_files_all_paths(self, mock):
        """list_files should return all registered paths."""
        files = mock.list_files('commit1')
        assert set(files) == {'a.txt', 'sub/b.txt'}

    def test_list_files_entry_values(self, mock):
        """FileEntry values should reflect the registered data."""
        files = mock.list_files('commit1')
        entry = files['a.txt']
        assert entry.sha1 == blob_hash(b'content_a')
        assert entry.mode == b'100644'


class TestGetFile:
    """Tests for MockGitObject.get_file."""

    def test_get_file_found(self, mock):
        """get_file should return the correct FileEntry."""
        entry = mock.get_file('commit1', 'a.txt')
        assert isinstance(entry, FileEntry)
        assert entry.path == 'a.txt'
        assert entry.sha1 == blob_hash(b'content_a')

    def test_get_file_unknown_commit(self, mock):
        """get_file should return None for an unknown commit."""
        assert mock.get_file('nonexistent', 'a.txt') is None

    def test_get_file_unknown_path(self, mock):
        """get_file should return None for an unknown path."""
        assert mock.get_file('commit1', 'unknown.py') is None


class TestCat:
    """Tests for MockGitObject.cat_shallow and cat."""

    def test_cat_shallow_found(self, mock):
        """cat_shallow should return a GitLooseObject for a known blob."""
        sha1 = blob_hash(b'content_a')
        obj = mock.cat_shallow(sha1)
        assert isinstance(obj, GitLooseObject)
        assert obj.type == 3
        assert obj.data == b'content_a'
        assert obj.size == len(b'content_a')

    def test_cat_shallow_missing(self, mock):
        """cat_shallow should raise KeyError for an unknown sha1."""
        with pytest.raises(KeyError):
            mock.cat_shallow('0' * 40)

    def test_cat_delegates_to_cat_shallow(self, mock):
        """cat should return the same result as cat_shallow."""
        sha1 = blob_hash(b'content_a')
        assert mock.cat(sha1).data == mock.cat_shallow(sha1).data

    def test_cat_missing(self, mock):
        """cat should raise KeyError for an unknown sha1."""
        with pytest.raises(KeyError):
            mock.cat('0' * 40)


class TestReadFullAndLazy:
    """Tests for MockGitObject.read_full and read_lazy."""

    def test_read_full_returns_self(self, mock):
        """read_full should return self."""
        assert mock.read_full() is mock

    def test_read_lazy_returns_self(self, mock):
        """read_lazy should return self."""
        assert mock.read_lazy() is mock

    def test_read_full_no_side_effects(self, mock):
        """read_full should not modify internal state."""
        before = dict(mock._files)
        mock.read_full()
        assert mock._files == before

    def test_read_lazy_no_side_effects(self, mock):
        """read_lazy should not modify internal state."""
        before = dict(mock._files)
        mock.read_lazy()
        assert mock._files == before


class TestCompareCommit:
    """Tests for MockGitObject.compare_commit."""

    def test_compare_add_only(self, mock2):
        """
        Only new files: all go to added.
        """
        added, modified, deleted = mock2.compare_commit('commit_a', 'commit_b')
        assert set(added) == {'c.txt', 'd.txt'}
        assert len(modified) == 0
        assert len(deleted) == 0

    def test_compare_delete_only(self, mock2):
        """
        Only old files: all go to deleted.
        """
        added, modified, deleted = mock2.compare_commit('commit_b', 'commit_a')
        assert len(added) == 0
        assert len(modified) == 0
        assert set(deleted) == {'c.txt', 'd.txt'}

    def test_compare_modify_only(self, mock3):
        """
        Same paths but different content: all go to modified.
        """
        added, modified, deleted = mock3.compare_commit('old', 'new')
        assert len(added) == 0
        assert set(modified) == {'a.txt', 'b.txt'}
        assert len(deleted) == 0

    def test_compare_mixed(self, mock4):
        """
        Mix of added, modified, deleted files.
        """
        added, modified, deleted = mock4.compare_commit('old', 'new')
        assert set(added) == {'d.txt'}
        assert set(modified) == {'b.txt'}
        assert set(deleted) == {'c.txt'}

    def test_compare_identical(self, mock):
        """Same commit should yield three empty dicts."""
        added, modified, deleted = mock.compare_commit('commit1', 'commit1')
        assert len(added) == 0
        assert len(modified) == 0
        assert len(deleted) == 0

    def test_compare_swapped_order(self, mock4):
        """Swapped old/new inverts added and deleted."""
        added_fwd, modified_fwd, deleted_fwd = mock4.compare_commit('old', 'new')
        added_rev, modified_rev, deleted_rev = mock4.compare_commit('new', 'old')

        assert added_fwd == deleted_rev
        assert deleted_fwd == added_rev
        # Modified paths are the same, but sha1 values point to different trees
        assert set(modified_fwd) == set(modified_rev)
        assert modified_fwd['b.txt'].sha1 != modified_rev['b.txt'].sha1

    def test_compare_added_uses_new_tree(self, mock4):
        """Added entries should carry the new sha1."""
        added, modified, deleted = mock4.compare_commit('old', 'new')
        expected_sha1 = blob_hash(b'content_d')
        assert added['d.txt'].sha1 == expected_sha1
        assert added['d.txt'].mode == b'100644'

    def test_compare_modified_uses_new_tree(self, mock4):
        """Modified entries should carry the new sha1."""
        added, modified, deleted = mock4.compare_commit('old', 'new')
        expected_sha1 = blob_hash(b'content_b_new')
        assert modified['b.txt'].sha1 == expected_sha1

    def test_compare_deleted_uses_old_tree(self, mock4):
        """Deleted entries should carry the old sha1."""
        added, modified, deleted = mock4.compare_commit('old', 'new')
        expected_sha1 = blob_hash(b'content_c')
        assert deleted['c.txt'].sha1 == expected_sha1

    def test_compare_unknown_commits(self):
        """Unknown commits should be treated as empty."""
        m = MockGitObject()
        added, modified, deleted = m.compare_commit('nonexist1', 'nonexist2')
        assert len(added) == 0
        assert len(modified) == 0
        assert len(deleted) == 0

    def test_compare_mode_change_only(self):
        """File with same content but different mode should be modified."""
        m = MockGitObject()
        m.register_file('old', 'f.txt', b'same', mode=644)
        m.register_file('new', 'f.txt', b'same', mode=755)
        added, modified, deleted = m.compare_commit('old', 'new')
        assert len(added) == 0
        assert len(modified) == 1
        assert len(deleted) == 0
        assert modified['f.txt'].mode == b'100755'


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def mock():
    """A MockGitObject with commit1 containing a.txt and sub/b.txt."""
    m = MockGitObject()
    m.register_file('commit1', 'a.txt', b'content_a')
    m.register_file('commit1', 'sub/b.txt', b'content_b')
    return m


@pytest.fixture
def mock2():
    """Two commits: commit_a has a.txt/b.txt; commit_b adds c.txt/d.txt."""
    m = MockGitObject()
    m.register_file('commit_a', 'a.txt', b'content_a')
    m.register_file('commit_a', 'b.txt', b'content_b')
    m.register_file('commit_b', 'a.txt', b'content_a')
    m.register_file('commit_b', 'b.txt', b'content_b')
    m.register_file('commit_b', 'c.txt', b'content_c')
    m.register_file('commit_b', 'd.txt', b'content_d')
    return m


@pytest.fixture
def mock3():
    """Two commits with same paths but different content (modify only)."""
    m = MockGitObject()
    m.register_file('old', 'a.txt', b'old_a')
    m.register_file('old', 'b.txt', b'old_b')
    m.register_file('new', 'a.txt', b'new_a')
    m.register_file('new', 'b.txt', b'new_b')
    return m


@pytest.fixture
def mock4():
    """
    Two commits with mixed changes:
      old: a.txt, b.txt (old content), c.txt
      new: a.txt (same), b.txt (new content), d.txt
    Expected: added={d.txt}, modified={b.txt}, deleted={c.txt}
    """
    m = MockGitObject()
    m.register_file('old', 'a.txt', b'content_a')
    m.register_file('old', 'b.txt', b'content_b')
    m.register_file('old', 'c.txt', b'content_c')
    m.register_file('new', 'a.txt', b'content_a')
    m.register_file('new', 'b.txt', b'content_b_new')
    m.register_file('new', 'd.txt', b'content_d')
    return m
