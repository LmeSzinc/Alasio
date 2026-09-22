"""
Tests of the asar binary framing.

The frame of an archive is read and written in one call each: a file is read
back to its header JSON and the offset of its content, and a header JSON is
written as the bytes an archive starts with. The golden bytes come from real
archives, the binary fixtures are described in ``tests/codegen/asar/fixture.py``:

- the app bundle of the desktop client, packed by electron-builder
  (@electron/asar 3.4.1): H=7332, J=7322, content at 7340
- ``fixture.tiny_341()`` (@electron/asar 3.4.1): H=524, J=515, content at 532
"""
import pytest

from alasio.codegen.asar.errors import AsarFormatError
from alasio.codegen.asar.format import BLOCK_SIZE, MAX_HEADER_SIZE, MAX_PATH_DEPTH, UINT32_MAX, pack_header, read_header
from alasio.testing.filesystem import fs  # noqa: F401
from tests.codegen.asar import fixture

# The frame of that bundle: header pickle 7332, payload 7328, JSON 7322
APP_ASAR_FRAME = b'\x04\x00\x00\x00\xa4\x1c\x00\x00\xa0\x1c\x00\x00\x9a\x1c\x00\x00'
EMPTY_HEADER = b'{"files":{}}'


class TestConstants:
    def test_constants(self):
        """The format constants must match the reference implementation."""
        assert BLOCK_SIZE == 4 * 1024 * 1024
        assert UINT32_MAX == 2 ** 32 - 1
        assert MAX_HEADER_SIZE == 16 * 1024 * 1024
        assert MAX_PATH_DEPTH == 256


class TestPackHeader:
    def test_golden_bytes(self):
        """An empty header JSON is framed exactly as the reference writes it."""
        assert pack_header(EMPTY_HEADER) == (
            b'\x04\x00\x00\x00\x14\x00\x00\x00'      # header pickle length 20
            b'\x10\x00\x00\x00\x0c\x00\x00\x00'      # payload 16, JSON 12
            b'{"files":{}}'
        )

    def test_golden_frame_of_the_app_bundle(self):
        """The frame of the bundle is reproduced from the size of its header JSON."""
        assert pack_header(b'a' * 7322)[:16] == APP_ASAR_FRAME

    def test_padding(self):
        """The JSON is padded with zero bytes to the next 4 byte boundary."""
        data = b'1234567890123'
        assert pack_header(data) == (
            b'\x04\x00\x00\x00\x18\x00\x00\x00'      # header pickle length 24
            b'\x14\x00\x00\x00\x0d\x00\x00\x00'      # payload 20, JSON 13
            + data + b'\x00\x00\x00'
        )

    @pytest.mark.parametrize('json_size', [0, 1, 4, 5, 12, 515, 7322])
    def test_lengths(self, json_size):
        """The payload holds the 4 byte length prefix plus the aligned JSON."""
        frame = pack_header(b'a' * json_size)
        header_size = int.from_bytes(frame[4:8], 'little')
        payload = int.from_bytes(frame[8:12], 'little')
        assert header_size == 8 + ((json_size + 3) & ~3)
        assert payload == 4 + header_size - 8
        assert len(frame) == 8 + header_size
        assert frame[8 + 8:8 + 8 + json_size] == b'a' * json_size
        assert frame[8 + 8 + json_size:] == b'\x00' * (header_size - 8 - json_size)

    @pytest.mark.parametrize('json_size', [0, 1, 4, 5, 12, 515, 7322])
    def test_round_trip(self, fs, json_size):
        """What pack_header writes is what read_header reads back."""
        json_bytes = b'a' * json_size
        frame = pack_header(json_bytes)
        fs.create_file('/frame.asar', contents=frame)
        with open('/frame.asar', 'rb') as f:
            assert read_header(f) == (json_bytes, len(frame))


class TestReadHeader:
    def test_read_tiny(self, fs):
        """The header JSON and the content offset of a 3.4.1 archive."""
        fs.create_file('/tiny.asar', contents=fixture.tiny_341())
        with open('/tiny.asar', 'rb') as f:
            json_bytes, data_offset = read_header(f)
        assert data_offset == 532
        assert len(json_bytes) == 515
        assert json_bytes.startswith(b'{"files"')

    def test_leaves_the_handle_on_the_content(self, fs):
        """The handle ends on the first content byte, ready for a sequential read."""
        fs.create_file('/tiny.asar', contents=fixture.tiny_341())
        with open('/tiny.asar', 'rb') as f:
            _, data_offset = read_header(f)
            assert f.tell() == data_offset
            assert f.read() == fixture.tiny_341()[data_offset:]

    def test_reads_from_the_beginning(self, fs):
        """The handle does not have to be at the beginning of the file."""
        fs.create_file('/tiny.asar', contents=fixture.tiny_341())
        with open('/tiny.asar', 'rb') as f:
            f.seek(100)
            json_bytes, data_offset = read_header(f)
            assert (len(json_bytes), data_offset) == (515, 532)

    def test_minimal_frame(self, fs):
        """A frame without any JSON byte is accepted, the reader decides if it is usable."""
        fs.create_file('/frame.asar', contents=pack_header(b''))
        with open('/frame.asar', 'rb') as f:
            assert read_header(f) == (b'', 16)

    @pytest.mark.parametrize('content, expected', [
        (b'', 'Archive is truncated, expected 8 bytes but got 0'),
        (b'\x04', 'Archive is truncated, expected 8 bytes but got 1'),
        (b'\x04\x00\x00\x00\x0c\x02\x00', 'Archive is truncated, expected 8 bytes but got 7'),
    ])
    def test_truncated_size_pickle(self, fs, content, expected):
        """A file shorter than the leading frame is a format error."""
        fs.create_file('/broken.asar', contents=content)
        with open('/broken.asar', 'rb') as f:
            with pytest.raises(AsarFormatError) as e:
                read_header(f)
        assert str(e.value) == expected

    @pytest.mark.parametrize('payload', [0, 1, 5, 8, 12, 4294967295])
    def test_broken_size_pickle(self, fs, payload):
        """The leading frame starts with a constant payload length."""
        content = payload.to_bytes(4, 'little') + b'\x00\x00\x00\x00' + b'\x00' * 32
        fs.create_file('/broken.asar', contents=content)
        with open('/broken.asar', 'rb') as f:
            with pytest.raises(AsarFormatError) as e:
                read_header(f)
        assert str(e.value) == (
            f'Broken size pickle, expected a payload of 4 bytes, got {payload}'
        )

    @pytest.mark.parametrize('header_size, expected', [
        (0, 'Header size 0 is smaller than the 8 bytes a header pickle needs'),
        (4, 'Header size 4 is smaller than the 8 bytes a header pickle needs'),
        (32 * 1024 * 1024, 'Header size 33554432 exceeds the 16777216 bytes limit'),
    ])
    def test_header_size_limits(self, fs, header_size, expected):
        """The header length is checked before any allocation."""
        fs.create_file('/broken.asar', contents=fixture.make_archive({'files': {}}, header_size=header_size))
        with open('/broken.asar', 'rb') as f:
            with pytest.raises(AsarFormatError) as e:
                read_header(f)
        assert str(e.value) == expected

    def test_header_outside_the_file(self, fs):
        """A header that does not fit in the file is rejected."""
        content = fixture.make_archive({'files': {}})
        fs.create_file('/broken.asar', contents=content[:16])
        with open('/broken.asar', 'rb') as f:
            with pytest.raises(AsarFormatError) as e:
                read_header(f)
        assert str(e.value) == (
            f'Header size {len(content) - 8} exceeds the archive size of 16 bytes'
        )

    def test_truncated_header_pickle(self, fs):
        """A header pickle that claims more bytes than the frame gives it is rejected."""
        content = b'\x04\x00\x00\x00\x0c\x00\x00\x00' + b'\x10\x00\x00\x00\x0c\x00\x00\x00' + b'a' * 4
        fs.create_file('/broken.asar', contents=content)
        with open('/broken.asar', 'rb') as f:
            with pytest.raises(AsarFormatError) as e:
                read_header(f)
        assert str(e.value) == 'Header pickle is truncated, payload claims 16 bytes, got 8'

    @pytest.mark.parametrize('json_size, payload', [
        (13, 8),
        (100, 8),
        (7322, 7324),
    ])
    def test_json_too_large(self, fs, json_size, payload):
        """A JSON that does not fit in the payload of the header pickle is rejected."""
        frame = bytearray(pack_header(b'a' * json_size))
        frame[8:12] = payload.to_bytes(4, 'little')
        fs.create_file('/broken.asar', contents=bytes(frame))
        with open('/broken.asar', 'rb') as f:
            with pytest.raises(AsarFormatError) as e:
                read_header(f)
        assert str(e.value) == (
            f'Header JSON does not fit in the header pickle, '
            f'JSON claims {json_size} bytes, payload is {payload}'
        )
