"""
Tests of the sequential extraction of a whole archive.

The interesting cases are the entries that share bytes: 4.3.0 writes identical
content once and lets several entries point at it, an entry may also be a prefix
of another one, or be nested inside another one. The scan merges them into
regions of the data area and reads every region once, so these tests assert both
the extracted content and the invariants of the scan: one read of the data area,
no seek, and every entry written exactly once. The statistics of an extraction
are not part of the API (a caller only needs the files), so they are observed
through a handle that records what is read from the archive.
"""
import os

import pytest

from alasio.codegen.asar import archive as archive_module
from alasio.codegen.asar.archive import REGION_BUDGET, AsarArchive
from alasio.codegen.asar.errors import AsarEntryNotFoundError, AsarFormatError, AsarPathError, AsarUnsupportedError
from alasio.codegen.asar.model import KIND_DIR, KIND_FILE, KIND_LINK
from alasio.ext.cache import InstanceCacheOperation
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


class SpyHandle:
    """
    An open archive that records how it is read.

    Only ``seek`` with an absolute offset is recorded: reading the header seeks
    to the end of the file to get its size, which is not a seek of the data area.
    """

    def __init__(self, handle):
        self.handle = handle
        self.seeks = []
        self.read_bytes = 0
        self.reads = []

    def seek(self, offset, whence=0):
        position = self.handle.seek(offset, whence)
        if whence == 0:
            self.seeks.append(offset)
        return position

    def read(self, size=-1):
        data = self.handle.read(size)
        self.reads.append(size)
        self.read_bytes += len(data)
        return data

    def __getattr__(self, name):
        return getattr(self.handle, name)


def spied_archive(fs, monkeypatch, path):
    """
    Open an archive whose handle records every read.

    Args:
        fs (FakeFilesystem): Fake filesystem
        monkeypatch (MonkeyPatch): Patch helper of pytest
        path (str): Path of the archive

    Returns:
        (AsarArchive, SpyHandle): The archive and the handle it reads through
    """
    original = archive_module.atomic_open
    handles = []

    def spied_open(*args, **kwargs):
        handle = SpyHandle(original(*args, **kwargs))
        handles.append(handle)
        return handle

    monkeypatch.setattr(archive_module, 'atomic_open', spied_open)
    archive = AsarArchive(path)
    # Read the header first, so what the caller observes is the extraction (this
    # is what entering the archive does)
    InstanceCacheOperation.warm(archive, 'header')
    return archive, handles


def paths_of(entries, kind):
    """
    Get the paths of the entries of one kind.

    Args:
        entries (list): ``[(path, info)]`` of an archive
        kind (str): 'file', 'dir' or 'link'

    Returns:
        list: Paths, in the canonical order of the archive
    """
    return [path for path, info in entries if info.kind == kind]


def extract(archive_path, dest, **kwargs):
    """
    Extract a whole archive to a directory.

    Args:
        archive_path (str): Path of the archive
        dest (str): Target directory
        **kwargs: Arguments of ``extract_all()``

    Returns:
        list: ``[(path, info)]`` of the entries, read before extraction
    """
    with AsarArchive(archive_path) as archive:
        entries = list(archive.iter_entries())
        archive.extract_all(dest, **kwargs)
    return entries


class TestExtractRegions:
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
        entries = extract(
            '/shared.asar', dest,
            region_budget=region_budget, chunk_size=chunk_size, verify=True,
        )
        assert len(paths_of(entries, KIND_FILE)) == 7
        assert len(paths_of(entries, KIND_DIR)) == 2
        for name, expected in EXPECTED.items():
            assert file_read_bytes(f'{dest}/{name}') == expected, name
        assert os.path.isdir(f'{dest}/dir')

    def test_data_area_is_read_once(self, fs, monkeypatch):
        """The data area is read once, however many entries share it."""
        fs.create_file('/shared.asar', contents=shared_archive())
        archive, handles = spied_archive(fs, monkeypatch, '/shared.asar')
        handle = archive.fd
        seeks = len(handle.seeks)
        reads = len(handle.reads)
        archive.extract_all('/out', verify=True)
        # The entries overlap and nest, the whole data area is one region
        assert len(handle.reads) == reads + 1
        assert handle.read_bytes - 8 - archive.header_size == len(DATA)
        assert len(handle.seeks) == seeks
        archive.close()

    def test_gap_needs_a_seek(self, fs, monkeypatch):
        """A gap between two entries is the only reason to seek."""
        header = {
            'files': {
                'a.txt': entry(4, 0, b'aaaa'),
                # A gap of 8 bytes that belongs to no entry
                'b.txt': entry(4, 12, b'bbbb'),
            },
        }
        fs.create_file('/gap.asar', contents=fixture.make_archive(header, b'aaaa' + b'x' * 8 + b'bbbb'))
        archive, handles = spied_archive(fs, monkeypatch, '/gap.asar')
        handle = archive.fd
        seeks = len(handle.seeks)
        archive.extract_all('/out', region_budget=0, chunk_size=2)
        assert file_read_bytes('/out/a.txt') == b'aaaa'
        assert file_read_bytes('/out/b.txt') == b'bbbb'
        assert len(handle.seeks) == seeks + 1
        archive.close()

    def test_unsorted_offsets(self, fs):
        """Entries are extracted in offset order, whatever the header order is."""
        header = {
            'files': {
                'b.txt': entry(4, 4, b'bbbb'),
                'a.txt': entry(4, 0, b'aaaa'),
            },
        }
        fs.create_file('/unordered.asar', contents=fixture.make_archive(header, b'aaaabbbb'))
        extract('/unordered.asar', '/out', region_budget=0, chunk_size=3)
        assert file_read_bytes('/out/a.txt') == b'aaaa'
        assert file_read_bytes('/out/b.txt') == b'bbbb'

    def test_extracted_twice(self, fs, monkeypatch):
        """A second extraction of the same archive reads the same content."""
        fs.create_file('/shared.asar', contents=shared_archive())
        archive, handles = spied_archive(fs, monkeypatch, '/shared.asar')
        handle = archive.fd
        seeks = len(handle.seeks)
        archive.extract_all('/out1')
        reads = handle.read_bytes
        # The handle is left at the end of the archive, extraction goes back to
        # the data area with a single seek, and reads no byte twice
        archive.extract_all('/out2')
        assert len(handle.seeks) == seeks + 1
        assert handle.read_bytes == reads + len(DATA)
        assert file_read_bytes('/out2/long.bin') == LONG
        archive.close()

    def test_empty_archive(self, fs):
        """An archive without entry extracts to an empty directory."""
        fs.create_file('/empty.asar', contents=fixture.make_archive({'files': {}}))
        entries = extract('/empty.asar', '/out')
        assert paths_of(entries, KIND_FILE) == []
        assert os.listdir('/out') == []

    def test_destination_is_created(self, fs):
        """The target directory is created when it does not exist yet."""
        fs.create_file('/shared.asar', contents=shared_archive())
        extract('/shared.asar', '/deep/nested/out', region_budget=0)
        assert file_read_bytes('/deep/nested/out/nested.txt') == TAIL

    def test_no_temporary_file_is_left(self, fs):
        """The atomic writes leave no temporary file behind."""
        fs.create_file('/gaps.asar', contents=shared_archive())
        extract('/gaps.asar', '/out', region_budget=0, chunk_size=1024)
        leftovers = [name for name in fs._files if name.endswith('.tmp')]
        assert leftovers == []


class TestExtractFixtures:
    def test_packthis(self, fs):
        """A 4.3.0 fixture is extracted file by file."""
        fs.create_file('/packthis.asar', contents=fixture.packthis_430())
        entries = extract('/packthis.asar', '/out', verify=True)
        assert len(paths_of(entries, KIND_FILE)) == 6
        assert len(paths_of(entries, KIND_DIR)) == 2
        assert file_read_bytes('/out/file0.txt') == b'file0 content'
        assert file_read_bytes('/out/emptyfile.txt') == b''
        assert file_read_bytes('/out/dir1/file1.txt') == b'file one.'
        assert len(file_read_bytes('/out/dir2/file2.png')) == 182

    def test_without_integrity(self, fs):
        """An archive without integrity is extracted, verification is refused."""
        fs.create_file('/extractthis.asar', contents=fixture.extractthis_430())
        entries = extract('/extractthis.asar', '/out')
        assert len(paths_of(entries, KIND_FILE)) == 5
        assert file_read_bytes('/out/file0.txt') == b'file0 content'
        with pytest.raises(AsarFormatError) as e:
            extract('/extractthis.asar', '/out2', verify=True)
        assert str(e.value) == 'Entry "dir1/file1.txt" has no integrity hash to verify'

    def test_unpacked_entries(self, fs):
        """Entries stored next to the archive are copied from .unpacked."""
        fs.create_file('/unpack.asar', contents=fixture.packthis_unpack_430())
        for name, content in fixture.unpacked_430_files().items():
            fs.create_file(f'/unpack.asar.unpacked/{name}', contents=content)
        entries = extract('/unpack.asar', '/out', verify=True)
        assert len([info for _, info in entries if info.unpacked]) == 1
        assert file_read_bytes('/out/dir2/file2.png') == fixture.unpacked_430_files()['dir2/file2.png']
        assert file_read_bytes('/out/dir2/file3.txt') == b'123'

    def test_unpacked_missing(self, fs):
        """An unpacked entry without its file next to the archive is an error."""
        fs.create_file('/unpack.asar', contents=fixture.packthis_unpack_430())
        with pytest.raises(FileNotFoundError):
            extract('/unpack.asar', '/out')

    @pytest.mark.skipif(os.name == 'nt', reason='symbolic links need elevation on Windows')
    def test_links(self, fs):
        """Links are created as symbolic links on POSIX."""
        fs.create_file('/links.asar', contents=fixture.packthis_symlink_430())
        entries = extract('/links.asar', '/out')
        assert paths_of(entries, KIND_LINK) == ['Current', 'real.txt']
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
        entries = extract('/links.asar', '/out')
        assert len(paths_of(entries, KIND_LINK)) == 2
        assert file_read_bytes('/out/l.txt') == b'hello'
        assert file_read_bytes('/out/deep/up.txt') == b'hi'

    @pytest.mark.skipif(os.name != 'nt', reason='the fallback is Windows only')
    def test_links_on_windows_directory(self, fs):
        """A link to a directory can not be extracted on Windows."""
        content = fixture.make_archive({'files': {'dir': {'files': {}}, 'link': {'link': 'dir'}}})
        fs.create_file('/broken.asar', contents=content)
        with pytest.raises(AsarUnsupportedError) as e:
            extract('/broken.asar', '/out')
        assert str(e.value) == (
            'Link "link" points to a directory, which can not be extracted on Windows'
        )
        # A dangling link is caught as well
        content = fixture.make_archive({'files': {'link': {'link': 'nope'}}})
        fs.create_file('/broken2.asar', contents=content)
        with pytest.raises(AsarEntryNotFoundError) as e:
            extract('/broken2.asar', '/out2')
        assert str(e.value) == 'Entry "nope" does not exist in the archive'


class TestExtractSafety:
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
            extract('/broken.asar', '/out', verify=True)
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
            extract('/broken.asar', '/out', verify=True)
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
            extract('/broken.asar', '/out')
        assert str(e.value).startswith('Invalid archive path "a:b"')

    @pytest.mark.parametrize('link', ['../escape', '/absolute', 'dir/../../escape'])
    def test_link_escape(self, fs, link):
        """A link that leaves the target directory is refused at extraction."""
        fs.create_file('/broken.asar', contents=fixture.make_archive(
            {'files': {'l.txt': {'link': link}}},
        ))
        with pytest.raises(AsarPathError) as e:
            extract('/broken.asar', '/out')
        assert str(e.value).startswith('Invalid link target of "l.txt": ')

    def test_broken_archive(self, fs):
        """A truncated archive fails before anything is written."""
        fs.create_file('/broken.asar', contents=shared_archive()[:100])
        with pytest.raises(AsarFormatError):
            extract('/broken.asar', '/out')

    def test_truncated_data_area(self, fs):
        """A data area that is shorter than the header claims is a format error."""
        fs.create_file('/broken.asar', contents=shared_archive()[:-10])
        with pytest.raises(AsarFormatError) as e:
            extract('/broken.asar', '/out')
        assert str(e.value) == (
            'Invalid entry at "prefix.txt": content is outside of the archive, '
            f'offset {len(LONG) + len(SHARED)} + size {len(PREFIX)} '
            f'exceeds the data area of {len(DATA) - 10} bytes'
        )

    @pytest.mark.skipif(os.name == 'nt', reason='Windows has no executable bit')
    def test_executable(self, fs):
        """An executable entry is extracted with its bit set."""
        header = {'files': {'run.sh': {'size': 2, 'offset': '0', 'executable': True}}}
        fs.create_file('/exec.asar', contents=fixture.make_archive(header, b'hi'))
        extract('/exec.asar', '/out')
        assert os.stat('/out/run.sh').st_mode & 0o777 == 0o755


class TestExtractFile:
    def test_seek_reads_one_entry(self, fs, monkeypatch):
        """Extracting one entry seeks once and reads only that entry."""
        fs.create_file('/shared.asar', contents=shared_archive())
        archive, handles = spied_archive(fs, monkeypatch, '/shared.asar')
        handle = archive.fd
        seeks = len(handle.seeks)
        archive.extract_file('nested.txt', '/out/nested.txt')
        assert len(handle.seeks) == seeks + 1
        assert handle.seeks[-1] == archive.data_offset + len(LONG) + len(SHARED) + len(PREFIX)
        assert handle.read_bytes - 8 - archive.header_size == len(TAIL)
        assert file_read_bytes('/out/nested.txt') == TAIL
        archive.close()

    def test_extract_file_unpacked(self, fs):
        """An unpacked entry is read next to the archive."""
        fs.create_file('/unpack.asar', contents=fixture.packthis_unpack_430())
        for name, content in fixture.unpacked_430_files().items():
            fs.create_file(f'/unpack.asar.unpacked/{name}', contents=content)
        with AsarArchive('/unpack.asar') as archive:
            archive.extract_file('dir2/file2.png', '/out/file2.png')
        assert file_read_bytes('/out/file2.png') == fixture.unpacked_430_files()['dir2/file2.png']

    def test_extract_file_link(self, fs):
        """Extracting a link extracts what it points at."""
        fs.create_file('/links.asar', contents=fixture.packthis_symlink_430())
        with AsarArchive('/links.asar') as archive:
            archive.extract_file('real.txt', '/out/real.txt')
        assert file_read_bytes('/out/real.txt') == b'I AM REAL TXT FILE\n'


class TestMixedTable:
    def test_extract_all(self, fs):
        """Archive entries, a local file and content in memory extract together."""
        fs.create_file('/shared.asar', contents=shared_archive())
        fs.create_file('/local.txt', contents=b'local content')
        with AsarArchive('/shared.asar') as archive:
            archive.add_file('/local.txt', 'added/local.txt')
            archive.add_file(data=b'generated', arc_path='added/generated.txt')
            assert archive.del_file('nested.txt') == 1
            archive.extract_all('/out', verify=True)
        for name, expected in EXPECTED.items():
            if name == 'nested.txt':
                continue
            assert file_read_bytes(f'/out/{name}') == expected, name
        assert not os.path.exists('/out/nested.txt')
        assert file_read_bytes('/out/added/local.txt') == b'local content'
        assert file_read_bytes('/out/added/generated.txt') == b'generated'

    def test_extract_file_of_a_local_entry(self, fs):
        """A single entry that was added is extracted from its own source."""
        fs.create_file('/shared.asar', contents=shared_archive())
        fs.create_file('/local.txt', contents=b'local content')
        with AsarArchive('/shared.asar') as archive:
            archive.add_file('/local.txt', 'local.txt')
            archive.extract_file('local.txt', '/deep/local.txt')
        assert file_read_bytes('/deep/local.txt') == b'local content'
