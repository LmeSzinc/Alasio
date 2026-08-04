"""
Tests for PackDecodeError: malformed packs and checksum failures.

Uses conftest.website_pack (a full pack of the mock modern full-stack
website) and verifies that every failure path raises PackDecodeError with
a message naming the failing section.
"""
import pytest

from alasio.deploy.decode_base import PackDecodeBase, PackDecodeError


class TestPackDecodeStructureError:
    """Structural errors during construction."""

    def test_not_a_pack(self):
        """Invalid magic must raise PackDecodeError."""
        with pytest.raises(PackDecodeError, match='header'):
            PackDecodeBase(b'XXXXgarbage')

    def test_truncated(self, website_pack):
        """Truncated pack must raise PackDecodeError with the section name."""
        with pytest.raises(PackDecodeError, match='index section'):
            PackDecodeBase(website_pack[:100])


class TestPackDecodeChecksumFail:
    """Checksum validation must reject tampered data."""

    def test_index_tampered(self, website_pack):
        """Modifying index bytes must fail validation."""
        data = bytearray(website_pack)
        data[20] ^= 0xFF  # inside index_data, structure stays parseable
        decoder = PackDecodeBase(data)
        with pytest.raises(PackDecodeError, match='index checksum'):
            decoder.validate()

    def test_data_tampered(self, website_pack):
        """Modifying file data must fail validation."""
        data = bytearray(website_pack)
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

    def test_index_data_length_mismatch(self, website_pack):
        """A wrong value count must raise with the section name."""
        from alasio.ext.algorithm.vint import decode_vint

        data = bytearray(website_pack)
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

    def test_validate_error_message(self, website_pack):
        """Checksum failure must be reported as PackDecodeError."""
        data = bytearray(website_pack)
        data[20] ^= 0xFF
        decoder = PackDecodeBase(data)
        with pytest.raises(PackDecodeError):
            decoder.validate()


class TestCatfileError:
    """catfile failures on unsupported data."""

    def test_catfile_zstd_patch_raises(self, website_pack):
        """zstd patch data (needs the old file) must raise PackDecodeError."""
        decoder = PackDecodeBase(website_pack)
        info = next(i for i in decoder.fileinfo if i.path == 'backend/main.py')
        info.algo = 2
        info.source_lookback = 1
        with pytest.raises(PackDecodeError, match='requires the old file'):
            decoder.catfile(info)

    def test_catfile_unknown_algo_raises(self, website_pack):
        """Unknown algo must raise PackDecodeError."""
        decoder = PackDecodeBase(website_pack)
        info = next(i for i in decoder.fileinfo if i.path == 'backend/main.py')
        info.algo = 99
        with pytest.raises(PackDecodeError, match='unknown algo'):
            decoder.catfile(info)
