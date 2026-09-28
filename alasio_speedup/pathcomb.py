"""
The path combination encoder in C, the accelerator of the filepath section
of a pack index.

Public API:

- ``encode_path_comb(paths)``: the filepath section of an index in one
  pass, the values of ``iter_path_comb()`` combined by
  ``encode_prefix_comb()`` and ``encode_suffix_comb()`` of
  alasio.ext.algorithm.pathcomb
- ``encode_prefix_comb(prefix_reuse, path_length)``: the combined prefix
  encoding alone
- ``encode_suffix_comb(suffix_reuse, suffix_lookback)``: the combined
  suffix encoding alone
- ``PARAMS``: the frozen parameters of the pack format this encoder speaks
- ``check()``: load the shared library, build it on first use
- ``LIBRARY``: the library of the encoder, see _library.py

The encoder is one frozen configuration, the one ``alasio.ext.algorithm``
encodes a pack with, and it emits exactly what the Python reference emits,
byte for byte: the two are interchangeable, they are compared on every
branch of the search by tests_speedup/test_pathcomb.py. The parameters are
the pack format, not a knob of the call: a library that reports other ones
is refused by ``check()``, because it would silently emit other bytes.
"""
import array
import ctypes

from alasio_speedup._library import AcceleratorLibrary

# interface version this module speaks, must match PATHCOMB_ABI_VERSION of
# pathcomb.c, a library of another version is refused instead of being
# called with the wrong signature
ABI_VERSION = 2

# the frozen parameters of the pack format, in the order of the
# pathcomb_params() export: MAX_PREFIX_REUSE, MIN_SUFFIX_REUSE,
# MAX_SUFFIX_REUSE, MAX_SUFFIX_LOOKBACK of the encoder
PARAMS = (32639, 3, 65535, 255)

# the shared library of the encoder, built on first use
LIBRARY = AcceleratorLibrary('pathcomb', ABI_VERSION)

# error codes of the C encoder, see pathcomb.c
ERR_ARGUMENT = -1
ERR_RANGE = -2
ERR_CAPACITY = -3

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
    lib.pathcomb_params.restype = ctypes.c_int64
    lib.pathcomb_params.argtypes = [ctypes.POINTER(ctypes.c_int64)]
    lib.pathcomb_encode_paths.restype = ctypes.c_int64
    lib.pathcomb_encode_paths.argtypes = [
        ctypes.c_char_p, ctypes.c_int64, ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint8), ctypes.c_int64,
    ]
    for name in ('pathcomb_encode_prefix_comb', 'pathcomb_encode_suffix_comb'):
        getattr(lib, name).restype = ctypes.c_int64
        getattr(lib, name).argtypes = [
            ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_int64, ctypes.POINTER(ctypes.c_uint32),
        ]


def check():
    """
    Load the shared library, check the parameters it was built with and
    encode a tiny pair of paths and the two combined encodings alone, the
    availability check of callers that have a fallback.

    Returns:
        bool: True when the accelerator is usable

    Raises:
        Exception: The build or load error, for the caller to decide
    """
    params = (ctypes.c_int64 * len(PARAMS))()
    count = library().pathcomb_params(params)
    if count != len(PARAMS) or tuple(params) != PARAMS:
        raise RuntimeError(
            f'[alasio_speedup.pathcomb] the library speaks the pack format {tuple(params)}, '
            f'this module needs {PARAMS}'
        )
    result = encode_path_comb(['a/1.png', 'b/2.png'])
    if result != ([7, 3], [0, 65], b'a/1.pngb/2'):
        raise RuntimeError(f'[alasio_speedup.pathcomb] check() encoded the wrong bytes: {result}')
    if encode_prefix_comb([0, 0], [7, 3]) != [7, 3]:
        raise RuntimeError('[alasio_speedup.pathcomb] check() encoded the wrong prefix comb')
    if encode_suffix_comb([0, 4], [0, 1]) != [0, 65]:
        raise RuntimeError('[alasio_speedup.pathcomb] check() encoded the wrong suffix comb')
    return True


def _to_uint32(values, n):
    """
    Copy n values into a C array of uint32.

    Args:
        values (Iterable[int]): Values to copy
        n (int): Number of values

    Returns:
        ctypes.Array: The array, the C side owns a copy of it
    """
    numbers = array.array('I')
    try:
        numbers = array.array('I', values)
    except (OverflowError, ValueError):
        # a negative value or one above 2^32 - 1, the C encoder refuses
        # those, report it the way the Python reference does
        raise ValueError('[alasio_speedup.pathcomb] value out of range')
    if len(numbers) != n:
        raise ValueError(
            f'[alasio_speedup.pathcomb] expected {n} values, got {len(numbers)}'
        )
    return (ctypes.c_uint32 * n).from_buffer_copy(numbers) if n else None


def _from_uint32(values, n):
    """
    Copy n uint32 written by the library into a list of int.

    Args:
        values (ctypes.Array): Pointer of the values array
        n (int): Number of values

    Returns:
        list[int]: The values
    """
    numbers = array.array('I')
    numbers.frombytes(ctypes.string_at(ctypes.addressof(values), n * 4))
    return numbers.tolist()


def _comb_call(name, first, second):
    """
    Call one of the two combined encoders of the library.

    Args:
        name (str): Name of the C entry point
        first (Iterable[int]): First input sequence
        second (Iterable[int]): Second input sequence

    Returns:
        list[int]: The combined values
    """
    first = list(first)
    second = list(second)
    if len(first) != len(second):
        raise ValueError(
            f'[alasio_speedup.pathcomb] the two lists must have the same length, '
            f'got {len(first)} vs {len(second)}'
        )
    n = len(first)
    if n == 0:
        return []
    inputs = (_to_uint32(first, n), _to_uint32(second, n))
    out = (ctypes.c_uint32 * n)()
    written = getattr(library(), name)(inputs[0], inputs[1], n, out)
    if written == ERR_RANGE:
        raise ValueError(
            '[alasio_speedup.pathcomb] the values cannot be encoded, one of them '
            'is out of the range of the pack format'
        )
    if written < 0:
        raise ValueError(f'[alasio_speedup.pathcomb] encoding failed, error={written}, n={n}')
    return _from_uint32(out, n)


def encode_prefix_comb(prefix_reuse, path_length):
    """
    Encode the prefix reuse and the remaining path lengths into the combined
    ints of ``encode_prefix_comb()``: the differential + zigzag reuse
    combined with the length, one int per entry.

    Args:
        prefix_reuse (Iterable[int]): Prefix reuse of every path
        path_length (Iterable[int]): Remaining path byte lengths

    Returns:
        list[int]: Combined values, same length as the input

    Raises:
        ValueError: If a value is out of the range of the pack format, or
            the two lists have different lengths
    """
    return _comb_call('pathcomb_encode_prefix_comb', prefix_reuse, path_length)


def encode_suffix_comb(suffix_reuse, suffix_lookback):
    """
    Encode the suffix reuse and the lookback distances into the combined
    ints of ``encode_suffix_comb()``, one int per entry.

    Args:
        suffix_reuse (Iterable[int]): Suffix reuse of every path
        suffix_lookback (Iterable[int]): Lookback distances

    Returns:
        list[int]: Combined values, same length as the input

    Raises:
        ValueError: If a value is out of the range of the pack format, or
            the two lists have different lengths
    """
    return _comb_call('pathcomb_encode_suffix_comb', suffix_reuse, suffix_lookback)


def encode_path_comb(paths):
    """
    Encode the filepath section of a pack index: the prefix and the suffix
    combination values of every path, and the remaining path bytes.

    The result is what ``iter_path_comb()`` yields, combined by
    ``encode_prefix_comb()`` and ``encode_suffix_comb()``: the encoder
    searches the reuse and combines it in one pass, the three sections
    never cross the language boundary separately.

    Args:
        paths (Iterable[str]): Full paths in encoded order, without NUL
            (a validated pack path never holds one)

    Returns:
        tuple[list[int], list[int], bytes]: Combined prefix values
            (prefix reuse + remaining path length), combined suffix values
            (suffix reuse + lookback) and the remaining paths concatenated
            in order

    Raises:
        ValueError: If a path holds a NUL, or the paths cannot be encoded,
            a remaining path longer than MAX_PATH_LEN for example
    """
    paths = list(paths)
    n = len(paths)
    if n == 0:
        return [], [], b''

    joined = '\0'.join(paths)
    if joined.count('\0') != n - 1:
        raise ValueError('[alasio_speedup.pathcomb] a path holds a NUL')
    source = joined.encode('utf-8')

    lib = library()
    # the remaining paths are the paths with the reused prefix and suffix
    # cut away, they never need more room than the paths themselves
    capacity = len(source)
    prefix_comb = (ctypes.c_uint32 * n)()
    suffix_comb = (ctypes.c_uint32 * n)()
    remaining = (ctypes.c_uint8 * capacity)()
    written = lib.pathcomb_encode_paths(
        source, n, prefix_comb, suffix_comb, remaining, capacity)
    if written == ERR_RANGE:
        raise ValueError(
            '[alasio_speedup.pathcomb] the paths cannot be encoded, '
            'a value is out of the range of the pack format'
        )
    if written < 0:
        raise ValueError(f'[alasio_speedup.pathcomb] encoding failed, error={written}, n={n}')

    return (
        _from_uint32(prefix_comb, n),
        _from_uint32(suffix_comb, n),
        ctypes.string_at(ctypes.addressof(remaining), written),
    )
