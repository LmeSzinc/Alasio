"""
Tests of the extraction of a whole archive.

The interesting cases are the entries that share bytes: 4.3.0 writes identical
content once and lets several entries point at it, an entry may also be a prefix
of another one, or be nested inside another one. Every entry is read from the
offset its header gives it, in the order of the data area, so these tests assert
the extracted content and the invariants of the reads: the offsets of the reads
follow each other and every entry is read exactly once. The statistics of an
extraction are not part of the API (a caller only needs the files), so they are
observed through a handle that records what is read from the archive.
"""
import os

import pytest

from alasio.codegen.asar import archive as archive_module
from alasio.codegen.asar.archive import AsarArchive, check_link_target, relative_link_target
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

# What an extraction reads from the data area: the content of every entry, the
# bytes two entries share are read by both of them and an empty entry reads
# nothing
ENTRY_BYTES = (
    len(LONG) + 2 * len(SHARED) + len(PREFIX) + (len(PREFIX) + len(TAIL)) + len(TAIL)
)


class SpyHandle:
    """
    An open archive that records how it is read.

    Only ``seek`` with an absolute offset is recorded: the offsets of the
    entries are the only ones, the header is read from the start of the file and
    its length comes from ``os.fstat()``.
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
        tuple[AsarArchive, SpyHandle]: The archive and the handle it reads
            through
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


class TestExtractSharedEntries:
    @pytest.mark.parametrize('chunk_size', [64 * 1024, 256 * 1024, 1024 * 1024])
    def test_shared_entries(self, fs, chunk_size):
        """Every chunk size extracts the same content from shared bytes."""
        fs.create_file('/shared.asar', contents=shared_archive())
        dest = f'/out-{chunk_size}'
        entries = extract('/shared.asar', dest, chunk_size=chunk_size, verify=True)
        assert len(paths_of(entries, KIND_FILE)) == 7
        assert len(paths_of(entries, KIND_DIR)) == 2
        for name, expected in EXPECTED.items():
            assert file_read_bytes(f'{dest}/{name}') == expected, name
        assert os.path.isdir(f'{dest}/dir')

    def test_every_entry_is_read_once(self, fs, monkeypatch):
        """Every entry is read from its own offset, and once."""
        fs.create_file('/shared.asar', contents=shared_archive())
        archive, handles = spied_archive(fs, monkeypatch, '/shared.asar')
        handle = archive.fd
        seeks = len(handle.seeks)
        archive.extract_all('/out', verify=True)
        # The offsets the entries are read from are the offsets of the entries,
        # in offset order: the entries of a real archive are stored back to back
        assert handle.seeks[seeks:] == sorted(
            info.source.offset
            for info in archive.files.values()
            if info.kind == KIND_FILE and info.size
        )
        # Every byte the entries hold is read once, the header is what comes
        # before the data area
        assert handle.read_bytes - archive.data_offset == ENTRY_BYTES
        archive.close()

    def test_gap_between_entries(self, fs, monkeypatch):
        """A gap between two entries is skipped, the reads follow the offsets."""
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
        archive.extract_all('/out')
        assert file_read_bytes('/out/a.txt') == b'aaaa'
        assert file_read_bytes('/out/b.txt') == b'bbbb'
        assert handle.seeks[seeks:] == [archive.data_offset, archive.data_offset + 12]
        archive.close()

    def test_unsorted_offsets(self, fs, monkeypatch):
        """Entries are read in the order of the data area, not of the table."""
        header = {
            'files': {
                'a.txt': entry(4, 4, b'aaaa'),
                'b.txt': entry(4, 0, b'bbbb'),
            },
        }
        fs.create_file('/unordered.asar', contents=fixture.make_archive(header, b'bbbbaaaa'))
        archive, handles = spied_archive(fs, monkeypatch, '/unordered.asar')
        handle = archive.fd
        seeks = len(handle.seeks)
        archive.extract_all('/out')
        assert handle.seeks[seeks:] == [archive.data_offset, archive.data_offset + 4]
        assert file_read_bytes('/out/a.txt') == b'aaaa'
        assert file_read_bytes('/out/b.txt') == b'bbbb'
        archive.close()

    def test_extracted_twice(self, fs, monkeypatch):
        """A second extraction reads the data area again, from its start."""
        fs.create_file('/shared.asar', contents=shared_archive())
        archive, handles = spied_archive(fs, monkeypatch, '/shared.asar')
        handle = archive.fd
        archive.extract_all('/out1')
        reads = handle.read_bytes
        archive.extract_all('/out2')
        assert handle.read_bytes == reads + ENTRY_BYTES
        assert handle.seeks[-1] == archive.data_offset + len(LONG) + len(SHARED) + len(PREFIX)
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
        extract('/shared.asar', '/deep/nested/out')
        assert file_read_bytes('/deep/nested/out/nested.txt') == TAIL

    def test_no_temporary_file_is_left(self, fs):
        """The atomic writes leave no temporary file behind."""
        fs.create_file('/gaps.asar', contents=shared_archive())
        extract('/gaps.asar', '/out', chunk_size=1024)
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
        entries = extract('/extractthis.asar', '/out', verify=False)
        assert len(paths_of(entries, KIND_FILE)) == 5
        assert file_read_bytes('/out/file0.txt') == b'file0 content'
        # There is nothing to compare the content with, so checking it (the
        # default) reports that instead of extracting silently
        with pytest.raises(AsarFormatError) as e:
            extract('/extractthis.asar', '/out2')
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

    def test_verify_broken_hash(self, fs):
        """A content that does not match its hash fails the extraction, by default."""
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
            extract('/broken.asar', '/out')
        assert str(e.value).startswith('Content hash of "a.txt" does not match')
        assert not os.path.exists('/out/a.txt')
        # The check can be turned off, the content is written as it is
        extract('/broken.asar', '/out2', verify=False)
        assert file_read_bytes('/out2/a.txt') == b'hello'

    def test_verify_empty_file(self, fs):
        """The hash of an empty file is checked without reading anything."""
        header = {
            'files': {
                'a.txt': {'size': 0, 'offset': '0', 'integrity': integrity(b'x')},
            },
        }
        fs.create_file('/broken.asar', contents=fixture.make_archive(header, b''))
        with pytest.raises(AsarFormatError) as e:
            extract('/broken.asar', '/out')
        assert str(e.value) == (
            'Content hash of "a.txt" does not match, it is '
            'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 instead of '
            '2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881'
        )

    def test_bad_entry_name(self, fs):
        """An entry that can not be created is refused when the archive is read."""
        header = {'files': {'a:b': {'size': 1, 'offset': '0'}}}
        fs.create_file('/broken.asar', contents=fixture.make_archive(header, b'x'))
        with pytest.raises(AsarPathError) as e:
            extract('/broken.asar', '/out')
        assert str(e.value) == 'Invalid entry name at "/": "a:b", Filename should not contain character: ":"'

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

    def test_verify_is_the_default(self, fs):
        """Extracting one entry checks its content against the header."""
        header = {'files': {'a.txt': {'size': 5, 'offset': '0', 'integrity': integrity(b'other')}}}
        fs.create_file('/broken.asar', contents=fixture.make_archive(header, b'hello'))
        with AsarArchive('/broken.asar') as archive:
            with pytest.raises(AsarFormatError) as e:
                archive.extract_file('a.txt', '/out/a.txt')
            assert str(e.value).startswith('Content hash of "a.txt" does not match')
            assert not os.path.exists('/out/a.txt')
            archive.extract_file('a.txt', '/out/b.txt', verify=False)
        assert file_read_bytes('/out/b.txt') == b'hello'

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


@pytest.fixture
def posix_links(monkeypatch):
    """
    Run the symbolic link branch of ``create_link()`` on every platform.

    Creating a symbolic link needs elevation on Windows, so the branch that
    writes the links of an archive is only exercised where the platform allows
    it (the tests that use the real os.symlink() are skipped here). The
    in-memory filesystem implements symlink() and resolves the links it holds,
    so the branch and the tree it builds are checked on every platform this way.

    Args:
        monkeypatch (MonkeyPatch): Patch helper of pytest
    """
    monkeypatch.setattr(archive_module, 'CAN_SYMLINK', True)


class TestRelativeLinkTarget:
    """
    The text of a symbolic link, rebased on the directory that holds it.

    The header stores the target of a link relative to the root of the archive,
    the file system needs it relative to the link. ``os.path`` of the platform
    is what builds the expectation, so the test runs everywhere.
    """
    @pytest.mark.parametrize('dest, target, link, expected', [
        # a link at the root of the extraction, the two are the same path
        ('/out', '/out/l.txt', 'a.txt', 'a.txt'),
        ('/out', '/out/l.txt', 'dir/a.txt', os.path.join('dir', 'a.txt')),
        # a link inside a directory, the target is the hop to its own directory
        ('/out', '/out/dir/l.txt', 'dir/a.txt', 'a.txt'),
        ('/out', '/out/dir/deep/up.txt', 'dir/a.txt', os.path.join('..', 'a.txt')),
        ('/out', '/out/A/reverse.txt', 'B/reverse.txt', os.path.join('..', 'B', 'reverse.txt')),
        ('/out', '/out/a/b/c.txt', 'd/e.txt', os.path.join('..', '..', 'd', 'e.txt')),
    ])
    def test_relative_link_target(self, dest, target, link, expected):
        """The hop from the directory of the link to its target."""
        assert relative_link_target(dest, target, link) == expected


class TestLinkTarget:
    """
    The links of an extracted tree.

    ``packthis_symlink_430`` holds a link inside a directory
    (``A/reverse-symlink.txt`` points at ``B/reverse-symlink.txt``), which the
    extraction used to write as the path of the header, a path that only means
    the right file when the link is at the root of the archive.
    """
    def test_a_link_of_a_subdirectory(self, fs, posix_links):
        """A link below the root points at its target from its own directory."""
        fs.create_file('/links.asar', contents=fixture.packthis_symlink_430())
        with AsarArchive('/links.asar') as archive:
            archive.extract_all('/out')
        assert os.readlink('/out/A/reverse-symlink.txt') == os.path.join('..', 'B', 'reverse-symlink.txt')
        assert os.path.realpath('/out/A/reverse-symlink.txt') == os.path.realpath('/out/B/reverse-symlink.txt')

    def test_the_links_of_the_root(self, fs, posix_links):
        """A link at the root of the archive keeps the path the header stores."""
        fs.create_file('/links.asar', contents=fixture.packthis_symlink_430())
        with AsarArchive('/links.asar') as archive:
            archive.extract_all('/out')
        assert os.readlink('/out/Current') == 'A'
        assert os.readlink('/out/real.txt') == os.path.join('Current', 'real.txt')

    def test_the_content_is_reachable_through_the_link(self, fs, posix_links):
        """The extracted tree resolves, reading through a link gives the content."""
        fs.create_file('/links.asar', contents=fixture.packthis_symlink_430())
        with AsarArchive('/links.asar') as archive:
            archive.extract_all('/out')
        assert file_read_bytes('/out/A/reverse-symlink.txt') == b'I SYMLINK TO SUPER DIR'
        assert file_read_bytes('/out/A/real.txt') == b'I AM REAL TXT FILE\n'

    def test_a_link_to_a_neighbour_and_to_the_directory_above(self, fs, posix_links):
        """The text of a link is relative to the directory it lives in."""
        header = {
            'files': {
                'dir': {
                    'files': {
                        'a.txt': entry(5, 0, b'hello'),
                        'l.txt': {'link': 'dir/a.txt'},
                        'deep': {'files': {'up.txt': {'link': 'dir/a.txt'}}},
                    },
                },
            },
        }
        fs.create_file('/links.asar', contents=fixture.make_archive(header, b'hello'))
        with AsarArchive('/links.asar') as archive:
            archive.extract_all('/out')
        # the target is a neighbour of the link itself
        assert os.readlink('/out/dir/l.txt') == 'a.txt'
        # the target is above the directory of the link
        assert os.readlink('/out/dir/deep/up.txt') == os.path.join('..', 'a.txt')
        assert file_read_bytes('/out/dir/l.txt') == b'hello'
        assert file_read_bytes('/out/dir/deep/up.txt') == b'hello'

    def test_extracting_twice_replaces_the_links(self, fs, posix_links):
        """A second extraction over the first one replaces the links it finds."""
        fs.create_file('/links.asar', contents=fixture.packthis_symlink_430())
        for _ in range(2):
            with AsarArchive('/links.asar') as archive:
                archive.extract_all('/out')
        assert os.readlink('/out/A/reverse-symlink.txt') == os.path.join('..', 'B', 'reverse-symlink.txt')
        assert file_read_bytes('/out/A/reverse-symlink.txt') == b'I SYMLINK TO SUPER DIR'

    def test_the_link_replaces_the_file_of_the_last_extraction(self, fs, posix_links):
        """The path of a link is replaced by the link, whatever was there."""
        fs.create_file('/plain.asar', contents=fixture.make_archive(
            {'files': {'A': {'files': {'reverse-symlink.txt': entry(4, 0, b'file')}}}},
            b'file',
        ))
        with AsarArchive('/plain.asar') as archive:
            archive.extract_all('/out')
        assert file_read_bytes('/out/A/reverse-symlink.txt') == b'file'
        fs.create_file('/links.asar', contents=fixture.packthis_symlink_430())
        with AsarArchive('/links.asar') as archive:
            archive.extract_all('/out')
        assert os.path.islink('/out/A/reverse-symlink.txt')
        assert file_read_bytes('/out/A/reverse-symlink.txt') == b'I SYMLINK TO SUPER DIR'


class TestLinkCheck:
    """The check of the target of a link, before the link is created."""
    def test_a_root_reached_through_a_link(self, fs):
        """A root that is itself a link is not a traversal of the directory it holds.

        The root of the extraction may be named through a symbolic link (a
        junction of Windows, "/var" of macOS), while the target of a link is
        checked on the path it really is.
        """
        fs.create_dir('/real')
        fs.create_symlink('/junction', '/real')
        check_link_target('/junction/out', 'sub/l.txt', 'a.txt')

    def test_a_target_that_leaves_the_tree(self, fs):
        """A target that is not inside the extraction is refused."""
        fs.create_dir('/real')
        fs.create_symlink('/junction', '/real')
        with pytest.raises(AsarPathError) as e:
            check_link_target('/junction/out', 'sub/l.txt', '../escape.txt')
        assert str(e.value).startswith('Invalid link target of "sub/l.txt": ')
