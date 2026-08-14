"""
Tests for EOL handling through the full encode/decode chain.

MockGitRepo normalizes line endings with the builtin default .gitattributes
rules only (registered .gitattributes files are ignored by the mock), so
the test data uses file suffixes that the builtin rules know:

- `* text=auto eol=lf` is the default: text files are LF
- `*.bat`, `*.cmd`, `*.ps1` are `text eol=crlf`: checkout gets CRLF
- `*.pkl`, `*.db`, ... are `binary`: stored and returned as-is

Chain under test:
    working tree content (CRLF possible)
        -> MockGitRepo blob (LF for text, as-is for binary)
        -> pack (blob content + eol rule from the builtin gitattributes)
        -> PackDecodeBase.catfile() -> working tree content
"""
from conftest import COMMIT

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy_dev.pack.pack_repo import PackFull
from alasio.git.mock.mock_repo import MockGitRepo

# {path: working tree content as registered}
# no custom .gitattributes: everything follows the builtin default rules
EOL_FILES = {
    # builtin `*.bat text eol=crlf` -> eol = 1
    'scripts/run.bat': b'@echo off\r\necho hi\r\n',
    # builtin `*.cmd text eol=crlf` -> eol = 1
    'scripts/run.cmd': b'@echo off\r\ncmd /c hi\r\n',
    # builtin `*.ps1 text eol=crlf` -> eol = 1
    'scripts/deploy.ps1': b'Write-Host "deploy"\r\n',
    # default `* text=auto eol=lf` -> eol = 0
    'docs/readme.txt': b'line1\nline2\n',
    # CRLF input under the default LF rule: blob LF, working tree LF
    'docs/windows.txt': b'crlf one\r\ncrlf two\r\n',
    # mixed input normalized to LF in the blob
    'docs/mixed.txt': b'mix1\r\nmix2\nmix3\r\n',
    # builtin `*.pkl binary` -> eol = 2, stored and returned as-is
    'data/cache.pkl': b'\x80\x02\r\n\x00data\r\n',
}


# Module level singletons, read-only test data (see conftest.py).
EOL_REPO = MockGitRepo()
for path, content in EOL_FILES.items():
    EOL_REPO.register_file(COMMIT, path, content)
EOL_REPO.register_commit(COMMIT, author_name='Author', message='')
EOL_PACK = b''.join(PackFull(EOL_REPO, commit=COMMIT).iter_pack_data())


class TestMockEolNormalize:
    """MockGitRepo must normalize line endings like git does."""

    def test_crlf_bat_becomes_lf(self):
        """CRLF text input must be stored as LF in the blob."""
        entry = EOL_REPO.list_files(COMMIT)['scripts/run.bat']
        blob = EOL_REPO.cat(entry.sha1).decoded
        assert blob == b'@echo off\necho hi\n'

    def test_lf_text_stays_lf(self):
        """LF text input must stay LF in the blob."""
        entry = EOL_REPO.list_files(COMMIT)['docs/readme.txt']
        blob = EOL_REPO.cat(entry.sha1).decoded
        assert blob == b'line1\nline2\n'

    def test_mixed_text_normalized(self):
        """Mixed line endings must be normalized to LF in the blob."""
        entry = EOL_REPO.list_files(COMMIT)['docs/mixed.txt']
        blob = EOL_REPO.cat(entry.sha1).decoded
        assert blob == b'mix1\nmix2\nmix3\n'

    def test_binary_stays_as_is(self):
        """Binary content (NUL bytes) must be stored as-is, CRLF kept."""
        entry = EOL_REPO.list_files(COMMIT)['data/cache.pkl']
        blob = EOL_REPO.cat(entry.sha1).decoded
        assert blob == EOL_FILES['data/cache.pkl']
        assert b'\r\n' in blob


class TestPackEolRules:
    """The eol field must follow the builtin gitattributes rules."""

    def test_eol_by_rule(self):
        """Every file must get the eol value from its builtin rule."""
        decoder = PackDecodeBase(EOL_PACK)
        files = decoder.fileinfo
        assert files['scripts/run.bat'].eol == 1  # *.bat eol=crlf
        assert files['scripts/run.cmd'].eol == 1  # *.cmd eol=crlf
        assert files['scripts/deploy.ps1'].eol == 1  # *.ps1 eol=crlf
        assert files['docs/readme.txt'].eol == 0  # default eol=lf
        assert files['docs/windows.txt'].eol == 0  # default eol=lf
        assert files['docs/mixed.txt'].eol == 0  # default eol=lf
        assert files['data/cache.pkl'].eol == 2  # *.pkl binary


class TestCatfileWorkingTree:
    """catfile must return the working tree content (rule applied)."""

    def test_crlf_bat_roundtrip(self):
        """CRLF input under eol=crlf: blob LF, catfile restores CRLF."""
        decoder = PackDecodeBase(EOL_PACK)
        info = decoder.fileinfo['scripts/run.bat']
        assert bytes(decoder.catdata(info)) == b'@echo off\necho hi\n'
        assert bytes(decoder.catfile(info)) == EOL_FILES['scripts/run.bat']

    def test_ps1_crlf(self):
        """CRLF powershell file roundtrips through the working tree."""
        decoder = PackDecodeBase(EOL_PACK)
        info = decoder.fileinfo['scripts/deploy.ps1']
        assert bytes(decoder.catdata(info)) == b'Write-Host "deploy"\n'
        assert bytes(decoder.catfile(info)) == EOL_FILES['scripts/deploy.ps1']

    def test_crlf_input_under_lf_rule(self):
        """CRLF input under the default LF rule: working tree is LF."""
        decoder = PackDecodeBase(EOL_PACK)
        info = decoder.fileinfo['docs/windows.txt']
        assert bytes(decoder.catdata(info)) == b'crlf one\ncrlf two\n'
        assert bytes(decoder.catfile(info)) == b'crlf one\ncrlf two\n'

    def test_mixed_input_gets_lf(self):
        """Mixed input under the default LF rule: working tree is all LF."""
        decoder = PackDecodeBase(EOL_PACK)
        info = decoder.fileinfo['docs/mixed.txt']
        assert bytes(decoder.catdata(info)) == b'mix1\nmix2\nmix3\n'
        assert bytes(decoder.catfile(info)) == b'mix1\nmix2\nmix3\n'

    def test_lf_text_stays_lf(self):
        """LF text under the default rule stays LF."""
        decoder = PackDecodeBase(EOL_PACK)
        info = decoder.fileinfo['docs/readme.txt']
        assert bytes(decoder.catfile(info)) == b'line1\nline2\n'

    def test_binary_untouched(self):
        """Binary files are stored and returned as-is."""
        decoder = PackDecodeBase(EOL_PACK)
        info = decoder.fileinfo['data/cache.pkl']
        assert bytes(decoder.catdata(info)) == EOL_FILES['data/cache.pkl']
        assert bytes(decoder.catfile(info)) == EOL_FILES['data/cache.pkl']


class TestEolCopiedFile:
    """Files identical after normalization become C (copied)."""

    def test_normalized_duplicate_is_copied(self):
        """LF and CRLF inputs with the same text share one blob -> C."""
        repo = MockGitRepo()
        repo.register_commit(COMMIT, author_name='Author', message='')
        repo.register_file(COMMIT, 'a.bat', b'same line\n')
        repo.register_file(COMMIT, 'b.bat', b'same line\r\n')
        pack = PackFull(repo, commit=COMMIT)
        decoder = PackDecodeBase(b''.join(pack.iter_pack_data()))

        files = decoder.fileinfo
        # both blobs are LF -> identical sha1 -> b.bat is C (copied)
        assert files['a.bat'].edit == 0
        assert files['b.bat'].edit == 0
        assert files['b.bat'].source_lookback > 0
        assert files['a.bat'].size == len(b'same line\n')
        # copied file restores size and sha1 from the source record
        assert files['b.bat'].size == files['a.bat'].size
        assert files['b.bat'].sha1 == files['a.bat'].sha1
        # copied file reads the same data as the source through the chain
        assert bytes(decoder.catfile(files['b.bat'])) == b'same line\r\n'

    def test_eol_value_of_copied_file(self):
        """Copied files carry their own eol, decoded from the pack."""
        repo = MockGitRepo()
        repo.register_commit(COMMIT, author_name='Author', message='')
        repo.register_file(COMMIT, 'a.bat', b'same line\n')
        repo.register_file(COMMIT, 'b.bat', b'same line\r\n')
        pack = PackFull(repo, commit=COMMIT)
        decoder = PackDecodeBase(b''.join(pack.iter_pack_data()))

        files = decoder.fileinfo
        source = files['a.bat']
        copied = files['b.bat']
        # the real rule (builtin *.bat eol=crlf) applies to both files,
        # the copied file keeps its own eol from the pack
        assert source.eol == 1
        assert copied.eol == 1
        # working tree content comes from the source through the chain
        assert bytes(decoder.catfile(source)) == b'same line\r\n'
