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
from alasio.deploy.pack.pack_model import IdxInfo
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
        assert decoder.refinfo == {}
        assert len(decoder.fileinfo) == len(WEBSITE_FILES) + 1  # + D marker


class TestFullDecode:
    """One-shot full decode: every record and every file content."""

    def test_full_decode_all_data(self):
        """Every record must decode to the hard-coded expected IdxInfo."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        decoder.validate()

        # hard-coded expectation per file, derived from WEBSITE_FILES:
        # sha1 is the content sha1 of the blob, data_start is the offset
        # of the data in the pack file
        assert decoder.refinfo == {}
        files = decoder.fileinfo
        assert files['.gitattributes'] == IdxInfo(
            path='.gitattributes', edit=0, eol=0, mode=0, algo=1,
            size=70, data_size=55, source_lookback=0, source_path='',
            sha1='37b6876fc38b3e141141270502ca0bc70339060c', data_start=707,
        )
        assert files['backend/__init__.py'] == IdxInfo(
            path='backend/__init__.py', edit=0, eol=0, mode=0, algo=0,
            size=0, data_size=0, source_lookback=0, source_path='',
            sha1='', data_start=0,
        )
        assert files['backend/config.py'] == IdxInfo(
            path='backend/config.py', edit=0, eol=0, mode=0, algo=0,
            size=43, data_size=43, source_lookback=0, source_path='',
            sha1='80c4a3c2cc87ffa168e205743b3b883ad3e08eb5', data_start=762,
        )
        assert files['backend/main.py'] == IdxInfo(
            path='backend/main.py', edit=0, eol=0, mode=0, algo=0,
            size=70, data_size=70, source_lookback=0, source_path='',
            sha1='a936d96edd251c2a5e78812a430076a528fc88e9', data_start=805,
        )
        assert files['backend/requirements.txt'] == IdxInfo(
            path='backend/requirements.txt', edit=0, eol=1, mode=0, algo=0,
            size=33, data_size=33, source_lookback=0, source_path='',
            sha1='3f5ff3ff1bd1a310ba43a663e7cb0fb946de82e6', data_start=875,
        )
        assert files['backend/settings.py'] == IdxInfo(
            path='backend/settings.py', edit=0, eol=0, mode=0, algo=0,
            size=43, data_size=43, source_lookback=3, source_path='backend/config.py',
            sha1='80c4a3c2cc87ffa168e205743b3b883ad3e08eb5', data_start=762,
        )
        assert files['backend/utils.py'] == IdxInfo(
            path='backend/utils.py', edit=0, eol=0, mode=0, algo=0,
            size=43, data_size=43, source_lookback=1, source_path='backend/settings.py',
            sha1='80c4a3c2cc87ffa168e205743b3b883ad3e08eb5', data_start=762,
        )
        assert files['backend/api/__init__.py'] == IdxInfo(
            path='backend/api/__init__.py', edit=0, eol=0, mode=0, algo=0,
            size=27, data_size=27, source_lookback=0, source_path='',
            sha1='5382076fa462350af2aa921c35ab72e2c8c8002f', data_start=908,
        )
        assert files['backend/api/routes.py'] == IdxInfo(
            path='backend/api/routes.py', edit=0, eol=0, mode=0, algo=0,
            size=77, data_size=77, source_lookback=0, source_path='',
            sha1='b3817c4fecaed2e877549b0afdef93e41b7dbc7d', data_start=935,
        )
        assert files['backend/static/logo.png'] == IdxInfo(
            path='backend/static/logo.png', edit=0, eol=2, mode=0, algo=1,
            size=25600, data_size=302, source_lookback=0, source_path='',
            sha1='bee4c060ee5e5290ab433d49d1c5676b6e57261e', data_start=1012,
        )
        assert files['backend/tools/__init__.py'] == IdxInfo(
            path='backend/tools/__init__.py', edit=2, eol=0, mode=0, algo=0,
            size=0, data_size=0, source_lookback=0, source_path='',
            sha1='', data_start=0,
        )
        assert files['backend/tools/helper.py'] == IdxInfo(
            path='backend/tools/helper.py', edit=0, eol=0, mode=0, algo=0,
            size=28, data_size=28, source_lookback=0, source_path='',
            sha1='2ba0e2135cea164cafcd2e9bdceb9789ef953133', data_start=1314,
        )
        assert files['docs/README.md'] == IdxInfo(
            path='docs/README.md', edit=0, eol=0, mode=0, algo=0,
            size=35, data_size=35, source_lookback=0, source_path='',
            sha1='8f95d6e53d81d2ae147cf083560cbce33b55d266', data_start=1342,
        )
        assert files['frontend/package.json'] == IdxInfo(
            path='frontend/package.json', edit=0, eol=0, mode=0, algo=0,
            size=44, data_size=44, source_lookback=0, source_path='',
            sha1='da2f8d9497d709fa5d98bf97429ff211fd485673', data_start=1377,
        )
        assert files['frontend/tsconfig.json'] == IdxInfo(
            path='frontend/tsconfig.json', edit=0, eol=0, mode=0, algo=0,
            size=50, data_size=50, source_lookback=0, source_path='',
            sha1='3587dbace20bd384c164cf5f4b242fa7f2a48fb1', data_start=1421,
        )
        assert files['frontend/src/App.svelte'] == IdxInfo(
            path='frontend/src/App.svelte', edit=0, eol=0, mode=0, algo=1,
            size=104, data_size=99, source_lookback=0, source_path='',
            sha1='d6aef44bc7a8fde97945171a59aac8d6b1cea8fb', data_start=1471,
        )
        assert files['frontend/src/lib/Button.svelte'] == IdxInfo(
            path='frontend/src/lib/Button.svelte', edit=0, eol=0, mode=0, algo=1,
            size=104, data_size=99, source_lookback=1, source_path='frontend/src/App.svelte',
            sha1='d6aef44bc7a8fde97945171a59aac8d6b1cea8fb', data_start=1471,
        )
        assert files['frontend/src/lib/styles.css'] == IdxInfo(
            path='frontend/src/lib/styles.css', edit=0, eol=0, mode=0, algo=1,
            size=120000, data_size=135, source_lookback=0, source_path='',
            sha1='d77a6725ecb2a2798d1df284b809de51da3e0399', data_start=1570,
        )
        assert files['frontend/src/routes/+page.svelte'] == IdxInfo(
            path='frontend/src/routes/+page.svelte', edit=0, eol=0, mode=0, algo=0,
            size=75, data_size=75, source_lookback=0, source_path='',
            sha1='7263ba1458fa5627d3c966251db887e0f518288e', data_start=1705,
        )
        assert files['scripts/deploy.sh'] == IdxInfo(
            path='scripts/deploy.sh', edit=0, eol=0, mode=1, algo=0,
            size=31, data_size=31, source_lookback=0, source_path='',
            sha1='2690963383249907ec8304c2f09e5d0a5d86f24d', data_start=1780,
        )
        assert files['scripts/run.bat'] == IdxInfo(
            path='scripts/run.bat', edit=0, eol=1, mode=0, algo=0,
            size=28, data_size=28, source_lookback=0, source_path='',
            sha1='30c7e458805ec8f7c2335f2d26b01f2ba8d66c16', data_start=1811,
        )
        assert files['scripts/run.sh'] == IdxInfo(
            path='scripts/run.sh', edit=0, eol=0, mode=1, algo=0,
            size=31, data_size=31, source_lookback=2, source_path='scripts/deploy.sh',
            sha1='2690963383249907ec8304c2f09e5d0a5d86f24d', data_start=1780,
        )

        # every file content, copied files resolved through the source path
        for dec in files.values():
            if dec.edit == 2:
                # deleted marker: not in WEBSITE_FILES, no content to check
                continue
            expected = WEBSITE_FILES[dec.path][0]
            if dec.edit == 0 and dec.source_lookback:
                source = files[dec.source_path]
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
        paths = decoder.fileinfo
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
        for info in files.values():
            if info.edit == 0 and info.source_lookback:
                source = files[info.source_path]
                assert WEBSITE_FILES[info.path][0] == WEBSITE_FILES[source.path][0]

    def test_eol_covered(self):
        """eol 0/1/2 must be covered."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        files = decoder.fileinfo
        assert files['backend/requirements.txt'].eol == 1  # CRLF via gitattributes
        assert files['scripts/run.bat'].eol == 1  # CRLF batch file
        assert files['backend/static/logo.png'].eol == 2  # binary
        assert files['backend/main.py'].eol == 0  # LF

    def test_algo_covered(self):
        """algo 0 (raw) and 1 (lzma) must be covered."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        files = decoder.fileinfo
        css = files['frontend/src/lib/styles.css']
        assert css.algo == 1
        assert css.size > css.data_size
        main = files['backend/main.py']
        assert main.algo == 0
        assert main.size == main.data_size

    def test_empty_file(self):
        """Empty files have size 0, no sha1 and no data."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        info = decoder.fileinfo['backend/__init__.py']
        assert info.edit == 0
        assert info.size == 0
        assert info.data_size == 0
        assert info.sha1 == ''
        assert info.data_start == 0

    def test_mode_covered(self):
        """mode 0 (644) and 1 (755) must come from the git entry mode."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        files = decoder.fileinfo
        assert files['scripts/deploy.sh'].mode == 1  # 755
        assert files['backend/main.py'].mode == 0  # 644


class TestSourcePath:
    """source_path must point at a mock repo file with the same blob sha1."""

    def test_source_matches_mock_repo_sha1(self):
        """Every record with a lookback must reference a file with the same sha1."""
        repo_files = WEBSITE_REPO.list_files(COMMIT)
        sha1_to_paths = {}
        for path, entry in repo_files.items():
            sha1_to_paths.setdefault(entry.sha1, []).append(path)

        decoder = PackDecodeBase(WEBSITE_PACK)
        for info in decoder.fileinfo.values():
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
        paths = decoder.fileinfo
        # cascade chain: utils.py -> settings.py -> config.py
        assert paths['backend/settings.py'].source_path == 'backend/config.py'
        assert paths['backend/utils.py'].source_path == 'backend/settings.py'
        assert paths['frontend/src/lib/Button.svelte'].source_path == 'frontend/src/App.svelte'
        assert paths['scripts/run.sh'].source_path == 'scripts/deploy.sh'

    def test_deleted_has_no_source(self):
        """D records have no source_lookback and no source_path."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        info = decoder.fileinfo['backend/tools/__init__.py']
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
        for info in decoder.fileinfo.values():
            if not info.data_size:
                continue
            content = bytes(decoder.catfile(info))
            assert content == WEBSITE_FILES[info.path][0], f'content mismatch: {info.path}'

    def test_catfile_returns_memoryview(self):
        """catfile must return a memoryview for both raw and lzma files."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        for info in decoder.fileinfo.values():
            if not info.data_size:
                continue
            content = decoder.catfile(info)
            assert isinstance(content, memoryview)
            assert bytes(content) == WEBSITE_FILES[info.path][0]

    def test_catfile_empty_file(self):
        """Files without data return an empty memoryview."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        info = decoder.fileinfo['backend/__init__.py']
        assert bytes(decoder.catfile(info)) == b''

    def test_catfile_copied_returns_source_content(self):
        """catfile of a copied file must return the source file content."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        for info in decoder.fileinfo.values():
            if info.edit == 0 and info.source_lookback:
                expected = WEBSITE_FILES[info.source_path][0]
                assert bytes(decoder.catfile(info)) == expected, (
                    f'copied content mismatch: {info.path}'
                )

    def test_catdata_raw(self):
        """catdata of a raw file returns the plain content."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        info = decoder.fileinfo['backend/main.py']
        data = decoder.catdata(info)
        assert isinstance(data, memoryview)
        assert bytes(data) == WEBSITE_FILES['backend/main.py'][0]

    def test_catdata_compressed(self):
        """catdata of a compressed file returns the compressed bytes."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        info = decoder.fileinfo['frontend/src/lib/styles.css']
        data = decoder.catdata(info)
        assert isinstance(data, memoryview)
        assert len(data) == info.data_size
        assert info.data_size < info.size  # compressed smaller than the file
        assert bytes(data) != WEBSITE_FILES[info.path][0]  # not the plain content

    def test_catfile_applies_eol(self):
        """catfile must apply the checkout line ending rule."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        info = decoder.fileinfo['backend/main.py']
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
        for info in decoder.fileinfo.values():
            if info.data_size:
                assert info.data_start + info.data_size <= len(decoder.data)
                assert info.data_start > len(decoder.index_section)

    def test_data_start_strictly_increasing(self):
        """Records with data must occupy continuous ranges."""
        decoder = PackDecodeBase(WEBSITE_PACK)
        prev_end = None
        for info in decoder.fileinfo.values():
            if not info.data_size or info.source_lookback:
                # copied files share the data range of their source record
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
        assert decoder.fileinfo == {}
        assert decoder.refinfo == {}
