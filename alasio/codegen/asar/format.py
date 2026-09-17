"""
Binary framing of an Electron app.asar archive.

The archive is a Chromium Pickle frame followed by the file contents::

    [0:4]      uint32 LE = 4               size pickle payload length (constant)
    [4:8]      uint32 LE = H               header pickle total length
    [8:12]     uint32 LE = 4 + align4(J)   header pickle payload length
    [12:16]    uint32 LE = J               header JSON byte length
    [16:16+J)                              header JSON, UTF-8, no BOM
    [16+J:16+align4(J))                    zero padding, JSON is 4 byte aligned
    [8+H:EOF)                              file contents, concatenated, no padding

``H = 8 + align4(J)``, so contents start at ``8 + H`` which is ``16 + align4(J)``.
Every integer is a little endian uint32, and a Chromium Pickle writes each value
padded to a 4 byte boundary (this naming is kept because ``pickle`` is a stdlib
module, and this is NOT the stdlib pickle format).
"""
import struct

from .errors import AsarFormatError

# Byte alignment of every value written into a Chromium Pickle
ALIGNMENT = 4
# Payload length of the leading size pickle, it holds a single uint32
SIZE_PICKLE_PAYLOAD = 4
# Byte length of the frame integers before the header JSON
FRAME_SIZE = 16
# Integrity block size used by @electron/asar (4 MiB)
BLOCK_SIZE = 4194304
# asar stores the file size in a uint32, checked from both sides
UINT32_MAX = 4294967295
# Refuse to allocate a header larger than this, an archive is untrusted input
MAX_HEADER_SIZE = 16 * 1024 * 1024
# Refuse to walk a path deeper than this, a deep tree would also break the
# recursive JSON decoder (msgspec raises RecursionError around 1000 levels)
MAX_PATH_DEPTH = 256


def align4(size):
    """
    Round a byte size up to the next 4 byte boundary.

    Args:
        size (int): Byte size

    Returns:
        int: Aligned byte size
    """
    return (size + 3) & ~3


def calc_header_size(json_size):
    """
    Calculate the header pickle length of a header JSON.

    Args:
        json_size (int): Header JSON byte length

    Returns:
        int: Header pickle length, ``8 + align4(json_size)``
    """
    return 2 * ALIGNMENT + align4(json_size)


def calc_data_offset(json_size):
    """
    Calculate the offset of the first file content.

    Args:
        json_size (int): Header JSON byte length

    Returns:
        int: Offset of the data area, ``16 + align4(json_size)``
    """
    return FRAME_SIZE + align4(json_size)


def pack_size_pickle(header_size):
    """
    Build the leading size pickle, it holds the length of the header pickle.

    Args:
        header_size (int): Header pickle length, see ``calc_header_size()``

    Returns:
        bytes: Size pickle, 8 bytes
    """
    return struct.pack('<II', SIZE_PICKLE_PAYLOAD, header_size)


def pack_header_pickle(data):
    """
    Build the header pickle, it holds the header JSON as a length prefixed string.

    Args:
        data (bytes): Header JSON, UTF-8 encoded

    Returns:
        bytes: Header pickle, ``8 + align4(len(data))`` bytes
    """
    size = len(data)
    padding = align4(size) - size
    return b''.join((
        struct.pack('<II', SIZE_PICKLE_PAYLOAD + align4(size), size),
        data,
        b'\x00' * padding,
    ))


def parse_size_pickle(data):
    """
    Parse the leading size pickle to get the header pickle length.

    Args:
        data (bytes): The first 8 bytes of an archive

    Returns:
        int: Header pickle length

    Raises:
        AsarFormatError: If the frame is truncated or the payload length is not
            the constant 4 written by Chromium Pickle
    """
    if len(data) < 8:
        raise AsarFormatError(
            f'Archive is truncated, expected 8 bytes of size pickle, got {len(data)}'
        )
    payload, header_size = struct.unpack_from('<II', data, 0)
    if payload != SIZE_PICKLE_PAYLOAD:
        raise AsarFormatError(
            f'Broken size pickle, expected a payload of {SIZE_PICKLE_PAYLOAD} bytes, got {payload}'
        )
    return header_size


def parse_header_pickle(data):
    """
    Extract the header JSON from a header pickle.

    Args:
        data (bytes): Header pickle bytes

    Returns:
        memoryview: Header JSON bytes, a view on ``data``

    Raises:
        AsarFormatError: If the pickle is truncated or its lengths do not match
    """
    if len(data) < 8:
        raise AsarFormatError(
            f'Header pickle is truncated, expected at least 8 bytes, got {len(data)}'
        )
    payload, json_size = struct.unpack_from('<II', data, 0)
    if payload + 4 > len(data):
        raise AsarFormatError(
            f'Header pickle is truncated, payload claims {payload} bytes, got {len(data) - 4}'
        )
    if ALIGNMENT + align4(json_size) > payload:
        raise AsarFormatError(
            f'Header JSON does not fit in the header pickle, '
            f'JSON claims {json_size} bytes, payload is {payload}'
        )
    return memoryview(data)[8:8 + json_size]
