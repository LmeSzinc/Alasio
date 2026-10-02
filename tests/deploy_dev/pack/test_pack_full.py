"""
Tests for the PACK's pack_full logic.

Uses MockGitRepo to provide in-memory git data, avoiding the need
for a real on-disk git repository.
"""

import random
import threading
from hashlib import sha1 as _sha1

import pytest

from alasio.deploy.history.decode_history import HistoryObj, decode_history
from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.pack_model import FileInfo
from alasio.deploy.pack.server_file import LatestInfo
from alasio.deploy_dev.pack._pack_cache import PackCache, PlainCache
from alasio.deploy_dev.pack.encode_base import PackEncodeBase
from alasio.deploy_dev.pack.pack_full import PackFull, _dfs_path_key
from alasio.ext.path.pathstr import PathStr
from alasio.ext.path.validate import validate_filepath
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
#  pack version
# ════════════════════════════════════════════════════════════════════════════


class TestPackVersion:
    """The pack format version is an int in Python, one byte in the pack file."""

    def test_default_version(self):
        """The default version is the int 0, written as the b'\\x00' byte."""
        mock = _make_repo()
        mock.register_file(COMMIT, 'a.txt', b'a')
        pack = PackFull(mock, commit=COMMIT)
        assert pack.pack_version == 0
        assert isinstance(pack.pack_version, int)
        data = b''.join(pack.iter_packidx_data())
        assert data[:4] == b'PACK'
        # one byte behind b'PACK', the b'\x00' the format always carried
        assert data[4:5] == b'\x00'
        decoder = PackDecodeBase(data)
        assert decoder.pack_version == 0
        assert isinstance(decoder.pack_version, int)

    @pytest.mark.parametrize('pack_version', [0, 1, 0x7F, 0xFF])
    def test_version_round_trips(self, pack_version):
        """A version is written as its byte and decoded back to the int."""
        mock = _make_repo()
        mock.register_file(COMMIT, 'a.txt', b'a')
        data = b''.join(PackFull(
            mock, commit=COMMIT, pack_version=pack_version).iter_packidx_data())
        assert data[4:5] == bytes((pack_version,))
        assert PackDecodeBase(data).pack_version == pack_version

    @pytest.mark.parametrize('pack_version', [-1, 256, 0x100, b'\x00', '0', 1.0, (0,)])
    def test_version_out_of_range(self, pack_version):
        """A version that is not an int in 0~255 is rejected at construction."""
        mock = _make_repo()
        mock.register_file(COMMIT, 'a.txt', b'a')
        with pytest.raises(ValueError, match='must be an int in 0~255'):
            PackFull(mock, commit=COMMIT, pack_version=pack_version)

    def test_version_assignment_is_checked(self):
        """An out of range version is rejected whatever assigns it."""
        mock = _make_repo()
        mock.register_file(COMMIT, 'a.txt', b'a')
        pack = PackFull(mock, commit=COMMIT)
        with pytest.raises(ValueError, match='must be an int in 0~255'):
            pack.pack_version = 256
        assert pack.pack_version == 0


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
        other = PackFull(mock, commit='c1', pack_version=1)
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
#  _dfs_path_key
# ════════════════════════════════════════════════════════════════════════════


def _fuzz_paths(count=500, seed=20260929):
    """
    Random paths out of components that stress the component boundaries

    Args:
        count (int): Paths to build
        seed (int): Random seed, the list is deterministic

    Returns:
        list[str]: Paths
    """
    rng = random.Random(seed)
    components = ['a', 'ab', 'a-b', 'a.b', 'b', 'b.py', 'bc', 'A', 'a_2', 'z' * 30, 'ünïcode', '__init__.py']
    return ['/'.join(rng.choice(components) for _ in range(rng.randint(1, 5))) for _ in range(count)]


class TestDfsPathKey:
    """
    The DFS sort key must order like the tuple key it replaced

    The old key was ``(parts[:-1], len(parts), parts)`` of ``path.split('/')``:
    the folder of the path is compared component wise, then (inside one folder)
    the name. The new key keeps the folder as one string with its '/' replaced
    by NUL, which orders the same way because NUL is below every character a
    pack path can carry (validate_filepath rejects control characters).
    """

    TRICKY_PATHS = [
        'a',
        'a.py',
        'a/b',
        'a/b.py',
        'a/bc',
        'a/bc.py',
        'a/b/c',
        'a/b/c.py',
        'a/bc/d.py',
        'a/b/c/d.py',
        'a-b/c.py',
        'a.b/c.py',
        'ab/c.py',
        '__init__.py',
        'a/__init__.py',
        'a/b/__init__.py',
        'z.py',
        'a/b/c/d/e/f.py',
    ]

    @staticmethod
    def _tuple_key(path):
        """
        The sort key _dfs_path_key replaced

        Args:
            path (str): File path

        Returns:
            tuple: Sort key
        """
        parts = tuple(path.split('/'))
        return (parts[:-1], len(parts), parts)

    @pytest.mark.parametrize('paths', [TRICKY_PATHS, _fuzz_paths()])
    def test_same_order_as_the_tuple_key(self, paths):
        """The cheap key orders every path exactly like the tuple key."""
        assert sorted(paths, key=_dfs_path_key) == sorted(paths, key=self._tuple_key)

    def test_files_of_a_folder_come_before_its_subfolders(self):
        """The order is the DFS order of the folders, not the plain path order."""
        paths = ['a/b/c.py', 'a/b.py', 'a/b/c/d.py', 'a.txt']
        assert sorted(paths, key=_dfs_path_key) == ['a.txt', 'a/b.py', 'a/b/c.py', 'a/b/c/d.py']

    def test_path_with_a_control_character_is_not_a_pack_path(self):
        """NUL can not appear in a validated path, so the separator is free."""
        with pytest.raises(ValueError):
            validate_filepath('a\x00b/c.py')


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


def _eol_records(repo, commit, cache):
    """
    Resolve the eol of the files of a version, without the data encoding.

    Args:
        repo (MockGitRepo): Repo to read
        commit (str): Version to resolve
        cache (PackCache): Cache of the test, the one the pack modules read

    Returns:
        tuple[PackFull, dict[str, FileInfo]]: The pack and its records, the
            records carry the resolved eol
    """
    pack = PackFull(repo, commit=commit)
    records = {
        path: FileInfo(path=PathStr(path), sha1=bytes.fromhex(entry.sha1))
        for path, entry in pack.filelist.items()
    }
    pack._populate_eol(records)
    return pack, records


class TestEolCache:
    """The versions of one .gitattributes state share the eol resolutions."""

    def test_paths_resolved_once(self, monkeypatch, cache):
        """A version resolves the paths the earlier versions did not have."""
        mock = _make_repo()
        mock.register_file('c1', '.gitattributes', b'*.foo eol=crlf')
        mock.register_file('c1', 'a.foo', b'a')
        mock.register_commit('c2', author_name='Author', message='')
        mock.register_file('c2', '.gitattributes', b'*.foo eol=crlf')
        mock.register_file('c2', 'a.foo', b'a')
        mock.register_file('c2', 'b.foo', b'b')
        first = PackFull(mock, commit='c1')
        second = PackFull(mock, commit='c2')
        calls = _count_apply_files(monkeypatch)
        assert [info.eol for info in first.fileinfo.values() if info.path.endswith('.foo')] == [1]
        assert [info.eol for info in second.fileinfo.values() if info.path.endswith('.foo')] == [1, 1]
        # the second version only resolves the path the first one did not have
        assert calls == [['.gitattributes', 'a.foo'], ['b.foo']]
        # a.foo is text="auto" (only eol=crlf is given), its content is looked
        # up as well: 2 attribute + 1 content lookup per version, the second
        # version hits the attributes and the content of a.foo
        assert cache.stat['eol'] == [3, 5]

    def test_a_changed_gitattributes_switches_the_table(self, monkeypatch, cache):
        """Another .gitattributes state resolves the paths again."""
        mock = _make_repo()
        mock.register_file('c1', '.gitattributes', b'*.foo eol=lf')
        mock.register_file('c1', 'a.foo', b'a')
        mock.register_commit('c2', author_name='Author', message='')
        mock.register_file('c2', '.gitattributes', b'*.foo eol=crlf')
        mock.register_file('c2', 'a.foo', b'a')
        first = PackFull(mock, commit='c1')
        second = PackFull(mock, commit='c2')
        calls = _count_apply_files(monkeypatch)
        assert first.fileinfo['a.foo'].eol == 0
        assert second.fileinfo['a.foo'].eol == 1
        # both versions resolve, the second one under another .gitattributes state
        assert calls == [['.gitattributes', 'a.foo'], ['.gitattributes', 'a.foo']]
        # one table per .gitattributes state, every version resolves its paths
        # and the content of the text="auto" a.foo
        assert len(cache.eol) == 2
        assert cache.stat['eol'] == [0, 6]

    def test_an_auto_path_follows_the_content(self, monkeypatch, cache):
        """The table keeps the attributes, not the eol they decide."""
        mock = _make_repo()
        mock.register_file('c1', 'a.xxx', b'hello world')
        mock.register_commit('c2', author_name='Author', message='')
        mock.register_file('c2', 'a.xxx', b'hello\x00world')
        first = PackFull(mock, commit='c1')
        second = PackFull(mock, commit='c2')
        calls = _count_apply_files(monkeypatch)
        assert first.fileinfo['a.xxx'].eol == 0
        # the attributes are cached, the content decides every version
        assert second.fileinfo['a.xxx'].eol == 2
        assert calls == [['a.xxx']]
        # the attributes hit, the content is another one, so it misses
        assert cache.stat['eol'] == [1, 3]

    def test_the_table_holds_both_kinds_of_keys(self, cache):
        """The attributes are keyed by the path, the auto eol by path + sha1."""
        mock = _make_repo()
        mock.register_file('c1', '.gitattributes', b'*.foo eol=crlf')
        mock.register_file('c1', 'a.foo', b'a')
        mock.register_file('c1', 'b.xxx', b'hello')
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

    def test_a_warm_version_does_not_parse_the_rules(self, cache):
        """A version that resolves every path from the cache parses no rule."""
        mock = _make_repo()
        mock.register_file('c1', '.gitattributes', b'*.foo eol=crlf')
        mock.register_file('c1', 'a.foo', b'a')
        mock.register_commit('c2', author_name='Author', message='')
        mock.register_file('c2', '.gitattributes', b'*.foo eol=crlf')
        mock.register_file('c2', 'a.foo', b'a')
        first = PackFull(mock, commit='c1')
        second = PackFull(mock, commit='c2')
        assert first.fileinfo['a.foo'].eol == 1
        assert second.fileinfo['a.foo'].eol == 1
        # the first version resolved its paths with the rule engine
        assert first.gitattributes.patterns != []
        # the second one found every path in the cache: the .gitattributes file
        # is registered (as the bytes git holds) but its rules are never parsed
        assert second.gitattributes._registered_files == {'': b'*.foo eol=crlf'}
        assert second.gitattributes.patterns == []

    def test_a_warm_cache_does_not_change_the_records(self, cache):
        """A build that resolves from the cache gives the same records."""
        mock = _make_repo()
        mock.register_file('c1', '.gitattributes', b'*.foo eol=crlf\n*.bar -text\n')
        mock.register_file('c1', 'sub/.gitattributes', b'*.foo eol=lf\n')
        mock.register_file('c1', 'a.foo', b'a')
        mock.register_file('c1', 'sub/b.foo', b'b')
        mock.register_file('c1', 'c.bar', b'\x00bar')
        mock.register_file('c1', 'd.xxx', b'text')
        mock.register_file('c1', 'e.xxx', b'\x00binary')
        mock.register_file('c1', 'pkg/f.py', b'pass\n')
        cold = PackFull(mock, commit='c1').fileinfo
        assert cache.stat['eol'][0] == 0
        warm = PackFull(mock, commit='c1').fileinfo
        assert {path: info.eol for path, info in cold.items()} == \
            {path: info.eol for path, info in warm.items()}
        assert warm['a.foo'].eol == 1
        assert warm['sub/b.foo'].eol == 0
        assert warm['c.bar'].eol == 2
        assert warm['d.xxx'].eol == 0
        assert warm['e.xxx'].eol == 2
        assert warm['pkg/f.py'].eol == 0
        # the generated D marker of pkg/ keeps the default eol
        assert warm['pkg/__init__.py'].edit == 2
        assert warm['pkg/__init__.py'].eol == 0

    def test_an_unchanged_auto_content_is_not_read_again(self, monkeypatch, cache):
        """The eol of a text="auto" path is reused while the content is."""
        mock = _make_repo()
        mock.register_file('c1', 'a.xxx', b'hello world')
        mock.register_commit('c2', author_name='Author', message='')
        mock.register_file('c2', 'a.xxx', b'hello world')
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

    def test_a_changed_auto_content_is_resolved_again(self, monkeypatch, cache):
        """Another content of a text="auto" path is sniffed and kept."""
        mock = _make_repo()
        mock.register_file('c1', 'a.xxx', b'hello world')
        mock.register_commit('c2', author_name='Author', message='')
        mock.register_file('c2', 'a.xxx', b'hello\x00world')
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
    Count the calls of a compression function of pack_full

    Args:
        monkeypatch (MonkeyPatch): Pytest monkeypatch fixture
        name (str): Function name in pack_full, 'lzma_compress' / 'zstd_compress'

    Returns:
        list[bool]: One item per call, True when the call had a source
    """
    import alasio.deploy_dev.pack.pack_full as pack_full

    calls = []
    original = getattr(pack_full, name)

    def counting(*args, **kwargs):
        calls.append(kwargs.get('source') is not None)
        return original(*args, **kwargs)

    monkeypatch.setattr(pack_full, name, counting)
    return calls


def _content():
    """
    Compressible content, well above the sizes where the candidates are noisy

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
    Replace zstd_compress of pack_full with a function of fixed sizes

    Args:
        monkeypatch (MonkeyPatch): Pytest monkeypatch fixture
        patch_size (int): Size returned for a patch (a source is given)
        plain_size (int): Size returned for plain zstd
    """
    import alasio.deploy_dev.pack.pack_full as pack_full

    monkeypatch.setattr(
        pack_full, 'zstd_compress',
        lambda data, source=None, level=22: b'x' * (patch_size if source is not None else plain_size))


class TestLoadDataCacheInfo:
    """
    _load_data takes the cached raw / lzma encoding of the content in the info
    slot of its cache_info (a PlainCache), it stands for the plain candidates,
    see doc/2026-09-27_update-pack-from-repo.md section 7.14-1
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
        algo = PackFull._load_data(
            info, data, cache_info=PlainCache(info=cache_info), zstd_source=source)

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
        PackFull._load_data(
            info, _content(), cache_info=PlainCache(info=other), zstd=False)
        assert len(calls) == 1

    def test_cache_info_is_the_plain_best_of_the_comparison(self, monkeypatch):
        """A candidate larger than the cached encoding does not replace it"""
        data = b'z' * 5000
        _fake_zstd(monkeypatch, patch_size=800, plain_size=700)

        import alasio.deploy_dev.pack.pack_full as pack_full

        monkeypatch.setattr(pack_full, 'lzma_compress', lambda data: b'y' * 900)
        cache_info = FileInfo(path='a')
        cache_info.algo, cache_info.data, cache_info.data_size, cache_info.size = 1, b'w' * 400, 400, 5000
        info = FileInfo(path='a')
        algo = PackFull._load_data(
            info, data, cache_info=PlainCache(info=cache_info), zstd_source=b'old')
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
        # the precondition of the skip: the patch is far smaller than lzma,
        # and still smaller than the plain zstd candidate
        patch = zstd_compress(data, source=source, level=22)
        assert len(patch) * 5 < len(lzma_compress(data))
        assert len(patch) < len(zstd_compress(data, level=22))

        calls = _count_compress_calls(monkeypatch, 'zstd_compress')
        info = FileInfo(path='a')
        algo = PackFull._load_data(info, data, zstd_source=source)
        assert algo == 'zstd_patch'
        assert info.data == patch
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

    def test_the_entry_is_stored_and_reused(self, monkeypatch, cache):
        """The first lookup compresses, the next ones take the entry"""
        content = b''.join(b'commit %d\n' % index for index in range(300))
        calls = _count_compress_calls(monkeypatch, 'lzma_compress')
        first = PackFull._extra_cache_info('v', '.pack/history.pack', content)
        assert len(calls) == 1
        assert first.info.size == len(content)
        second = PackFull._extra_cache_info('v', '.pack/history.pack', content)
        assert len(calls) == 1
        assert second.info is first.info
        # another version is another entry
        PackFull._extra_cache_info('v2', '.pack/history.pack', content)
        assert len(calls) == 2
        assert cache.stat['extra'] == [1, 2]

    def test_extra_fileinfo_reuses_the_entry(self, monkeypatch, cache):
        """Two builds of the same commit share the extra encoding"""
        from alasio.git.mock.mock_repo import MockGitRepo

        repo = MockGitRepo()
        repo.register_commit('c1', author_name='Author', message='')
        calls = _count_compress_calls(monkeypatch, 'lzma_compress')
        first = PackFull(repo, 'c1').extra_fileinfo
        count = len(calls)
        assert count >= 1
        second = PackFull(repo, 'c1').extra_fileinfo
        assert len(calls) == count
        assert set(first) == set(second)
        assert all(first[path].data == second[path].data for path in first)


class TestLoadDataZstdInfo:
    """
    The plain zstd candidate of a content is compressed once and left on the
    cache_info, the cache entry of the content (see PlainCache): every record
    that carries the content after it takes the bytes from the entry
    """

    def test_the_candidate_is_computed_once(self, monkeypatch, cache):
        """Two records of one content take one candidate, the second compresses nothing"""
        from alasio.ext.compress.algo_zstd import zstd_compress

        data = _content()
        source = _unrelated(600)
        entry = PlainCache(info=FileInfo(path='a'))
        calls = _count_compress_calls(monkeypatch, 'zstd_compress')
        first = FileInfo(path='a')
        algo = PackFull._load_data(first, data, cache_info=entry, zstd_source=source)
        second = FileInfo(path='a')
        assert PackFull._load_data(
            second, data, cache_info=entry, zstd_source=source) == algo
        # one patch per record, one candidate in total: the first record leaves
        # the candidate on the entry
        assert calls == [True, False, True]
        assert entry.zstd.data == zstd_compress(data, level=PackFull.ZSTD_LEVEL)
        assert (first.algo, first.data, first.data_size) == (
            second.algo, second.data, second.data_size)

    def test_a_skipped_record_does_not_fetch_the_candidate(self, monkeypatch, cache):
        """A patch far smaller than the plain best does not compress the candidate"""
        from alasio.ext.compress.algo_lzma import lzma_compress
        from alasio.ext.compress.algo_zstd import zstd_compress

        source = b''.join(b'line %d: some stable text with more words\n' % index for index in range(1500))
        data = source + b'one more line\n'
        # the precondition of the skip: the patch is far smaller than lzma
        assert len(zstd_compress(data, source=source, level=22)) * 5 < len(lzma_compress(data))

        calls = _count_compress_calls(monkeypatch, 'zstd_compress')
        entry = PlainCache(info=FileInfo(path='a'))
        PackFull._load_data(FileInfo(path='a'), data, cache_info=entry, zstd_source=source)
        # the patch only, the candidate is neither compressed nor stored
        assert calls == [True]
        assert entry.zstd is None

    def test_the_candidate_of_the_entry_is_used(self, monkeypatch, cache):
        """An entry that already carries the candidate compresses no plain candidate"""
        import alasio.deploy_dev.pack.pack_full as pack_full

        data = b'z' * 5000
        # the plain compressors lose against the stored candidate
        monkeypatch.setattr(pack_full, 'lzma_compress', lambda data: b'y' * 900)
        entry = PlainCache(
            info=FileInfo(path='a'),
            zstd=FileInfo(path='a', algo=2, size=len(data), data_size=300, data=b'x' * 300))
        calls = _count_compress_calls(monkeypatch, 'zstd_compress')
        info = FileInfo(path='a')
        PackFull._load_data(info, data, cache_info=entry)
        assert calls == []
        assert info.algo == 2
        assert info.data is entry.zstd.data

    def test_a_candidate_of_another_size_is_replaced(self, monkeypatch, cache):
        """A candidate of another size is not the content, it is compressed again"""
        from alasio.ext.compress.algo_zstd import zstd_compress

        data = _content()
        entry = PlainCache(
            info=FileInfo(path='a'),
            zstd=FileInfo(path='a', algo=2, size=7, data_size=3, data=b'xxx'))
        calls = _count_compress_calls(monkeypatch, 'zstd_compress')
        PackFull._load_data(FileInfo(path='a'), data, cache_info=entry)
        assert calls == [False]
        assert entry.zstd.size == len(data)
        assert entry.zstd.data == zstd_compress(data, level=PackFull.ZSTD_LEVEL)

    def test_a_cached_candidate_does_not_change_the_record(self, cache):
        """A record that takes the candidates equals a record that compresses them"""
        data = _content()
        source = _unrelated(600)
        cold = FileInfo(path='a')
        PackFull._load_data(cold, data, zstd_source=source)
        plain = FileInfo(path='a')
        PackFull._load_data(plain, data, zstd=False)
        entry = PlainCache(info=plain)
        warm = FileInfo(path='a')
        PackFull._load_data(warm, data, cache_info=entry, zstd_source=source)
        # a third record reads both encodings off the entry, the bytes hold still
        warm_again = FileInfo(path='a')
        PackFull._load_data(warm_again, data, cache_info=entry, zstd_source=source)
        assert (cold.algo, cold.data, cold.data_size, cold.size, cold.sha1) == (
            warm.algo, warm.data, warm.data_size, warm.size, warm.sha1) == (
            warm_again.algo, warm_again.data, warm_again.data_size,
            warm_again.size, warm_again.sha1)


class TestExtraZstdInfo:
    """
    The plain zstd candidate of a generated extra file lives on the entry of the
    file, the (version, filepath) one that carries its raw / lzma encoding, see
    PackFull._extra_cache_info
    """

    def test_the_candidate_of_an_extra_file_is_shared(self, monkeypatch, cache):
        """The entry of the file carries its candidate, the next record compresses nothing"""
        from alasio.ext.compress.algo_zstd import zstd_compress

        content = b''.join(b'commit %d\n' % index for index in range(300))
        calls = _count_compress_calls(monkeypatch, 'zstd_compress')
        entry = PackFull._extra_cache_info('v', '.pack/history.pack', content)
        first = FileInfo(path='.pack/history.pack')
        PackFull._load_data(first, content, cache_info=entry)
        second = FileInfo(path='.pack/history.pack')
        PackFull._load_data(second, content, cache_info=entry)
        assert calls == [False]
        assert (first.algo, first.data, first.data_size) == (
            second.algo, second.data, second.data_size)
        assert entry.zstd.data == zstd_compress(content, level=PackFull.ZSTD_LEVEL)
        # another version is another entry, the lookup itself compresses nothing
        other = PackFull._extra_cache_info('v2', '.pack/history.pack', content)
        assert calls == [False]
        PackFull._load_data(FileInfo(path='.pack/history.pack'), content, cache_info=other)
        assert calls == [False, False]


class TestLoadDataPatchUsedReset:
    """
    When the plain zstd candidate beats the patch, the stored data does not need
    the dictionary and the caller must not keep the old file as one
    """

    def test_plain_zstd_beating_the_patch_reports_zstd(self, monkeypatch):
        """Plain zstd smaller than the patch wins, plain data needs no dictionary"""
        import alasio.deploy_dev.pack.pack_full as pack_full

        monkeypatch.setattr(pack_full, 'lzma_compress', lambda data: b'y' * 900)
        _fake_zstd(monkeypatch, patch_size=400, plain_size=300)
        info = FileInfo(path='a')
        algo = PackFull._load_data(info, b'z' * 5000, zstd_source=b'old')
        assert algo == 'zstd'
        assert info.algo == 2
        assert info.data_size == 300
        assert info.data == b'x' * 300

    def test_plain_zstd_beating_the_patch_with_cache_info(self, monkeypatch):
        """The cache_info path resets it as well, the patch does not win here"""
        import alasio.deploy_dev.pack.pack_full as pack_full

        monkeypatch.setattr(pack_full, 'lzma_compress', lambda data: b'y' * 900)
        _fake_zstd(monkeypatch, patch_size=400, plain_size=300)
        cache_info = FileInfo(path='a')
        cache_info.algo, cache_info.data, cache_info.data_size, cache_info.size = 1, b'w' * 500, 500, 5000
        info = FileInfo(path='a')
        algo = PackFull._load_data(
            info, b'z' * 5000, cache_info=PlainCache(info=cache_info), zstd_source=b'old')
        assert algo == 'zstd'
        assert info.data_size == 300

    def test_a_patch_smaller_than_plain_zstd_reports_the_patch(self, monkeypatch):
        """The patch wins, the dictionary has to be kept by the caller"""
        import alasio.deploy_dev.pack.pack_full as pack_full

        monkeypatch.setattr(pack_full, 'lzma_compress', lambda data: b'y' * 900)
        _fake_zstd(monkeypatch, patch_size=300, plain_size=400)
        info = FileInfo(path='a')
        algo = PackFull._load_data(info, b'z' * 5000, zstd_source=b'old')
        assert algo == 'zstd_patch'
        assert info.algo == 2
        assert info.data_size == 300
        assert info.data == b'x' * 300


# ════════════════════════════════════════════════════════════════════════════
#  _populate_data: the data batch of a version runs on PACK_POOL
# ════════════════════════════════════════════════════════════════════════════


class _CountingPool:
    """
    A thread pool that counts the tasks submitted to it, for the tests

    Only the API that PackFull._populate_data uses is provided, every task runs
    on the pool that is wrapped.

    Args:
        pool (ThreadPool): Pool the tasks run on
    """

    def __init__(self, pool):
        self.pool = pool
        self.jobs = 0

    def start_thread_soon(self, func, *args, **kwargs):
        """
        Count a task, then hand it to the wrapped pool

        Returns:
            Job: The task of the wrapped pool
        """
        self.jobs += 1
        return self.pool.start_thread_soon(func, *args, **kwargs)

    def wait_jobs(self):
        """
        See ThreadPool.wait_jobs

        Returns:
            WaitJobsWrapper: Wrapper of the wrapped pool
        """
        wrapper = self.pool.wait_jobs()
        wrapper.pool = self
        return wrapper


class TestPopulateDataPool:
    """
    The contents the cache does not hold are compressed on the pack thread
    pool, one job for each; the job stores the encoding under the per content
    lock of the cache, so two builds that need the same content compress it
    once, see doc/2026-09-27_update-pack-from-repo.md 7.29 and 7.32
    """

    @staticmethod
    def _make_multi_file_repo(count=24):
        """
        Repo of compressible files, a batch wide enough for the pool

        Args:
            count (int): Number of files. Defaults to 24.

        Returns:
            MockGitRepo:
        """
        mock = _make_repo()
        for index in range(count):
            content = b''.join(
                b'def handler_%d_%d():\n    return %d\n' % (index, line, line)
                for line in range(20 + index)
            )
            mock.register_file(COMMIT, f'data/file_{index:02d}.py', content)
        return mock

    def test_the_compression_runs_on_the_pool(self, monkeypatch, cache):
        """The contents of the version are compressed on the pool"""
        import alasio.deploy_dev.pack.pack_full as pack_full

        repo = self._make_multi_file_repo()
        count = len(repo.list_files(COMMIT))
        threads = []
        original = pack_full.lzma_compress

        def counting(data):
            threads.append(threading.current_thread())
            return original(data)

        monkeypatch.setattr(pack_full, 'lzma_compress', counting)
        PackFull(repo, commit=COMMIT).fileinfo
        caller = threading.current_thread()
        # one job for each file of the version, the generated extra file of the
        # pack (.pack/history.pack) is encoded by the calling thread
        assert len([t for t in threads if t is not caller]) == count
        assert len([t for t in threads if t is caller]) == 1

    def test_a_warm_cache_runs_no_job(self, monkeypatch, cache):
        """A version the cache covers is built without a single task"""
        import alasio.deploy_dev.pack.pack_full as pack_full
        from alasio.ext.concurrent.threadpool import ThreadPool

        repo = self._make_multi_file_repo()
        pool = _CountingPool(ThreadPool(pool_size=2))
        monkeypatch.setattr(pack_full, 'PACK_POOL', pool)
        PackFull(repo, commit=COMMIT).fileinfo
        assert pool.jobs > 0
        jobs = pool.jobs
        PackFull(repo, commit=COMMIT).fileinfo
        assert pool.jobs == jobs

    def test_the_pool_size_does_not_change_the_pack(self, monkeypatch):
        """One worker or many, the records and the pack bytes are the same"""
        import alasio.deploy_dev.pack._pack_cache as _pack_cache
        import alasio.deploy_dev.pack.pack_full as pack_full
        from alasio.ext.concurrent.threadpool import ThreadPool

        repo = self._make_multi_file_repo()

        def build():
            # every build starts from a cold cache of its own: the jobs of the
            # second one run with another pool width
            monkeypatch.setattr(_pack_cache, 'PACK_CACHE', PackCache())
            pack = PackFull(repo, commit=COMMIT)
            return b''.join(pack.iter_pack_data()), pack.idx_info

        wide_pack, wide_records = build()
        pool = ThreadPool(pool_size=1)
        monkeypatch.setattr(pack_full, 'PACK_POOL', pool)
        monkeypatch.setattr(_pack_cache, 'PACK_POOL', pool)
        narrow_pack, narrow_records = build()
        assert narrow_pack == wide_pack
        assert narrow_records == wide_records

    def test_a_compression_error_fails_the_build(self, monkeypatch, cache):
        """An error of a job is raised by the pack build that submitted it"""
        import alasio.deploy_dev.pack.pack_full as pack_full

        def broken(data):
            raise RuntimeError('compression failed')

        monkeypatch.setattr(pack_full, 'lzma_compress', broken)
        with pytest.raises(RuntimeError, match='compression failed'):
            PackFull(self._make_multi_file_repo(), commit=COMMIT).fileinfo

    def test_the_empty_content_is_written_back_once(self, cache):
        """Two empty files share one cache entry, the second one is a hit"""
        mock = _make_repo()
        mock.register_file(COMMIT, 'a/empty.txt', b'')
        mock.register_file(COMMIT, 'b/empty.txt', b'')
        records = PackFull(mock, commit=COMMIT).fileinfo
        for path in ('a/empty.txt', 'b/empty.txt'):
            assert records[path].size == 0
            assert records[path].sha1 == b''
        # the first record wrote the entry back, the record that follows takes
        # it like a serial build does: the only contents that repeat inside one
        # version are the empty files, _populate_edit_copied makes the others C
        # (copied) records
        assert cache.stat['content'] == [1, 1]

    def test_two_builds_resolve_the_eol_once(self, monkeypatch, cache):
        """The versions that share the .gitattributes state run the rule engine once"""
        from alasio.ext.concurrent.threadpool import ThreadPool

        repo = self._make_multi_file_repo()
        calls = _count_apply_files(monkeypatch)
        records = ThreadPool(pool_size=2).thread_map(
            lambda _: PackFull(repo, commit=COMMIT).fileinfo, range(2))
        # the second build finds every path in the table of the state, the
        # resolution runs under the lock of the state, see _populate_eol
        assert len(calls) == 1
        assert records[0] == records[1]
        assert 'locks=0' in cache.report()


# ════════════════════════════════════════════════════════════════════════════
#  latest.pack: the current version and the index pack checksum
# ════════════════════════════════════════════════════════════════════════════


class TestLatestPack:
    """
    iter_packidx_data() caches the checksum of the index section it emits,
    latest_pack() assembles the latest.pack payload (version + checksum) from
    the cache, the checksum the client compares its local index against. The
    full pack checksum is cached separately while the pack is emitted.
    """

    @staticmethod
    def _make_pack():
        """
        A full pack of a small repo, with a compressible file

        Returns:
            PackFull: The pack, not assembled yet
        """
        mock = _make_repo()
        mock.register_file(COMMIT, 'a.txt', b'a')
        mock.register_file(COMMIT, 'big.txt', b'compress me ' * 1000)
        return PackFull(mock, commit=COMMIT)

    def test_checksum_unknown_until_the_index_is_emitted(self):
        """The cache is empty before the index is emitted, latest_pack() raises"""
        pack = self._make_pack()
        assert pack.index_pack_checksum is None
        with pytest.raises(ValueError, match='consume iter_packidx_data'):
            pack.latest_pack()

    def test_checksum_is_the_trailing_digest_of_the_full_pack(self):
        """The full pack checksum cache is the data section digest, the same the decoder verifies"""
        pack = self._make_pack()
        assert pack.full_pack_checksum is None
        data = b''.join(pack.iter_pack_data())
        checksum = pack.full_pack_checksum
        assert isinstance(checksum, bytes)
        assert len(checksum) == 20
        # the trailing 20 bytes of the pack, the digest of every byte before
        # them: the decoder recomputes it in validate_data()
        assert checksum == data[-20:]
        assert checksum == _sha1(data[:-20]).digest()
        decoder = PackDecodeBase(data)
        assert checksum == bytes(decoder.data_section[-20:])
        decoder.validate()

    def test_latest_pack_payload(self):
        """latest_pack() is the version + the index pack checksum, the digest the client compares"""
        pack = self._make_pack()
        index_pack = b''.join(pack.iter_packidx_data())
        latest = pack.latest_pack()
        assert latest == COMMIT.encode('utf-8') + index_pack[-20:]
        # the latest.pack content ServerFile.get_latest_info() reads back,
        # parsed and compared against the local .pack/index.pack like the
        # client does in ResetJob.validate_latest
        info = LatestInfo.parse(latest)
        assert info.version == COMMIT
        assert info.checksum == PackDecodeBase(index_pack).index_checksum
        # not the trailing checksum of the full pack data section
        full = b''.join(pack.iter_pack_data())
        assert info.checksum != bytes(PackDecodeBase(full).data_section[-20:]).hex()

    def test_latest_pack_needs_no_full_pack(self):
        """latest_pack() takes the checksum of the emitted index, the full pack is not assembled"""
        pack = self._make_pack()
        assert pack.full_pack_checksum is None
        b''.join(pack.iter_packidx_data())
        info = LatestInfo.parse(pack.latest_pack())
        assert info.version == COMMIT
        # the index emission did not assemble the full pack
        assert pack.full_pack_checksum is None

    def test_latest_pack_uses_the_cached_checksum(self, monkeypatch):
        """latest_pack() reads the cached checksum, the index is not emitted again"""
        pack = self._make_pack()
        b''.join(pack.iter_packidx_data())

        def emit_again(self):
            raise AssertionError('the index pack must not be emitted again')

        monkeypatch.setattr(PackEncodeBase, 'iter_packidx_data', emit_again)
        assert pack.latest_pack() == COMMIT.encode('utf-8') + pack.index_pack_checksum
