"""
Tests for the C vlenint encoder of ``alasio_speedup.bit2``.

``encode_vlenint()`` is the vlenint format: the vint count prefix, the byte
lengths of the values packed with the bit2 encoder, then the values
themselves in little endian. The contract is the result, like the bit2
encoder:

- ``decode_vlenint(encode_vlenint(data))`` returns ``data``
- the encoded size must not be larger than the Python encoder

The accelerator is optional: the tests skip when the shared library is
missing and cannot be built, a machine without a C compiler still runs the
rest of the test suite.
"""
import random

import pytest

from alasio.ext.algorithm.bit2coding.vlenint_decode import decode_vlenint
from alasio.ext.algorithm.bit2coding.vlenint_encode_c import encode_vlenint
from alasio.ext.algorithm.bit2coding.vlenint_encode_python import encode_vlenint as encode_vlenint_python

bit2 = pytest.importorskip('alasio_speedup.bit2')

# the error of the first build/load attempt, None while every test passes
_encoder_error = None


@pytest.fixture(autouse=True)
def c_encoder_available():
    """Skip the tests when the C encoder is not built and cannot be built."""
    global _encoder_error
    if _encoder_error is None:
        try:
            bit2.check()
        except Exception as e:
            _encoder_error = str(e)
    if _encoder_error:
        pytest.skip(f'C bit2coding encoder is not available: {_encoder_error}')


def iter_cases():
    """
    Values that cover the length classes of the format and its boundaries.

    Yields:
        tuple[str, list[int]]: (label, values)
    """
    yield 'empty', []
    yield 'zero', [0]
    yield 'zeros', [0] * 64
    # one value per byte length class (0, 1, 2, 3, 4)
    classes = [0, 1, 255, 256, 65535, 65536, 16777215, 16777216, 4294967295]
    yield 'length classes', list(classes)
    yield 'length classes reversed', list(reversed(classes))
    yield 'length classes repeated', classes * 40
    # runs of one length class, the run and copy codes of the bit2 encoder
    for value in (0, 1, 255, 256, 65536, 16777216, 4294967295):
        yield f'run of {value}', [value] * 100
        yield f'run of {value} tail', [value] * 100 + list(classes)
    yield 'alternating', [0, 4294967295] * 200
    yield 'ascending', list(range(1000))
    yield 'descending', list(range(1000, 0, -1))


def iter_fuzz_cases(rounds=100, seed=0):
    """
    Random cases, the values of an index are sizes and offsets, so the
    magnitudes are mixed on purpose.

    Args:
        rounds (int): Number of cases
        seed (int): Seed of the generator, cases are reproducible

    Yields:
        tuple[str, list[int]]: (label, values)
    """
    rng = random.Random(seed)
    for index in range(rounds):
        n = rng.randrange(0, 400)
        kind = rng.choice(['small', 'mixed', 'large', 'sparse'])
        if kind == 'small':
            values = [rng.randrange(0, 256) for _ in range(n)]
        elif kind == 'large':
            values = [rng.randrange(0, 2 ** 32) for _ in range(n)]
        elif kind == 'sparse':
            values = [0 if rng.random() < 0.8 else rng.randrange(0, 2 ** 32) for _ in range(n)]
        else:
            values = [rng.choice([0, 1, 255, 256, 65535, 65536, 16777215, 16777216, 4294967295])
                      for _ in range(n)]
        yield f'fuzz#{index} {kind} n={n}', values


CASES = list(iter_cases()) + list(iter_fuzz_cases())

case_ids = [label for label, _ in CASES]


class TestRoundtrip:
    """``decode_vlenint(encode_vlenint(data))`` returns ``data``."""

    @pytest.mark.parametrize('label, values', CASES, ids=case_ids)
    def test_decode_matches(self, label, values):
        """The C encoder decodes back to the input, whatever the parse is."""
        encoded = encode_vlenint(values)
        decoded, read = decode_vlenint(encoded)
        assert read == len(encoded)
        assert decoded == values


class TestCompression:
    """The C encoder never needs more bytes than the Python one."""

    @pytest.mark.parametrize('label, values', CASES, ids=case_ids)
    def test_not_larger_than_python(self, label, values):
        """``len(encode_vlenint(data)) <= len(encode_vlenint_python(data))``."""
        encoded_py = encode_vlenint_python(values)
        encoded_c = encode_vlenint(values)
        assert len(encoded_c) <= len(encoded_py), (
            f'{label}: C {len(encoded_c)}B > python {len(encoded_py)}B, n={len(values)}'
        )


class TestFormat:
    """The sections of the format itself."""

    def test_empty(self):
        """No values is the count 0 alone."""
        assert encode_vlenint([]) == b'\x00'
        assert decode_vlenint(b'\x00') == ([], 1)

    def test_count_prefix(self):
        """The payload starts with the vint count of the values."""
        from alasio.ext.algorithm.vint import encode_vint

        for n in (0, 1, 127, 128, 129, 16511, 16512):
            encoded = encode_vlenint([1] * n)
            assert encoded.startswith(encode_vint(n)), f'n={n}'

    def test_values_section(self):
        """The lengths are packed as bit2, the values follow in little endian."""
        from alasio.ext.algorithm.bit2coding.bit2coding_decode import decode_bit2
        from alasio.ext.algorithm.vint import encode_vint

        values = [0, 1, 256, 65536]
        encoded = encode_vlenint(values)
        assert encoded.startswith(encode_vint(len(values)))
        # the lengths section is the bit2 encoding of the lengths (ext8, a
        # length is 0~4): the decoder of bit2 stops once it has decoded all
        # of them and tells where the values start
        payload = encode_vint(len(values)) + encoded[1:]
        lengths, read = decode_bit2(payload, ext8=True)
        assert lengths == [0, 1, 2, 3]
        assert encoded[read:] == b'\x01' + b'\x00\x01' + b'\x00\x00\x01'

    def test_deterministic(self):
        """The same input always emits the same bytes."""
        values = [0, 1, 255, 65536, 4294967295] * 50
        assert encode_vlenint(values) == encode_vlenint(values)


class TestValueCheck:
    """``encode_vlenint()`` rejects values out of range."""

    def test_negative_value(self):
        """A negative value is invalid."""
        with pytest.raises(ValueError):
            encode_vlenint([0, -1])

    def test_value_too_large(self):
        """A value above 2^32 - 1 is invalid."""
        with pytest.raises(ValueError):
            encode_vlenint([0, 2 ** 32])


class TestEncoderSwitch:
    """The module encodes with the accelerator exactly when it is available."""

    def test_encoder_of_the_environment(self):
        """The encoder falls back to Python exactly when the accelerator is missing."""
        from alasio.speedup import accelerator

        if accelerator('bit2') is not None:
            assert encode_vlenint is not encode_vlenint_python
        else:
            assert encode_vlenint is encode_vlenint_python
