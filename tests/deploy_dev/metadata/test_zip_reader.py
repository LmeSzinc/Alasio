"""
Tests for the zip reader of the metadata of a wheel
(alasio/deploy_dev/metadata/zip_reader.py).

The reader works on the bytes of a window of an archive, so every test builds a
real archive in memory and reads it as the provider would: the whole archive is
one window, and the functions are asked the same questions a range request
answers.
"""
import struct
import zipfile
from io import BytesIO

import pytest
from msgspec.structs import replace

from alasio.deploy_dev.metadata.zip_reader import (
    TAIL_SIZE, CentralDirectory, ContentRange, ZipLayoutError, central_directory, content_range, decompress_member,
    end_of_central_directory, find_metadata, local_data_offset
)

pytestmark = [
    pytest.mark.parallel,
]

DIST_INFO = 'demo-1.0.dist-info'


def archive(members, compression=zipfile.ZIP_DEFLATED):
    """
    Build a zip archive in memory.

    Args:
        members (dict[str, bytes | str]): {path in the archive: content}
        compression (int): Compression of the archive. Defaults to deflate

    Returns:
        bytes: The archive
    """
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=compression) as zf:
        for path, content in members.items():
            zf.writestr(path, content)
    return buffer.getvalue()


def entries_of(data):
    """
    The central directory entries of an archive held in memory.

    Args:
        data (bytes): The archive

    Returns:
        list[ZipEntry]: The entries
    """
    central = end_of_central_directory(data)
    directory = data[central.offset:central.offset + central.size]
    return central_directory(directory, central.count)


def member_bytes(data, entry):
    """
    The compressed bytes of one member, from the offset of its local header.

    Args:
        data (bytes): The archive
        entry (ZipEntry): The entry of the member

    Returns:
        bytes: The compressed bytes
    """
    offset = local_data_offset(data[entry.header_offset:])
    start = entry.header_offset + offset
    return data[start:start + entry.compress_size]


class TestEndOfCentralDirectory:
    """The record that locates the index of an archive."""

    def test_the_record_of_an_archive(self):
        data = archive({f'{DIST_INFO}/METADATA': 'Name: demo\n'})

        central = end_of_central_directory(data)

        assert central.count == 1
        # the directory ends where the record begins, and the record is the
        # last 22 bytes of an archive without a comment
        assert central.offset + central.size == len(data) - 22

    def test_a_comment_after_the_record(self):
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, 'w') as zf:
            zf.writestr(f'{DIST_INFO}/METADATA', 'Name: demo\n')
            zf.comment = b'a comment of the archive'
        data = buffer.getvalue()

        central = end_of_central_directory(data)

        assert central.count == 1

    def test_the_bytes_hold_no_record(self):
        with pytest.raises(ZipLayoutError, match='end of the central directory is not'):
            end_of_central_directory(b'not a zip archive at all')

    def test_a_saturated_record_without_the_zip64_one(self):
        data = struct.pack(
            '<4sHHHHIIH', b'PK\x05\x06', 0, 0, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0)

        with pytest.raises(ZipLayoutError, match='zip64'):
            end_of_central_directory(data)

    def test_a_saturated_record_with_the_zip64_one(self):
        record = struct.pack(
            '<4sQHHIIQQQQ', b'PK\x06\x06', 44, 45, 45, 0, 0, 7, 7, 100, 0)
        locator = struct.pack('<4sIQI', b'PK\x06\x07', 0, 0, 1)
        end = struct.pack(
            '<4sHHHHIIH', b'PK\x05\x06', 0, 0, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0)

        central = end_of_central_directory(record + locator + end)

        assert central == CentralDirectory(offset=0, size=100, count=7)

    def test_the_zip64_record_outside_the_window(self):
        # the locator points at a record before the window: the caller reads
        # more bytes, the reader never guesses the directory
        record = struct.pack(
            '<4sQHHIIQQQQ', b'PK\x06\x06', 44, 45, 45, 0, 0, 7, 7, 100, 0)
        locator = struct.pack('<4sIQI', b'PK\x06\x07', 0, 0, 1)
        end = struct.pack(
            '<4sHHHHIIH', b'PK\x05\x06', 0, 0, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0)
        data = record + locator + end

        with pytest.raises(ZipLayoutError, match='zip64 end of central directory is not'):
            end_of_central_directory(data, tail_start=len(record))


class TestCentralDirectory:
    """The entries of the index of an archive."""

    def test_the_entries(self):
        data = archive({
            f'{DIST_INFO}/METADATA': 'Name: demo\n',
            f'{DIST_INFO}/RECORD': 'demo-1.0.dist-info/METADATA,,\n',
        })
        with zipfile.ZipFile(BytesIO(data)) as zf:
            expected = [(info.filename, info.header_offset) for info in zf.infolist()]

        entries = entries_of(data)

        assert [(entry.filename, entry.header_offset) for entry in entries] == expected

    def test_the_declared_count_stops_the_read(self):
        data = archive({
            f'{DIST_INFO}/METADATA': 'Name: demo\n',
            f'{DIST_INFO}/RECORD': '\n',
        })

        entries = entries_of(data)

        assert len(central_directory(
            data[end_of_central_directory(data).offset:], 1
        )) == 1

    def test_the_bytes_hold_no_entry(self):
        with pytest.raises(ZipLayoutError, match='no central directory entry'):
            central_directory(b'\x00' * 64)

    def test_an_entry_with_the_zip64_extra_field(self):
        # an entry whose sizes are saturated is written with the zip64 extra
        # field, which the reader does not follow: the caller reads the wheel
        header = struct.pack(
            '<4sHHHHHHIIIHHHHHII', b'PK\x01\x02', 20, 20, 0, 8, 0, 0, 0,
            0xFFFFFFFF, 0xFFFFFFFF, 12, 0, 0, 0, 0, 0, 0xFFFFFFFF)
        data = header + f'{DIST_INFO}/METADATA'.encode('utf-8')[:12]

        with pytest.raises(ZipLayoutError, match='zip64 extra field'):
            central_directory(data)


class TestFindMetadata:
    """The member that holds the headers of the distribution."""

    def test_the_member_of_the_dist_info(self):
        data = archive({
            'demo/__init__.py': '',
            f'{DIST_INFO}/METADATA': 'Name: demo\n',
            f'{DIST_INFO}/RECORD': '\n',
        })

        entry = find_metadata(entries_of(data))

        assert entry.filename == f'{DIST_INFO}/METADATA'

    def test_an_archive_without_the_member(self):
        data = archive({'demo/__init__.py': ''})

        with pytest.raises(ZipLayoutError, match='no .*METADATA member'):
            find_metadata(entries_of(data))


class TestLocalDataOffset:
    """Where the bytes of a member start, from its local header."""

    def test_the_header_size(self):
        data = archive({f'{DIST_INFO}/METADATA': 'Name: demo\n'})
        entry = find_metadata(entries_of(data))

        offset = local_data_offset(data[entry.header_offset:])

        # the header is 30 bytes, then the filename and the extra field
        assert offset == 30 + len(entry.filename)

    def test_the_bytes_are_not_a_header(self):
        with pytest.raises(ZipLayoutError, match='local file header'):
            local_data_offset(b'\x00' * 64)

    def test_the_bytes_are_too_short(self):
        with pytest.raises(ZipLayoutError, match='no local file header'):
            local_data_offset(b'PK\x03\x04')


class TestDecompressMember:
    """The bytes of a member, checked against its entry."""

    def test_a_deflated_member(self):
        content = 'Name: demo\n\n' + 'description\n' * 100
        data = archive({f'{DIST_INFO}/METADATA': content}, zipfile.ZIP_DEFLATED)
        entry = find_metadata(entries_of(data))

        assert decompress_member(member_bytes(data, entry), entry) == content.encode('utf-8')

    def test_a_stored_member(self):
        content = 'Name: demo\n'
        data = archive({f'{DIST_INFO}/METADATA': content}, zipfile.ZIP_STORED)
        entry = find_metadata(entries_of(data))

        assert decompress_member(member_bytes(data, entry), entry) == content.encode('utf-8')

    def test_a_crc_that_does_not_match(self):
        data = archive({f'{DIST_INFO}/METADATA': 'Name: demo\n'}, zipfile.ZIP_STORED)
        entry = find_metadata(entries_of(data))
        broken = bytearray(member_bytes(data, entry))
        broken[0] ^= 0xFF

        with pytest.raises(ZipLayoutError, match='does not match the CRC'):
            decompress_member(bytes(broken), entry)

    def test_a_size_that_does_not_match(self):
        data = archive({f'{DIST_INFO}/METADATA': 'Name: demo\n'}, zipfile.ZIP_STORED)
        entry = find_metadata(entries_of(data))

        with pytest.raises(ZipLayoutError, match='its entry declares'):
            decompress_member(member_bytes(data, entry), replace(entry, file_size=999))

    def test_a_compression_method_the_reader_does_not_know(self):
        data = archive({f'{DIST_INFO}/METADATA': 'Name: demo\n'})
        entry = find_metadata(entries_of(data))

        with pytest.raises(ZipLayoutError, match='only stored .0. and deflate .8. are read'):
            decompress_member(member_bytes(data, entry), replace(entry, compress_type=99))

    def test_the_tail_of_an_archive_is_one_window(self):
        # the case the provider relies on: the member of a small wheel is in
        # the tail, so the whole metadata is read by one request
        data = archive({f'{DIST_INFO}/METADATA': 'Name: demo\n'})
        tail = data[-TAIL_SIZE:]

        central = end_of_central_directory(tail, len(data) - len(tail))
        entries = central_directory(tail[central.offset - (len(data) - len(tail)):], central.count)
        entry = find_metadata(entries)
        offset = local_data_offset(tail[entry.header_offset - (len(data) - len(tail)):])
        start = entry.header_offset - (len(data) - len(tail)) + offset

        assert decompress_member(tail[start:start + entry.compress_size], entry) == b'Name: demo\n'


class TestContentRange:
    """The header that names a span of a file and the size of the whole."""

    @pytest.mark.parametrize('value, expected', [
        ('bytes 0-0/48213', ContentRange(0, 0, 48213)),
        ('bytes 16384-31999/32000', ContentRange(16384, 31999, 32000)),
        ('bytes 0-16283/*', ContentRange(0, 16283, -1)),
        ('bytes */48213', ContentRange(-1, -1, 48213)),
        # a header that is not a byte range, and the shapes that do not parse
        ('items 0-0/1', None),
        ('bytes 0-0', None),
        ('', None),
    ])
    def test_parse(self, value, expected):
        assert content_range(value) == expected
