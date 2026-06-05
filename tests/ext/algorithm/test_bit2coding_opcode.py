"""
Tests for alasio.ext.algorithm.bit2coding.

``encode_bit2_opcode_iter(data)`` encodes a 2-bit value list (values 0-3) into
opcode tuples using run-length and LZ77-style copy detection.

``decode_bit2_opcode(opcodes)`` decodes opcode tuples back to the original
list[int].
"""

import pytest

from alasio.ext.algorithm.bit2coding import decode_bit2_opcode, encode_bit2_opcode_iter


# ==============================================================================
# encode_bit2_opcode_iter — edge cases
# ==============================================================================


class TestEncodeEmptyAndSingleton:
    """Empty and single-element inputs."""

    def test_empty_data(self):
        """Empty input yields no opcodes."""
        assert list(encode_bit2_opcode_iter([])) == []

    @pytest.mark.parametrize("val", [0, 1, 2, 3])
    def test_single_element(self, val):
        """Single element always yields a literal opcode."""
        result = list(encode_bit2_opcode_iter([val]))
        assert len(result) == 1
        op_type, values = result[0]
        assert op_type == 0
        assert list(values) == [val]

    def test_two_different_elements(self):
        """Two different elements produce one literal."""
        result = list(encode_bit2_opcode_iter([1, 2]))
        assert len(result) == 1
        assert result[0][0] == 0
        assert list(result[0][1]) == [1, 2]


# ==============================================================================
# encode_bit2_opcode_iter — literal path
# ==============================================================================


class TestEncodeLiteral:
    """Cases where no run (>=4) or copy (>=3) is found → literals accumulate."""

    def test_four_distinct_values(self):
        """4 distinct values, no repeats → single literal block."""
        data = [0, 1, 2, 3]
        result = list(encode_bit2_opcode_iter(data))
        assert len(result) == 1
        assert result[0][0] == 0
        assert list(result[0][1]) == [0, 1, 2, 3]


# ==============================================================================
# encode_bit2_opcode_iter — run path
# ==============================================================================


class TestEncodeRun:
    """Cases where a run (4+ identical consecutive values) is found."""

    def test_simple_run(self):
        """4+ identical values → run opcode."""
        data = [0, 0, 0, 0, 0]
        result = list(encode_bit2_opcode_iter(data))
        assert result == [(1, 0, 5)]

    def test_run_of_all_same(self):
        """Entirely uniform data → single run opcode."""
        for val in range(4):
            data = [val] * 10
            result = list(encode_bit2_opcode_iter(data))
            assert result == [(1, val, 10)]

    def test_run_then_literal(self):
        """Run followed by non-repeating values."""
        data = [2, 2, 2, 2, 2, 0, 1, 3]
        result = list(encode_bit2_opcode_iter(data))
        assert result == [(1, 2, 5), (0, [0, 1, 3])]

    def test_multiple_runs(self):
        """Multiple run segments separated by distinct values."""
        data = [1, 1, 1, 1, 1, 3, 0, 0, 0, 0]
        result = list(encode_bit2_opcode_iter(data))
        assert result == [(1, 1, 5), (0, [3]), (1, 0, 4)]


# ==============================================================================
# encode_bit2_opcode_iter — copy path
# ==============================================================================


class TestEncodeCopy:
    """Cases where LZ77-style copy is used."""

    def test_copy_small_offset_simple(self):
        """Non-run pattern that repeats → copy opcode."""
        data = [0, 1, 2, 0, 1, 2]
        result = list(encode_bit2_opcode_iter(data))
        assert result == [(0, [0, 1, 2]), (2, 3, 3)]

    def test_copy_four_bytes(self):
        """4-byte repetition produces copy."""
        data = [0, 1, 2, 3, 0, 1, 2, 3]
        result = list(encode_bit2_opcode_iter(data))
        # At i=4: copy_len=4 at offset 4 (history matches whole 4-byte pattern).
        # The encoder flushes pending [0,1,2,3] and copies all 4 bytes.
        assert result == [(0, [0, 1, 2, 3]), (2, 4, 4)]

    def test_copy_after_literal_flush(self):
        """Copy after a literal block that's already been flushed."""
        data = [1, 2, 3, 1, 2, 3]
        result = list(encode_bit2_opcode_iter(data))
        assert result == [(0, [1, 2, 3]), (2, 3, 3)]


# ==============================================================================
# encode_bit2_opcode_iter — run vs copy decision
# ==============================================================================


class TestEncodeRunVsCopy:
    """Decision logic when both run and copy are candidates.

    The encoder prefers run unless ``copy_len > run_len + 2``.
    """

    def test_run_wins_when_copy_not_longer_enough(self):
        """When copy_len <= run_len + 2, run is preferred."""
        data = [0, 0, 0, 0, 1, 1, 1, 1, 0, 0, 0, 0, 1, 1, 1, 1]
        result = list(encode_bit2_opcode_iter(data))
        # At i=8: run_len=4 (zeros), copy_len=8 (matches data[0:8] at offset 8).
        #         8 > 4+2? Yes → copy wins over run at the 2nd half.
        assert result == [(1, 0, 4), (1, 1, 4), (2, 8, 8)]


# ==============================================================================
# decode_bit2_opcode — individual opcode types
# ==============================================================================


class TestDecodeEmpty:
    """Empty / degenerate inputs."""

    def test_no_opcodes(self):
        """Empty opcode list returns empty list."""
        assert decode_bit2_opcode([]) == []

    def test_empty_literal(self):
        """Literal opcode with empty values produces nothing."""
        result = decode_bit2_opcode([(0, [])])
        assert result == []


class TestDecodeLiteral:
    """Decoding literal opcodes."""

    @pytest.mark.parametrize("values", [
        (0,),
        (1,),
        (3, 2, 1, 0),
        (0, 0, 0, 0),
        (1, 0, 2, 3, 2),
    ])
    def test_literal_values(self, values):
        """Literal values are appended as-is."""
        expected = list(values)
        result = decode_bit2_opcode([(0, expected)])
        assert result == expected

    def test_literal_as_list(self):
        """Literal opcode works with list values (not just deque)."""
        result = decode_bit2_opcode([(0, [1, 2, 3])])
        assert result == [1, 2, 3]


class TestDecodeRun:
    """Decoding run opcodes."""

    @pytest.mark.parametrize("val, length", [
        (0, 1),
        (1, 5),
        (2, 10),
        (3, 100),
    ])
    def test_run(self, val, length):
        """Run opcode produces ``[val] * length``."""
        result = decode_bit2_opcode([(1, val, length)])
        assert result == [val] * length

    def test_multiple_runs(self):
        """Multiple runs in sequence."""
        result = decode_bit2_opcode([(1, 0, 3), (1, 1, 4)])
        assert result == [0, 0, 0, 1, 1, 1, 1]


class TestDecodeCopy:
    """Decoding copy opcodes — both simple and rolling copy."""

    def test_simple_copy(self):
        """Copy with length <= offset is a simple slice."""
        result = decode_bit2_opcode([(0, [0, 1, 2, 3]), (2, 4, 3)])
        # res = [0,1,2,3], then copy offset=4 → start=0, copy res[0:3]=[0,1,2]
        assert result == [0, 1, 2, 3, 0, 1, 2]

    def test_copy_exact_offset(self):
        """Copy with length == offset copies the entire sliced window."""
        result = decode_bit2_opcode([(0, [1, 2, 3, 4]), (2, 4, 4)])
        assert result == [1, 2, 3, 4, 1, 2, 3, 4]

    def test_rolling_copy_offset_1(self):
        """Rolling copy: offset=1 repeats a single value."""
        result = decode_bit2_opcode([(0, [0]), (2, 1, 5)])
        # pattern = [0], repeats=5, remainder=0
        assert result == [0, 0, 0, 0, 0, 0]

    def test_rolling_copy_offset_2(self):
        """Rolling copy: offset=2 with odd length extends via pattern repeat."""
        result = decode_bit2_opcode([(0, [0, 1]), (2, 2, 5)])
        # pattern = [0,1], repeats=2, remainder=1 → [0,1,0,1,0]
        assert result == [0, 1, 0, 1, 0, 1, 0]

    def test_rolling_copy_offset_3_len_8(self):
        """Rolling copy: offset=3, length=8 builds extended pattern."""
        result = decode_bit2_opcode([(0, [1, 2, 3]), (2, 3, 8)])
        # pattern = [1,2,3], repeats=2, remainder=2 → [1,2,3,1,2,3,1,2]
        assert result == [1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2]

    def test_rolling_copy_exact_multiples(self):
        """Rolling copy: length is exact multiple of offset."""
        result = decode_bit2_opcode([(0, [2, 1]), (2, 2, 6)])
        # pattern = [2,1], repeats=3, remainder=0 → [2,1,2,1,2,1]
        assert result == [2, 1, 2, 1, 2, 1, 2, 1]


class TestDecodeMixed:
    """Decoding sequences mixing all three opcode types."""

    def test_literal_then_run_then_copy(self):
        """Mixed opcodes produce the combined output."""
        opcodes = [
            (0, [0, 1]),
            (1, 2, 3),
            (2, 5, 4),
        ]
        # After literal+run: [0,1,2,2,2] (5 elements)
        # Copy: start=5-5=0, length(4) <= offset(5) → simple copy res[0:4]
        result = decode_bit2_opcode(opcodes)
        assert result == [0, 1, 2, 2, 2, 0, 1, 2, 2]

    def test_multiple_copies(self):
        """Consecutive copy opcodes build on previous output."""
        opcodes = [
            (0, [1, 2, 3]),
            (2, 3, 3),     # copy last 3, take 3 → [1,2,3]
            (2, 6, 4),     # copy last 6, take 4 → [1,2,3,1]
        ]
        result = decode_bit2_opcode(opcodes)
        assert result == [1, 2, 3, 1, 2, 3, 1, 2, 3, 1]

    def test_all_three_types(self):
        """Literal, run, and copy in one sequence."""
        opcodes = [
            (0, [1]),
            (1, 0, 3),
            (2, 4, 2),
        ]
        # After literal+run: [1,0,0,0] (4 elements)
        # Copy: start=4-4=0, take 2 → [1,0]
        result = decode_bit2_opcode(opcodes)
        assert result == [1, 0, 0, 0, 1, 0]


# ==============================================================================
# Round-trip tests — encode then decode returns the original
# ==============================================================================


class TestRoundtrip:
    """``decode_bit2_opcode(encode_bit2_opcode_iter(data)) == data``."""

    ROUNDTRIP_CASES = [
        # Empty
        [],
        # Singletons
        [0],
        [1],
        [2],
        [3],
        # Small literals (no run, no copy)
        [0, 1, 2, 3],
        [3, 2, 1, 0],
        [0, 1, 2, 3, 0, 1],
        # Pure run (single value repeated)
        [0, 0, 0, 0],
        [1, 1, 1, 1, 1],
        [2, 2, 2, 2, 2, 2],
        [3, 3, 3, 3, 3, 3, 3],
        # Alternating pattern (triggers copy)
        [0, 1, 0, 1],
        [1, 2, 1, 2],
        [0, 1, 2, 0, 1, 2],
        [0, 1, 2, 3, 0, 1, 2, 3],
        [0, 0, 1, 0, 0, 1],
        # Pattern with run inside
        [0, 0, 0, 0, 1, 2, 3],
        [0, 1, 2, 3, 3, 3, 3],
        [0, 1, 2, 3, 0, 0, 0, 0, 1, 2, 3],
        # Two runs separated by literals
        [0, 0, 0, 0, 1, 1, 1, 1],
        [2, 2, 2, 2, 3, 3, 3, 3],
        # Run then copy
        [0, 0, 0, 0, 1, 2, 0, 0, 0, 0, 1, 2],
        # Copy beats run (copy_len > run_len + 2)
        [0, 0, 0, 0, 1, 1, 1, 1, 0, 0, 0, 0, 1, 1, 1, 1],
        # Long constant run
        [2] * 50,
        [0] * 100,
        # Repeated short pattern
        [0, 1] * 10,
        [1, 2, 3] * 10,
        # Mixed patterns
        [0, 0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 3],
        [0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
        # Sawtooth
        [0, 1, 2, 3, 0, 1, 2, 3, 0, 1, 2, 3],
        # All 4 values cycling
        [0, 1, 2, 3] * 25,
        # Long literal sequence
        [i % 4 for i in range(50)],
        # Descending
        [3, 2, 1, 0, 3, 2, 1, 0],
    ]

    @pytest.mark.parametrize("data", ROUNDTRIP_CASES)
    def test_roundtrip(self, data):
        """decode(encode(data)) == data for a variety of inputs."""
        encoded = list(encode_bit2_opcode_iter(data))
        decoded = decode_bit2_opcode(encoded)
        assert decoded == data

    def test_roundtrip_large_synthetic(self):
        """Large synthetic data round-trips correctly."""
        data = []
        for i in range(2000):
            if i % 7 == 0:
                data.extend([i % 4] * 5)  # run
            else:
                data.append(i % 4)         # literal
        encoded = list(encode_bit2_opcode_iter(data))
        decoded = decode_bit2_opcode(encoded)
        assert decoded == data

    def test_deterministic(self):
        """Encoding the same input twice produces identical output."""
        data = [0, 1, 2, 3, 0, 1, 2, 3, 0, 0, 0, 0, 1, 1, 1, 1]
        assert list(encode_bit2_opcode_iter(data)) == list(encode_bit2_opcode_iter(data))


# ==============================================================================
# Large data stress test
# ==============================================================================


class TestLargeData:
    """Correctness with large inputs."""

    LARGE_CASES = [
        ([0] * 10000, "all zeros"),
        ([1, 2, 3] * 3334, "repeating pattern"),
        ([i % 4 for i in range(5000)], "cycling values"),
    ]

    @pytest.mark.parametrize("data, name", LARGE_CASES)
    def test_large_roundtrip(self, data, name):
        """Large data round-trips without error and produces correct output."""
        encoded = list(encode_bit2_opcode_iter(data))
        decoded = decode_bit2_opcode(encoded)
        assert decoded == data

    def test_no_run_opcode_for_values_ge4(self):
        """Values >= 4 should never be encoded as run opcodes.

        The run opcode format only supports 2-bit values (0-3).
        Runs of values 4-7 must fall through to the literal/copy path.
        """
        for val in [4, 5, 6, 7]:
            data = [val] * 5
            opcodes = list(encode_bit2_opcode_iter(data))
            assert not any(op[0] == 1 for op in opcodes), (
                f"Value {val} should not produce a run opcode; got {opcodes}"
            )

    def test_compression_ratio_non_trivial(self):
        """Compressed output should be smaller than input for repetitive data."""
        data = [0, 1, 2, 3] * 1000
        encoded = list(encode_bit2_opcode_iter(data))
        total_output = 0
        for op in encoded:
            if op[0] == 0:
                total_output += len(op[1])
            elif op[0] == 1:
                total_output += 2
            else:  # op[0] == 2
                total_output += 2
        assert total_output < len(data), (
            f"Encoded size ({total_output}) should be less than "
            f"input size ({len(data)}) for repetitive data"
        )
