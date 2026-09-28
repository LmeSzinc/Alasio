"""
The bit2coding encoder in C, the accelerator of the bit2 format.

Public API:

- ``encode_bit2_stream(data, ext8=False)``: the bit2 encoder, values in,
  the stream bytes of ``encode_bit2()`` out, without its VINT count prefix
- ``encode_bit2_opcode(data)``: the search alone, for tests and benchmarks
- ``check()``: load the shared library, build it on first use
- ``LIBRARY``: the library of the encoder, see _library.py

The encoder is one frozen configuration, the one ``bit2coding`` calls, so
that every caller of a given version emits the same bytes: no chain limit,
no tiebreak, the lossless prunings on. The prunings are the one thing a
caller may turn off (``lossless_prune``), and only to verify them: the test
suite compares the plain search against the pruned one. See the module
comment of bit2_encode.c for the configuration and for the alternatives
that were measured before it was frozen.
"""
import ctypes

from alasio_speedup._library import AcceleratorLibrary

# interface version this module speaks, must match BIT2_ABI_VERSION of
# bit2_encode.c, a library of another version is refused instead of being
# called with the wrong signature
ABI_VERSION = 2

# the shared library of the encoder, built on first use
LIBRARY = AcceleratorLibrary('bit2_encode', ABI_VERSION)


class Bit2Op(ctypes.Structure):
    """
    Opcode of the C encoder, keep in sync with Bit2Op in bit2_encode.c

    Attributes:
        type (int): 0 literal, 1 run, 2 copy
        a (int): literal source offset / run value / copy offset
        b (int): literal value count / run length / copy length
    """
    _fields_ = [
        ('type', ctypes.c_uint32),
        ('a', ctypes.c_uint32),
        ('b', ctypes.c_uint32),
    ]


_lib = None


def library():
    """
    Load the shared library, building it on first use and caching it.

    Returns:
        ctypes.CDLL: Loaded library with the exports declared

    Raises:
        BuildError: If the library is missing, cannot be built, or does not
            speak the expected interface version
        OSError: If the loaded library does not export the encoder
    """
    global _lib
    if _lib is None:
        _lib = LIBRARY.load()
        _bind(_lib)
    return _lib


def _bind(lib):
    """
    Declare the argument types of the exports of the library.

    Args:
        lib (ctypes.CDLL): Loaded library
    """
    lib.bit2_encode_opcodes.restype = ctypes.c_int64
    lib.bit2_encode_opcodes.argtypes = [
        ctypes.c_char_p, ctypes.c_int64, ctypes.POINTER(Bit2Op), ctypes.c_int64, ctypes.c_int64,
    ]
    lib.bit2_encode_stream.restype = ctypes.c_int64
    lib.bit2_encode_stream.argtypes = [
        ctypes.c_char_p, ctypes.c_int64, ctypes.POINTER(ctypes.c_uint8), ctypes.c_int64,
        ctypes.c_int64, ctypes.c_int64,
    ]


def check():
    """
    Load the shared library and encode a tiny input, the availability check
    of callers that have a fallback.

    Returns:
        bool: True when the accelerator is usable

    Raises:
        Exception: The build or load error, for the caller to decide
    """
    stream = encode_bit2_stream([0, 1, 2, 3, 2, 2, 2, 2, 0, 1])
    if not stream:
        raise RuntimeError('[alasio_speedup.bit2] check() encoded an empty stream')
    return True


def encode_bit2_stream(data, ext8=False, lossless_prune=True):
    """
    Encode 2 bit values into the bit2 stream, the body of ``encode_bit2()``:
    the caller stores the VINT count prefix and reads the result back with
    ``decode_bit2()``.

    The encoder is the frozen configuration (the exact byte cost DP, the
    lossless prunings, no chain limit, no tiebreak), see the module comment
    of bit2_encode.c. ``lossless_prune`` is the verification switch of the
    test suite, which proves the prunings cost not a single byte: turning
    it off runs the plain search, production callers use the default.

    Args:
        data (list[int] | bytes): Data to encode, 0~3, or 0~7 when ext8 is enabled
        ext8 (bool): True to enable ext8 support to allow 4/5/6/7 as literal values,
            the format of the data, not a tuning knob
        lossless_prune (bool): Enable the lossless prunings of the search,
            the frozen configuration, for the tests that compare against
            the plain DP

    Returns:
        list[int]: Compressed stream, one int per byte

    Raises:
        ValueError: If a value is out of range, or the encoder rejects the input
    """
    source = bytes(data)
    n = len(source)
    if n == 0:
        return []
    limit = 7 if ext8 else 3
    max_val = max(source)
    if max_val > limit:
        raise ValueError(f'[alasio_speedup.bit2] Invalid value: {max_val}, value must be <= {limit}')

    lib = library()
    # no opcode writes more bytes than the values it covers, the all literal
    # parse fits in n + 1 bytes, the margin is free and keeps the guard clear
    capacity = n + 64
    out = (ctypes.c_uint8 * capacity)()
    written = lib.bit2_encode_stream(source, n, out, capacity, int(ext8), int(lossless_prune))
    if written < 0:
        raise ValueError(f'[alasio_speedup.bit2] Encoding failed, n={n}, capacity={capacity}')
    # the buffer format of a ctypes array is not a native one, read the
    # stream back by address instead of slicing the memoryview
    return list(ctypes.string_at(ctypes.addressof(out), written))


def encode_bit2_opcode(data, lossless_prune=True):
    """
    Encode values into the opcode list with the C search alone, the packing
    of the list into bytes is ``encode_bit2_stream()``. Kept for the tests
    and the benchmarks that look at the parse itself, it runs the same
    frozen configuration as the stream encoder.

    Args:
        data (list[int] | bytes): Data to encode, 0~3 (0~7 to pack the
            result with ext8, the codes are single item literals)
        lossless_prune (bool): Enable the lossless prunings of the search,
            the frozen configuration, for the tests that compare against
            the plain DP

    Returns:
        list[tuple]: Operation code, see ``encode_bit2_opcode_iter()``
            - (0, list[int]): literal values
            - (1, int, int): run value and length
            - (2, int, int): copy offset and length

    Raises:
        ValueError: If a value is out of range, or the encoder rejects the input
    """
    source = bytes(data)
    n = len(source)
    if n == 0:
        return []
    max_val = max(source)
    if max_val > 7:
        raise ValueError(f'[alasio_speedup.bit2] Invalid value: {max_val}, value must be <= 7')

    lib = library()
    # one opcode consumes at least one value, n + 1 slots always fit
    capacity = n + 1
    ops = (Bit2Op * capacity)()
    count = lib.bit2_encode_opcodes(source, n, ops, capacity, int(lossless_prune))
    if count < 0:
        raise ValueError(f'[alasio_speedup.bit2] Encoding failed, n={n}, capacity={capacity}')

    result = []
    for op in ops[:count]:
        if op.type == 0:
            result.append((0, list(source[op.a: op.a + op.b])))
        elif op.type == 1:
            result.append((1, op.a, op.b))
        elif op.type == 2:
            result.append((2, op.a, op.b))
        else:
            raise ValueError(f'[alasio_speedup.bit2] Invalid opcode: (type={op.type}, a={op.a}, b={op.b})')
    return result
