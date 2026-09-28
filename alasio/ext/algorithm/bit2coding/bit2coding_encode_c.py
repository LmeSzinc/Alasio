"""
The C accelerated encoder, used when the alasio_speedup module is installed
(see alasio/speedup/__init__.py).

Only ``encode_bit2()`` itself is replaced: the C encoder takes the values
and returns the stream bytes, the opcode list never crosses the language
boundary, and the decoder (bit2coding_decode.py) reads the result back as
usual. The parse may differ from the Python encoder, the contract is the
same result:

- ``decode_bit2(encode_bit2(data))`` returns ``data``
- ``len(encode_bit2(data))`` is never larger than the Python one, the C
  search minimises the exact byte cost of the format and its prunings are
  lossless

See alasio_speedup/README.md for the build and the install of the module.
"""
from alasio.ext.algorithm.bit2coding.bit2coding_encode_python import _encode_value_check
from alasio.ext.algorithm.vint import encode_vint
from alasio.speedup import ERRORS, accelerator

_bit2 = accelerator('bit2')
if _bit2 is None:
    # import this module only when the accelerator is available, the
    # caller falls back to bit2coding_encode_python otherwise
    raise ImportError(
        f'[bit2coding] the bit2 accelerator is not available: {ERRORS.get("bit2")}, '
        'use the Python encoder'
    )


def encode_bit2(data, ext8=False):
    """
    Encode data to bit2 format with a vint count prefix (see encode_vint):
    [count]: number of values
    [stream]: bit2 compressed values, written by the C encoder

    Args:
        data (list[int] | deque[int]): Data to encode
        ext8 (bool): True to enable ext8 support to allow 4/5/6/7 as literal values

    Returns:
        bytes: Encoded data
    """
    _encode_value_check(data, ext8)
    return encode_vint(len(data)) + bytes(_bit2.encode_bit2_stream(data, ext8=ext8))
