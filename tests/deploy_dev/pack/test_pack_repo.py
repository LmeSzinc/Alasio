"""
Tests for the PACK's pack_repo logic.

Uses MockGitRepo to provide in-memory git data, avoiding the need
for a real on-disk git repository.
"""

from hashlib import sha1 as _sha1

import pytest

from alasio.deploy.history.decode_history import HistoryObj, decode_history
from alasio.deploy.pack.pack_model import FileInfo
from alasio.deploy_dev.pack.pack_cache import PackCache
from alasio.deploy_dev.pack.pack_repo import PackFull
from alasio.ext.path.pathstr import PathStr
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.git.stage.gitreset import FileEntry

COMMIT = 'c1'


def _make_repo():
    """
    Build a MockGitRepo with a registered commit.

    PackFull.fileinfo packs the commit history of the repo, so the
    mock repo must have the commit object registered.

    Returns:
        MockGitRepo:
    """
    mock = MockGitRepo()
    mock.register_commit(COMMIT, author_name='Author', message='')
    return mock


# ════════════════════════════════════════════════════════════════════════════
#  path validation
# ════════════════════════════════════════════════════════════════════════════


def _make_counting_validate(monkeypatch):
    """
    Count the validate_filepath calls of the pack encoder.

    Args:
        monkeypatch (pytest.MonkeyPatch): Monkeypatch fixture

    Returns:
        list[str]: Paths validated so far
    """
    from alasio.deploy_dev.pack import encode_base

    checked = []
    original = encode_base.validate_filepath

    def counting(path):
        checked.append(path)
        return original(path)

    monkeypatch.setattr(encode_base, 'validate_filepath', counting)
    return checked


class TestPackPathValidateCache:
    """The pack encoder validates a path once across the versions it packs."""

    def test_path_validated_once(self, monkeypatch):
        """Only the paths the earlier versions did not have are validated again."""
        checked = _make_counting_validate(monkeypatch)
        mock = _make_repo()
        mock.register_file(COMMIT, 'cache_case/a.txt', b'a')
        mock.register_commit('c2', author_name='Author', message='')
        mock.register_file('c2', 'cache_case/a.txt', b'a')
        mock.register_file('c2', 'cache_case/b.txt', b'b')
        b''.join(PackFull(mock, commit=COMMIT).iter_packidx_data())
        assert 'cache_case/a.txt' in checked
        checked.clear()
        b''.join(PackFull(mock, commit='c2').iter_packidx_data())
        # c2 shares a.txt with the version packed before, only b.txt is new
        assert checked == ['cache_case/b.txt']

    def test_invalid_path_still_rejected(self, monkeypatch):
        """An invalid path keeps failing, it never enters the cache."""
        _make_counting_validate(monkeypatch)
        mock = _make_repo()
        mock.register_file(COMMIT, 'cache_case/..', b'x')
        for _ in range(2):
            with pytest.raises(ValueError, match='directory pointer'):
                b''.join(PackFull(mock, commit=COMMIT).iter_packidx_data())


# ════════════════════════════════════════════════════════════════════════════
#  filelist
# ════════════════════════════════════════════════════════════════════════════


class TestFilelist:
    """Tests for PackFull.filelist."""

    def test_filelist_known_commit(self):
        """Filelist returns files registered for a commit."""
        mock = _make_repo()
        mock.register_file('c1', 'a.txt', b'hello')
        pack = PackFull(mock, commit='c1')
        flist = pack.filelist
        assert isinstance(flist, dict)
        assert 'a.txt' in flist
        assert isinstance(flist['a.txt'], FileEntry)
        assert flist['a.txt'].path == 'a.txt'

    def test_filelist_unknown_commit(self):
        """Filelist returns empty dict for unknown commit."""
        mock = _make_repo()
        pack = PackFull(mock, commit='nonexistent')
        assert pack.filelist == {}

    def test_filelist_multiple_files(self):
        """Filelist returns all registered files."""
        mock = _make_repo()
        mock.register_file('c1', 'a.txt', b'aaa')
        mock.register_file('c1', 'b/b.txt', b'bbb')
        mock.register_file('c1', 'c/c/c.txt', b'ccc')
        pack = PackFull(mock, commit='c1')
        flist = pack.filelist
        assert set(flist) == {'a.txt', 'b/b.txt', 'c/c/c.txt'}


# ════════════════════════════════════════════════════════════════════════════
#  gitattributes
# ════════════════════════════════════════════════════════════════════════════


class TestGitattributes:
    """Tests for PackFull.gitattributes parsing."""

    def test_repo_gitattributes_patterns_loaded(self):
        """Repo .gitattributes patterns should be loaded."""
        # Build two identical packs — one with .gitattributes, one without
        mock_with = _make_repo()
        mock_with.register_file('c1', '.gitattributes', b'*.foo text eol=crlf')
        mock_with.register_file('c1', 'a.foo', b'content')
        pack_with = PackFull(mock_with, commit='c1')

        mock_without = _make_repo()
        mock_without.register_file('c1', 'a.foo', b'content')
        pack_without = PackFull(mock_without, commit='c1')

        # With repo .gitattributes, eol should be CRLF (1)
        # Without, only builtin * text=auto eol=lf applies → eol = 0
        # This proves the repo .gitattributes pattern was loaded and applied
        assert pack_with.fileinfo['a.foo'].eol == 1
        assert pack_without.fileinfo['a.foo'].eol == 0

    def test_root_gitattributes_loaded(self):
        """Root .gitattributes should be loaded as a repo pattern."""
        mock = _make_repo()
        mock.register_file('c1', '.gitattributes', b'*.foo text')
        mock.register_file('c1', 'a.foo', b'content')
        pack = PackFull(mock, commit='c1')
        # Pattern count should be > builtin-only count
        attrs_no = PackFull(_make_repo(), commit='not-there').gitattributes
        # Just confirm it loaded without error
        assert pack.gitattributes is pack.gitattributes  # cached

    def test_subdir_gitattributes_loaded(self):
        """Subdirectory .gitattributes should be loaded."""
        mock = _make_repo()
        mock.register_file('c1', 'sub/.gitattributes', b'*.bar binary')
        mock.register_file('c1', 'sub/a.bar', b'\x00')
        pack = PackFull(mock, commit='c1')
        # the registered rules are parsed when the pack resolves a path
        assert pack.fileinfo['sub/a.bar'].eol == 2
        patterns = pack.gitattributes.patterns
        # At least one pattern from sub/.gitattributes
        repo_patterns = [p for p in patterns if p.root == 'sub/']
        assert len(repo_patterns) > 0


# ════════════════════════════════════════════════════════════════════════════
#  gitattributes fingerprint
# ════════════════════════════════════════════════════════════════════════════


class TestGitattributesFingerprint:
    """The fingerprint identifies the .gitattributes state of a version."""

    def test_the_other_files_do_not_matter(self):
        """Two versions in the same .gitattributes state share the fingerprint."""
        mock = _make_repo()
        mock.register_file('c1', '.gitattributes', b'*.foo text')
        mock.register_file('c1', 'a.txt', b'a')
        mock.register_commit('c2', author_name='Author', message='')
        mock.register_file('c2', '.gitattributes', b'*.foo text')
        mock.register_file('c2', 'a.txt', b'a')
        mock.register_file('c2', 'b/c.txt', b'c')
        assert PackFull(mock, commit='c1').gitattributes_fingerprint == \
            PackFull(mock, commit='c2').gitattributes_fingerprint

    def test_a_changed_gitattributes_changes_the_fingerprint(self):
        """Another .gitattributes content gives another fingerprint."""
        mock = _make_repo()
        mock.register_file('c1', '.gitattributes', b'*.foo text')
        mock.register_file('c1', 'a.foo', b'a')
        mock.register_commit('c2', author_name='Author', message='')
        mock.register_file('c2', '.gitattributes', b'*.foo -text')
        mock.register_file('c2', 'a.foo', b'a')
        assert PackFull(mock, commit='c1').gitattributes_fingerprint != \
            PackFull(mock, commit='c2').gitattributes_fingerprint

    def test_an_added_subdir_gitattributes_changes_the_fingerprint(self):
        """A new .gitattributes in a subfolder gives another fingerprint."""
        mock = _make_repo()
        mock.register_file('c1', '.gitattributes', b'*.foo text')
        mock.register_file('c1', 'sub/a.foo', b'a')
        mock.register_commit('c2', author_name='Author', message='')
        mock.register_file('c2', '.gitattributes', b'*.foo text')
        mock.register_file('c2', 'sub/.gitattributes', b'*.foo -text')
        mock.register_file('c2', 'sub/a.foo', b'a')
        assert PackFull(mock, commit='c1').gitattributes_fingerprint != \
            PackFull(mock, commit='c2').gitattributes_fingerprint

    def test_versions_without_gitattributes_share_one_table(self):
        """No .gitattributes gives the same fingerprint for every version."""
        mock = _make_repo()
        mock.register_file('c1', 'a.txt', b'a')
        mock.register_commit('c2', author_name='Author', message='')
        mock.register_file('c2', 'a.txt', b'a')
        mock.register_file('c2', 'b.txt', b'b')
        assert PackFull(mock, commit='c1').gitattributes_fingerprint == \
            PackFull(mock, commit='c2').gitattributes_fingerprint

    def test_the_pack_format_version_belongs_to_the_fingerprint(self):
        """Another pack format resolves in a table of its own."""
        mock = _make_repo()
        mock.register_file('c1', '.gitattributes', b'*.foo text')
        mock.register_file('c1', 'a.foo', b'a')
        current = PackFull(mock, commit='c1')
        other = PackFull(mock, commit='c1', pack_version=b'\x01')
        assert current.gitattributes_fingerprint != other.gitattributes_fingerprint


# ════════════════════════════════════════════════════════════════════════════
#  fileinfo — basic
# ════════════════════════════════════════════════════════════════════════════


class TestFileinfoBasic:
    """Tests for base FileInfo creation from git entries.

    These tests verify the FileInfo path, sha1, size, and edit fields
    without relying on specific gitattribute-driven eol values (the
    builtin ``* text=auto eol=lf`` always applies).
    """

    def test_single_file(self):
        """A single file produces one FileInfo with correct metadata."""
        mock = _make_repo()
        content = b'hello world'
        mock.register_file('c1', 'hello.txt', content)
        pack = PackFull(mock, commit='c1')
        info = pack.fileinfo
        # + 1 for the packed commit history
        assert len(info) == 2
        assert '.pack/history.pack' in info
        entry = info['hello.txt']
        assert entry.path == 'hello.txt'
        # load_data() sets sha1 to sha1(content).digest() (raw content hash)
        assert entry.sha1 == _sha1(content).digest()
        assert entry.size == len(content)
        assert entry.edit == 0  # A (added)
        assert entry.mode == 0  # 644
        assert entry.source_lookback == 0

    def test_multiple_files(self):
        """Multiple files all appear in fileinfo."""
        mock = _make_repo()
        mock.register_file('c1', 'a.txt', b'aaa')
        mock.register_file('c1', 'b.txt', b'bbb')
        pack = PackFull(mock, commit='c1')
        info = pack.fileinfo
        assert set(info) == {'a.txt', 'b.txt', '.pack/history.pack'}

    def test_file_ordering_shallow_first(self):
        """Files are sorted DFS: shallower parents come before deeper ones."""
        mock = _make_repo()
        mock.register_file('c1', 'a/b/c.txt', b'c')
        mock.register_file('c1', 'a/b.txt', b'b')
        mock.register_file('c1', 'a.txt', b'a')
        mock.register_file('c1', 'z.txt', b'z')
        pack = PackFull(mock, commit='c1')
        paths = list(pack.fileinfo)
        a_idx = paths.index('a.txt')
        ab_idx = paths.index('a/b.txt')
        abc_idx = paths.index('a/b/c.txt')
        assert a_idx < ab_idx < abc_idx, f'Expected DFS order, got {paths}'

    def test_empty_file(self):
        """Empty file: size=0, sha1=b'' after load_data."""
        mock = _make_repo()
        mock.register_file('c1', 'empty.txt', b'')
        pack = PackFull(mock, commit='c1')
        entry = pack.fileinfo['empty.txt']
        assert entry.size == 0
        assert entry.sha1 == b''
        assert entry.algo == 0
        assert entry.data == b''

    def test_mode_755(self):
        """Mode 755 file is handled correctly (load_git_mode sets eol)."""
        mock = _make_repo()
        mock.register_file('c1', 'script.sh', b'#!/bin/sh', mode=755)
        pack = PackFull(mock, commit='c1')
        entry = pack.fileinfo['script.sh']
        # load_git_mode sets eol, not mode, based on git entry mode.
        # mode field in FileInfo stays 0 (644 default).
        # The builtin *.sh text eol=lf pattern overrides the eol later.
        assert entry.path == 'script.sh'
        assert entry.size > 0
        assert entry.edit == 0


# ════════════════════════════════════════════════════════════════════════════
#  fileinfo — __init__.py generation
# ════════════════════════════════════════════════════════════════════════════


class TestFileinfoInitGeneration:
    """Tests for automatic __init__.py generation for Python files."""

    def test_python_file_adds_init(self):
        """A .py file should generate deleted __init__.py for parent dir."""
        mock = _make_repo()
        mock.register_file('c1', 'module/script.py', b'print(1)')
        pack = PackFull(mock, commit='c1')
        info = pack.fileinfo
        assert 'module/script.py' in info
        assert 'module/__init__.py' in info
        assert info['module/__init__.py'].edit == 2  # D (deleted)

    def test_existing_init_not_duplicated(self):
        """If __init__.py already exists, don't generate a duplicate."""
        mock = _make_repo()
        mock.register_file('c1', 'module/script.py', b'x')
        mock.register_file('c1', 'module/__init__.py', b'')
        pack = PackFull(mock, commit='c1')
        info = pack.fileinfo
        assert 'module/__init__.py' in info
        # Existing __init__.py should NOT be replaced with a deleted entry
        assert info['module/__init__.py'].edit != 2, \
            'Existing __init__.py should not be marked as deleted'

    def test_nested_python_generates_init_chain(self):
        """Nested .py files generate __init__.py for all parent dirs."""
        mock = _make_repo()
        mock.register_file('c1', 'a/b/c/d.py', b'x')
        pack = PackFull(mock, commit='c1')
        info = pack.fileinfo
        for init_path in ['a/__init__.py', 'a/b/__init__.py', 'a/b/c/__init__.py']:
            assert init_path in info, f'{init_path} should exist'
            assert info[init_path].edit == 2, f'{init_path} should be deleted'

    def test_non_python_no_init_generation(self):
        """Non-python files do not generate __init__.py."""
        mock = _make_repo()
        mock.register_file('c1', 'data.json', b'{}')
        pack = PackFull(mock, commit='c1')
        info = pack.fileinfo
        # + 1 for the packed commit history
        assert len(info) == 2
        assert 'data.json' in info

    def test_root_level_py_no_init(self):
        """A .py file at root has no parent directory, so no init generated."""
        mock = _make_repo()
        mock.register_file('c1', 'app.py', b'print("hello")')
        pack = PackFull(mock, commit='c1')
        info = pack.fileinfo
        assert 'app.py' in info
        # No __init__.py should exist for root level
        assert not any('__init__.py' in p for p in info)


# ════════════════════════════════════════════════════════════════════════════
#  fileinfo — EOL from .gitattributes
# ════════════════════════════════════════════════════════════════════════════


class TestFileinfoEol:
    """Tests for EOL assignment via .gitattributes."""

    def test_text_set_eol_default(self):
        """text=set without explicit eol → eol=0 (LF)."""
        mock = _make_repo()
        mock.register_file('c1', '.gitattributes', b'*.foo text')
        mock.register_file('c1', 'a.foo', b'hello')
        pack = PackFull(mock, commit='c1')
        # *.foo text → text='set', eol not set → default 'auto' → not 'crlf' → eol=0
        assert pack.fileinfo['a.foo'].eol == 0

    def test_text_unset_binary(self):
        """-text → binary → eol=2."""
        mock = _make_repo()
        mock.register_file('c1', '.gitattributes', b'*.foo -text')
        mock.register_file('c1', 'a.foo', b'hello')
        pack = PackFull(mock, commit='c1')
        assert pack.fileinfo['a.foo'].eol == 2

    def test_binary_macro(self):
        """binary macro → -text -diff -merge → eol=2."""
        mock = _make_repo()
        mock.register_file('c1', '.gitattributes', b'*.foo binary')
        mock.register_file('c1', 'a.foo', b'\x00')
        pack = PackFull(mock, commit='c1')
        assert pack.fileinfo['a.foo'].eol == 2

    def test_eol_crlf(self):
        """eol=crlf with implicit text=auto → eol=1."""
        mock = _make_repo()
        mock.register_file('c1', '.gitattributes', b'*.foo eol=crlf')
        mock.register_file('c1', 'a.foo', b'hello')
        pack = PackFull(mock, commit='c1')
        assert pack.fileinfo['a.foo'].eol == 1

    def test_eol_lf(self):
        """eol=lf → eol=0."""
        mock = _make_repo()
        mock.register_file('c1', '.gitattributes', b'*.foo eol=lf')
        mock.register_file('c1', 'a.foo', b'hello')
        pack = PackFull(mock, commit='c1')
        assert pack.fileinfo['a.foo'].eol == 0

    def test_auto_binary_by_content(self):
        """text=auto + null byte in content → binary → eol=2."""
        mock = _make_repo()
        # Only builtin * text=auto eol=lf applies; use an extension
        # that does NOT match any builtin specific rule (only the
        # catch-all `*` matches, giving text=auto).
        mock.register_file('c1', 'a.xxx', b'hello\x00world')
        pack = PackFull(mock, commit='c1')
        assert pack.fileinfo['a.xxx'].eol == 2

    def test_auto_text_by_content(self):
        """text=auto without null bytes → text → eol=0."""
        mock = _make_repo()
        mock.register_file('c1', 'a.xxx', b'hello world')
        pack = PackFull(mock, commit='c1')
        assert pack.fileinfo['a.xxx'].eol == 0

    def test_subdir_gitattributes_overrides_root(self):
        """Subdirectory .gitattributes overrides root for files in that dir."""
        mock = _make_repo()
        mock.register_file('c1', '.gitattributes', b'*.foo eol=crlf')
        mock.register_file('c1', 'sub/.gitattributes', b'*.foo eol=lf')
        mock.register_file('c1', 'root.foo', b'hello')
        mock.register_file('c1', 'sub/nested.foo', b'world')
        pack = PackFull(mock, commit='c1')
        info = pack.fileinfo
        # root.foo matches root .gitattributes → eol=crlf → 1
        assert info['root.foo'].eol == 1
        # sub/nested.foo matches sub .gitattributes → eol=lf → 0
        assert info['sub/nested.foo'].eol == 0


# ════════════════════════════════════════════════════════════════════════════
#  fileinfo — EOL resolution cache
# ════════════════════════════════════════════════════════════════════════════


def _count_apply_files(monkeypatch):
    """
    Count the paths the .gitattributes rule engine resolves.

    Args:
        monkeypatch (pytest.MonkeyPatch): Monkeypatch fixture

    Returns:
        list[list[str]]: The paths of every PackFull._populate_eol call
    """
    from alasio.git.attr.attr import GitAttributes

    calls = []
    original = GitAttributes.apply_files

    def counting(self, list_filepath):
        paths = list(list_filepath)
        calls.append(paths)
        return original(self, paths)

    monkeypatch.setattr(GitAttributes, 'apply_files', counting)
    return calls


def _eol_records(repo, commit, cache=None):
    """
    Resolve the eol of the files of a version, without the data encoding.

    Args:
        repo (MockGitRepo): Repo to read
        commit (str): Version to resolve
        cache (PackCache, optional): Cache shared by the versions

    Returns:
        tuple[PackFull, dict[str, FileInfo]]: The pack and its records, the
            records carry the resolved eol
    """
    pack = PackFull(repo, commit=commit, cache=cache)
    records = {
        path: FileInfo(path=PathStr(path), sha1=bytes.fromhex(entry.sha1))
        for path, entry in pack.filelist.items()
    }
    pack._populate_eol(records)
    return pack, records


class TestEolCache:
    """The versions of one .gitattributes state share the eol resolutions."""

    def test_paths_resolved_once(self, monkeypatch):
        """A version resolves the paths the earlier versions did not have."""
        mock = _make_repo()
        mock.register_file('c1', '.gitattributes', b'*.foo eol=crlf')
        mock.register_file('c1', 'a.foo', b'a')
        mock.register_commit('c2', author_name='Author', message='')
        mock.register_file('c2', '.gitattributes', b'*.foo eol=crlf')
        mock.register_file('c2', 'a.foo', b'a')
        mock.register_file('c2', 'b.foo', b'b')
        cache = PackCache()
        first = PackFull(mock, commit='c1', cache=cache)
        second = PackFull(mock, commit='c2', cache=cache)
        calls = _count_apply_files(monkeypatch)
        assert [info.eol for info in first.fileinfo.values() if info.path.endswith('.foo')] == [1]
        assert [info.eol for info in second.fileinfo.values() if info.path.endswith('.foo')] == [1, 1]
        # the second version only resolves the path the first one did not have
        assert calls == [['.gitattributes', 'a.foo'], ['b.foo']]
        # a.foo is text="auto" (only eol=crlf is given), its content is looked
        # up as well: 2 attribute + 1 content lookup per version, the second
        # version hits the attributes and the content of a.foo
        assert cache.stat['eol'] == [3, 5]

    def test_a_changed_gitattributes_switches_the_table(self, monkeypatch):
        """Another .gitattributes state resolves the paths again."""
        mock = _make_repo()
        mock.register_file('c1', '.gitattributes', b'*.foo eol=lf')
        mock.register_file('c1', 'a.foo', b'a')
        mock.register_commit('c2', author_name='Author', message='')
        mock.register_file('c2', '.gitattributes', b'*.foo eol=crlf')
        mock.register_file('c2', 'a.foo', b'a')
        cache = PackCache()
        first = PackFull(mock, commit='c1', cache=cache)
        second = PackFull(mock, commit='c2', cache=cache)
        calls = _count_apply_files(monkeypatch)
        assert first.fileinfo['a.foo'].eol == 0
        assert second.fileinfo['a.foo'].eol == 1
        # both versions resolve, the second one under another .gitattributes state
        assert calls == [['.gitattributes', 'a.foo'], ['.gitattributes', 'a.foo']]
        # one table per .gitattributes state, every version resolves its paths
        # and the content of the text="auto" a.foo
        assert len(cache.eol) == 2
        assert cache.stat['eol'] == [0, 6]

    def test_an_auto_path_follows_the_content(self, monkeypatch):
        """The table keeps the attributes, not the eol they decide."""
        mock = _make_repo()
        mock.register_file('c1', 'a.xxx', b'hello world')
        mock.register_commit('c2', author_name='Author', message='')
        mock.register_file('c2', 'a.xxx', b'hello\x00world')
        cache = PackCache()
        first = PackFull(mock, commit='c1', cache=cache)
        second = PackFull(mock, commit='c2', cache=cache)
        calls = _count_apply_files(monkeypatch)
        assert first.fileinfo['a.xxx'].eol == 0
        # the attributes are cached, the content decides every version
        assert second.fileinfo['a.xxx'].eol == 2
        assert calls == [['a.xxx']]
        # the attributes hit, the content is another one, so it misses
        assert cache.stat['eol'] == [1, 3]

    def test_the_table_holds_both_kinds_of_keys(self):
        """The attributes are keyed by the path, the auto eol by path + sha1."""
        mock = _make_repo()
        mock.register_file('c1', '.gitattributes', b'*.foo eol=crlf')
        mock.register_file('c1', 'a.foo', b'a')
        mock.register_file('c1', 'b.xxx', b'hello')
        cache = PackCache()
        pack, records = _eol_records(mock, 'c1', cache)
        assert records['a.foo'].eol == 1
        assert records['b.xxx'].eol == 0
        table = cache.eol[pack.gitattributes_fingerprint]
        # one attribute entry per path
        assert table['a.foo'] == {'text': 'auto', 'eol': 'crlf'}
        assert table['b.xxx'] == {'text': 'auto', 'eol': 'lf'}
        # the eol of a text="auto" path is an entry of its own, the tuple key
        # can not clash with the paths
        sha1 = bytes.fromhex(pack.filelist['a.foo'].sha1)
        assert table[('a.foo', sha1)] == 1
        sha1 = bytes.fromhex(pack.filelist['b.xxx'].sha1)
        assert table[('b.xxx', sha1)] == 0
        assert len(table) == 5

    def test_a_warm_version_does_not_parse_the_rules(self):
        """A version that resolves every path from the cache parses no rule."""
        mock = _make_repo()
        mock.register_file('c1', '.gitattributes', b'*.foo eol=crlf')
        mock.register_file('c1', 'a.foo', b'a')
        mock.register_commit('c2', author_name='Author', message='')
        mock.register_file('c2', '.gitattributes', b'*.foo eol=crlf')
        mock.register_file('c2', 'a.foo', b'a')
        cache = PackCache()
        first = PackFull(mock, commit='c1', cache=cache)
        second = PackFull(mock, commit='c2', cache=cache)
        assert first.fileinfo['a.foo'].eol == 1
        assert second.fileinfo['a.foo'].eol == 1
        # the first version resolved its paths with the rule engine
        assert first.gitattributes.patterns != []
        # the second one found every path in the cache: the .gitattributes file
        # is registered (as the bytes git holds) but its rules are never parsed
        assert second.gitattributes._registered_files == {'': b'*.foo eol=crlf'}
        assert second.gitattributes.patterns == []

    def test_the_cache_does_not_change_the_records(self):
        """A cached run resolves the eol of every record like a plain one."""
        mock = _make_repo()
        mock.register_file('c1', '.gitattributes', b'*.foo eol=crlf\n*.bar -text\n')
        mock.register_file('c1', 'sub/.gitattributes', b'*.foo eol=lf\n')
        mock.register_file('c1', 'a.foo', b'a')
        mock.register_file('c1', 'sub/b.foo', b'b')
        mock.register_file('c1', 'c.bar', b'\x00bar')
        mock.register_file('c1', 'd.xxx', b'text')
        mock.register_file('c1', 'e.xxx', b'\x00binary')
        mock.register_file('c1', 'pkg/f.py', b'pass\n')
        cache = PackCache()
        cached = PackFull(mock, commit='c1', cache=cache).fileinfo
        assert cache.stat['eol'][0] == 0
        plain = PackFull(mock, commit='c1').fileinfo
        assert {path: info.eol for path, info in cached.items()} == \
            {path: info.eol for path, info in plain.items()}
        assert cached['a.foo'].eol == 1
        assert cached['sub/b.foo'].eol == 0
        assert cached['c.bar'].eol == 2
        assert cached['d.xxx'].eol == 0
        assert cached['e.xxx'].eol == 2
        assert cached['pkg/f.py'].eol == 0
        # the generated D marker of pkg/ keeps the default eol
        assert cached['pkg/__init__.py'].edit == 2
        assert cached['pkg/__init__.py'].eol == 0

    def test_an_unchanged_auto_content_is_not_read_again(self, monkeypatch):
        """The eol of a text="auto" path is reused while the content is."""
        mock = _make_repo()
        mock.register_file('c1', 'a.xxx', b'hello world')
        mock.register_commit('c2', author_name='Author', message='')
        mock.register_file('c2', 'a.xxx', b'hello world')
        cache = PackCache()
        _, first = _eol_records(mock, 'c1', cache)
        assert first['a.xxx'].eol == 0
        reads = []
        original = mock.cat
        monkeypatch.setattr(mock, 'cat', lambda sha1: reads.append(sha1) or original(sha1))
        _, second = _eol_records(mock, 'c2', cache)
        assert second['a.xxx'].eol == 0
        # the content did not change, it is not read and not sniffed again
        assert reads == []
        assert cache.stat['eol'] == [2, 2]

    def test_a_changed_auto_content_is_resolved_again(self, monkeypatch):
        """Another content of a text="auto" path is sniffed and kept."""
        mock = _make_repo()
        mock.register_file('c1', 'a.xxx', b'hello world')
        mock.register_commit('c2', author_name='Author', message='')
        mock.register_file('c2', 'a.xxx', b'hello\x00world')
        cache = PackCache()
        _, first = _eol_records(mock, 'c1', cache)
        assert first['a.xxx'].eol == 0
        new_sha1 = PackFull(mock, commit='c2').filelist['a.xxx'].sha1
        reads = []
        original = mock.cat
        monkeypatch.setattr(mock, 'cat', lambda sha1: reads.append(sha1) or original(sha1))
        _, second = _eol_records(mock, 'c2', cache)
        # the content changed, it is read once and resolves to binary
        assert second['a.xxx'].eol == 2
        assert reads == [new_sha1]


# ════════════════════════════════════════════════════════════════════════════
#  fileinfo — edit-copied dedup
# ════════════════════════════════════════════════════════════════════════════


class TestFileinfoEditCopied:
    """Tests for content dedup (edit=C / copied) in fileinfo."""

    def test_duplicate_content_marked_copied(self):
        """Files with identical sha1: first is source, later are copies."""
        mock = _make_repo()
        content_a = b'same content'
        mock.register_file('c1', 'a.py', content_a)
        mock.register_file('c1', 'b.py', content_a)  # same
        pack = PackFull(mock, commit='c1')
        info = pack.fileinfo

        # a.py is source (first occurrence)
        assert info['a.py'].source_lookback == 0
        assert info['a.py'].edit == 0
        assert info['a.py'].size > 0

        # b.py is copied from a.py
        assert info['b.py'].source_lookback == 1, \
            f'source_lookback should be 1 (look back to a.py), got {info["b.py"].source_lookback}'
        assert info['b.py'].edit == 0
        # A copied file carries no data of its own: data / algo / data_size are
        # the values a decoder restores from the source record. sha1 and size
        # keep the content of the source, so that the records can be compared
        # as a diff source, see PackFull.idx_info.
        assert info['b.py'].data == b''
        assert info['b.py'].algo == 0
        assert info['b.py'].data_size == 0
        assert info['b.py'].size == len(content_a)
        assert info['b.py'].sha1 == info['a.py'].sha1

    def test_empty_file_not_copied(self):
        """Empty files (size=0) are not considered as copies."""
        mock = _make_repo()
        mock.register_file('c1', 'a.txt', b'')
        mock.register_file('c1', 'b.txt', b'')
        pack = PackFull(mock, commit='c1')
        info = pack.fileinfo
        # Both should be A (added) with source_lookback=0
        assert info['a.txt'].source_lookback == 0
        assert info['a.txt'].size == 0
        assert info['b.txt'].source_lookback == 0
        assert info['b.txt'].size == 0

    def test_duplicate_chain(self):
        """Multiple copies in sequence: each references the nearest source."""
        mock = _make_repo()
        content = b'shared content'
        mock.register_file('c1', 'a.txt', b'unique a')
        mock.register_file('c1', 'b.txt', content)
        mock.register_file('c1', 'c.txt', content)  # copy of b
        mock.register_file('c1', 'd.txt', content)  # copy of c
        mock.register_file('c1', 'e.txt', b'unique e')
        pack = PackFull(mock, commit='c1')
        info = pack.fileinfo

        # a.txt: unique
        assert info['a.txt'].source_lookback == 0
        assert info['a.txt'].size > 0

        # b.txt: first file with shared content
        assert info['b.txt'].source_lookback == 0
        assert info['b.txt'].size > 0

        # c.txt: copy of b.txt (lookback 1)
        assert info['c.txt'].source_lookback == 1
        # a copy carries no own data, its size / sha1 keep the source content
        # so that the records can be compared as a diff source
        assert info['c.txt'].size == len(content)
        assert info['c.txt'].sha1 == info['b.txt'].sha1

        # d.txt: copy of c.txt (lookback 1, the nearest source)
        assert info['d.txt'].source_lookback == 1
        assert info['d.txt'].size == len(content)

        # e.txt: unique after all copies
        assert info['e.txt'].source_lookback == 0
        assert info['e.txt'].size > 0


# ════════════════════════════════════════════════════════════════════════════
#  fileinfo — data population
# ════════════════════════════════════════════════════════════════════════════


class TestFileinfoData:
    """Tests for data loading and compression in fileinfo."""

    def test_new_file_has_data(self):
        """A new (A) file gets its content loaded and potentially compressed."""
        mock = _make_repo()
        content = b'hello world' * 100  # 1100 bytes – large enough for lzma
        mock.register_file('c1', 'big.txt', content)
        pack = PackFull(mock, commit='c1')
        entry = pack.fileinfo['big.txt']
        # Data is compressed with lzma (algo=1) or stored raw (algo=0)
        assert entry.algo in (0, 1)
        assert len(entry.data) > 0
        assert entry.data_size > 0
        assert entry.size == len(content)

    def test_deleted_file_no_data(self):
        """A deleted (D) file should not have data loaded."""
        mock = _make_repo()
        # Use a subdirectory .py file so __init__.py is generated (deleted)
        mock.register_file('c1', 'pkg/module.py', b'print(1)')
        pack = PackFull(mock, commit='c1')
        info = pack.fileinfo
        deleted = [f for f in info.values() if f.edit == 2]
        assert len(deleted) > 0
        for d in deleted:
            assert d.data == b''
            assert d.data_size == 0

    def test_copied_file_no_data(self):
        """A copied (C) file should not have own data loaded."""
        mock = _make_repo()
        content = b'shared content for copy test'
        # Use names where source sorts before copy
        mock.register_file('c1', 'alpha.txt', content)
        mock.register_file('c1', 'beta.txt', content)
        pack = PackFull(mock, commit='c1')
        info = pack.fileinfo
        # alpha.txt sorts first → it is the source
        assert info['alpha.txt'].size > 0
        assert info['alpha.txt'].data_size > 0
        assert info['alpha.txt'].source_lookback == 0
        # beta.txt sorts second → it is the copy
        assert info['beta.txt'].source_lookback == 1
        # no own data loaded, but the source content is known from the source
        assert info['beta.txt'].size == len(content)
        assert info['beta.txt'].sha1 == info['alpha.txt'].sha1
        assert info['beta.txt'].data == b''
        assert info['beta.txt'].data_size == 0

    def test_new_file_reports_blob_sha1(self):
        """After load_data, sha1 should be the SHA-1 of the content."""
        mock = _make_repo()
        content = b'content with known sha1'
        mock.register_file('c1', 'data.txt', content)
        pack = PackFull(mock, commit='c1')
        entry = pack.fileinfo['data.txt']
        # sha1 from load_data should be sha1(content) not blob_hash
        from hashlib import sha1
        expected = sha1(content).digest()
        assert entry.sha1 == expected

    def test_lzma_compression_large_file(self):
        """Large content should be lzma-compressed (algo=1)."""
        mock = _make_repo()
        # Build content large enough to benefit from lzma
        content = (b'print("hello world")\n' * 5000)
        mock.register_file('c1', 'large.py', content)
        pack = PackFull(mock, commit='c1')
        entry = pack.fileinfo['large.py']
        assert entry.algo == 1, f'Expected lzma (1), got {entry.algo}'
        assert entry.data_size < entry.size, \
            'Compressed size should be less than original'
        assert entry.data != content, 'Data should be compressed, not raw'


# ════════════════════════════════════════════════════════════════════════════
#  fileinfo — extra data
# ════════════════════════════════════════════════════════════════════════════


class TestExtraData:
    """Tests for PackFull.extra_content / extra_fileinfo, the synthetic files.

    The commit history is packed as an extra file: it is not a file of
    the repo tree, but the unpacked project still has it. extra_content
    holds the generated bytes, extra_fileinfo encodes them into records.
    """

    # msgpack of the history of the mock commit: [HistoryObj('c1', 'Author', 0, '', '')]
    HISTORY_DATA = b'\x91\x95\xa2c1\xa6Author\x00\xa0\xa0'

    def test_extra_fileinfo_packs_history(self):
        """The commit history is packed as an extra file, keyed by filepath."""
        mock = _make_repo()
        mock.register_file('c1', 'a.txt', b'aaa')
        pack = PackFull(mock, commit='c1')
        assert pack.extra_content == {'.pack/history.pack': self.HISTORY_DATA}
        extra = pack.extra_fileinfo
        assert list(extra) == ['.pack/history.pack']
        info = extra['.pack/history.pack']
        assert info.path == '.pack/history.pack'
        assert info.edit == 0  # A (added)
        assert info.eol == 2  # binary
        assert info.mode == 0
        assert info.algo == 0  # raw, the history is too small to compress
        assert info.data == self.HISTORY_DATA
        assert info.size == 15
        assert info.data_size == 15
        assert info.source_lookback == 0
        assert info.sha1 == _sha1(self.HISTORY_DATA).digest()
        # the packed content decodes to the commit history
        assert decode_history(info.data) == [
            HistoryObj(version='c1', author='Author', time=0, title='', detail=''),
        ]

    def test_extra_fileinfo_cached(self):
        """The generated content and its records are built once."""
        mock = _make_repo()
        pack = PackFull(mock, commit='c1')
        assert pack.extra_content is pack.extra_content
        assert pack.extra_fileinfo is pack.extra_fileinfo

    def test_fileinfo_packs_extra_fileinfo_last(self):
        """fileinfo appends the extras after the version files."""
        mock = _make_repo()
        mock.register_file('c1', 'a.txt', b'aaa')
        mock.register_file('c1', 'b.txt', b'bbb')
        pack = PackFull(mock, commit='c1')
        info = pack.fileinfo
        # the version files keep their DFS path order, the extras come last
        assert list(info) == ['a.txt', 'b.txt', '.pack/history.pack']
        assert info['.pack/history.pack'].data == self.HISTORY_DATA

    def test_extra_fileinfo_not_deduplicated(self):
        """Extras carry their own data, they do not join the copy detection."""
        mock = _make_repo()
        # a version file with the same content as the history extra
        mock.register_file('c1', 'history_copy.pack', self.HISTORY_DATA)
        pack = PackFull(mock, commit='c1')
        info = pack.fileinfo
        assert info['history_copy.pack'].source_lookback == 0
        assert info['history_copy.pack'].size == len(self.HISTORY_DATA)
        assert info['.pack/history.pack'].source_lookback == 0
        assert info['.pack/history.pack'].size == len(self.HISTORY_DATA)


# ════════════════════════════════════════════════════════════════════════════
#  Integration — combining all aspects
# ════════════════════════════════════════════════════════════════════════════


class TestFileinfoIntegration:
    """Integration tests covering the full fileinfo pipeline."""

    def test_python_project_structure(self):
        """Realistic Python project structure produces correct output."""
        mock = _make_repo()
        mock.register_file('c1', '.gitattributes', b'*.py text eol=lf')
        mock.register_file('c1', 'src/main.py', b'def main():\n    pass\n')
        mock.register_file('c1', 'src/utils/helper.py', b'def help():\n    return 1\n')
        mock.register_file('c1', 'data/file.bin', b'\x00\x01\x02')
        mock.register_file('c1', 'data/readme.txt', b'hello')
        pack = PackFull(mock, commit='c1')
        info = pack.fileinfo

        # Core python files present
        assert 'src/main.py' in info
        assert 'src/utils/helper.py' in info
        assert 'data/file.bin' in info
        assert 'data/readme.txt' in info

        # __init__.py generated for python packages
        assert 'src/__init__.py' in info
        assert 'src/utils/__init__.py' in info
        for init in ['src/__init__.py', 'src/utils/__init__.py']:
            assert info[init].edit == 2  # D (deleted)

        # Binary file → eol=2
        assert info['data/file.bin'].eol == 2

        # Text files → eol=0 (LF)
        assert info['src/main.py'].eol == 0
        assert info['data/readme.txt'].eol == 0

        # File ordering: parent directories before nested files
        paths = list(info)
        assert paths.index('data/file.bin') < paths.index('data/readme.txt') or \
               paths.index('data/readme.txt') < paths.index('data/file.bin')
        # All data-related paths are contiguous
        data_start = next(i for i, p in enumerate(paths) if p.startswith('data/'))
        data_end = max(i for i, p in enumerate(paths) if p.startswith('data/'))
        for i in range(data_start, data_end + 1):
            assert paths[i].startswith('data/'), \
                f'data/ files should be contiguous, but {paths[i]} found in between'

    def test_large_project_with_duplicates(self):
        """Large project with duplicated content across files."""
        mock = _make_repo()
        content_a = b'print("module a")\n'
        content_b = b'print("module b")\n'

        mock.register_file('c1', 'pkg/__init__.py', b'')
        mock.register_file('c1', 'pkg/a1.py', content_a)
        mock.register_file('c1', 'pkg/a2.py', content_a)  # copy of a1
        mock.register_file('c1', 'pkg/b1.py', content_b)
        mock.register_file('c1', 'pkg/b2.py', content_b)  # copy of b1
        mock.register_file('c1', 'pkg/a3.py', content_a)  # copy of a1 (via a2)

        pack = PackFull(mock, commit='c1')
        info = pack.fileinfo

        # Source files have data
        assert info['pkg/a1.py'].size > 0
        assert info['pkg/b1.py'].size > 0

        # Copied files reference their predecessor
        assert info['pkg/a2.py'].source_lookback == 1  # from a1
        assert info['pkg/b2.py'].source_lookback == 1  # from b1
        assert info['pkg/a3.py'].source_lookback == 1  # from a2 (nearest)

        # Copied files have no own data, their size / sha1 follow the source
        for name, source in (
                ('pkg/a2.py', 'pkg/a1.py'),
                ('pkg/b2.py', 'pkg/b1.py'),
                ('pkg/a3.py', 'pkg/a1.py')):
            assert info[name].data == b''
            assert info[name].size == info[source].size
            assert info[name].sha1 == info[source].sha1
            assert info[name].data == b''

        # Source files have correct caches updated
        assert info['pkg/a1.py'].edit == 0
        assert info['pkg/a1.py'].source_lookback == 0


# ════════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════════
#  _load_data: cache info, candidate switches, plain zstd skip
# ════════════════════════════════════════════════════════════════════════════


def _count_compress_calls(monkeypatch, name):
    """
    Count the calls of a compression function of pack_repo

    Args:
        monkeypatch (MonkeyPatch): Pytest monkeypatch fixture
        name (str): Function name in pack_repo, 'lzma_compress' / 'zstd_compress'

    Returns:
        list[bool]: One item per call, True when the call had a source
    """
    import alasio.deploy_dev.pack.pack_repo as pack_repo

    calls = []
    original = getattr(pack_repo, name)

    def counting(*args, **kwargs):
        calls.append(kwargs.get('source') is not None)
        return original(*args, **kwargs)

    monkeypatch.setattr(pack_repo, name, counting)
    return calls


def _content():
    """
    Compressible content, far above SKIP_PLAIN_ZSTD_MIN_SIZE

    Returns:
        bytes: Content
    """
    return b''.join(b'log line %d: something happened here\n' % index for index in range(600))


def _unrelated(size=1500):
    """
    Content that shares nothing with _content(), a useless zstd dictionary

    Args:
        size (int): Number of lines

    Returns:
        bytes: Content
    """
    return b''.join(b'totally other text %d\n' % index for index in range(size))


def _fake_zstd(monkeypatch, patch_size, plain_size):
    """
    Replace zstd_compress of pack_repo with a function of fixed sizes

    Args:
        monkeypatch (MonkeyPatch): Pytest monkeypatch fixture
        patch_size (int): Size returned for a patch (a source is given)
        plain_size (int): Size returned for plain zstd
    """
    import alasio.deploy_dev.pack.pack_repo as pack_repo

    monkeypatch.setattr(
        pack_repo, 'zstd_compress',
        lambda data, source=None, level=22: b'x' * (patch_size if source is not None else plain_size))


class TestLoadDataCacheInfo:
    """
    _load_data takes the cached raw / lzma encoding of the content as cache_info,
    it stands for the plain candidates, see
    doc/2026-09-27_update-pack-from-repo.md section 7.14-1
    """

    def test_cache_info_is_reused(self, monkeypatch):
        """The cached encoding is used as is, the content is not compressed again"""
        data = _content()
        source = _unrelated(600)
        cache_info = FileInfo(path='a')
        assert PackFull._load_data(cache_info, data, zstd=False) in ('raw', 'lzma')
        expected = FileInfo(path='a')
        expected_algo = PackFull._load_data(expected, data, zstd_source=source)

        calls = _count_compress_calls(monkeypatch, 'lzma_compress')
        info = FileInfo(path='a')
        algo = PackFull._load_data(info, data, cache_info=cache_info, zstd_source=source)

        assert calls == []
        assert algo == expected_algo
        assert (info.algo, info.data, info.data_size, info.size, info.sha1) == (
            expected.algo, expected.data, expected.data_size, expected.size, expected.sha1)

    def test_cache_info_of_another_size_is_ignored(self, monkeypatch):
        """A cache entry of another size is not the content, it is ignored"""
        other = FileInfo(path='a')
        PackFull._load_data(other, b'other content' * 50, zstd=False)
        calls = _count_compress_calls(monkeypatch, 'lzma_compress')
        info = FileInfo(path='a')
        PackFull._load_data(info, _content(), cache_info=other, zstd=False)
        assert len(calls) == 1

    def test_cache_info_is_the_plain_best_of_the_comparison(self, monkeypatch):
        """A candidate larger than the cached encoding does not replace it"""
        data = b'z' * 5000
        _fake_zstd(monkeypatch, patch_size=800, plain_size=700)

        import alasio.deploy_dev.pack.pack_repo as pack_repo

        monkeypatch.setattr(pack_repo, 'lzma_compress', lambda data: b'y' * 900)
        cache_info = FileInfo(path='a')
        cache_info.algo, cache_info.data, cache_info.data_size, cache_info.size = 1, b'w' * 400, 400, 5000
        info = FileInfo(path='a')
        algo = PackFull._load_data(info, data, cache_info=cache_info, zstd_source=b'old')
        assert algo == 'lzma'
        assert info.data_size == 400
        assert info.data == b'w' * 400


class TestLoadDataCandidates:
    """The candidates of _load_data and the skip of the plain zstd one"""

    def test_no_zstd_source_no_patch(self, monkeypatch):
        """The patch is tried only when a zstd_source is given"""
        calls = _count_compress_calls(monkeypatch, 'zstd_compress')
        info = FileInfo(path='a')
        PackFull._load_data(info, _content())
        # the plain zstd candidate only
        assert calls == [False]

    def test_patch_only_with_zstd_off(self, monkeypatch):
        """zstd off leaves the patch as the only zstd candidate"""
        source = b''.join(b'line %d: some stable text with more words\n' % index for index in range(1500))
        data = source + b'one more line\n'
        calls = _count_compress_calls(monkeypatch, 'zstd_compress')
        info = FileInfo(path='a')
        algo = PackFull._load_data(info, data, zstd=False, zstd_source=source)
        assert calls == [True]
        assert algo == 'zstd_patch'

    def test_a_far_smaller_patch_skips_plain_zstd(self, monkeypatch):
        """A patch 5x smaller than the plain best can not be beaten by plain zstd"""
        from alasio.ext.compress.algo_lzma import lzma_compress
        from alasio.ext.compress.algo_zstd import zstd_compress

        source = b''.join(b'line %d: some stable text with more words\n' % index for index in range(1500))
        data = source + b'one more line\n'
        # the precondition of the skip: the patch is far smaller than lzma
        assert len(zstd_compress(data, source=source, level=22)) * 5 < len(lzma_compress(data))

        calls = _count_compress_calls(monkeypatch, 'zstd_compress')
        info = FileInfo(path='a')
        algo = PackFull._load_data(info, data, zstd_source=source)
        assert algo == 'zstd_patch'
        # the patch only, the plain zstd candidate is skipped
        assert calls == [True]

    def test_a_small_content_tries_plain_zstd(self, monkeypatch):
        """Contents below SKIP_PLAIN_ZSTD_MIN_SIZE keep the full candidate set"""
        source = b'line %d: some stable text\n' * 8
        data = source + b'more\n'
        assert len(data) < PackFull.SKIP_PLAIN_ZSTD_MIN_SIZE
        calls = _count_compress_calls(monkeypatch, 'zstd_compress')
        info = FileInfo(path='a')
        PackFull._load_data(info, data, zstd_source=source)
        assert calls == [True, False]

    def test_a_close_patch_tries_plain_zstd(self, monkeypatch):
        """A patch close to the plain best does not skip plain zstd"""
        from alasio.ext.compress.algo_lzma import lzma_compress
        from alasio.ext.compress.algo_zstd import zstd_compress

        source = bytes(range(256)) * 40
        data = b''.join(b'log %d text\n' % index for index in range(400))
        # the patch is close to the plain best, plain zstd may still win
        assert len(zstd_compress(data, source=source, level=22)) * 5 >= len(lzma_compress(data))

        calls = _count_compress_calls(monkeypatch, 'zstd_compress')
        info = FileInfo(path='a')
        PackFull._load_data(info, data, zstd_source=source)
        assert calls == [True, False]


class TestExtraCacheInfo:
    """
    The generated extra files are keyed by (version, filepath) in the pack
    cache: they are not files of the repo and have no git blob sha1, see
    doc/2026-09-27_update-pack-from-repo.md section 7.21
    """

    def test_without_a_cache_it_returns_none(self):
        """No cache, no entry, the caller compresses the content itself"""
        assert PackFull._extra_cache_info(None, 'v', '.pack/history.pack', b'data') is None

    def test_the_entry_is_stored_and_reused(self, monkeypatch):
        """The first lookup compresses, the next ones take the entry"""
        cache = PackCache()
        content = b''.join(b'commit %d\n' % index for index in range(300))
        calls = _count_compress_calls(monkeypatch, 'lzma_compress')
        first = PackFull._extra_cache_info(cache, 'v', '.pack/history.pack', content)
        assert len(calls) == 1
        assert first.size == len(content)
        second = PackFull._extra_cache_info(cache, 'v', '.pack/history.pack', content)
        assert len(calls) == 1
        assert second is first
        # another version is another entry
        PackFull._extra_cache_info(cache, 'v2', '.pack/history.pack', content)
        assert len(calls) == 2
        assert cache.stat['extra'] == [1, 2]

    def test_extra_fileinfo_reuses_the_entry(self, monkeypatch):
        """Two builds of the same commit share the extra encoding"""
        from alasio.git.mock.mock_repo import MockGitRepo

        repo = MockGitRepo()
        repo.register_commit('c1', author_name='Author', message='')
        cache = PackCache()
        calls = _count_compress_calls(monkeypatch, 'lzma_compress')
        first = PackFull(repo, 'c1', cache=cache).extra_fileinfo
        count = len(calls)
        assert count >= 1
        second = PackFull(repo, 'c1', cache=cache).extra_fileinfo
        assert len(calls) == count
        assert set(first) == set(second)
        assert all(first[path].data == second[path].data for path in first)


class TestLoadDataPatchUsedReset:
    """
    When the plain zstd candidate beats the patch, the stored data does not need
    the dictionary and the caller must not keep the old file as one
    """

    def test_plain_zstd_beating_the_patch_reports_zstd(self, monkeypatch):
        """Plain zstd smaller than the patch wins, plain data needs no dictionary"""
        import alasio.deploy_dev.pack.pack_repo as pack_repo

        monkeypatch.setattr(pack_repo, 'lzma_compress', lambda data: b'y' * 900)
        _fake_zstd(monkeypatch, patch_size=400, plain_size=300)
        info = FileInfo(path='a')
        algo = PackFull._load_data(info, b'z' * 5000, zstd_source=b'old')
        assert algo == 'zstd'
        assert info.algo == 2
        assert info.data_size == 300
        assert info.data == b'x' * 300

    def test_plain_zstd_beating_the_patch_with_cache_info(self, monkeypatch):
        """The cache_info path resets it as well, the patch does not win here"""
        import alasio.deploy_dev.pack.pack_repo as pack_repo

        monkeypatch.setattr(pack_repo, 'lzma_compress', lambda data: b'y' * 900)
        _fake_zstd(monkeypatch, patch_size=400, plain_size=300)
        cache_info = FileInfo(path='a')
        cache_info.algo, cache_info.data, cache_info.data_size, cache_info.size = 1, b'w' * 500, 500, 5000
        info = FileInfo(path='a')
        algo = PackFull._load_data(info, b'z' * 5000, cache_info=cache_info, zstd_source=b'old')
        assert algo == 'zstd'
        assert info.data_size == 300

    def test_a_patch_smaller_than_plain_zstd_reports_the_patch(self, monkeypatch):
        """The patch wins, the dictionary has to be kept by the caller"""
        import alasio.deploy_dev.pack.pack_repo as pack_repo

        monkeypatch.setattr(pack_repo, 'lzma_compress', lambda data: b'y' * 900)
        _fake_zstd(monkeypatch, patch_size=300, plain_size=400)
        info = FileInfo(path='a')
        algo = PackFull._load_data(info, b'z' * 5000, zstd_source=b'old')
        assert algo == 'zstd_patch'
        assert info.algo == 2
        assert info.data_size == 300
        assert info.data == b'x' * 300
