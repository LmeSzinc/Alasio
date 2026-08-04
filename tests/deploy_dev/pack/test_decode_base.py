"""
Tests for PackDecodeBase: full pack decode and validation.

Uses conftest.WEBSITE_REPO (a mock modern full-stack website) to build a
full pack with PackFull, then verifies the decode side restores every
record type: A/C/D edits, eol 0/1/2, algo 0/1, empty files, sha1s and
the data section via data_start / data_size.

PackDecodeError tests live in test_decode_error.py.
"""
from conftest import COMMIT, WEBSITE_FILES, WEBSITE_PACK, WEBSITE_REPO

from alasio.deploy.decode_base import PackDecodeBase
from alasio.deploy_dev.pack.pack_repo import PackFull


class TestPackDecodeBasic:
    """Basic structure decode."""

    def test_validate_passes(self):
        """A well-formed pack must validate."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        decoder.validate()  # must not raise

    def test_header(self):
        """Header magic and pack version must be decoded."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        assert decoder.pack_version == b'\x00'
        assert decoder.version == COMMIT

    def test_sections_are_memoryview(self):
        """index_section / data_section must be memoryview slices."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        assert isinstance(decoder.index_section, memoryview)
        assert isinstance(decoder.data_section, memoryview)
        assert len(decoder.index_section) > 0
        assert len(decoder.data_section) > 0

    def test_refinfo_empty_in_full_pack(self):
        """Full pack has no refinfo, all files are in fileinfo."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        assert decoder.refinfo == []
        assert len(decoder.fileinfo) == len(WEBSITE_FILES) + 1  # + D marker


class TestFullDecode:
    """One-shot full decode: every record and every file content."""

    def test_full_decode_all_data(self):
        """Every record, every field and every content must decode correctly."""
        pack = PackFull(WEBSITE_REPO, commit=COMMIT)
        decoder = PackDecodeBase(WEBSITE_PACK)
        decoder.validate()

        # every record, field by field, in the encoded order
        enc_files = list(pack.fileinfo.values())
        dec_files = decoder.fileinfo
        assert len(dec_files) == len(enc_files)
        for enc, dec in zip(enc_files, dec_files):
            for field in ('path', 'edit', 'eol', 'mode', 'algo', 'size',
                          'data_size', 'source_lookback'):
                assert getattr(enc, field) == getattr(dec, field), (
                    f'{field} mismatch for {enc.path}: '
                    f'{getattr(enc, field)!r} != {getattr(dec, field)!r}'
                )
            # sha1: A files carry it in the pack; C files resolve via source
            if enc.edit != 2 and not (enc.edit == 0 and enc.source_lookback):
                assert enc.sha1 == dec.sha1, f'sha1 mismatch for {enc.path}'

        # every file content, copied files resolved through the lookback chain
        for i, dec in enumerate(dec_files):
            if dec.edit == 2:
                # deleted marker: not in WEBSITE_FILES, no content to check
                continue
            expected = WEBSITE_FILES[dec.path][0]
            if dec.edit == 0 and dec.source_lookback:
                source = dec_files[i - dec.source_lookback]
                assert bytes(decoder.catfile(source)) == expected, (
                    f'copied content mismatch: {dec.path}'
                )
            else:
                assert bytes(decoder.catfile(dec)) == expected, (
                    f'content mismatch: {dec.path}'
                )


class TestPackDecodeRoundtrip:
    """Decoded records must match the encoder side field by field."""

    def test_edit_types_covered(self):
        """Full pack must cover A, C and D edits."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        paths = {info.path: info for info in decoder.fileinfo}
        # C (copied) files with correct lookback chains
        assert paths['backend/utils.py'].edit == 0
        assert paths['backend/utils.py'].source_lookback > 0
        assert paths['frontend/src/lib/Button.svelte'].source_lookback > 0
        assert paths['scripts/run.sh'].source_lookback > 0
        # D (deleted marker) for folder without __init__.py
        assert paths['backend/tools/__init__.py'].edit == 2

    def test_copied_content_matches_source(self):
        """C files must reference a file with identical content."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        files = decoder.fileinfo
        for i, info in enumerate(files):
            if info.edit == 0 and info.source_lookback:
                source = files[i - info.source_lookback]
                assert WEBSITE_FILES[info.path][0] == WEBSITE_FILES[source.path][0]

    def test_eol_covered(self):
        """eol 0/1/2 must be covered."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        by_eol = {info.path: info.eol for info in decoder.fileinfo}
        assert by_eol['backend/requirements.txt'] == 1  # CRLF via gitattributes
        assert by_eol['scripts/run.bat'] == 1  # CRLF batch file
        assert by_eol['backend/static/logo.png'] == 2  # binary
        assert by_eol['backend/main.py'] == 0  # LF

    def test_algo_covered(self):
        """algo 0 (raw) and 1 (lzma) must be covered."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        algos = {info.path: (info.algo, info.size, info.data_size)
                 for info in decoder.fileinfo}
        assert algos['frontend/src/lib/styles.css'][0] == 1
        assert algos['frontend/src/lib/styles.css'][1] > algos['frontend/src/lib/styles.css'][2]
        assert algos['backend/main.py'][0] == 0
        assert algos['backend/main.py'][1] == algos['backend/main.py'][2]

    def test_empty_file(self):
        """Empty files have size 0, no sha1 and no data."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        info = next(i for i in decoder.fileinfo if i.path == 'backend/__init__.py')
        assert info.edit == 0
        assert info.size == 0
        assert info.data_size == 0
        assert info.sha1 == ''
        assert info.data_start == 0

    def test_mode_covered(self):
        """mode 0 (644) and 1 (755) must come from the git entry mode."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        by_mode = {info.path: info.mode for info in decoder.fileinfo}
        assert by_mode['scripts/deploy.sh'] == 1  # 755
        assert by_mode['backend/main.py'] == 0  # 644


class TestSourcePath:
    """source_path must point at a mock repo file with the same blob sha1."""

    def test_source_matches_mock_repo_sha1(self):
        """Every record with a lookback must reference a file with the same sha1."""
        repo_files = WEBSITE_REPO.list_files(COMMIT)
        sha1_to_paths = {}
        for path, entry in repo_files.items():
            sha1_to_paths.setdefault(entry.sha1, []).append(path)

        decoder = PackDecodeBase(WEBSITE_PACK)
        for info in decoder.fileinfo:
            if info.source_lookback:
                # the source must be another file with identical content
                same_sha1 = sha1_to_paths[repo_files[info.path].sha1]
                assert info.source_path in same_sha1, (
                    f'source_path mismatch for {info.path}: '
                    f'{info.source_path!r} not in {same_sha1}'
                )
                assert info.source_path != info.path
            else:
                assert info.source_path == '', (
                    f'unexpected source_path for {info.path}: {info.source_path!r}'
                )

    def test_known_sources(self):
        """C files must reference their duplicate-content source file."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        paths = {info.path: info for info in decoder.fileinfo}
        assert paths['backend/utils.py'].source_path == 'backend/config.py'
        assert paths['frontend/src/lib/Button.svelte'].source_path == 'frontend/src/App.svelte'
        assert paths['scripts/run.sh'].source_path == 'scripts/deploy.sh'

    def test_deleted_has_no_source(self):
        """D records have no source_lookback and no source_path."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        info = next(i for i in decoder.fileinfo if i.edit == 2)
        assert info.source_lookback == 0
        assert info.source_path == ''


class TestApplyEol:
    """apply_eol must convert LF blob content to the checkout form."""

    def test_lf_stays_lf(self):
        """eol == 0 (LF) must keep the content as-is."""
        content = b'line1\nline2\n'
        assert PackDecodeBase.apply_eol(content, 0) == content

    def test_lf_to_crlf(self):
        """eol == 1 (CRLF) must convert LF to CRLF."""
        content = b'line1\nline2\n'
        assert PackDecodeBase.apply_eol(content, 1) == b'line1\r\nline2\r\n'

    def test_crlf_stays_crlf(self):
        """eol == 1 must not double-convert existing CRLF."""
        content = b'line1\r\nline2\r\n'
        assert PackDecodeBase.apply_eol(content, 1) == content

    def test_binary_stays_as_is(self):
        """eol == 2 (binary) must keep the content as-is."""
        content = b'line1\r\nline2\r\n\x00tail'
        assert PackDecodeBase.apply_eol(content, 2) == content


class TestPackDecodeData:
    """Data section extraction via data_start / data_size."""

    def test_all_contents_extract(self):
        """Every file with data must restore its original content."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        for info in decoder.fileinfo:
            if not info.data_size:
                continue
            content = bytes(decoder.catfile(info))
            assert content == WEBSITE_FILES[info.path][0], f'content mismatch: {info.path}'

    def test_catfile_returns_memoryview(self):
        """catfile must return a memoryview for both raw and lzma files."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        for info in decoder.fileinfo:
            if not info.data_size:
                continue
            content = decoder.catfile(info)
            assert isinstance(content, memoryview)
            assert bytes(content) == WEBSITE_FILES[info.path][0]

    def test_catfile_empty_file(self):
        """Files without data return an empty memoryview."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        info = next(i for i in decoder.fileinfo if i.path == 'backend/__init__.py')
        assert bytes(decoder.catfile(info)) == b''

    def test_catdata_raw(self):
        """catdata of a raw file returns the plain content."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        info = next(i for i in decoder.fileinfo if i.path == 'backend/main.py')
        data = decoder.catdata(info)
        assert isinstance(data, memoryview)
        assert bytes(data) == WEBSITE_FILES['backend/main.py'][0]

    def test_catdata_compressed(self):
        """catdata of a compressed file returns the compressed bytes."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        info = next(i for i in decoder.fileinfo if i.path == 'frontend/src/lib/styles.css')
        data = decoder.catdata(info)
        assert isinstance(data, memoryview)
        assert len(data) == info.data_size
        assert info.data_size < info.size  # compressed smaller than the file
        assert bytes(data) != WEBSITE_FILES[info.path][0]  # not the plain content

    def test_catfile_applies_eol(self):
        """catfile must apply the checkout line ending rule."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        info = next(i for i in decoder.fileinfo if i.path == 'backend/main.py')
        lf_content = bytes(decoder.catdata(info))
        assert lf_content == WEBSITE_FILES['backend/main.py'][0]
        # simulate an eol=1 (CRLF) checkout rule: catfile must convert
        info.eol = 1
        crlf_content = bytes(decoder.catfile(info))
        assert crlf_content == lf_content.replace(b'\n', b'\r\n')
        assert b'\r\n' in crlf_content

    def test_data_start_is_file_offset(self):
        """data_start must be an offset into the pack file."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        for info in decoder.fileinfo:
            if info.data_size:
                assert info.data_start + info.data_size <= len(decoder.data)
                assert info.data_start > len(decoder.index_section)

    def test_data_start_strictly_increasing(self):
        """Records with data must occupy continuous ranges."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        prev_end = None
        for info in decoder.fileinfo:
            if not info.data_size:
                continue
            if prev_end is not None:
                assert info.data_start == prev_end
            prev_end = info.data_start + info.data_size


class TestPackDecodeCached:
    """idx_info must be computed lazily and cached."""

    def test_lazy(self):
        """Constructing the decoder must not decode idx_info."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        assert '_len_refinfo' not in decoder.__dict__

    def test_cached(self):
        """Repeated access must return the same list object."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        first = decoder.idx_info
        assert decoder.idx_info is first


class TestPackDecodeEmptyRepo:
    """A pack with no files must decode."""

    def test_empty(self):
        """Empty repo produces an empty but valid pack."""
        from alasio.git.mock.mock_repo import MockGitRepo

        repo = MockGitRepo()
        pack = PackFull(repo, commit='c1')
        data = b''.join(pack.iter_pack_data())
        decoder = PackDecodeBase(data)
        decoder.validate()
        assert decoder.fileinfo == []
        assert decoder.refinfo == []
