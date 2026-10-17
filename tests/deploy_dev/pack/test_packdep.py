"""
Tests for .pack/packdep.pack: the dependency snapshot of a repo version.

The format is a metadata file of the pack area, not a pack: a header
(PACKDEP_MAGIC + one byte version) and a msgpack array of PackDepObj. The
encoder is on the pack server side, the decoder is shared with the client.
"""
import msgspec
import pytest

from alasio.deploy.pack.decode_base import PackDecodeError
from alasio.deploy.pack.packdep import PACKDEP_MAGIC, PACKDEP_PATH, PACKDEP_VERSION, PackDepObj, decode_packdep
from alasio.deploy_dev.pack.encode_packdep import encode_packdep

CHECKSUM_A = bytes(range(20))
CHECKSUM_B = bytes(range(20, 40))


class TestEncode:
    """Encoding of the dependency snapshots."""

    def test_empty(self):
        """An empty mapping encodes the header and an empty msgpack array."""
        data = encode_packdep({})
        assert data[:4] == PACKDEP_MAGIC
        assert data[4] == PACKDEP_VERSION
        assert decode_packdep(data) == {}

    def test_roundtrip(self):
        """The entries decode back to the same name, version and checksum."""
        data = encode_packdep({
            'demo': ('1.0', CHECKSUM_A),
            'other-dist': ('2.0', CHECKSUM_B),
        })
        entries = decode_packdep(data)
        assert list(entries) == ['demo', 'other-dist']
        assert entries['demo'].version == '1.0'
        assert entries['demo'].index_checksum == CHECKSUM_A
        assert entries['other-dist'].version == '2.0'
        assert entries['other-dist'].index_checksum == CHECKSUM_B

    def test_order_is_deterministic(self):
        """The bytes only depend on the entries, not on the mapping order."""
        first = encode_packdep({'b': ('1.0', CHECKSUM_A), 'a': ('2.0', CHECKSUM_B)})
        second = encode_packdep({'a': ('2.0', CHECKSUM_B), 'b': ('1.0', CHECKSUM_A)})
        assert first == second
        assert list(decode_packdep(first)) == ['a', 'b']

    @pytest.mark.parametrize('name', ['', '../x', 'a/b', 'CON', 'a b', 'Demo', 'a..b'])
    def test_invalid_name(self, name):
        """A name that is not a safe, PEP 503 normalized path component is refused."""
        with pytest.raises(ValueError):
            encode_packdep({name: ('1.0', CHECKSUM_A)})

    @pytest.mark.parametrize('version', ['', '../1.0', '1.0/2', 'CON'])
    def test_invalid_version(self, version):
        """A version that is not a safe path component is refused."""
        with pytest.raises(ValueError):
            encode_packdep({'demo': (version, CHECKSUM_A)})

    @pytest.mark.parametrize('checksum', [b'', b'x' * 19, b'x' * 21, 'x' * 20])
    def test_invalid_checksum(self, checksum):
        """A checksum that is not 20 bytes is refused."""
        with pytest.raises(ValueError):
            encode_packdep({'demo': ('1.0', checksum)})


class TestDecode:
    """Decoding and validation of the file."""

    def test_path(self):
        """The file lives directly under the pack area."""
        assert PACKDEP_PATH == '.pack/packdep.pack'

    @pytest.mark.parametrize('data, message', [
        (b'', '5 bytes header'),
        (b'PD', '5 bytes header'),
        (b'PACK\x00', 'not a packdep file'),
        (b'PDEP\x01', 'unsupported format version'),
    ])
    def test_invalid_header(self, data, message):
        """A malformed header is a decode error with the reason."""
        with pytest.raises(PackDecodeError, match=message):
            decode_packdep(data)

    def test_invalid_payload(self):
        """A payload that is not a msgpack array of the struct is refused."""
        with pytest.raises(PackDecodeError, match='Failed to decode packdep entries'):
            decode_packdep(PACKDEP_MAGIC + bytes((PACKDEP_VERSION,)) + b'\xc1')

    def test_invalid_checksum_length(self):
        """An entry checksum that is not 20 bytes is refused."""
        data = PACKDEP_MAGIC + bytes((PACKDEP_VERSION,)) + msgspec.msgpack.encode([
            PackDepObj(name='demo', version='1.0', index_checksum=b'x' * 19),
        ])
        with pytest.raises(PackDecodeError, match='expected 20'):
            decode_packdep(data)

    @pytest.mark.parametrize('name, version', [
        ('../demo', '1.0'),
        ('demo', '../1.0'),
        ('', '1.0'),
    ])
    def test_invalid_entry_path(self, name, version):
        """An entry name or version that is not a safe path component is refused."""
        data = PACKDEP_MAGIC + bytes((PACKDEP_VERSION,)) + msgspec.msgpack.encode([
            PackDepObj(name=name, version=version, index_checksum=CHECKSUM_A),
        ])
        with pytest.raises(PackDecodeError, match='Failed to decode packdep entry'):
            decode_packdep(data)

    def test_duplicate_name(self):
        """Two entries of the same dependency make the file unusable."""
        data = PACKDEP_MAGIC + bytes((PACKDEP_VERSION,)) + msgspec.msgpack.encode([
            PackDepObj(name='demo', version='1.0', index_checksum=CHECKSUM_A),
            PackDepObj(name='demo', version='2.0', index_checksum=CHECKSUM_B),
        ])
        with pytest.raises(PackDecodeError, match='duplicate dependency'):
            decode_packdep(data)

    def test_checksum_is_kept_as_bytes(self):
        """The 20 bytes checksum is not converted to text."""
        data = encode_packdep({'demo': ('1.0', CHECKSUM_A)})
        assert decode_packdep(data)['demo'].index_checksum == CHECKSUM_A
