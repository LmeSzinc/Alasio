import pytest

from alasio.ext.algorithm.vint import decode_vint, decode_vint_list, encode_vint, encode_vint_list


class TestDecodeVint:
    """Tests for decode_vint()"""

    @pytest.mark.parametrize("data, expected_val, expected_read", [
        # Single byte values
        (b'\x00', 0, 1),
        (b'\x7f', 127, 1),
        # Boundary: 128 and 129 require a second byte
        (b'\x80\x00', 128, 2),
        (b'\x80\x01', 129, 2),
        # Borrow case: 16384 = 128*128 -> 0xff, 0x00
        (b'\xff\x00', 16384, 2),
        (b'\xff\x01', 16385, 2),
        # Three-byte values
        (b'\x80\x80\x00', 16512, 3),
        (b'\x80\x80\x01', 16513, 3),
    ])
    def test_decode_values(self, data, expected_val, expected_read):
        """Parametrized decode test covering single-byte, boundary, borrow and multi-byte values."""
        val, read = decode_vint(data)
        assert val == expected_val
        assert read == expected_read

    def test_decode_bytearray_input(self):
        """decode_vint should accept bytearray as well as bytes."""
        val, read = decode_vint(bytearray(b'\x80\x00'))
        assert val == 128
        assert read == 2

    def test_decode_partial_read(self):
        """If longer data is passed, only the bytes needed are consumed."""
        val, read = decode_vint(b'\x80\x01\xff\xff\xff')
        assert val == 129
        assert read == 2


class TestEncodeVint:
    """Tests for encode_vint()"""

    @pytest.mark.parametrize("num, expected", [
        (0, b'\x00'),
        (127, b'\x7f'),
        (128, b'\x80\x00'),
        (129, b'\x80\x01'),
        # Borrow case: 16384 = 128*128 -> 0xff, 0x00
        (16384, b'\xff\x00'),
        (16385, b'\xff\x01'),
        # Three-byte values
        (16512, b'\x80\x80\x00'),
        (16513, b'\x80\x80\x01'),
        # Larger values
        (500000, b'\x9d\xc1\x20'),
        (1048576, b'\xbe\xff\x00'),   # 2^20
        (16777216, b'\x86\xfe\xff\x00'),  # 2^24
    ])
    def test_encode_values(self, num, expected):
        """Parametrized encode test covering single-byte, boundary, borrow and large values."""
        assert encode_vint(num) == expected

    @pytest.mark.parametrize("negative", [-1, -128, -129, -16384, -100000])
    def test_encode_negative_does_not_raise(self, negative):
        """encode_vint should not raise on negative input (even if result is meaningless)."""
        encoded = encode_vint(negative)
        assert isinstance(encoded, bytes)


class TestVintRoundTrip:
    """Round-trip tests: encode then decode must return the original value."""

    @pytest.mark.parametrize("value", [
        0, 1, 5, 10,
        126, 127, 128, 129, 130,
        255, 256, 257,
        16383, 16384, 16385, 16386,
        16512, 16513,
        20000, 100000, 500000,
        1048576,    # 2^20
        16777215,   # 2^24 - 1
        16777216,   # 2^24
    ])
    def test_round_trip(self, value):
        encoded = encode_vint(value)
        decoded, read = decode_vint(encoded)
        assert decoded == value
        assert read == len(encoded)

    def test_round_trip_boundary_127_128_129(self):
        """Explicitly verify the 126-130 boundary range."""
        for v in range(126, 131):
            encoded = encode_vint(v)
            decoded, read = decode_vint(encoded)
            assert decoded == v
            assert read == len(encoded)

    @pytest.mark.parametrize("value", range(0, 500))
    def test_round_trip_many_values(self, value):
        """Round-trip for 0..499 to catch off-by-one errors."""
        encoded = encode_vint(value)
        decoded, read = decode_vint(encoded)
        assert decoded == value
        assert read == len(encoded)

    @pytest.mark.parametrize("shift, offset", [
        (s, o) for s in range(7, 25) for o in (-2, -1, 0, 1, 2)
    ])
    def test_round_trip_powers_of_two(self, shift, offset):
        """Round-trip for values near powers of two (2^7 .. 2^24 +/- 2)."""
        v = (1 << shift) + offset
        if v < 0:
            pytest.skip("Negative offset makes value negative")
        encoded = encode_vint(v)
        decoded, read = decode_vint(encoded)
        assert decoded == v, f"Round-trip failed for {v} (2^{shift} + {offset})"
        assert read == len(encoded)


class TestEncodeVintList:
    """Tests for encode_vint_list()"""

    @pytest.mark.parametrize("nums, expected", [
        ([0], b'\x00'),
        ([0, 0], b'\x00\x00'),
        ([127, 128], b'\x7f\x80\x00'),
        ([128, 129], b'\x80\x00\x80\x01'),
        ([16384, 0], b'\xff\x00\x00'),
        ([1, 2, 3], b'\x01\x02\x03'),
        ([], b''),
    ])
    def test_encode_values(self, nums, expected):
        """Parametrized encode test covering single, multiple, and edge cases."""
        assert encode_vint_list(nums) == expected

    @pytest.mark.parametrize("nums", [
        [0, 1, 5, 10],
        [126, 127, 128, 129, 130],
        [16383, 16384, 16385],
        [16512, 16513],
        [500000, 1048576, 16777216],
    ])
    def test_encode_always_returns_bytes(self, nums):
        """encode_vint_list should always return bytes."""
        result = encode_vint_list(nums)
        assert isinstance(result, bytes)

    def test_large_list(self):
        """Encode a large list of values and verify length grows reasonably."""
        nums = list(range(100))
        result = encode_vint_list(nums)
        # Each value 0-99 encodes as single byte, so length should be 100
        assert len(result) == 100


class TestDecodeVintList:
    """Tests for decode_vint_list()"""

    @pytest.mark.parametrize("data, total, expected", [
        (b'\x00', 1, [0]),
        (b'\x00\x00', 2, [0, 0]),
        (b'\x7f\x80\x00', 2, [127, 128]),
        (b'\x80\x00\x80\x01', 2, [128, 129]),
        (b'\xff\x00\x00', 2, [16384, 0]),
        (b'\x01\x02\x03', 3, [1, 2, 3]),
        (b'', 0, []),
    ])
    def test_decode_values(self, data, total, expected):
        """Parametrized decode test for vint list."""
        result, _ = decode_vint_list(data, total)
        assert result == expected

    def test_decode_bytearray_input(self):
        """decode_vint_list should accept bytearray as well as bytes."""
        result, _ = decode_vint_list(bytearray(b'\x80\x00\x80\x01'), 2)
        assert result == [128, 129]

    def test_decode_extra_trailing_data(self):
        """Trailing bytes beyond total count should be ignored."""
        # \x80\x01 is 129, the trailing \xff\xff is ignored
        result, _ = decode_vint_list(b'\x80\x01\xff\xff\xff', 1)
        assert result == [129]

    def test_total_exceeds_available_data(self):
        """If total exceeds available data, only available integers are decoded."""
        result, _ = decode_vint_list(b'\x80\x01', 5)
        assert result == [129]

    def test_decode_empty_data_with_nonzero_total(self):
        """Decoding with total > 0 on empty data returns empty list."""
        result, _ = decode_vint_list(b'', 3)
        assert result == []


class TestVintListRoundTrip:
    """Round-trip tests for encode_vint_list / decode_vint_list."""

    @pytest.mark.parametrize("nums", [
        [0],
        [0, 1, 2, 3],
        [127, 128, 129],
        [16383, 16384, 16385],
        [16512, 16513, 16514],
        [500000, 1048576, 16777216],
        [],
        list(range(100)),
    ])
    def test_round_trip(self, nums):
        """Encode then decode must return the original list."""
        encoded = encode_vint_list(nums)
        result, _ = decode_vint_list(encoded, len(nums))
        assert result == nums

    @pytest.mark.parametrize("nums", [
        [10, 20, 30],
        [128, 256, 512],
        [1000, 10000, 100000],
    ])
    def test_round_trip_subset_decode(self, nums):
        """Decode only first N elements of an encoded list."""
        encoded = encode_vint_list(nums)
        result, _ = decode_vint_list(encoded, 2)
        assert result == nums[:2]

    def test_round_trip_concatenation(self):
        """Concatenating two encoded lists should decode as a single list."""
        list_a = [10, 20]
        list_b = [30, 40]
        encoded = encode_vint_list(list_a) + encode_vint_list(list_b)
        result, _ = decode_vint_list(encoded, 4)
        assert result == [10, 20, 30, 40]
