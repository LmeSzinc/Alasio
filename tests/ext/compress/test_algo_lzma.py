import pytest

from alasio.ext.compress.algo_lzma import (
    MAX_DICT_SIZE, _lzma_dictsize, lzma2_uncompressed_size, lzma_compress, lzma_decompress
)

pytestmark = [
    pytest.mark.parallel,
]


class TestLzmaDictsize:
    """Tests for _lzma_dictsize function."""

    @pytest.mark.parametrize("length, expected", [
        # Boundary: 0 and min
        pytest.param(0, 4096, id="zero"),
        pytest.param(1, 4096, id="single-byte"),
        pytest.param(4095, 4096, id="just-below-4k"),
        pytest.param(4096, 4096, id="exactly-4k"),
        # Powers of 2
        pytest.param(4097, 8192, id="just-above-4k"),
        pytest.param(8192, 8192, id="exactly-8k"),
        pytest.param(8193, 16384, id="just-above-8k"),
        pytest.param(16384, 16384, id="exactly-16k"),
        pytest.param(16385, 32768, id="just-above-16k"),
        pytest.param(32768, 32768, id="exactly-32k"),
        pytest.param(32769, 65536, id="just-above-32k"),
        pytest.param(65536, 65536, id="exactly-64k"),
        pytest.param(65537, 131072, id="just-above-64k"),
        pytest.param(131072, 131072, id="exactly-128k"),
        pytest.param(131073, 262144, id="just-above-128k"),
        pytest.param(262144, 262144, id="exactly-256k"),
        pytest.param(262145, 524288, id="just-above-256k"),
        pytest.param(524288, 524288, id="exactly-512k"),
        pytest.param(524289, 1048576, id="just-above-512k"),
        pytest.param(1048576, 1048576, id="exactly-1m"),
        pytest.param(1048577, 2097152, id="just-above-1m"),
        pytest.param(2097152, 2097152, id="exactly-2m"),
        pytest.param(2097153, 4194304, id="just-above-2m"),
        pytest.param(4194304, 4194304, id="exactly-4m"),
        pytest.param(4194305, 8388608, id="just-above-4m"),
        pytest.param(8388608, 8388608, id="exactly-8m"),
        pytest.param(8388609, 16777216, id="just-above-8m"),
        pytest.param(16777216, 16777216, id="exactly-16m"),
        pytest.param(16777217, 33554432, id="just-above-16m"),
        pytest.param(33554432, 33554432, id="exactly-32m"),
        pytest.param(33554433, 67108864, id="just-above-32m"),
        # Max
        pytest.param(67108864, 67108864, id="exactly-64m"),
        pytest.param(67108865, 67108864, id="just-above-64m"),
        pytest.param(100000000, 67108864, id="large"),
    ])
    def test_no_cap(self, length, expected):
        """Without max_dict_size, should choose smallest power of 2 >= length."""
        assert _lzma_dictsize(length) == expected

    @pytest.mark.parametrize("length, max_dict_size, expected", [
        # max_dict_size < 4096 → cap to 4096
        pytest.param(5000, 0, 4096, id="cap-zero"),
        pytest.param(5000, 1, 4096, id="cap-one"),
        pytest.param(5000, 100, 4096, id="cap-small"),
        pytest.param(5000, 4095, 4096, id="cap-4095"),
        # max_dict_size = 4096 (power of 2)
        pytest.param(5000, 4096, 4096, id="cap-4k-power-of-2"),
        # max_dict_size not power of 2 → floor to nearest
        pytest.param(5000, 4097, 4096, id="cap-4097-floor-4k"),
        pytest.param(5000, 5000, 4096, id="cap-5000-floor-4k"),
        pytest.param(5000, 8191, 4096, id="cap-8191-floor-4k"),
        pytest.param(5000, 8192, 8192, id="cap-8k-power-of-2"),
        pytest.param(5000, 10000, 8192, id="cap-10000-floor-8k"),
        pytest.param(40000, 50000, 32768, id="cap-50000-floor-32768"),
        # max_dict_size power of 2
        pytest.param(40000, 65536, 65536, id="cap-65536-power-of-2"),
        pytest.param(100000, 65536, 65536, id="cap-65536-capped"),
        pytest.param(100000, 131072, 131072, id="cap-131072-power-of-2"),
        # max_dict_size exceeding 64MB → still cap at 64MB
        pytest.param(100000000, 134217728, 67108864, id="cap-above-64m"),
        # max_dict_size below data size
        pytest.param(1000000, 8192, 8192, id="data-exceeds-cap"),
        # max_dict_size = None → no cap
        pytest.param(5000, None, 8192, id="no-cap"),
    ])
    def test_with_cap(self, length, max_dict_size, expected):
        """With max_dict_size, should cap at nearest power of 2."""
        assert _lzma_dictsize(length, max_dict_size=max_dict_size) == expected


class TestLzmaCompress:
    """Tests for lzma_compress function."""

    @pytest.mark.parametrize("data", [
        pytest.param(b"", id="empty"),
        pytest.param(b"a", id="single-byte"),
        pytest.param(b"Hello Alasio!", id="short"),
        pytest.param(b"Hello Alasio! " * 100, id="medium"),
        pytest.param(b"Hello Alasio LZMA! " * 5000, id="large"),
        pytest.param(b"\x00\x01\x02\x03\xff\xfe\xfd\xfc" * 50, id="all-byte-values"),
        pytest.param("Hello, 世界! ".encode("utf-8") * 50, id="unicode"),
        pytest.param(b"A" * 100000, id="highly-compressible"),
    ])
    def test_compress_decompress_roundtrip(self, data):
        """Compress then decompress should return original data."""
        compressed = lzma_compress(data)
        decompressed = lzma_decompress(compressed)
        assert decompressed == data

    @pytest.mark.parametrize("data", [
        pytest.param(b"", id="empty"),
        pytest.param(b"a", id="single"),
        pytest.param(b"ab", id="two-bytes"),
        pytest.param(b"Hello", id="short-string"),
    ])
    def test_small_data_roundtrip(self, data):
        """Small data should survive roundtrip."""
        compressed = lzma_compress(data)
        decompressed = lzma_decompress(compressed)
        assert decompressed == data

    @pytest.mark.parametrize("max_dict_size", [
        pytest.param(None, id="no-cap"),
        pytest.param(4096, id="4k"),
        pytest.param(65536, id="64k"),
        pytest.param(1048576, id="1m"),
        pytest.param(67108864, id="64m"),
        pytest.param(50000, id="not-power-of-2"),
    ])
    def test_with_max_dict_size(self, max_dict_size):
        """Compress with max_dict_size should roundtrip correctly."""
        data = b"Hello Alasio LZMA! " * 200
        compressed = lzma_compress(data, max_dict_size=max_dict_size)
        decompressed = lzma_decompress(compressed)
        assert decompressed == data

    def test_compress_less_than_raw(self):
        """Compression should reduce size for compressible data."""
        data = b"Hello Alasio LZMA! " * 5000
        compressed = lzma_compress(data)
        # LZMA with preset 9 should compress this well
        assert len(compressed) < len(data)

    def test_multiple_operations(self):
        """Multiple compress/decompress operations should not interfere."""
        datasets = [
            b"First dataset with repeated content. " * 50,
            b"Second dataset with different repeated content. " * 50,
            b"Third dataset, completely different. " * 50,
        ]
        for data in datasets:
            compressed = lzma_compress(data)
            decompressed = lzma_decompress(compressed)
            assert decompressed == data


class TestLzmaMemoryview:
    """Tests for memoryview input support in compress and decompress."""

    @pytest.mark.parametrize("data", [
        pytest.param(b"", id="empty"),
        pytest.param(b"Hello Alasio!", id="short"),
        pytest.param(b"Hello Alasio LZMA! " * 500, id="medium"),
        pytest.param(b"\x00\x01\x02\x03\xff\xfe\xfd\xfc" * 50, id="all-byte-values"),
        pytest.param(b"A" * 100000, id="highly-compressible"),
    ])
    def test_compress_memoryview(self, data):
        """Compress should accept memoryview input."""
        compressed = lzma_compress(memoryview(data))
        assert lzma_decompress(compressed) == data

    @pytest.mark.parametrize("data", [
        pytest.param(b"", id="empty"),
        pytest.param(b"Hello Alasio!", id="short"),
        pytest.param(b"Hello Alasio LZMA! " * 500, id="medium"),
        pytest.param(b"\x00\x01\x02\x03\xff\xfe\xfd\xfc" * 50, id="all-byte-values"),
    ])
    def test_decompress_memoryview(self, data):
        """Decompress should accept memoryview input."""
        compressed = lzma_compress(data)
        assert lzma_decompress(memoryview(compressed)) == data

    @pytest.mark.parametrize("max_dict_size", [
        pytest.param(None, id="no-cap"),
        pytest.param(65536, id="64k"),
        pytest.param(50000, id="not-power-of-2"),
    ])
    def test_memoryview_with_max_dict_size(self, max_dict_size):
        """Compress memoryview with max_dict_size should roundtrip correctly."""
        data = b"Hello Alasio LZMA! " * 200
        compressed = lzma_compress(memoryview(data), max_dict_size=max_dict_size)
        assert lzma_decompress(memoryview(compressed)) == data

    def test_memoryview_slice(self):
        """Memoryview slice of a larger buffer should roundtrip correctly."""
        data = b"prefix" + b"Hello Alasio LZMA! " * 100 + b"suffix"
        view = memoryview(data)[6:-6]
        compressed = lzma_compress(view)
        assert lzma_decompress(memoryview(compressed)) == bytes(view)
        assert lzma_decompress(compressed) == b"Hello Alasio LZMA! " * 100


def _pseudo_random(size, seed=b'Alasio'):
    """
    Deterministic pseudo random bytes, a sha256 chain.

    Args:
        size (int): Number of bytes
        seed (bytes): Seed of the chain

    Returns:
        bytes: The bytes
    """
    import hashlib

    out = bytearray()
    digest = hashlib.sha256(seed).digest()
    while len(out) < size:
        out += digest
        digest = hashlib.sha256(digest).digest()
    return bytes(out[:size])


@pytest.fixture(scope='module')
def large_stream():
    """
    A content and its stream whose matches reach past the default dictionary.

    A raw LZMA2 decoder is given a dictionary size, and the default of 8MB
    fails this stream: the copy of the head at the end matches at a distance
    above 8MB, which only the 16MB dictionary of the stream holds.

    Returns:
        tuple[bytes, bytes]: (content, compressed stream)
    """
    data = _pseudo_random(8 * 1024 * 1024 + 1024)
    # the repeat of the head at the end, the match is further than 8MB
    data += data[:2048]
    return data, lzma_compress(data)


def _chunk_count(data):
    """
    Number of chunks of a raw LZMA2 stream, a helper of the tests.

    Args:
        data (bytes): The stream

    Returns:
        int: The chunks, the end marker excluded
    """
    count = 0
    offset = 0
    end = len(data)
    while offset < end:
        control = data[offset]
        if control == 0x00:
            return count
        if control >= 0x80:
            packed = ((data[offset + 3] << 8) | data[offset + 4]) + 1
            offset += (6 if control >= 0xC0 else 5) + packed
        else:
            packed = ((data[offset + 1] << 8) | data[offset + 2]) + 1
            offset += 3 + packed
        count += 1
    raise AssertionError('no end marker')


class TestLzma2UncompressedSize:
    """The size of a stream read from its chunk headers."""

    @pytest.mark.parametrize('data', [
        pytest.param(b'', id='empty'),
        pytest.param(b'a', id='one-byte'),
        pytest.param(b'abc' * 100, id='one-lzma-chunk'),
        pytest.param(b'\x00' * (5 * 1024 * 1024), id='large-compressible'),
        pytest.param(b'some repeated line of text\n' * 120000, id='multi-chunk'),
        pytest.param(_pseudo_random(300000), id='incompressible'),
    ])
    def test_reads_the_length(self, data):
        """The walk reports the length of the content, without a decode."""
        assert lzma2_uncompressed_size(lzma_compress(data)) == len(data)

    def test_reads_a_memoryview(self):
        """A memoryview of the stream, the shape of a pack data section."""
        data = b'abc' * 100
        compressed = lzma_compress(data)
        assert lzma2_uncompressed_size(memoryview(compressed)) == len(data)

    def test_uncompressed_chunk(self):
        """Incompressible content is stored as it is, its header names the size."""
        data = _pseudo_random(5000)
        compressed = lzma_compress(data)
        assert compressed[0] <= 0x02
        assert lzma2_uncompressed_size(compressed) == len(data)

    def test_truncated_header(self):
        """A chunk header the stream ends in the middle of is refused."""
        with pytest.raises(ValueError, match='Truncated LZMA2 chunk header'):
            lzma2_uncompressed_size(b'\xe0\x01')

    def test_invalid_control(self):
        """A control byte of no chunk kind is refused."""
        with pytest.raises(ValueError, match='Invalid LZMA2 control byte'):
            lzma2_uncompressed_size(b'\x03\x00\x00')

    def test_invalid_control_in_a_long_stream(self):
        """The control byte is refused even when the stream has bytes behind it."""
        with pytest.raises(ValueError, match='Invalid LZMA2 control byte'):
            lzma2_uncompressed_size(b'\x40' + b'\x00' * 16)

    def test_chunk_out_of_range(self):
        """A chunk that names more bytes than the stream holds is refused."""
        with pytest.raises(ValueError, match='out of range'):
            lzma2_uncompressed_size(b'\x01\x00\x63abc')

    def test_no_end_marker(self):
        """A stream without the end marker is refused."""
        # one chunk of three bytes, and no 0x00 behind it
        with pytest.raises(ValueError, match='no end marker'):
            lzma2_uncompressed_size(b'\x01\x00\x02abc')

    def test_truncated_properties(self):
        """An LZMA chunk whose sizes are past the end is refused."""
        with pytest.raises(ValueError, match='Truncated LZMA2 chunk header'):
            lzma2_uncompressed_size(b'\x80\x00')

    def test_a_multi_chunk_stream(self):
        """Every chunk of a stream of many chunks is walked, not the first only."""
        data = b'some repeated line of text\n' * 120000
        compressed = lzma_compress(data)
        assert _chunk_count(compressed) > 1
        assert lzma2_uncompressed_size(compressed) == len(data)

    def test_an_uncompressed_chunk_among_lzma_chunks(self):
        """An uncompressed chunk is skipped by its own size field."""
        # an LZMA chunk of one byte (properties present, so 5 header bytes
        # behind the control), then an uncompressed chunk of three, then the end
        compressed = b'\xe0\x00\x00\x00\x00\x5dX\x02\x00\x02abc\x00'
        assert lzma2_uncompressed_size(compressed) == 1 + 3


class TestLzmaLargeContent:
    """A content whose dictionary is larger than the default of the decoder."""

    def test_the_default_dictionary_is_too_small(self, large_stream):
        """
        The default 8MB dictionary of the raw decoder fails such a stream.

        The precondition of the class: the stream is coded with a 16MB
        dictionary, and the raw stream itself does not carry it.
        """
        import lzma

        data, compressed = large_stream
        with pytest.raises(lzma.LZMAError):
            lzma.decompress(
                compressed, format=lzma.FORMAT_RAW, filters=[{'id': lzma.FILTER_LZMA2}])

    def test_roundtrip(self, large_stream):
        """lzma_decompress decodes a stream whose matches reach past 8MB."""
        data, compressed = large_stream
        assert lzma_decompress(compressed) == data

    def test_roundtrip_with_a_known_length(self, large_stream):
        """The length of the content, the input of a pack record, decodes too."""
        data, compressed = large_stream
        assert lzma_decompress(compressed, length=len(data)) == data


class TestLzmaDictionary:
    """The dictionary of a decode is the one of the encoder."""

    def _spy(self, monkeypatch):
        """
        Record the dictionary size of every decoder the decode builds.

        Returns:
            list[int]: The sizes, in the order they were built
        """
        import lzma as lzma_module

        import alasio.ext.compress.algo_lzma as algo_lzma

        sizes = []
        original = lzma_module.decompress

        def spy(data, format=None, filters=None):
            sizes.append(filters[0]['dict_size'])
            return original(data, format=format, filters=filters)

        monkeypatch.setattr(algo_lzma.lzma, 'decompress', spy)
        return sizes

    def test_the_dictionary_is_derived_from_the_length(self, monkeypatch):
        """A small content is decoded once, with the dictionary of its length."""
        data = b'Hello Alasio! ' * 100
        sizes = self._spy(monkeypatch)
        assert lzma_decompress(lzma_compress(data)) == data
        assert sizes == [_lzma_dictsize(len(data))]

    def test_a_big_content_gets_the_dictionary_it_needs(self, monkeypatch, large_stream):
        """The dictionary follows the content, one decode and no retry."""
        data, compressed = large_stream
        sizes = self._spy(monkeypatch)
        assert lzma_decompress(compressed) == data
        assert sizes == [_lzma_dictsize(len(data))]
        # the dictionary of the stream, not the largest one of the decoder
        assert sizes[0] < MAX_DICT_SIZE

    def test_a_known_length_skips_the_walk(self, monkeypatch):
        """A caller that knows the length does not read the stream for it."""
        import alasio.ext.compress.algo_lzma as algo_lzma

        data = b'Hello Alasio! ' * 100
        compressed = lzma_compress(data)

        def fail(data):
            raise AssertionError('the size of the stream was read')

        monkeypatch.setattr(algo_lzma, 'lzma2_uncompressed_size', fail)
        assert lzma_decompress(compressed, length=len(data)) == data

    def test_a_wrong_length_falls_back(self, monkeypatch, large_stream):
        """A length the content does not have falls back to the largest dictionary."""
        data, compressed = large_stream
        declared = len(data) // 4096
        sizes = self._spy(monkeypatch)
        assert lzma_decompress(compressed, length=declared) == data
        assert sizes == [_lzma_dictsize(declared), MAX_DICT_SIZE]

    def test_a_broken_stream_raises(self):
        """Data that is not a stream is refused, never decoded into junk."""
        with pytest.raises(ValueError):
            lzma_decompress(b'\xff' * 64)
