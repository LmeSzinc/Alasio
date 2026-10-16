"""
Tests for PackDecodeError: malformed packs and checksum failures.

Uses conftest.WEBSITE_FULL_PACK (a full pack of the mock modern full-stack
website) and verifies that every failure path raises PackDecodeError with
a message naming the failing section.
"""
import pytest

from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.deploy_dev.pack.pack_full import PackFull
from alasio.ext.algorithm.vint import decode_vint
from tests.deploy_dev.pack.conftest import COMMIT, WEBSITE_FULL_PACK, WEBSITE_INDEX_PACK, WEBSITE_REPO


class TestPackDecodeStructureError:
    """Structural errors during construction."""

    def test_not_a_pack(self):
        """Invalid magic must raise PackDecodeError."""
        with pytest.raises(PackDecodeError, match='header'):
            PackDecodeBase(b'XXXXgarbage')

    def test_truncated(self):
        """Truncated pack must raise PackDecodeError with the section name."""
        with pytest.raises(PackDecodeError, match='index section'):
            PackDecodeBase(WEBSITE_FULL_PACK[:100])


class TestPackDecodeVersionPart:
    """The version part holds the current version and the old version."""

    @staticmethod
    def _version_offset(pack):
        """
        Offset of the version part in a pack.

        The version part is the first part of the index section, right
        behind the index section length vint.

        Args:
            pack (bytes): Pack data

        Returns:
            int: Offset of the current version length
        """
        section = PackDecodeBase(pack).index_section
        # skip the index section length vint
        _, read = decode_vint(section)
        return 5 + read

    def test_layout(self):
        """The part is the current version, then the old version: each
        one a length and the string behind it, as two headers."""
        data = WEBSITE_FULL_PACK
        offset = self._version_offset(data)
        # current version: length then the string
        assert data[offset] == len(COMMIT)
        assert bytes(data[offset + 1:offset + 1 + len(COMMIT)]) == COMMIT.encode()
        # old version: length 0 and no string, the pack is a full pack
        assert data[offset + 1 + len(COMMIT)] == 0

    def test_decode_versions(self):
        """Both versions are decoded from an offset, the old one may be
        empty."""
        decode = PackDecodeBase._decode_versions
        assert decode(b'\x03old\x00', 0) == (['old', ''], 5)
        assert decode(b'\x03old\x03new', 0) == (['old', 'new'], 8)
        # the versions are read at the offset, not at the beginning
        assert decode(b'\xff\x03old\x03new', 1) == (['old', 'new'], 9)

    @pytest.mark.parametrize('data, offset', [
        (b'', 0),               # no current version length
        (b'\x03old', 0),        # the old version length is cut off
        (b'\x03old\x03ne', 0),  # the old version string is cut off
        (b'\x03old', 2),        # the current version length is cut off
    ])
    def test_truncated_versions_raise(self, data, offset):
        """A cut off version must raise ValueError, the decoder wraps it
        into PackDecodeError."""
        with pytest.raises(ValueError):
            PackDecodeBase._decode_versions(data, offset)


class TestPackDecodeChecksumFail:
    """Checksum validation must reject tampered data."""

    def test_index_tampered(self):
        """Modifying index bytes must fail validation."""
        data = bytearray(WEBSITE_FULL_PACK)
        data[20] ^= 0xFF  # inside index_data, structure stays parseable
        decoder = PackDecodeBase(data)
        with pytest.raises(PackDecodeError, match='index checksum'):
            decoder.validate()

    def test_data_tampered(self):
        """Modifying file data must fail validation."""
        data = bytearray(WEBSITE_FULL_PACK)
        # last file data byte is right before the data digest
        data[-21] ^= 0xFF
        decoder = PackDecodeBase(data)
        with pytest.raises(PackDecodeError, match='data checksum'):
            decoder.validate()


class TestPackDecodeErrorType:
    """PackDecodeError type and message contents."""

    def test_is_value_error(self):
        """PackDecodeError must be a ValueError subclass."""
        assert issubclass(PackDecodeError, ValueError)

    def test_index_data_length_mismatch(self):
        """A wrong value count must raise with the section name."""
        data = bytearray(WEBSITE_FULL_PACK)
        decoder = PackDecodeBase(data)

        # locate index_data inside the pack: skip index length vint,
        # the version part (the current version, then the old version)
        # and the data length part, then the index part length vint
        sec = decoder.index_section
        offset = 0
        _, read = decode_vint(sec[offset:])
        offset += read
        for _ in range(2):
            ver_len, read = decode_vint(sec[offset:])
            offset += read + ver_len
        data_len, read = decode_vint(sec[offset:])
        offset += read + data_len
        _, read = decode_vint(sec[offset:])
        offset += read
        # flip the second vint in index_data: len_fileinfo 20 -> 21,
        # so the prefix comb count check must fail
        data[5 + offset + 1] ^= 0x01

        decoder = PackDecodeBase(data)
        with pytest.raises(PackDecodeError, match='prefix comb'):
            _ = decoder.idx_info

    def test_validate_error_message(self):
        """Checksum failure must be reported as PackDecodeError."""
        data = bytearray(WEBSITE_FULL_PACK)
        data[20] ^= 0xFF
        decoder = PackDecodeBase(data)
        with pytest.raises(PackDecodeError):
            decoder.validate()

    def test_source_lookback_out_of_range(self):
        """A source lookback pointing before the first record must raise."""
        pack = PackFull(WEBSITE_REPO, commit=COMMIT)
        # tamper a C file's source_lookback to point before the first
        # record, then encode the modified records into a new pack
        for i, file in enumerate(pack.fileinfo.values()):
            if file.source_lookback:
                file.source_lookback = i + 1
                break
        data = b''.join(pack.iter_pack_data())

        decoder = PackDecodeBase(data)
        with pytest.raises(PackDecodeError, match='source lookback out of range'):
            _ = decoder.idx_info

    def test_duplicate_path(self):
        """Two records sharing a path must raise instead of overwriting."""
        pack = PackFull(WEBSITE_REPO, commit=COMMIT)
        # tamper two records to share the same path, then encode
        files = list(pack.fileinfo.values())
        files[1].path = files[0].path
        data = b''.join(pack.iter_pack_data())

        decoder = PackDecodeBase(data)
        with pytest.raises(PackDecodeError, match='duplicate path'):
            _ = decoder.fileinfo


class TestCatfileError:
    """catfile failures on unsupported data."""

    def test_catfile_zstd_patch_raises(self):
        """zstd patch data (needs the old file) must raise PackDecodeError."""
        decoder = PackDecodeBase(WEBSITE_FULL_PACK)
        info = decoder.fileinfo['backend/main.py']
        info.algo = 2
        info.source_lookback = 1
        with pytest.raises(PackDecodeError, match='requires the old file'):
            decoder.catfile(info)

    def test_catfile_unknown_algo_raises(self):
        """Unknown algo must raise PackDecodeError."""
        decoder = PackDecodeBase(WEBSITE_FULL_PACK)
        info = decoder.fileinfo['backend/main.py']
        info.algo = 99
        with pytest.raises(PackDecodeError, match='unknown algo'):
            decoder.catfile(info)


def _assert_truncations_fail(pack):
    """
    Every truncation of pack must raise PackDecodeError.

    Both validate() and the full decode path (fileinfo + catfile) must
    raise PackDecodeError for any end in range(len(pack)), never another
    exception and never succeed silently.

    Args:
        pack (bytes): Intact pack to truncate
    """
    pack = memoryview(pack)
    for end in range(len(pack)):
        truncated = pack[:end]
        try:
            PackDecodeBase(truncated).validate()
        except PackDecodeError:
            pass
        except Exception as e:
            raise AssertionError(
                f'truncate at {end}: validate raised {type(e).__name__}: {e}'
            ) from e
        else:
            raise AssertionError(f'truncate at {end}: validate did not raise')
        try:
            decoder = PackDecodeBase(truncated)
            for info in decoder.fileinfo.values():
                decoder.catfile(info)
        except PackDecodeError:
            pass
        except Exception as e:
            raise AssertionError(
                f'truncate at {end}: decode raised {type(e).__name__}: {e}'
            ) from e
        else:
            raise AssertionError(f'truncate at {end}: decode did not raise')


class TestPackDecodeTruncate:
    """Truncation at any byte must fail with PackDecodeError, never with
    another exception and never succeed silently.

    Full and index packs are tested separately because an index pack has
    no data section: validate_data and catfile raise on it by design.
    Both packs are small (1935 / 752 bytes), brute forcing every truncation
    point takes milliseconds, so no sampling is needed.
    """

    def test_full_intact(self):
        """An intact full pack must validate and decode every file."""
        PackDecodeBase(WEBSITE_FULL_PACK).validate()
        decoder = PackDecodeBase(WEBSITE_FULL_PACK)
        infos = list(decoder.fileinfo.values())
        assert infos
        for info in infos:
            decoder.catfile(info)

    def test_full_any_truncation(self):
        """Every truncation of the full pack must raise PackDecodeError."""
        _assert_truncations_fail(WEBSITE_FULL_PACK)

    def test_index_intact(self):
        """An intact index pack must validate its index and decode records.

        The index pack has no data section, so validate_data and catfile
        raise by design (covered in test_decode_index.py).
        """
        decoder = PackDecodeBase(WEBSITE_INDEX_PACK)
        decoder.validate_index()
        assert decoder.fileinfo

    def test_index_any_truncation(self):
        """Every truncation of the index pack must raise PackDecodeError."""
        _assert_truncations_fail(WEBSITE_INDEX_PACK)
