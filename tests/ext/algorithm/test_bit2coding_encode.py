"""
Tests for the top-level ``encode_bit2`` and ``decode_bit2`` convenience wrappers.

These are thin wrappers around the well-tested iterator functions:
  - ``encode_bit2(data)`` → bytes
  - ``decode_bit2(data, total)`` → (list[int], int)

The underlying iterators (``encode_bit2_opcode_iter``, ``encode_bit2_stream_iter``,
``decode_bit2_stream_iter``, ``decode_bit2_opcode``) have comprehensive coverage
elsewhere; this file verifies the wrappers behave correctly end-to-end.
"""

import pytest

from alasio.ext.algorithm.bit2coding import decode_bit2, encode_bit2


# ==============================================================================
# encode_bit2 — return type and basic structure
# ==============================================================================


class TestEncodeBit2:
    """``encode_bit2(data) → bytes``."""

    def test_empty_returns_empty_bytes(self):
        """Empty input produces empty bytes."""
        assert encode_bit2([]) == b""

    def test_returns_bytes(self):
        """Result is always bytes, not list[int] or generator."""
        result = encode_bit2([1, 2, 3])
        assert isinstance(result, bytes)

    def test_single_values(self):
        """Single values encode to a single byte."""
        for val in range(4):
            result = encode_bit2([val])
            assert result == bytes([val])

    def test_two_values(self):
        """Two values use the 2-item literal format."""
        result = encode_bit2([1, 2])
        # 16 + 1*4 + 2 = 22
        assert result == bytes([22])

    def test_deterministic(self):
        """Same input produces identical bytes."""
        data = [0, 1, 2, 3, 0, 0, 0, 0, 1, 1, 1, 1]
        assert encode_bit2(data) == encode_bit2(data)


# ==============================================================================
# decode_bit2 — return type and basic structure
# ==============================================================================


class TestDecodeBit2:
    """``decode_bit2(data, total) → (list[int], int)``."""

    def test_empty_input(self):
        """Empty data (total=0) returns empty list and 0 bytes read."""
        data, read = decode_bit2(b"", 0)
        assert data == []
        assert read == 0

    def test_returns_tuple(self):
        """Result is a (list[int], int) tuple."""
        encoded = encode_bit2([1, 2, 3])
        result = decode_bit2(encoded, 3)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_decoded_data_is_list_of_ints(self):
        """First element is a list of int, each 0-3."""
        encoded = encode_bit2([0, 1, 2, 3])
        data, _ = decode_bit2(encoded, 4)
        assert isinstance(data, list)
        for v in data:
            assert isinstance(v, int)
            assert 0 <= v <= 3

    def test_read_bytes_count(self):
        """Second element is the number of bytes consumed."""
        encoded = encode_bit2([0, 1, 2, 3])
        _, read = decode_bit2(encoded, 4)
        assert read == len(encoded)

    def test_truncated_data_raises(self):
        """Not enough bytes for the requested total raises ValueError."""
        with pytest.raises(ValueError, match="Data truncated"):
            decode_bit2(b"\x00", total=10)

    def test_invalid_opcode_raises(self):
        """Byte with value 4-15 (invalid) raises ValueError."""
        with pytest.raises(ValueError, match="Invalid opcode"):
            decode_bit2(bytes([5]), total=1)


# ==============================================================================
# Round-trip: encode_bit2 + decode_bit2
# ==============================================================================


class TestRoundtrip:
    """``decode_bit2(encode_bit2(data), len(data))[0] == data``."""

    ROUNDTRIP_CASES = [
        # Empty
        [],
        # Singletons
        [0],
        [1],
        [2],
        [3],
        # Small sequences
        [0, 1, 2, 3],
        [3, 2, 1, 0],
        [0, 1, 0, 1],
        [1, 2, 1, 2],
        [0, 1, 2, 0, 1, 2],
        # Run (4+ identical)
        [0, 0, 0, 0],
        [1, 1, 1, 1, 1],
        [2, 2, 2, 2, 2, 2, 2],
        [3] * 10,
        # Mixed
        [0, 0, 0, 0, 1, 1, 1, 1],
        [0, 0, 0, 0, 1, 2, 3],
        [0, 1, 2, 3, 0, 0, 0, 0],
        # Longer runs, copies, patterns
        [0, 1] * 15,
        [1, 2, 3] * 10,
        [0, 1, 2, 3] * 25,
        # Large constant
        [2] * 50,
        [0] * 100,
        # Long literal sequence
        [i % 4 for i in range(50)],
        # Sawtooth
        [0, 1, 2, 3, 0, 1, 2, 3, 0, 1, 2, 3],
        # Descending
        [3, 2, 1, 0, 3, 2, 1, 0],
    ]

    @pytest.mark.parametrize("data", ROUNDTRIP_CASES)
    def test_roundtrip(self, data):
        """``decode_bit2(encode_bit2(data), len(data))[0] == data``."""
        encoded = encode_bit2(data)
        decoded, read = decode_bit2(encoded, len(data))
        assert decoded == data
        assert read == len(encoded)

    def test_roundtrip_large_synthetic(self):
        """Large synthetic data round-trips correctly."""
        data = []
        for i in range(2000):
            if i % 7 == 0:
                data.extend([i % 4] * 5)  # run
            else:
                data.append(i % 4)         # literal
        encoded = encode_bit2(data)
        decoded, read = decode_bit2(encoded, len(data))
        assert decoded == data
        assert read == len(encoded)

    def test_roundtrip_large_constant(self):
        """Large constant run round-trips correctly."""
        data = [3] * 5000
        encoded = encode_bit2(data)
        decoded, _ = decode_bit2(encoded, len(data))
        assert decoded == data

    def test_roundtrip_repeating_pattern(self):
        """Repeating 3-value pattern round-trips correctly."""
        data = [1, 2, 3] * 3334
        encoded = encode_bit2(data)
        decoded, _ = decode_bit2(encoded, len(data))
        assert decoded == data

    def test_roundtrip_cycling_values(self):
        """Cycling 0-3 sequence round-trips correctly."""
        data = [i % 4 for i in range(5000)]
        encoded = encode_bit2(data)
        decoded, _ = decode_bit2(encoded, len(data))
        assert decoded == data


# ==============================================================================
# decode_bit2 — partial reads (total < full length)
# ==============================================================================


class TestPartialRead:
    """Decoding with a total smaller than the full encoded stream."""

    def test_partial_read_stops_early(self):
        """When total is reached before the end of data, the rest is ignored."""
        # Use data where the first value is encoded as a separate opcode
        # from the rest: a run opcode (5 identical values) followed by a literal.
        data = [0, 0, 0, 0, 0, 1, 2, 3]
        encoded = encode_bit2(data)
        # total=5 should only decode the run (5 zeros), not the trailing literals
        decoded, read = decode_bit2(encoded, total=5)
        assert decoded == [0, 0, 0, 0, 0]
        assert read < len(encoded)  # didn't consume all bytes

    def test_large_total_small_data_raises(self):
        """Requesting more values than available raises ValueError."""
        encoded = encode_bit2([1, 2, 3])
        with pytest.raises(ValueError, match="Data truncated"):
            decode_bit2(encoded, total=100)
