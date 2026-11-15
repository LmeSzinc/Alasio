"""
Read one member of a zip archive from the bytes a range request answers.

A wheel is a zip archive, and the METADATA of a distribution is one member of
it: a reader that only needs the headers does not have to transfer the archive.
Every function here works on the bytes of one window of the file and the
absolute offset of that window, so a caller fetches only what the reader asks
for: the tail of the archive, the central directory when the tail does not hold
it, then the member itself. The cost of one metadata is a few KB of a wheel of
a few hundred KB (see alasio.deploy_dev.metadata.provider for the numbers).

Two structures are read:

- the end of central directory (EOCD), the record an archive ends with, which
  gives the offset and the size of the central directory: the tail of a wheel
  ends with it, so one read of the tail locates the index of the archive;
- the central directory entry of the member, which gives the offset of its
  local header, the compression method and the sizes: the entry is the index,
  the local header is the start of the bytes.

The zip64 records are read when the classic record is saturated. A member the
reader cannot reach is reported by :class:`ZipLayoutError`, never guessed: the
caller reads the whole wheel then, which is the fallback of the format and the
only answer that is certainly right.

Usage:
    from alasio.deploy_dev.metadata.zip_reader import (
        central_directory, decompress_member, end_of_central_directory,
        find_metadata, local_data_offset)

    central = end_of_central_directory(tail, tail_start)
    entries = central_directory(tail[central.offset - tail_start:], central.count)
    entry = find_metadata(entries)
    offset = local_data_offset(window)
    content = decompress_member(window[offset:offset + entry.compress_size], entry)
"""

import struct
import zlib

from msgspec import Struct

__all__ = [
    'CENTRAL_HEADER_SIZE',
    'LOCAL_HEADER_SIZE',
    'TAIL_SIZE',
    'WINDOW_PADDING',
    'CentralDirectory',
    'ContentRange',
    'ZipEntry',
    'ZipLayoutError',
    'central_directory',
    'content_range',
    'decompress_member',
    'end_of_central_directory',
    'find_metadata',
    'local_data_offset',
]

# Signatures of the records the reader looks for: the end of the central
# directory and its zip64 twin, the locator that points at the zip64 record,
# and the two headers of a member (the index entry and the start of its bytes)
EOCD_SIGNATURE = b'PK\x05\x06'
ZIP64_EOCD_SIGNATURE = b'PK\x06\x06'
ZIP64_LOCATOR_SIGNATURE = b'PK\x06\x07'
CENTRAL_SIGNATURE = b'PK\x01\x02'
LOCAL_SIGNATURE = b'PK\x03\x04'

# Fixed sizes of the records, the fields the reader reads follow them
EOCD_SIZE = 22
ZIP64_EOCD_SIZE = 56
ZIP64_LOCATOR_SIZE = 20
CENTRAL_HEADER_SIZE = 46
LOCAL_HEADER_SIZE = 30

# Bytes of the tail of an archive one request reads: the value uv reads (its
# CENTRAL_DIRECTORY_SIZE), which holds the central directory of every wheel of
# the dependencies of this project. A directory that does not fit is read by a
# second request, never by a bigger tail
TAIL_SIZE = 16384

# Bytes of slack a window adds around a member: the local header is 30 bytes
# plus the filename and the extra field, which are a few dozens of bytes of a
# wheel, so the window of a member plus this slack is one request
WINDOW_PADDING = 1024

# A size or an offset the classic records cannot hold, the mark of a zip64
# entry: a wheel of a dependency is never that big, so the reader refuses it
# instead of reading the extra field
SATURATED = 0xFFFFFFFF


class ZipLayoutError(ValueError):
    """
    Raised when the bytes a window holds are not enough to answer.

    The caller reads more bytes, or falls back to the whole archive: a layout
    the reader does not know must not become a partial answer.

    Attributes:
        reason (str): Why the bytes cannot answer, the message of the error
    """


class CentralDirectory(Struct, frozen=True):
    """
    Where the index of an archive is, as the EOCD records it.

    Attributes:
        offset (int): Absolute offset of the first central directory entry
        size (int): Size of the central directory in bytes
        count (int): Number of entries
    """
    offset: int
    size: int
    count: int


class ContentRange(Struct, frozen=True):
    """
    The ``Content-Range`` header of a partial answer.

    Attributes:
        start (int): First byte of the answer, -1 when the header does not
            name a span (``bytes */1234``)
        end (int): Last byte of the answer, -1 like start
        total (int): Size of the whole file, -1 when the server does not tell
    """
    start: int
    end: int
    total: int


class ZipEntry(Struct, frozen=True):
    """
    One entry of the central directory, the fields the reader needs.

    Attributes:
        filename (str): Name of the member, e.g. 'httpx2-2.12.0.dist-info/METADATA'
        compress_type (int): Method of the member: 0 is stored, 8 is deflate
        crc (int): CRC-32 of the uncompressed bytes, the check of the member
        compress_size (int): Size of the member in the archive
        file_size (int): Size of the member uncompressed
        header_offset (int): Absolute offset of the local header of the member
    """
    filename: str
    compress_type: int
    crc: int
    compress_size: int
    file_size: int
    header_offset: int


def end_of_central_directory(tail, tail_start=0):
    """
    Locate the central directory from the bytes an archive ends with.

    The EOCD is the last record of an archive: the reader looks for its
    signature from the end, so a comment after it does not hide it. A record
    that is saturated (more than 65535 entries, more than 4 GiB, the zip64
    case) is read through the zip64 records, which are the 20 bytes before the
    EOCD and the record they point at; both have to be in the bytes read.

    Args:
        tail (bytes): Bytes of the end of the archive
        tail_start (int): Absolute offset of the first byte of tail, 0 when the
            bytes are the whole archive. Defaults to 0

    Returns:
        CentralDirectory: The offset, the size and the entry count

    Raises:
        ZipLayoutError: If the bytes hold no EOCD, or the zip64 records the
            classic one points at are not in them
    """
    position = tail.rfind(EOCD_SIGNATURE)
    if position < 0 or position + EOCD_SIZE > len(tail):
        raise ZipLayoutError(
            'The end of the central directory is not in the bytes read')
    count, size, offset = struct.unpack_from('<HII', tail, position + 10)
    if count < 0xFFFF and size < SATURATED and offset < SATURATED:
        return CentralDirectory(offset, size, count)
    return _zip64_central_directory(tail, tail_start, position)


def _zip64_central_directory(tail, tail_start, position):
    """
    Read the central directory of a saturated EOCD from the zip64 records.

    Args:
        tail (bytes): Bytes of the end of the archive
        tail_start (int): Absolute offset of the first byte of tail
        position (int): Index of the classic EOCD in tail

    Returns:
        CentralDirectory: The offset, the size and the entry count

    Raises:
        ZipLayoutError: If the locator or the record is not in the bytes
    """
    locator = position - ZIP64_LOCATOR_SIZE
    if locator < 0 or tail[locator:locator + 4] != ZIP64_LOCATOR_SIGNATURE:
        locator = tail.rfind(ZIP64_LOCATOR_SIGNATURE, 0, position)
    if locator < 0:
        raise ZipLayoutError(
            'The archive declares a zip64 central directory, the locator of it is not in '
            'the bytes read')
    offset = struct.unpack_from('<Q', tail, locator + 8)[0]
    record = offset - tail_start
    if record < 0 or record + ZIP64_EOCD_SIZE > len(tail):
        raise ZipLayoutError(
            'The zip64 end of central directory is not in the bytes read')
    if tail[record:record + 4] != ZIP64_EOCD_SIGNATURE:
        raise ZipLayoutError(
            'The locator points at bytes that are not a zip64 end of central directory')
    # the record: signature, its own size, the versions, the disks, then the
    # counts and the directory at offsets 32, 40 and 48
    count, size, directory = struct.unpack_from('<QQQ', tail, record + 32)
    return CentralDirectory(directory, size, count)


def central_directory(data, count=0):
    """
    Read the entries of the central directory from its bytes.

    The entries are read until the bytes run out or the declared count is
    reached, so a caller may pass a window that starts at the directory and
    ends anywhere after it.

    Args:
        data (bytes): Bytes of the central directory, starting at its first entry
        count (int): Number of entries the EOCD declares, 0 to read until the
            bytes run out. Defaults to 0

    Returns:
        list[ZipEntry]: The entries, in the order of the directory

    Raises:
        ZipLayoutError: If the bytes hold no entry, or an entry is written with
            the zip64 extra field
    """
    entries = []
    position = 0
    while position + CENTRAL_HEADER_SIZE <= len(data):
        if data[position:position + 4] != CENTRAL_SIGNATURE:
            break
        compress_type = struct.unpack_from('<H', data, position + 10)[0]
        crc, compress_size, file_size = struct.unpack_from('<III', data, position + 16)
        name_len, extra_len, comment_len = struct.unpack_from('<HHH', data, position + 28)
        header_offset = struct.unpack_from('<I', data, position + 42)[0]
        filename = data[position + 46:position + 46 + name_len].decode('utf-8', 'replace')
        if compress_size == SATURATED or file_size == SATURATED or header_offset == SATURATED:
            raise ZipLayoutError(
                f'The member "{filename}" is written with a zip64 extra field, the '
                f'reader does not follow it')
        entries.append(ZipEntry(
            filename, compress_type, crc, compress_size, file_size, header_offset))
        position += CENTRAL_HEADER_SIZE + name_len + extra_len + comment_len
        if count and len(entries) >= count:
            break
    if not entries:
        raise ZipLayoutError('The bytes hold no central directory entry')
    return entries


def find_metadata(entries):
    """
    The ``{dist-info}/METADATA`` entry of an archive.

    The folder of the entry is named after the wheel, so the reader matches the
    member name rather than an exact path.

    Args:
        entries (Iterable[ZipEntry]): Entries of the central directory

    Returns:
        ZipEntry: The METADATA entry

    Raises:
        ZipLayoutError: If the archive holds no such entry
    """
    for entry in entries:
        folder, sep, path = entry.filename.partition('/')
        if sep and path == 'METADATA' and folder.endswith('.dist-info'):
            return entry
    raise ZipLayoutError('The archive holds no *.dist-info/METADATA member')


def local_data_offset(local_header):
    """
    The offset of the member bytes from the start of its local header.

    The local header is 30 bytes, then the filename and the extra field, whose
    sizes the header records: the extra field of the local header may differ
    from the one of the central directory, so the sizes are read here rather
    than reused from the entry.

    Args:
        local_header (bytes): Bytes of the local header, its first 30 at least

    Returns:
        int: Offset of the member bytes from the start of the local header

    Raises:
        ZipLayoutError: If the bytes are too short, or not a local header
    """
    if len(local_header) < LOCAL_HEADER_SIZE:
        raise ZipLayoutError('The bytes hold no local file header')
    if local_header[:4] != LOCAL_SIGNATURE:
        raise ZipLayoutError('The bytes at the member offset are not a local file header')
    name_len, extra_len = struct.unpack_from('<HH', local_header, 26)
    return LOCAL_HEADER_SIZE + name_len + extra_len


def decompress_member(data, entry):
    """
    Decompress the bytes of one member, and check them against its entry.

    The size and the CRC of the entry are the integrity check of a member read
    by range: a partial read is caught here rather than becoming a truncated
    METADATA.

    Args:
        data (bytes): Compressed bytes of the member, exactly its compress_size
        entry (ZipEntry): The entry of the member

    Returns:
        bytes: The member uncompressed

    Raises:
        ZipLayoutError: If the method is not stored or deflate, the bytes do not
            decompress, or the size or the CRC does not match the entry
    """
    if entry.compress_type == 0:
        raw = data
    elif entry.compress_type == 8:
        try:
            raw = zlib.decompress(data, -15)
        except zlib.error as e:
            raise ZipLayoutError(
                f'The member "{entry.filename}" does not decompress: {e}') from e
    else:
        raise ZipLayoutError(
            f'The member "{entry.filename}" is compressed with the method '
            f'{entry.compress_type}, only stored (0) and deflate (8) are read')
    if len(raw) != entry.file_size:
        raise ZipLayoutError(
            f'The member "{entry.filename}" has {len(raw)} bytes, its entry declares '
            f'{entry.file_size}')
    if zlib.crc32(raw) & 0xFFFFFFFF != entry.crc:
        raise ZipLayoutError(
            f'The member "{entry.filename}" does not match the CRC of its entry')
    return raw


def content_range(value):
    """
    Parse the ``Content-Range`` header of a partial answer.

    Args:
        value (str): Value of the header, e.g. 'bytes 0-0/48213'

    Returns:
        ContentRange | None: The span and the size of the whole file, None when
            the header does not name a byte range
    """
    unit, sep, spec = value.partition(' ')
    if not sep or unit.lower() != 'bytes':
        return None
    span, sep, total = spec.partition('/')
    if not sep:
        return None
    total = int(total) if total.strip() != '*' else -1
    start, sep, end = span.partition('-')
    if not sep:
        return ContentRange(-1, -1, total)
    return ContentRange(int(start), int(end), total)
