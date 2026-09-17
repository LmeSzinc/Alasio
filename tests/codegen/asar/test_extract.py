"""
Tests of the sequential extraction of a whole archive.

The interesting cases are the entries that share bytes: 4.3.0 writes identical
content once and lets several entries point at it, an entry may also be a prefix
of another one, or be nested inside another one. The scan merges them into
regions of the data area and reads every region once, so these tests assert both
the extracted content and the invariants of the scan (region count, seek count).
"""
import os

import pytest

from alasio.codegen.asar.archive import REGION_BUDGET, unpack
from alasio.codegen.asar.errors import AsarEntryNotFoundError, AsarFormatError, AsarPathError, AsarUnsupportedError
from alasio.ext.path.atomic import file_read_bytes
from alasio.testing.filesystem import fs  # noqa: F401
from tests.codegen.asar import fixture

# The data area of the synthetic archive: one long file, a duplicated pair, a
# prefix pair, a nested entry and an empty file
LONG = bytes(range(256)) * 2800  # 716800 bytes
SHARED = b'shared content'
PREFIX = b'prefix content'
TAIL = b'nested'
DATA = LONG + SHARED + PREFIX + TAIL


def integrity(content):
    """
    Build the integrity of a content.

    Args:
        content (bytes): Content of the entry

    Returns:
        dict: Integrity object
    """
    import hashlib
    digest = hashlib.sha256(content).hexdigest()
    return {'algorithm': 'SHA256', 'hash': digest, 'blockSize': 4194304, 'blocks': [digest]}


def entry(size, offset, content=None):
    """
    Build a file node.

    Args:
        size (int): Byte length of the entry
        offset (int): Offset of the content in the data area
        content (bytes): Content, used to build the integrity, None for no integrity

    Returns:
        dict: File node
    """
    node = {'size': size, 'offset': str(offset)}
    if content is not None:
        node['integrity'] = integrity(content)
    return node


def shared_archive():
    """
    Build an archive whose entries share, overlap and nest.

    Returns:
        bytes: Archive bytes
    """
    long_size = len(LONG)
    shared_offset = long_size
    prefix_offset = shared_offset + len(SHARED)
    tail_offset = prefix_offset + len(PREFIX)
    header = {
        'files': {
            'long.bin': entry(long_size, 0, LONG),
            # Identical content, 4.3.0 writes it once
            'dup': {
                'files': {
                    'a.txt': entry(len(SHARED), shared_offset, SHARED),
                    'b.txt': entry(len(SHARED), shared_offset, SHARED),
                },
            },
            # Same offset, a different size: the shorter one is a prefix
            'prefix.txt': entry(len(PREFIX), prefix_offset, PREFIX),
            'prefix-plus.txt': entry(len(PREFIX) + len(TAIL), prefix_offset, PREFIX + TAIL),
            # An entry nested inside the one above
            'nested.txt': entry(len(TAIL), tail_offset, TAIL),
            'empty.txt': entry(0, tail_offset + len(TAIL), b''),
            'dir': {'files': {}},
        },
    }
    return fixture.make_archive(header, DATA)


EXPECTED = {
    'long.bin': LONG,
    'dup/a.txt': SHARED,
    'dup/b.txt': SHARED,
    'prefix.txt': PREFIX,
    'prefix-plus.txt': PREFIX + TAIL,
    'nested.txt': TAIL,
    'empty.txt': b'',
}


class TestUnpackRegions:
    @pytest.mark.parametrize('chunk_size, region_budget', [
        (64 * 1024, 0),
        (256 * 1024, 0),
        (1024 * 1024, 0),
        (64 * 1024, 64 * 1024),
        (256 * 1024, 256 * 1024),
        (256 * 1024, REGION_BUDGET),
        (1024 * 1024, 1024 * 1024),
    ])
    def test_shared_entries(self, fs, chunk_size, region_budget):
        """Every configuration extracts the same content from shared bytes."""
        fs.create_file('/shared.asar', contents=shared_archive())
        dest = f'/out-{chunk_size}-{region_budget}'
        result = unpack(
            '/shared.asar', dest, region_budget=region_budget, chunk_size=chunk_size, verify=True,
        )
        assert result.seek_count == 0
        assert result.region_count == 1
        assert result.file_count == 7
        assert result.dir_count == 2
        assert result.data_size == len(DATA)
        for name, expected in EXPECTED.items():
            assert file_read_bytes(f'{dest}/{name}') == expected, name
        assert os.path.isdir(f'{dest}/dir')

    def test_regions_are_merged(self, fs):
        """Overlapping and touching entries end up in a single region."""
        fs.create_file('/shared.asar', contents=shared_archive())
        result = unpack('/shared.asar', '/out', region_budget=0, chunk_size=4096)
        # 7 entries share one region, they are stored back to back
        assert result.region_count == 1
        assert result.seek_count == 0

    def test_gap_needs_a_seek(self, fs):
        """A gap between two entries is the only reason to seek."""
        header = {
            'files': {
                'a.txt': entry(4, 0, b'aaaa'),
                # A gap of 8 bytes that belongs to no entry
                'b.txt': entry(4, 12, b'bbbb'),
            },
        }
        fs.create_file('/gap.asar', contents=fixture.make_archive(header, b'aaaa' + b'x' * 8 + b'bbbb'))
        result = unpack('/gap.asar', '/out', region_budget=0, chunk_size=2)
        assert file_read_bytes('/out/a.txt') == b'aaaa'
        assert file_read_bytes('/out/b.txt') == b'bbbb'
        assert result.region_count == 2
        assert result.seek_count == 1

    def test_unsorted_offsets(self, fs):
        """Entries are extracted in offset order, whatever the header order is."""
        header = {
            'files': {
                'b.txt': entry(4, 4, b'bbbb'),
                'a.txt': entry(4, 0, b'aaaa'),
            },
        }
        fs.create_file('/unordered.asar', contents=fixture.make_archive(header, b'aaaabbbb'))
        result = unpack('/unordered.asar', '/out', region_budget=0, chunk_size=3)
        assert file_read_bytes('/out/a.txt') == b'aaaa'
        assert file_read_bytes('/out/b.txt') == b'bbbb'
        assert result.seek_count == 0

    def test_empty_archive(self, fs):
        """An archive without entry extracts to an empty directory."""
        fs.create_file('/empty.asar', contents=fixture.make_archive({'files': {}}))
        result = unpack('/empty.asar', '/out')
        assert (result.file_count, result.dir_count, result.region_count) == (0, 0, 0)
        assert result.seek_count == 0

    def test_destination_is_created(self, fs):
        """The target directory is created when it does not exist yet."""
        fs.create_file('/shared.asar', contents=shared_archive())
        unpack('/shared.asar', '/deep/nested/out', region_budget=0)
        assert file_read_bytes('/deep/nested/out/nested.txt') == TAIL


class TestUnpackFixtures:
    def test_packthis(self, fs):
        """A 4.3.0 fixture is extracted file by file."""
        fs.create_file('/packthis.asar', contents=fixture.packthis_430())
        result = unpack('/packthis.asar', '/out', verify=True)
        assert (result.file_count, result.dir_count) == (6, 2)
        assert result.seek_count == 0
        assert file_read_bytes('/out/file0.txt') == b'file0 content'
        assert file_read_bytes('/out/emptyfile.txt') == b''
        assert file_read_bytes('/out/dir1/file1.txt') == b'file one.'
        assert len(file_read_bytes('/out/dir2/file2.png')) == 182

    def test_without_integrity(self, fs):
        """An archive without integrity is extracted, verification is refused."""
        fs.create_file('/extractthis.asar', contents=fixture.extractthis_430())
        result = unpack('/extractthis.asar', '/out')
        assert result.file_count == 5
        assert file_read_bytes('/out/file0.txt') == b'file0 content'
        with pytest.raises(AsarFormatError) as e:
            unpack('/extractthis.asar', '/out2', verify=True)
        assert str(e.value) == 'Entry "dir1/file1.txt" has no integrity hash to verify'

    def test_unpacked_entries(self, fs):
        """Entries stored next to the archive are copied from .unpacked."""
        fs.create_file('/unpack.asar', contents=fixture.packthis_unpack_430())
        for name, content in fixture.unpacked_430_files().items():
            fs.create_file(f'/unpack.asar.unpacked/{name}', contents=content)
        result = unpack('/unpack.asar', '/out', verify=True)
        assert result.unpacked_count == 1
        assert file_read_bytes('/out/dir2/file2.png') == fixture.unpacked_430_files()['dir2/file2.png']
        assert file_read_bytes('/out/dir2/file3.txt') == b'123'

    def test_unpacked_missing(self, fs):
        """An unpacked entry without its file next to the archive is an error."""
        fs.create_file('/unpack.asar', contents=fixture.packthis_unpack_430())
        with pytest.raises(FileNotFoundError):
            unpack('/unpack.asar', '/out')

    @pytest.mark.skipif(os.name == 'nt', reason='symbolic links need elevation on Windows')
    def test_links(self, fs):
        """Links are created as symbolic links on POSIX."""
        fs.create_file('/links.asar', contents=fixture.packthis_symlink_430())
        result = unpack('/links.asar', '/out')
        assert result.link_count == 2
        assert os.readlink('/out/Current') == 'A'
        assert os.readlink('/out/real.txt') == 'Current/real.txt'

    @pytest.mark.skipif(os.name != 'nt', reason='the fallback is Windows only')
    def test_links_on_windows(self, fs):
        """A link to a file is materialized as a copy on Windows."""
        header = {
            'files': {
                'real.txt': entry(5, 0, b'hello'),
                'l.txt': {'link': 'real.txt'},
                'deep': {
                    'files': {
                        'inside.txt': entry(2, 5, b'hi'),
                        'up.txt': {'link': 'deep/inside.txt'},
                    },
                },
            },
        }
        fs.create_file('/links.asar', contents=fixture.make_archive(header, b'hellohi'))
        result = unpack('/links.asar', '/out')
        assert result.link_count == 2
        assert file_read_bytes('/out/l.txt') == b'hello'
        assert file_read_bytes('/out/deep/up.txt') == b'hi'

    @pytest.mark.skipif(os.name != 'nt', reason='the fallback is Windows only')
    def test_links_on_windows_directory(self, fs):
        """A link to a directory can not be extracted on Windows."""
        content = fixture.make_archive({'files': {'dir': {'files': {}}, 'link': {'link': 'dir'}}})
        fs.create_file('/broken.asar', contents=content)
        with pytest.raises(AsarUnsupportedError) as e:
            unpack('/broken.asar', '/out')
        assert str(e.value) == (
            'Link "link" points to a directory, which can not be extracted on Windows'
        )
        # A dangling link is caught as well
        content = fixture.make_archive({'files': {'link': {'link': 'nope'}}})
        fs.create_file('/broken2.asar', contents=content)
        with pytest.raises(AsarEntryNotFoundError) as e:
            unpack('/broken2.asar', '/out2')
        assert str(e.value) == 'Entry "nope" does not exist in the archive'


class TestUnpackSafety:
    def test_verify_broken_hash(self, fs):
        """A content that does not match its hash fails the extraction."""
        header = {
            'files': {
                'a.txt': {
                    'size': 5,
                    'offset': '0',
                    'integrity': integrity(b'other'),
                },
            },
        }
        fs.create_file('/broken.asar', contents=fixture.make_archive(header, b'hello'))
        with pytest.raises(AsarFormatError) as e:
            unpack('/broken.asar', '/out', verify=True)
        assert str(e.value).startswith('Content hash of "a.txt" does not match')

    def test_verify_empty_file(self, fs):
        """The hash of an empty file is checked without reading anything."""
        header = {
            'files': {
                'a.txt': {'size': 0, 'offset': '0', 'integrity': integrity(b'x')},
            },
        }
        fs.create_file('/broken.asar', contents=fixture.make_archive(header, b''))
        with pytest.raises(AsarFormatError) as e:
            unpack('/broken.asar', '/out', verify=True)
        assert str(e.value) == (
            'Content hash of "a.txt" does not match, it is empty but its integrity hash is '
            '2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881 '
            'instead of e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
        )

    def test_bad_entry_name(self, fs):
        """An entry that can not be created is refused before extraction."""
        header = {'files': {'a:b': {'size': 1, 'offset': '0'}}}
        fs.create_file('/broken.asar', contents=fixture.make_archive(header, b'x'))
        with pytest.raises(AsarPathError) as e:
            unpack('/broken.asar', '/out')
        assert str(e.value).startswith('Invalid archive path "a:b"')

    def test_broken_archive(self, fs):
        """A truncated archive fails before anything is written."""
        fs.create_file('/broken.asar', contents=shared_archive()[:100])
        with pytest.raises(AsarFormatError):
            unpack('/broken.asar', '/out')

    def test_no_temporary_file_is_left(self, fs):
        """The atomic writes leave no temporary file behind."""
        fs.create_file('/gaps.asar', contents=shared_archive())
        unpack('/gaps.asar', '/out', region_budget=0, chunk_size=1024)
        leftovers = [name for name in fs._files if name.endswith('.tmp')]
        assert leftovers == []
