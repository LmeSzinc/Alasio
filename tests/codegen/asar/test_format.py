"""
Tests of the asar binary framing.

Golden bytes come from the real archives, see the ``frame`` fixture values in
``tests/codegen/asar/fixture.py``:

- ``webapp/release/app.asar`` (electron-builder, @electron/asar 3.4.1):
  H=7332, J=7322, header total 7340, first bytes ``04 00 00 00 a4 1c 00 00``
- ``fixture.tiny_341()`` (@electron/asar 3.4.1): H=524, J=515
"""
import pytest

from alasio.codegen.asar.errors import AsarFormatError
from alasio.codegen.asar.format import (
    BLOCK_SIZE, MAX_HEADER_SIZE, MAX_PATH_DEPTH, SIZE_PICKLE_PAYLOAD, UINT32_MAX, align4, calc_data_offset,
    calc_header_size, pack_header_pickle, pack_size_pickle, parse_header_pickle, parse_size_pickle
)

# The first 16 bytes of webapp/release/app.asar
APP_ASAR_FRAME = b'\x04\x00\x00\x00\xa4\x1c\x00\x00\xa0\x1c\x00\x00\x9a\x1c\x00\x00'


class TestConstants:
    def test_constants(self):
        """The format constants must match the reference implementation."""
        assert SIZE_PICKLE_PAYLOAD == 4
        assert BLOCK_SIZE == 4 * 1024 * 1024
        assert UINT32_MAX == 2 ** 32 - 1
        assert MAX_HEADER_SIZE == 16 * 1024 * 1024
        assert MAX_PATH_DEPTH == 256


class TestAlign4:
    @pytest.mark.parametrize('size, expected', [
        (0, 0),
        (1, 4),
        (2, 4),
        (3, 4),
        (4, 4),
        (5, 8),
        (7, 8),
        (8, 8),
        (12, 12),
        (13, 16),
        (515, 516),
        (7322, 7324),
        (7323, 7324),
        (7324, 7324),
        (7325, 7328),
        (4194304, 4194304),
        (UINT32_MAX, 4294967296),
    ])
    def test_align4(self, size, expected):
        """align4 rounds up to the next 4 byte boundary."""
        assert align4(size) == expected


class TestCalcSizes:
    @pytest.mark.parametrize('json_size, header_size, data_offset', [
        # Empty header JSON `{"files":{}}`
        (12, 20, 28),
        # tiny-341.asar
        (515, 524, 532),
        # webapp/release/app.asar
        (7322, 7332, 7340),
        # JSON already aligned, no padding is added
        (516, 524, 532),
        (520, 528, 536),
    ])
    def test_calc_sizes(self, json_size, header_size, data_offset):
        """Header size is 8 + align4(json), the data area starts at 8 + header."""
        assert calc_header_size(json_size) == header_size
        assert calc_data_offset(json_size) == data_offset
        assert data_offset == 8 + header_size

    def test_calc_sizes_are_consistent(self):
        """The two calculcations must always agree, whatever the JSON size is."""
        for json_size in range(0, 64):
            assert calc_data_offset(json_size) == 8 + calc_header_size(json_size)
            assert calc_header_size(json_size) % 4 == 0


class TestPackSizePickle:
    def test_pack_size_pickle_golden(self):
        """The size pickle is the constant uint32 4 followed by the header size."""
        assert pack_size_pickle(524) == b'\x04\x00\x00\x00\x0c\x02\x00\x00'
        assert pack_size_pickle(7332) == b'\x04\x00\x00\x00\xa4\x1c\x00\x00'
        assert pack_size_pickle(20) == b'\x04\x00\x00\x00\x14\x00\x00\x00'

    def test_pack_size_pickle_app_asar(self):
        """The size pickle of the real archive is reproduced byte for byte."""
        assert pack_size_pickle(7332) == APP_ASAR_FRAME[:8]


class TestPackHeaderPickle:
    def test_pack_header_pickle_no_padding(self):
        """A JSON of 12 bytes needs no padding."""
        assert pack_header_pickle(b'{"files":{}}') == (
            b'\x10\x00\x00\x00\x0c\x00\x00\x00{"files":{}}'
        )

    def test_pack_header_pickle_with_padding(self):
        """The JSON is padded with zero bytes to the next 4 byte boundary."""
        data = b'1234567890123'
        assert pack_header_pickle(data) == (
            b'\x14\x00\x00\x00\x0d\x00\x00\x00' + data + b'\x00\x00\x00'
        )

    @pytest.mark.parametrize('json_size, payload', [
        (0, 4),
        (1, 8),
        (4, 8),
        (5, 12),
        (515, 520),
        (7322, 7328),
    ])
    def test_pack_header_pickle_lengths(self, json_size, payload):
        """The payload holds the 4 byte string length prefix plus aligned JSON."""
        pickle = pack_header_pickle(b'a' * json_size)
        assert pickle[:4] == payload.to_bytes(4, 'little')
        assert pickle[4:8] == json_size.to_bytes(4, 'little')
        assert len(pickle) == 4 + payload
        assert len(pickle) == calc_header_size(json_size)
        assert pickle[8 + json_size:] == b'\x00' * (align4(json_size) - json_size)


class TestParseSizePickle:
    def test_parse_size_pickle(self):
        """The header length is read from the second uint32."""
        assert parse_size_pickle(b'\x04\x00\x00\x00\x0c\x02\x00\x00') == 524
        assert parse_size_pickle(APP_ASAR_FRAME[:8]) == 7332
        assert parse_size_pickle(b'\x04\x00\x00\x00\x00\x00\x00\x00') == 0

    @pytest.mark.parametrize('data', [
        b'',
        b'\x04',
        b'\x04\x00\x00\x00\x0c\x02\x00',
    ])
    def test_parse_size_pickle_truncated(self, data):
        """A truncated frame is a format error, the message names the length."""
        with pytest.raises(AsarFormatError) as e:
            parse_size_pickle(data)
        assert str(e.value) == (
            f'Archive is truncated, expected 8 bytes of size pickle, got {len(data)}'
        )

    @pytest.mark.parametrize('payload', [0, 1, 5, 8, 12, 4294967295])
    def test_parse_size_pickle_broken(self, payload):
        """A size pickle without the constant payload length is rejected."""
        with pytest.raises(AsarFormatError) as e:
            parse_size_pickle(payload.to_bytes(4, 'little') + b'\x00\x00\x00\x00')
        assert str(e.value) == (
            f'Broken size pickle, expected a payload of 4 bytes, got {payload}'
        )


class TestParseHeaderPickle:
    def test_parse_header_pickle(self):
        """The header JSON is extracted without copying the pickle."""
        pickle = pack_header_pickle(b'{"files":{}}')
        json_bytes = parse_header_pickle(pickle)
        assert bytes(json_bytes) == b'{"files":{}}'
        # A view on the input, not a copy
        assert json_bytes.obj is pickle

    def test_parse_header_pickle_padding_is_dropped(self):
        """The zero padding of the pickle is not part of the JSON."""
        pickle = pack_header_pickle(b'{"files":{"a":1}}' + b' ')
        assert bytes(parse_header_pickle(pickle)) == b'{"files":{"a":1}} '

    def test_parse_header_pickle_app_asar(self):
        """A real frame is parsed back to the same lengths."""
        pickle = pack_header_pickle(b'a' * 7322)
        assert len(pickle) == 7332
        assert len(parse_header_pickle(pickle)) == 7322

    @pytest.mark.parametrize('data', [
        b'',
        b'\x04',
        b'\x08\x00\x00\x00\x00\x00\x00',
    ])
    def test_parse_header_pickle_truncated(self, data):
        """A header pickle shorter than 8 bytes is a format error."""
        with pytest.raises(AsarFormatError) as e:
            parse_header_pickle(data)
        assert str(e.value) == (
            f'Header pickle is truncated, expected at least 8 bytes, got {len(data)}'
        )

    def test_parse_header_pickle_payload_too_small(self):
        """A payload that claims more bytes than the buffer holds is rejected."""
        with pytest.raises(AsarFormatError) as e:
            parse_header_pickle(b'\x10\x00\x00\x00\x0c\x00\x00\x00' + b'a' * 4)
        assert str(e.value) == 'Header pickle is truncated, payload claims 16 bytes, got 8'

    @pytest.mark.parametrize('json_size, payload', [
        (13, 8),
        (100, 8),
        (7322, 7324),
    ])
    def test_parse_header_pickle_json_too_large(self, json_size, payload):
        """A JSON that does not fit in the payload is rejected."""
        data = payload.to_bytes(4, 'little') + json_size.to_bytes(4, 'little')
        data += b'\x00' * (payload - 4)
        with pytest.raises(AsarFormatError) as e:
            parse_header_pickle(data)
        assert str(e.value) == (
            f'Header JSON does not fit in the header pickle, '
            f'JSON claims {json_size} bytes, payload is {payload}'
        )
