"""
Golden bytes of the C bit2 encoder.

The encoder reads back with ``decode_bit2()`` whatever parse it picks, so a
change of the search stays correct as long as the bytes it emits for a given
input stay the same: the pack of a repository is published, its index is
cached across runs, and the Python reference packs the very same index in the
tests of the pack encoder. This file pins the bytes, so an optimisation of the
search that picks another parse of the same size fails here instead of
silently changing every pack the server builds.

The cases are the shapes the search has to handle: runs of one value, the
values above 3 that only ext8 carries (a run opcode cannot), the band edges of
the copy lengths, and the low entropy sequences a real index is made of
(seeded, so every run generates the same bytes).

The hashes were taken from the encoder before the record chains were added to
the copy search, which is the reference of the format.
"""
import hashlib
import random

import pytest

bit2 = pytest.importorskip('alasio_speedup.bit2')

# the first build/load attempt, None while every test passes
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
    Yields:
        tuple[str, bytes, str]: (label, values, sha1 of the encoded stream)
    """
    # hand cases: the run opcode, its band edges, the ext8 values, and the
    # literal packing of the values a vlenint length array holds
    yield 'empty', b'', 'da39a3ee5e6b4b0d3255bfef95601890afd80709'
    yield 'single', bytes([1]), 'bf8b4530d8d246dd74ac53a13471bba17941dff7'
    yield 'literals', bytes([0, 1, 2, 3, 2, 1, 0, 3]), 'f7eef98c7cb5988414f4d3259e1e65e171dafa30'
    yield 'run 34', bytes([2]) * 34, '3fa5bfd93317ad25772680071d5ac3259cd2384f'
    yield 'run 35', bytes([2]) * 35, '0cd979583b209ce71603efc4a398e6a9efa8d872'
    yield 'run 1000', bytes([1]) * 1000, '465be47ac897a0718f0a2337818106035f5e7514'
    yield 'ext8 values', bytes([0, 4, 5, 6, 7, 4, 0, 7, 7, 7, 7]), \
        '42828126e0abdf930898946eab0cf761378132c0'
    # a run of a value a run opcode cannot carry, the run rule of the plain
    # walk must not skip those candidates
    yield 'high run 4', bytes([2, 3, 2] + [4] * 200 + [1, 0, 2]), \
        '97909f7954b8e830db34c58aba1829514bfe2d85'
    yield 'high run 5', bytes([2, 3, 2] + [5] * 200 + [1, 0, 2]), \
        'fb34befd15f8d0c6754d24497b2feb584b3ce407'
    yield 'high run 6', bytes([2, 3, 2] + [6] * 200 + [1, 0, 2]), \
        '859b9270914287a0d0dbde0e614c217ba9f8ae3b'
    yield 'high run 7', bytes([2, 3, 2] + [7] * 200 + [1, 0, 2]), \
        '0819dc4c468d7e4e5fc2456dd212381c143d08a0'

    # seeded cases: the alphabets and the patterns of a real index, the
    # generator is fixed here so every run builds the same input
    hashes = [
        '68487a54988f26c0448015fdc66afd6fffd2b8db', '33e7dfa6b92791c4ab3ede8de74c23519daa1d6b',
        '909be955232a8eaa28b74cc31dc8d0d341abc2d2', '72e3eda1655fa48ecb2dfa32feb62bca21278549',
        'fc156370715d3be0893c7e84f9067845fa84516d', '3e8c2e817ac3c02179cb21214703df29de8db246',
        '2e4203615a1017d225e067232a181b121c3c3dc6', 'f4c3932eff321835660e8625e8be503069ab2254',
        '0c2f8f09c42e0523630db5d9c5e6221b27766b9c', '3d3fb56729ff92a3ead6061280d36329cce9a185',
        '6677aa46b7ed8ed66b9708b5822fe4d2640fffd0', '5616d95f3b1972bbf9abb73dc71523452e52b422',
        '2e91cd8dce53a24390a354bc0735a52e18046d4a', '6e7e681a227f78d85833200b6e7d1f5c1c3b163c',
        '18c3a2f9cb0e68d22de307e3ed7806b09fd77dc1', 'a137b13e1a8c2670ca8f03f7c5c996bfd8fab184',
        'a2f60e37f398f7d605cfe443e89fda163b97b6d7', '690492a1fce0d15aafab7d942a1cb7f0475b3a57',
        'ed63a4405008f939ad842c1bb9ee635509e05f26', '8a95c90a74aa552300a2865c466f7787e71c943b',
        '5b35203790ca48ab68a8fb8c9a78a3b9e387f6d3', '3529fca4708551d72fdab1784203fec437363ebf',
        '8f36f74ef504b2a3f57240e06ec7078661e9c2dd', 'bdde6d591672e6626e4193e15cc72db71d4ee4c6',
    ]
    rng = random.Random(7)
    for index, digest in enumerate(hashes):
        n = rng.randrange(0, 3000)
        kind = index % 6
        if kind == 0:
            data = bytes(rng.randrange(0, 8) for _ in range(n))
        elif kind == 1:
            data = bytes(rng.randrange(0, 2) for _ in range(n))
        elif kind == 2:
            data = bytes(rng.choice([1, 2]) for _ in range(n))
        elif kind == 3:
            data = bytes(rng.choice([0, 1, 1, 1, 2, 2]) for _ in range(n))
        elif kind == 4:
            data = bytes(rng.randrange(0, 5) for _ in range(n))
        else:
            period = rng.randrange(1, 6)
            base = bytes(rng.randrange(0, 5) for _ in range(period))
            data = (base * (n // period + 1))[:n]
        yield f'fuzz{index}k{kind}', data, digest


CASES = list(iter_cases())

case_ids = [label for label, _, _ in CASES]


class TestGoldenBytes:
    """The bytes of the encoder never change, whatever the search does."""

    @pytest.mark.parametrize('label, values, digest', CASES, ids=case_ids)
    def test_stream_bytes(self, label, values, digest):
        """The encoded stream hashes to the pinned golden bytes."""
        stream = bytes(bit2.encode_bit2_stream(values, ext8=True))
        assert hashlib.sha1(stream).hexdigest() == digest, (
            f'{label}: {len(stream)} bytes, {hashlib.sha1(stream).hexdigest()}'
        )

    @pytest.mark.parametrize('label, values, digest', CASES, ids=case_ids)
    def test_plain_search_bytes(self, label, values, digest):
        """The plain search emits the same bytes as the pruned one."""
        stream = bytes(bit2.encode_bit2_stream(values, ext8=True, lossless_prune=False))
        assert hashlib.sha1(stream).hexdigest() == digest, (
            f'{label}: {len(stream)} bytes, {hashlib.sha1(stream).hexdigest()}'
        )
