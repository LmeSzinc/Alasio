"""
Tests for PackDecodeBase: full pack decode and validation.

Uses conftest.website_repo (a mock modern full-stack website) to build a
full pack with PackFull, then verifies the decode side restores every
record type: A/C/D edits, eol 0/1/2, algo 0/1, empty files, sha1s and
the data section via data_start / data_size.
"""
import lzma

import pytest
from conftest import COMMIT, WEBSITE_FILES

from alasio.deploy.decode_base import PackDecodeBase, PackDecodeError
from alasio.deploy_dev.pack.pack_repo import PackFull
from alasio.ext.compress.algo_lzma import _lzma_dictsize


def _pack_bytes(website_repo):
    """
    Encode a full pack from the website repo.

    Args:
        website_repo (MockGitRepo): Fixture repo

    Returns:
        bytes: Full pack file content
    """
    pack = PackFull(website_repo, commit=COMMIT)
    return b''.join(pack.iter_pack_data())


def _decode_file(decoder, info):
    """
    Extract and decompress the content of an IdxInfo from the pack.

    Args:
        decoder (PackDecodeBase): Decoder holding the pack bytes
        info (IdxInfo): Record with data_start / data_size / algo

    Returns:
        bytes: Original file content
    """
    chunk = bytes(decoder.data[info.data_start:info.data_start + info.data_size])
    if info.algo == 0:
        return chunk
    filters = [{
        'id': lzma.FILTER_LZMA2,
        'dict_size': _lzma_dictsize(info.size),
        'preset': 9,
        'nice_len': 273,
        'mf': lzma.MF_BT4,
    }]
    return lzma.decompress(chunk, format=lzma.FORMAT_RAW, filters=filters)


class TestPackDecodeBasic:
    """Basic structure decode."""

    def test_validate_passes(self, website_repo):
        """A well-formed pack must validate."""
        decoder = PackDecodeBase(_pack_bytes(website_repo))
        decoder.validate()  # must not raise

    def test_header(self, website_repo):
        """Header magic and pack version must be decoded."""
        decoder = PackDecodeBase(_pack_bytes(website_repo))
        assert decoder.pack_version == b'\x00'
        assert decoder.version == COMMIT

    def test_sections_are_memoryview(self, website_repo):
        """index_section / data_section must be memoryview slices."""
        decoder = PackDecodeBase(_pack_bytes(website_repo))
        assert isinstance(decoder.index_section, memoryview)
        assert isinstance(decoder.data_section, memoryview)
        assert len(decoder.index_section) > 0
        assert len(decoder.data_section) > 0

    def test_refinfo_empty_in_full_pack(self, website_repo):
        """Full pack has no refinfo, all files are in fileinfo."""
        decoder = PackDecodeBase(_pack_bytes(website_repo))
        assert decoder.refinfo == []
        assert len(decoder.fileinfo) == len(WEBSITE_FILES) + 1  # + D marker

    def test_not_a_pack(self):
        """Invalid magic must raise PackDecodeError."""
        with pytest.raises(PackDecodeError, match='header'):
            PackDecodeBase(b'XXXXgarbage')

    def test_truncated(self, website_repo):
        """Truncated pack must raise PackDecodeError with the section name."""
        data = _pack_bytes(website_repo)
        with pytest.raises(PackDecodeError, match='index section'):
            PackDecodeBase(data[:100])


class TestPackDecodeValidateFail:
    """Checksum validation must reject tampered data."""

    def test_index_tampered(self, website_repo):
        """Modifying index bytes must fail validation."""
        data = bytearray(_pack_bytes(website_repo))
        data[20] ^= 0xFF  # inside index_data, structure stays parseable
        decoder = PackDecodeBase(data)
        with pytest.raises(PackDecodeError, match='index checksum'):
            decoder.validate()

    def test_data_tampered(self, website_repo):
        """Modifying file data must fail validation."""
        data = bytearray(_pack_bytes(website_repo))
        # last file data byte is right before the data digest
        data[-21] ^= 0xFF
        decoder = PackDecodeBase(data)
        with pytest.raises(PackDecodeError, match='data checksum'):
            decoder.validate()


class TestPackDecodeError:
    """PackDecodeError must carry the failing section in its message."""

    def test_is_value_error(self):
        """PackDecodeError must be a ValueError subclass."""
        assert issubclass(PackDecodeError, ValueError)

    def test_index_data_length_mismatch(self, website_repo):
        """A wrong value count must raise with the section name."""
        from alasio.ext.algorithm.vint import decode_vint

        data = bytearray(_pack_bytes(website_repo))
        decoder = PackDecodeBase(data)

        # locate index_data inside the pack: skip index length vint and
        # the version part, then the index part length vint
        sec = decoder.index_section
        offset = 0
        _, read = decode_vint(sec[offset:])
        offset += read
        ver_len, read = decode_vint(sec[offset:])
        offset += read + ver_len
        _, read = decode_vint(sec[offset:])
        offset += read
        # flip the second vint in index_data: len_fileinfo 20 -> 21,
        # so the prefix comb count check must fail
        data[5 + offset + 1] ^= 0x01

        decoder = PackDecodeBase(data)
        with pytest.raises(PackDecodeError, match='prefix comb'):
            decoder.idx_info

    def test_validate_error_message(self, website_repo):
        """Checksum failure must be reported as PackDecodeError."""
        data = bytearray(_pack_bytes(website_repo))
        data[20] ^= 0xFF
        decoder = PackDecodeBase(data)
        with pytest.raises(PackDecodeError):
            decoder.validate()


class TestPackDecodeRoundtrip:
    """Decoded records must match the encoder side field by field."""

    def test_fields_match(self, website_repo):
        """Every field of every file must roundtrip."""
        pack = PackFull(website_repo, commit=COMMIT)
        decoder = PackDecodeBase(b''.join(pack.iter_pack_data()))
        decoder.validate()

        enc_files = list(pack.fileinfo.values())
        dec_files = decoder.fileinfo
        assert len(enc_files) == len(dec_files)
        for enc, dec in zip(enc_files, dec_files):
            for field in ('path', 'edit', 'eol', 'mode', 'algo', 'size',
                          'data_size', 'source_lookback'):
                assert getattr(enc, field) == getattr(dec, field), (
                    f'{field} mismatch for {enc.path}: {getattr(enc, field)!r} != {getattr(dec, field)!r}'
                )
            # sha1: A files carry it in the pack; C files resolve via source
            if enc.edit != 2 and not (enc.edit == 0 and enc.source_lookback):
                assert enc.sha1 == dec.sha1, f'sha1 mismatch for {enc.path}'

    def test_edit_types_covered(self, website_repo):
        """Full pack must cover A, C and D edits."""
        decoder = PackDecodeBase(_pack_bytes(website_repo))
        paths = {info.path: info for info in decoder.fileinfo}
        # C (copied) files with correct lookback chains
        assert paths['backend/utils.py'].edit == 0
        assert paths['backend/utils.py'].source_lookback > 0
        assert paths['frontend/src/lib/Button.svelte'].source_lookback > 0
        assert paths['scripts/run.sh'].source_lookback > 0
        # D (deleted marker) for folder without __init__.py
        assert paths['backend/tools/__init__.py'].edit == 2

    def test_copied_content_matches_source(self, website_repo):
        """C files must reference a file with identical content."""
        decoder = PackDecodeBase(_pack_bytes(website_repo))
        files = decoder.fileinfo
        for i, info in enumerate(files):
            if info.edit == 0 and info.source_lookback:
                source = files[i - info.source_lookback]
                assert WEBSITE_FILES[info.path][0] == WEBSITE_FILES[source.path][0]

    def test_eol_covered(self, website_repo):
        """eol 0/1/2 must be covered."""
        decoder = PackDecodeBase(_pack_bytes(website_repo))
        by_eol = {info.path: info.eol for info in decoder.fileinfo}
        assert by_eol['backend/requirements.txt'] == 1  # CRLF via gitattributes
        assert by_eol['backend/static/logo.png'] == 2  # binary
        assert by_eol['backend/main.py'] == 0  # LF

    def test_algo_covered(self, website_repo):
        """algo 0 (raw) and 1 (lzma) must be covered."""
        decoder = PackDecodeBase(_pack_bytes(website_repo))
        algos = {info.path: (info.algo, info.size, info.data_size)
                 for info in decoder.fileinfo}
        assert algos['frontend/src/lib/styles.css'][0] == 1
        assert algos['frontend/src/lib/styles.css'][1] > algos['frontend/src/lib/styles.css'][2]
        assert algos['backend/main.py'][0] == 0
        assert algos['backend/main.py'][1] == algos['backend/main.py'][2]

    def test_empty_file(self, website_repo):
        """Empty files have size 0, no sha1 and no data."""
        decoder = PackDecodeBase(_pack_bytes(website_repo))
        info = next(i for i in decoder.fileinfo if i.path == 'backend/__init__.py')
        assert info.edit == 0
        assert info.size == 0
        assert info.data_size == 0
        assert info.sha1 == ''
        assert info.data_start == 0


class TestPackDecodeData:
    """Data section extraction via data_start / data_size."""

    def test_all_contents_extract(self, website_repo):
        """Every file with data must restore its original content."""
        decoder = PackDecodeBase(_pack_bytes(website_repo))
        for info in decoder.fileinfo:
            if not info.data_size:
                continue
            content = _decode_file(decoder, info)
            assert content == WEBSITE_FILES[info.path][0], f'content mismatch: {info.path}'

    def test_data_start_is_file_offset(self, website_repo):
        """data_start must be an offset into the pack file."""
        decoder = PackDecodeBase(_pack_bytes(website_repo))
        for info in decoder.fileinfo:
            if info.data_size:
                assert info.data_start + info.data_size <= len(decoder.data)
                assert info.data_start > len(decoder.index_section)

    def test_data_start_strictly_increasing(self, website_repo):
        """Records with data must occupy continuous ranges."""
        decoder = PackDecodeBase(_pack_bytes(website_repo))
        prev_end = None
        for info in decoder.fileinfo:
            if not info.data_size:
                continue
            if prev_end is not None:
                assert info.data_start == prev_end
            prev_end = info.data_start + info.data_size


class TestPackDecodeCached:
    """idx_info must be computed lazily and cached."""

    def test_lazy(self, website_repo):
        """Constructing the decoder must not decode idx_info."""
        decoder = PackDecodeBase(_pack_bytes(website_repo))
        assert '_len_refinfo' not in decoder.__dict__

    def test_cached(self, website_repo):
        """Repeated access must return the same list object."""
        decoder = PackDecodeBase(_pack_bytes(website_repo))
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
