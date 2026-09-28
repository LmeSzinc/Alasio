"""
bit2coding: compress a stream of 2 bit values into opcodes and bytes, the
format behind the pack index, the vlenint byte lengths and the file lists.

The package is split by role, the encoder by implementation:

- ``bit2coding_decode.py``: the decoder, the reference of the format
- ``bit2coding_encode_python.py``: the pure Python encoder
- ``bit2coding_encode_c.py``: the C encoder, used when alasio_speedup is
  installed (see ``alasio.speedup``)

``encode_bit2()`` is the only function that switches implementation: it is
the C encoder when the accelerator is installed and the Python encoder
when it is not, both decode back with ``decode_bit2()`` and the C one is
never larger. The decoder, the stream packer and the value check are
shared, the opcode iterator and the stream packer stay the Python
reference even when the accelerator is on.
"""
from alasio import speedup as _speedup
from alasio.ext.algorithm.bit2coding.bit2coding_decode import (
    decode_bit2 as decode_bit2, decode_bit2_opcode as decode_bit2_opcode,
    decode_bit2_stream_iter as decode_bit2_stream_iter
)
from alasio.ext.algorithm.bit2coding.bit2coding_encode_python import (
    _encode_literal_iter as _encode_literal_iter, _encode_value_check as _encode_value_check,
    encode_bit2_opcode_iter as encode_bit2_opcode_iter, encode_bit2_stream_iter as encode_bit2_stream_iter,
    encode_length_int as encode_length_int
)

if _speedup.accelerator('bit2') is not None:
    from alasio.ext.algorithm.bit2coding.bit2coding_encode_c import encode_bit2
else:
    from alasio.ext.algorithm.bit2coding.bit2coding_encode_python import encode_bit2 as encode_bit2
