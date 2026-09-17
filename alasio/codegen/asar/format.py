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

The frame is a detail of the format: ``read_header()`` gives the header JSON and
the offset of the content, ``pack_header()`` gives the bytes an archive starts
with. Nothing else in the module works with the frame.
"""
import os
import struct

from .errors import AsarFormatError

# Byte alignment of every value written into a Chromium Pickle
ALIGNMENT = 4
# Payload length of the leading size pickle, it holds a single uint32
SIZE_PICKLE_PAYLOAD = 4
# Integrity block size used by @electron/asar (4 MiB)
BLOCK_SIZE = 4194304
# asar stores the file size in a uint32, checked from both sides
UINT32_MAX = 4294967295
# Refuse to allocate a header larger than this, an archive is untrusted input
MAX_HEADER_SIZE = 16 * 1024 * 1024
# Refuse to walk a path deeper than this, a deep tree would also break the
# recursive JSON decoder (msgspec raises RecursionError around 1000 levels)
MAX_PATH_DEPTH = 256


def _align4(size):
    """
    Round a byte size up to the next 4 byte boundary.

    Args:
        size (int): Byte size

    Returns:
        int: Aligned byte size
    """
    return (size + 3) & ~3


def read_header(fd):
    """
    Read the header of an archive.

    The handle is left on the first content byte, so a sequential read of the
    data area (``extract_all()``) starts right where the header ends.

    Args:
        fd (io.IOBase): Open handle of the archive file, the file is read from
            its beginning whatever the position of the handle is

    Returns:
        tuple[bytes, int]: Header JSON, and the offset of the first content byte

    Raises:
        AsarFormatError: If the archive is truncated, or if the frame does not
            match the header it holds
    """
    # The size comes from the file itself, seeking to its end and back would
    # move the handle without telling anything new about it
    archive_size = os.fstat(fd.fileno()).st_size
    fd.seek(0)
    frame = fd.read(8)
    if len(frame) != 8:
        raise AsarFormatError(f'Archive is truncated, expected 8 bytes but got {len(frame)}')
    payload, header_size = struct.unpack('<II', frame)
    if payload != SIZE_PICKLE_PAYLOAD:
        raise AsarFormatError(
            f'Broken size pickle, expected a payload of {SIZE_PICKLE_PAYLOAD} bytes, got {payload}'
        )
    if header_size < 8:
        raise AsarFormatError(
            f'Header size {header_size} is smaller than the 8 bytes a header pickle needs'
        )
    if header_size > MAX_HEADER_SIZE:
        raise AsarFormatError(
            f'Header size {header_size} exceeds the {MAX_HEADER_SIZE} bytes limit'
        )
    if header_size + 8 > archive_size:
        raise AsarFormatError(
            f'Header size {header_size} exceeds the archive size of {archive_size} bytes'
        )
    pickle = fd.read(header_size)
    if len(pickle) != header_size:
        raise AsarFormatError(
            f'Archive is truncated, expected {header_size} bytes but got {len(pickle)}'
        )
    payload, json_size = struct.unpack_from('<II', pickle, 0)
    if payload + 4 > header_size:
        raise AsarFormatError(
            f'Header pickle is truncated, payload claims {payload} bytes, got {header_size - 4}'
        )
    if ALIGNMENT + _align4(json_size) > payload:
        raise AsarFormatError(
            f'Header JSON does not fit in the header pickle, '
            f'JSON claims {json_size} bytes, payload is {payload}'
        )
    return pickle[8:8 + json_size], 8 + header_size


def pack_header(json_bytes):
    """
    Build the frame an archive starts with.

    Args:
        json_bytes (bytes): Header JSON, UTF-8 encoded

    Returns:
        bytes: Size pickle and header pickle, the content goes right after them
    """
    json_size = len(json_bytes)
    aligned = _align4(json_size)
    return b''.join((
        struct.pack('<II', SIZE_PICKLE_PAYLOAD, 2 * ALIGNMENT + aligned),
        struct.pack('<II', SIZE_PICKLE_PAYLOAD + aligned, json_size),
        json_bytes,
        b'\x00' * (aligned - json_size),
    ))
