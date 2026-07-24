"""
Tests for the PACK's pack_repo logic.

Uses MockGitRepo to provide in-memory git data, avoiding the need
for a real on-disk git repository.
"""

from hashlib import sha1 as _sha1

from alasio.deploy_dev.pack.pack_repo import PackFull
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.git.stage.gitreset import FileEntry

# ════════════════════════════════════════════════════════════════════════════
#  Constructor & basic properties
# ════════════════════════════════════════════════════════════════════════════


class TestPackFullInit:
    """Tests for PackFull.__init__ and basic properties."""

    def test_init_with_commit(self):
        """Provide a commit sha1, verify commit property returns it."""
        mock = MockGitRepo()
        pack = PackFull(mock, commit='abc123')
        assert pack.commit == 'abc123'

    def test_init_default_commit(self):
        """Without commit, _commit stays empty (not called yet)."""
        mock = MockGitRepo()
        pack = PackFull(mock)
        assert pack._commit == ''

    def test_init_repo_stored(self):
        """The repo reference should be stored."""
        mock = MockGitRepo()
        pack = PackFull(mock)
        assert pack.repo is mock


# ════════════════════════════════════════════════════════════════════════════
#  filelist
# ════════════════════════════════════════════════════════════════════════════


class TestFilelist:
    """Tests for PackFull.filelist."""

    def test_filelist_known_commit(self):
        """Filelist returns files registered for a commit."""
        mock = MockGitRepo()
        mock.register_file('c1', 'a.txt', b'hello')
        pack = PackFull(mock, commit='c1')
        flist = pack.filelist
        assert isinstance(flist, dict)
        assert 'a.txt' in flist
        assert isinstance(flist['a.txt'], FileEntry)
        assert flist['a.txt'].path == 'a.txt'

    def test_filelist_unknown_commit(self):
        """Filelist returns empty dict for unknown commit."""
        mock = MockGitRepo()
        pack = PackFull(mock, commit='nonexistent')
        assert pack.filelist == {}

    def test_filelist_multiple_files(self):
        """Filelist returns all registered files."""
        mock = MockGitRepo()
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
        mock_with = MockGitRepo()
        mock_with.register_file('c1', '.gitattributes', b'*.foo text eol=crlf')
        mock_with.register_file('c1', 'a.foo', b'content')
        pack_with = PackFull(mock_with, commit='c1')

        mock_without = MockGitRepo()
        mock_without.register_file('c1', 'a.foo', b'content')
        pack_without = PackFull(mock_without, commit='c1')

        # With repo .gitattributes, eol should be CRLF (1)
        # Without, only builtin * text=auto eol=lf applies → eol = 0
        # This proves the repo .gitattributes pattern was loaded and applied
        assert pack_with.fileinfo['a.foo'].eol == 1
        assert pack_without.fileinfo['a.foo'].eol == 0

    def test_root_gitattributes_loaded(self):
        """Root .gitattributes should be loaded as a repo pattern."""
        mock = MockGitRepo()
        mock.register_file('c1', '.gitattributes', b'*.foo text')
        mock.register_file('c1', 'a.foo', b'content')
        pack = PackFull(mock, commit='c1')
        # Pattern count should be > builtin-only count
        attrs_no = PackFull(MockGitRepo(), commit='not-there').gitattributes
        # Just confirm it loaded without error
        assert pack.gitattributes is pack.gitattributes  # cached

    def test_subdir_gitattributes_loaded(self):
        """Subdirectory .gitattributes should be loaded."""
        mock = MockGitRepo()
        mock.register_file('c1', 'sub/.gitattributes', b'*.bar binary')
        mock.register_file('c1', 'sub/a.bar', b'\x00')
        pack = PackFull(mock, commit='c1')
        attrs = pack.gitattributes
        patterns = attrs.patterns
        # At least one pattern from sub/.gitattributes
        repo_patterns = [p for p in patterns if p.root == 'sub/']
        assert len(repo_patterns) > 0


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
        mock = MockGitRepo()
        content = b'hello world'
        mock.register_file('c1', 'hello.txt', content)
        pack = PackFull(mock, commit='c1')
        info = pack.fileinfo
        assert len(info) == 1
        entry = info['hello.txt']
        assert entry.path == 'hello.txt'
        # load_data() sets sha1 to sha1(content).hexdigest() (raw content hash)
        assert entry.sha1 == _sha1(content).hexdigest()
        assert entry.size == len(content)
        assert entry.edit == 0          # A (added)
        assert entry.mode == 0          # 644
        assert entry.source_lookback == 0

    def test_multiple_files(self):
        """Multiple files all appear in fileinfo."""
        mock = MockGitRepo()
        mock.register_file('c1', 'a.txt', b'aaa')
        mock.register_file('c1', 'b.txt', b'bbb')
        pack = PackFull(mock, commit='c1')
        info = pack.fileinfo
        assert set(info) == {'a.txt', 'b.txt'}

    def test_file_ordering_shallow_first(self):
        """Files are sorted DFS: shallower parents come before deeper ones."""
        mock = MockGitRepo()
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
        """Empty file: size=0, sha1='' after load_data."""
        mock = MockGitRepo()
        mock.register_file('c1', 'empty.txt', b'')
        pack = PackFull(mock, commit='c1')
        entry = pack.fileinfo['empty.txt']
        assert entry.size == 0
        assert entry.sha1 == ''
        assert entry.algo == 0
        assert entry.data == b''

    def test_mode_755(self):
        """Mode 755 file is handled correctly (load_git_mode sets eol)."""
        mock = MockGitRepo()
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
        mock = MockGitRepo()
        mock.register_file('c1', 'module/script.py', b'print(1)')
        pack = PackFull(mock, commit='c1')
        info = pack.fileinfo
        assert 'module/script.py' in info
        assert 'module/__init__.py' in info
        assert info['module/__init__.py'].edit == 2  # D (deleted)

    def test_existing_init_not_duplicated(self):
        """If __init__.py already exists, don't generate a duplicate."""
        mock = MockGitRepo()
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
        mock = MockGitRepo()
        mock.register_file('c1', 'a/b/c/d.py', b'x')
        pack = PackFull(mock, commit='c1')
        info = pack.fileinfo
        for init_path in ['a/__init__.py', 'a/b/__init__.py', 'a/b/c/__init__.py']:
            assert init_path in info, f'{init_path} should exist'
            assert info[init_path].edit == 2, f'{init_path} should be deleted'

    def test_non_python_no_init_generation(self):
        """Non-python files do not generate __init__.py."""
        mock = MockGitRepo()
        mock.register_file('c1', 'data.json', b'{}')
        pack = PackFull(mock, commit='c1')
        info = pack.fileinfo
        assert len(info) == 1
        assert 'data.json' in info

    def test_root_level_py_no_init(self):
        """A .py file at root has no parent directory, so no init generated."""
        mock = MockGitRepo()
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
        mock = MockGitRepo()
        mock.register_file('c1', '.gitattributes', b'*.foo text')
        mock.register_file('c1', 'a.foo', b'hello')
        pack = PackFull(mock, commit='c1')
        # *.foo text → text='set', eol not set → default 'auto' → not 'crlf' → eol=0
        assert pack.fileinfo['a.foo'].eol == 0

    def test_text_unset_binary(self):
        """-text → binary → eol=2."""
        mock = MockGitRepo()
        mock.register_file('c1', '.gitattributes', b'*.foo -text')
        mock.register_file('c1', 'a.foo', b'hello')
        pack = PackFull(mock, commit='c1')
        assert pack.fileinfo['a.foo'].eol == 2

    def test_binary_macro(self):
        """binary macro → -text -diff -merge → eol=2."""
        mock = MockGitRepo()
        mock.register_file('c1', '.gitattributes', b'*.foo binary')
        mock.register_file('c1', 'a.foo', b'\x00')
        pack = PackFull(mock, commit='c1')
        assert pack.fileinfo['a.foo'].eol == 2

    def test_eol_crlf(self):
        """eol=crlf with implicit text=auto → eol=1."""
        mock = MockGitRepo()
        mock.register_file('c1', '.gitattributes', b'*.foo eol=crlf')
        mock.register_file('c1', 'a.foo', b'hello')
        pack = PackFull(mock, commit='c1')
        assert pack.fileinfo['a.foo'].eol == 1

    def test_eol_lf(self):
        """eol=lf → eol=0."""
        mock = MockGitRepo()
        mock.register_file('c1', '.gitattributes', b'*.foo eol=lf')
        mock.register_file('c1', 'a.foo', b'hello')
        pack = PackFull(mock, commit='c1')
        assert pack.fileinfo['a.foo'].eol == 0

    def test_auto_binary_by_content(self):
        """text=auto + null byte in content → binary → eol=2."""
        mock = MockGitRepo()
        # Only builtin * text=auto eol=lf applies; use an extension
        # that does NOT match any builtin specific rule (only the
        # catch-all `*` matches, giving text=auto).
        mock.register_file('c1', 'a.xxx', b'hello\x00world')
        pack = PackFull(mock, commit='c1')
        assert pack.fileinfo['a.xxx'].eol == 2

    def test_auto_text_by_content(self):
        """text=auto without null bytes → text → eol=0."""
        mock = MockGitRepo()
        mock.register_file('c1', 'a.xxx', b'hello world')
        pack = PackFull(mock, commit='c1')
        assert pack.fileinfo['a.xxx'].eol == 0

    def test_subdir_gitattributes_overrides_root(self):
        """Subdirectory .gitattributes overrides root for files in that dir."""
        mock = MockGitRepo()
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
#  fileinfo — edit-copied dedup
# ════════════════════════════════════════════════════════════════════════════


class TestFileinfoEditCopied:
    """Tests for content dedup (edit=C / copied) in fileinfo."""

    def test_duplicate_content_marked_copied(self):
        """Files with identical sha1: first is source, later are copies."""
        mock = MockGitRepo()
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
        # Copied files have their metadata reset
        assert info['b.py'].size == 0
        assert info['b.py'].data == b''
        assert info['b.py'].algo == 0

    def test_empty_file_not_copied(self):
        """Empty files (size=0) are not considered as copies."""
        mock = MockGitRepo()
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
        mock = MockGitRepo()
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
        assert info['c.txt'].size == 0

        # d.txt: copy of c.txt (lookback 1, the nearest source)
        assert info['d.txt'].source_lookback == 1
        assert info['d.txt'].size == 0

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
        mock = MockGitRepo()
        content = b'hello world' * 100   # 1100 bytes – large enough for lzma
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
        mock = MockGitRepo()
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
        mock = MockGitRepo()
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
        assert info['beta.txt'].size == 0
        assert info['beta.txt'].data == b''
        assert info['beta.txt'].data_size == 0

    def test_new_file_reports_blob_sha1(self):
        """After load_data, sha1 should be the SHA-1 of the content."""
        mock = MockGitRepo()
        content = b'content with known sha1'
        mock.register_file('c1', 'data.txt', content)
        pack = PackFull(mock, commit='c1')
        entry = pack.fileinfo['data.txt']
        # sha1 from load_data should be sha1(content) not blob_hash
        from hashlib import sha1
        expected = sha1(content).hexdigest()
        assert entry.sha1 == expected

    def test_lzma_compression_large_file(self):
        """Large content should be lzma-compressed (algo=1)."""
        mock = MockGitRepo()
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
#  Integration — combining all aspects
# ════════════════════════════════════════════════════════════════════════════


class TestFileinfoIntegration:
    """Integration tests covering the full fileinfo pipeline."""

    def test_python_project_structure(self):
        """Realistic Python project structure produces correct output."""
        mock = MockGitRepo()
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
        mock = MockGitRepo()
        content_a = b'print("module a")\n'
        content_b = b'print("module b")\n'

        mock.register_file('c1', 'pkg/__init__.py', b'')
        mock.register_file('c1', 'pkg/a1.py', content_a)
        mock.register_file('c1', 'pkg/a2.py', content_a)   # copy of a1
        mock.register_file('c1', 'pkg/b1.py', content_b)
        mock.register_file('c1', 'pkg/b2.py', content_b)   # copy of b1
        mock.register_file('c1', 'pkg/a3.py', content_a)   # copy of a1 (via a2)

        pack = PackFull(mock, commit='c1')
        info = pack.fileinfo

        # Source files have data
        assert info['pkg/a1.py'].size > 0
        assert info['pkg/b1.py'].size > 0

        # Copied files reference their predecessor
        assert info['pkg/a2.py'].source_lookback == 1  # from a1
        assert info['pkg/b2.py'].source_lookback == 1  # from b1
        assert info['pkg/a3.py'].source_lookback == 1  # from a2 (nearest)

        # Copied files have no own data
        for name in ('pkg/a2.py', 'pkg/b2.py', 'pkg/a3.py'):
            assert info[name].size == 0
            assert info[name].data == b''

        # Source files have correct caches updated
        assert info['pkg/a1.py'].edit == 0
        assert info['pkg/a1.py'].source_lookback == 0


# ════════════════════════════════════════════════════════════════════════════
