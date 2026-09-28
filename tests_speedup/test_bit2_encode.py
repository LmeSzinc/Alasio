"""
Tests for the C bit2coding encoder, the ``bit2`` accelerator of
``alasio_speedup``.

``encode_bit2_stream()`` is the whole encoder in C: the opcode DP and the
packing of its opcodes into the stream bytes. The contract is not the byte
stream, the parse may differ from the Python encoder, the contract is the
result:

- ``decode_bit2(encode_vint(n) + bytes(encode_bit2_stream(data)))`` must
  return ``data``
- the encoded size must not be larger than the Python encoder
- the pruned and the unpruned search must emit the same size

The accelerator is optional: the tests skip when the shared library is
missing and cannot be built, a machine without a C compiler still runs the
rest of the test suite.
"""
import ctypes
import inspect
import random

import pytest

from alasio.ext.algorithm.bit2coding.bit2coding_decode import decode_bit2
from alasio.ext.algorithm.bit2coding.bit2coding_encode_c import encode_bit2
from alasio.ext.algorithm.bit2coding.bit2coding_encode_python import encode_bit2_stream_iter
from alasio.ext.algorithm.vint import encode_vint
from tests.ext.algorithm.test_bit2coding_encode import TestRoundtrip as TestRoundtripEncode
from tests.ext.algorithm.test_bit2coding_ext8 import TestRoundtripExt8
from tests.ext.algorithm.test_bit2coding_opcode import TestLargeData, TestRoundtrip as TestRoundtripOpcode

# the accelerator under test, the C encoder of the bit2 format, the module
# wraps the shared library built from bit2_encode.c
speedup = pytest.importorskip('alasio_speedup.bit2')

# the error of the first build/load attempt, None while every test passes
_encoder_error = None


@pytest.fixture(autouse=True)
def c_encoder_available():
    """Skip the tests when the C encoder is not built and cannot be built."""
    global _encoder_error
    if _encoder_error is None:
        try:
            speedup.check()
        except Exception as e:
            _encoder_error = str(e)
    if _encoder_error:
        pytest.skip(f'C bit2coding encoder is not available: {_encoder_error}')


def encode_stream(values, ext8=False, lossless_prune=True):
    """
    Encode values with the C encoder, vint count prefix included.

    Args:
        values (list[int]): Values to encode
        ext8 (bool): True to enable ext8 support to allow 4/5/6/7 as literal values
        lossless_prune (bool): Enable the lossless prunings, the frozen
            configuration, False runs the plain search

    Returns:
        bytes: Encoded data, ready for decode_bit2()
    """
    stream = speedup.encode_bit2_stream(values, ext8=ext8, lossless_prune=lossless_prune)
    return encode_vint(len(values)) + bytes(stream)


def iter_cases():
    """
    Every case of the pure Python bit2coding tests, plus the values that
    cover the format boundaries of the encoder itself.

    Yields:
        tuple[str, list[int], bool]: (label, values, ext8)
    """
    for index, data in enumerate(TestRoundtripEncode.ROUNDTRIP_CASES):
        yield f'encode#{index}', list(data), False
    for index, data in enumerate(TestRoundtripOpcode.ROUNDTRIP_CASES):
        yield f'opcode#{index}', list(data), False
    for index, (data, name) in enumerate(TestLargeData.LARGE_CASES):
        yield f'large#{index} {name}', list(data), False
    for index, data in enumerate(TestRoundtripExt8.ROUNDTRIP_CASES):
        yield f'ext8#{index}', list(data), True

    # run lengths around the run format boundaries (34|35, 290|291)
    for run in (3, 33, 34, 35, 36, 289, 290, 291, 292, 1024):
        yield f'run#{run}', [2] * run, False
        yield f'run#{run} tail', [2] * run + [0, 1, 2, 3], False
    # copy offsets around the copy format boundaries (32, 256, 257)
    for offset in (1, 2, 3, 4, 5, 32, 33, 255, 256, 257, 258, 1024):
        pattern = [(i * 3 + offset) % 4 for i in range(offset)]
        yield f'repeat#{offset}', pattern * max(2, 1024 // offset), False
    # literal lengths around the literal format boundaries (2, 3, 34, 35)
    for length in (1, 2, 3, 4, 33, 34, 35, 36, 68):
        yield f'literal#{length}', [(i * 5 + i // 7) % 4 for i in range(length)], False


def iter_fuzz_cases(rounds=100, seed=0):
    """
    Random cases with a mixed value distribution.

    Args:
        rounds (int): Number of cases
        seed (int): Seed of the generator, cases are reproducible

    Yields:
        tuple[str, list[int], bool]: (label, values, ext8)
    """
    rng = random.Random(seed)
    for index in range(rounds):
        n = rng.randrange(0, 400)
        kind = rng.choice(['random', 'run', 'period', 'sparse', 'ext8'])
        if kind == 'run':
            values = [rng.randrange(4)] * n
        elif kind == 'period':
            period = rng.randrange(1, 40)
            pattern = [rng.randrange(4) for _ in range(period)]
            values = (pattern * (n // period + 1))[:n]
        elif kind == 'sparse':
            values = [0 if rng.random() < 0.9 else rng.randrange(4) for _ in range(n)]
        elif kind == 'ext8':
            values = [rng.randrange(8) for _ in range(n)]
        else:
            values = [rng.randrange(4) for _ in range(n)]
        yield f'fuzz#{index} {kind} n={n}', values, kind == 'ext8'


CASES = list(iter_cases()) + list(iter_fuzz_cases())

case_ids = [label for label, _, _ in CASES]


class TestRoundtrip:
    """``decode_bit2(encode_bit2_stream(data))`` returns ``data``."""

    @pytest.mark.parametrize('label, values, ext8', CASES, ids=case_ids)
    def test_decode_matches(self, label, values, ext8):
        """The C encoder decodes back to the input, whatever the parse is."""
        encoded = encode_stream(values, ext8=ext8)
        decoded, read = decode_bit2(encoded, ext8=ext8)
        assert read == len(encoded)
        assert decoded == values


class TestCompression:
    """The C encoder never needs more bytes than the Python one."""

    @pytest.mark.parametrize('label, values, ext8', CASES, ids=case_ids)
    def test_not_larger_than_python(self, label, values, ext8):
        """``len(encode_bit2_stream(data)) <= len(encode_bit2(data))``."""
        encoded_py = encode_bit2(values, ext8=ext8)
        encoded_c = encode_stream(values, ext8=ext8)
        assert len(encoded_c) <= len(encoded_py), (
            f'{label}: C {len(encoded_c)}B > python {len(encoded_py)}B, '
            f'ext8={ext8}, n={len(values)}'
        )


class TestLosslessPrune:
    """
    The prunings of the C encoder must not cost a single byte.

    They drop tie updates in wide bands (same cost, different parse) and
    skip whole bands and chain entries that cannot improve any position, so
    the parse may differ, the encoded size may not. This is the switch the
    test suite keeps, see TestFrozenConfiguration: production callers run
    the pruned search only.
    """

    @pytest.mark.parametrize('label, values, ext8', CASES, ids=case_ids)
    def test_prune_keeps_the_size(self, label, values, ext8):
        """Pruned and unpruned search emit the same number of bytes."""
        plain = encode_stream(values, ext8=ext8, lossless_prune=False)
        pruned = encode_stream(values, ext8=ext8, lossless_prune=True)
        assert len(pruned) == len(plain), (
            f'{label}: pruned {len(pruned)}B vs plain {len(plain)}B, ext8={ext8}, n={len(values)}'
        )

    @pytest.mark.parametrize('label, values, ext8', CASES, ids=case_ids)
    def test_prune_decodes_the_same(self, label, values, ext8):
        """Pruned and unpruned search decode back to the input."""
        for prune in (False, True):
            encoded = encode_stream(values, ext8=ext8, lossless_prune=prune)
            decoded, read = decode_bit2(encoded, ext8=ext8)
            assert read == len(encoded)
            assert decoded == values, f'{label}: lossless_prune={prune}'

    @pytest.mark.parametrize('label, values, ext8', CASES[:40], ids=case_ids[:40])
    def test_prune_keeps_the_opcode_cost(self, label, values, ext8):
        """The opcode list of the plain search packs into the same size."""
        pruned = speedup.encode_bit2_opcode(values)
        plain = speedup.encode_bit2_opcode(values, lossless_prune=False)
        packed_pruned = bytes(encode_bit2_stream_iter(pruned, ext8=ext8))
        packed_plain = bytes(encode_bit2_stream_iter(plain, ext8=ext8))
        assert len(packed_pruned) == len(packed_plain), (
            f'{label}: pruned {len(packed_pruned)}B vs plain {len(packed_plain)}B, ext8={ext8}'
        )


class TestFrozenConfiguration:
    """
    The encoder exposes one frozen configuration and one verification switch.

    The variants of the search (the plain DP, a limited hash chain, the
    literal tiebreak) were measured while the encoder was written, none of
    them changes the encoded size, and they are not an interface: the bytes
    of an encoder version are not a call parameter, changing them is a new
    version, see the module comment of bit2_encode.c. The lossless prunings
    are the exception: the tests turn them off (``lossless_prune=False``,
    the default is the frozen value) to compare against the plain search.
    """

    def test_no_tuning_parameters(self):
        """The API takes the data, the format flag and the verification switch."""
        assert list(inspect.signature(speedup.encode_bit2_stream).parameters) == [
            'data', 'ext8', 'lossless_prune',
        ]
        assert list(inspect.signature(speedup.encode_bit2_opcode).parameters) == [
            'data', 'lossless_prune',
        ]

    def test_frozen_switches_are_rejected(self):
        """The switches that are not an interface are not accepted."""
        with pytest.raises(TypeError):
            speedup.encode_bit2_stream([0, 1, 2], max_chain=8)
        with pytest.raises(TypeError):
            speedup.encode_bit2_stream([0, 1, 2], lit_tiebreak=True)
        with pytest.raises(TypeError):
            speedup.encode_bit2_opcode([0, 1, 2], max_chain=8)

    def test_deterministic(self):
        """The same input always emits the same bytes."""
        data = [(i * 5 + i // 7) % 4 for i in range(500)] + [2] * 300
        assert speedup.encode_bit2_stream(data) == speedup.encode_bit2_stream(data)
        assert speedup.encode_bit2_opcode(data) == speedup.encode_bit2_opcode(data)


class TestStreamPacking:
    """
    The packing of the opcodes moved into C, it must stay the packing the
    Python encoder writes: same opcodes in, same bytes out.
    """

    @pytest.mark.parametrize('label, values, ext8', CASES, ids=case_ids)
    def test_packing_matches_python(self, label, values, ext8):
        """The packed stream equals the Python packer on the same opcodes."""
        opcodes = speedup.encode_bit2_opcode(values)
        packed_py = bytes(encode_bit2_stream_iter(opcodes, ext8=ext8))
        packed_c = bytes(speedup.encode_bit2_stream(values, ext8=ext8))
        assert packed_c == packed_py, f'{label}: ext8={ext8}, n={len(values)}'


class TestAbiVersion:
    """The library declares the interface the wrapper speaks."""

    def test_library_declares_the_abi(self):
        """The loaded library reports the ABI of this module, see bit2_encode.c."""
        lib = speedup.library()
        assert hasattr(lib, 'abi_version'), 'the library does not declare its ABI'
        lib.abi_version.restype = ctypes.c_int64
        lib.abi_version.argtypes = []
        assert lib.abi_version() == speedup.ABI_VERSION


class TestBoundaries:
    """The values and lengths at the edge of the format."""

    def test_empty_input(self):
        """Empty input encodes to the empty stream, with the count 0 prefix alone."""
        assert speedup.encode_bit2_stream([]) == []
        encoded = encode_stream([])
        assert encoded == b"\x00"
        assert decode_bit2(encoded) == ([], 1)

    @pytest.mark.parametrize('value', [0, 1, 2, 3])
    def test_single_value(self, value):
        """One value is one literal byte, the compact 000000XX format."""
        assert speedup.encode_bit2_stream([value]) == [value]

    def test_two_values(self):
        """Two values share one byte, the compact 0001XXYY format."""
        assert speedup.encode_bit2_stream([1, 2]) == [16 + 1 * 4 + 2]

    def test_ext8_single_values(self):
        """With ext8 on, one value 4~7 is one item byte, 000001XX."""
        for value in (4, 5, 6, 7):
            assert speedup.encode_bit2_stream([value], ext8=True) == [value]

    def test_max_run(self):
        """A run longer than the 34 value short format uses the long format."""
        # 0110XXDD: run 2 for 35 + N times, N in D + 1 little endian bytes
        stream = speedup.encode_bit2_stream([2] * 35)
        assert stream[0] == 96 + 2 * 4 + 0
        assert stream[1:3] == [0]

    def test_large_offset_copy(self):
        """A copy of a distant offset still decodes back."""
        values = [(i * 3) % 4 for i in range(300)] * 2
        encoded = encode_stream(values)
        assert decode_bit2(encoded)[0] == values


class TestValueCheck:
    """``encode_bit2_stream()`` rejects values out of range."""

    def test_value_too_large(self):
        """A value above 3 without ext8 is invalid."""
        with pytest.raises(ValueError):
            speedup.encode_bit2_stream([0, 1, 4])

    def test_value_too_large_ext8(self):
        """A value above 7 with ext8 is invalid."""
        with pytest.raises(ValueError):
            speedup.encode_bit2_stream([0, 1, 8], ext8=True)

    def test_negative_value(self):
        """A negative value is invalid."""
        with pytest.raises(ValueError):
            speedup.encode_bit2_stream([0, -1])
