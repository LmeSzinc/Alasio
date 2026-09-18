"""
Tests of the write side: the entry table is turned into an archive file, the
content is read twice (scan and write) and both passes are compared.

The byte level expectations come from the reference implementation, see
``tests/codegen/asar/fixture.py``.
"""
import hashlib
import os

import msgspec
import pytest

from alasio.codegen.asar import AsarArchive, pack_sha256
from alasio.codegen.asar.errors import AsarEntryNotFoundError, AsarError, AsarPathError, AsarUnsupportedError
from alasio.codegen.asar.format import BLOCK_SIZE, read_header
from alasio.codegen.asar.model import KIND_DIR, KIND_FILE, KIND_LINK, AsarFileInfo, Integrity, iter_entries, keys_path
from alasio.codegen.asar.pack import CACHE_FILE_SIZE, ContentVerifier, hash_content, mark_unpacked, write_content
from alasio.codegen.asar.source import MemorySource
from alasio.ext.path.atomic import file_read_bytes
from alasio.testing.filesystem import fs  # noqa: F401
from tests.codegen.asar import fixture


def build_source_tree(fs):
    """
    Create the source tree of ``tiny.asar`` in the fake filesystem.

    Args:
        fs (FakeFilesystem): Fake filesystem

    Returns:
        str: Root path of the tree
    """
    root = f'{fs.root_dir.path}/src'
    fs.create_dir(root)
    fs.create_dir(f'{root}/sub')
    fs.create_file(f'{root}/hello.txt', contents=b'hello asar')
    fs.create_file(f'{root}/sub/bin.dat', contents=b'PAYLOAD')
    return root


def stored_header(archive_path):
    """
    Read the header of an archive file as it is stored.

    Args:
        archive_path (str): Path of the archive

    Returns:
        dict: Decoded header
    """
    with open(archive_path, 'rb') as f:
        json_bytes, _ = read_header(f)
    return msgspec.json.decode(json_bytes)


def archive_data(archive_path):
    """
    Read the content of the data area of an archive file.

    Args:
        archive_path (str): Path of the archive

    Returns:
        bytes: Content of the data area
    """
    with open(archive_path, 'rb') as f:
        _, data_offset = read_header(f)
    return file_read_bytes(archive_path)[data_offset:]


def stored_paths(archive_path):
    """
    Read the entry paths of an archive file in the order they are stored in.

    Args:
        archive_path (str): Path of the archive

    Returns:
        list: Archive paths
    """
    return [keys_path(keys) for keys, _, _ in iter_entries(stored_header(archive_path))]


def entry_paths(archive):
    """
    Read the entry paths of an archive, in the canonical order.

    Args:
        archive (AsarArchive): Archive to read

    Returns:
        list: Archive paths
    """
    return [path for path, _ in archive.iter_entries()]


def counts(archive):
    """
    Count the entries of an archive by kind.

    Args:
        archive (AsarArchive): Archive to count

    Returns:
        tuple[int, int, int, int]: Packed files, unpacked files, directories and
            links
    """
    packed = 0
    unpacked = 0
    directories = 0
    links = 0
    for _, info in archive.iter_entries():
        if info.kind == KIND_DIR:
            directories += 1
        elif info.kind == KIND_LINK:
            links += 1
        elif info.unpacked:
            unpacked += 1
        else:
            packed += 1
    return packed, unpacked, directories, links


def sha256(data):
    """
    Hash a content.

    Args:
        data (bytes): Content

    Returns:
        str: SHA256 hex digest
    """
    return hashlib.sha256(data).hexdigest()


class TestAddFile:
    def test_add_file_bytes(self, fs):
        """Adding files in an explicit order reproduces the 3.4.1 archive."""
        root = build_source_tree(fs)
        archive = AsarArchive()
        info = archive.add_file(f'{root}/hello.txt')
        assert info.kind == KIND_FILE
        assert archive.entry('hello.txt') is info
        archive.add_file(f'{root}/sub/bin.dat', 'sub/bin.dat')
        assert archive.write('/out.asar') is None
        assert file_read_bytes('/out.asar') == fixture.tiny_341()
        assert counts(archive) == (2, 0, 1, 0)
        assert os.path.getsize('/out.asar') == 549
        assert archive.header_size == 524
        assert archive.data_offset == 532
        assert pack_sha256('/out.asar') == sha256(fixture.tiny_341())

    def test_add_file_arc_path(self, fs):
        """The archive path defaults to the file name and may be given."""
        root = build_source_tree(fs)
        archive = AsarArchive()
        archive.add_file(f'{root}/hello.txt', 'deep/nested/renamed.txt')
        archive.write('/out.asar')
        header = stored_header('/out.asar')
        assert header == {
            'files': {
                'deep': {
                    'files': {
                        'nested': {
                            'files': {
                                'renamed.txt': {
                                    'size': 10,
                                    'offset': '0',
                                    'integrity': {
                                        'algorithm': 'SHA256',
                                        'hash': sha256(b'hello asar'),
                                        'blockSize': BLOCK_SIZE,
                                        'blocks': [sha256(b'hello asar')],
                                    },
                                },
                            },
                        },
                    },
                },
            },
        }

    def test_add_file_content(self, fs):
        """Content that does not exist on disk can be added."""
        archive = AsarArchive()
        archive.add_file(data=b'{"name":"alasio"}', arc_path='package.json')
        archive.write('/out.asar')
        assert archive_data('/out.asar') == b'{"name":"alasio"}'
        with AsarArchive('/out.asar') as reader:
            assert bytes(reader.read_file('package.json')) == b'{"name":"alasio"}'

    @pytest.mark.parametrize('kwargs, expected', [
        ({}, 'add_file() needs a path or a content, use mark_unpack() to mark an entry'),
        ({'data': b'x'}, 'add_file() needs an arc_path when there is no path'),
    ])
    def test_add_file_arguments(self, fs, kwargs, expected):
        """add_file needs a source, and a path for its content."""
        with pytest.raises(ValueError) as e:
            AsarArchive().add_file(**kwargs)
        assert str(e.value) == expected

    @pytest.mark.parametrize('arc_path, expected', [
        ('', 'Archive path must be a non empty string'),
        ('/absolute.txt', 'Archive path must be relative'),
        ('a:b.txt', 'Invalid archive path "a:b.txt"'),
        ('dir/CON', 'Invalid archive path "dir/CON"'),
        ('dir/', 'Invalid archive path "dir/"'),
    ])
    def test_add_file_invalid_path(self, fs, arc_path, expected):
        """A local path that can not be extracted everywhere is refused."""
        with pytest.raises(AsarPathError) as e:
            AsarArchive().add_file(data=b'x', arc_path=arc_path)
        assert expected in str(e.value)

    def test_add_file_replaces_in_place(self, fs):
        """Adding the same path again replaces the entry, keeping its position."""
        root = build_source_tree(fs)
        archive = AsarArchive()
        archive.add_file(f'{root}/hello.txt')
        archive.add_file(f'{root}/sub/bin.dat', 'sub/bin.dat')
        assert entry_paths(archive) == ['hello.txt', 'sub', 'sub/bin.dat']
        archive.add_file(data=b'changed', arc_path='hello.txt')
        assert entry_paths(archive) == ['hello.txt', 'sub', 'sub/bin.dat']
        archive.write('/out.asar')
        with AsarArchive('/out.asar') as reader:
            assert entry_paths(reader) == ['hello.txt', 'sub', 'sub/bin.dat']
            assert bytes(reader.read_file('hello.txt')) == b'changed'
            assert bytes(reader.read_file('sub/bin.dat')) == b'PAYLOAD'

    def test_add_file_parent_directories(self, fs):
        """The parent directories of an added file are entries of the table."""
        archive = AsarArchive()
        archive.add_file(data=b'x', arc_path='deep/nested/a.txt')
        assert entry_paths(archive) == ['deep', 'deep/nested', 'deep/nested/a.txt']
        assert archive.entry('deep').kind == KIND_DIR
        archive.add_file(data=b'y', arc_path='deep/other.txt')
        assert entry_paths(archive) == ['deep', 'deep/nested', 'deep/nested/a.txt', 'deep/other.txt']

    def test_add_file_parent_is_a_file(self, fs):
        """A directory may not be created where a file already is."""
        archive = AsarArchive()
        archive.add_file(data=b'x', arc_path='deep')
        with pytest.raises(AsarPathError) as e:
            archive.add_file(data=b'y', arc_path='deep/nested.txt')
        assert str(e.value) == 'Archive path "deep" is already a file'

    def test_add_file_over_a_directory(self, fs):
        """A file may not take the path of a directory."""
        archive = AsarArchive()
        archive.add_file(data=b'x', arc_path='deep/a.txt')
        with pytest.raises(AsarPathError) as e:
            archive.add_file(data=b'y', arc_path='deep')
        assert str(e.value) == 'Archive path "deep" is already a directory'


class TestAddFolder:
    def test_add_folder_tiny(self, fs):
        """A tree is added level by level, directories before their content."""
        root = build_source_tree(fs)
        archive = AsarArchive()
        assert archive.add_folder(root) == 3
        archive.write('/out.asar')
        assert file_read_bytes('/out.asar') == fixture.tiny_341()

    def test_add_folder_keeps_empty_dirs(self, fs):
        """Empty directories are entries of the archive, nested ones as well."""
        root = build_source_tree(fs)
        fs.create_dir(f'{root}/empty')
        fs.create_dir(f'{root}/sub/deeper')
        archive = AsarArchive()
        archive.add_folder(root)
        assert entry_paths(archive) == [
            'empty', 'hello.txt', 'sub', 'sub/bin.dat', 'sub/deeper',
        ]
        archive.write('/out.asar')
        with AsarArchive('/out.asar') as reader:
            assert entry_paths(reader) == ['empty', 'hello.txt', 'sub', 'sub/bin.dat', 'sub/deeper']
            assert reader.entry('empty').kind == KIND_DIR
            assert reader.entry('sub/deeper').kind == KIND_DIR

    def test_add_folder_unpack(self, fs):
        """A tree that is added unpacked is stored next to the archive."""
        root = build_source_tree(fs)
        archive = AsarArchive()
        archive.add_folder(root, unpack=True)
        archive.write('/out.asar')
        assert file_read_bytes('/out.asar.unpacked/hello.txt') == b'hello asar'
        assert file_read_bytes('/out.asar.unpacked/sub/bin.dat') == b'PAYLOAD'
        header = stored_header('/out.asar')
        assert header['files']['hello.txt'] == {
            'size': 10,
            'unpacked': True,
            'integrity': {
                'algorithm': 'SHA256',
                'hash': sha256(b'hello asar'),
                'blockSize': BLOCK_SIZE,
                'blocks': [sha256(b'hello asar')],
            },
        }
        assert header['files']['sub']['unpacked'] is True
        assert 'offset' not in header['files']['sub']['files']['bin.dat']
        with AsarArchive('/out.asar') as reader:
            assert counts(reader) == (0, 2, 1, 0)
            assert bytes(reader.read_file('sub/bin.dat')) == b'PAYLOAD'
            assert bytes(reader.read_file('hello.txt')) == b'hello asar'

    def test_add_file_unpack(self, fs):
        """A single file can be marked as unpacked with add_file()."""
        root = build_source_tree(fs)
        archive = AsarArchive()
        archive.add_folder(root)
        archive.add_file(f'{root}/sub/bin.dat', arc_path='sub/bin.dat', unpack=True)
        archive.write('/out.asar')
        assert file_read_bytes('/out.asar.unpacked/sub/bin.dat') == b'PAYLOAD'
        header = stored_header('/out.asar')
        assert header['files']['hello.txt']['offset'] == '0'
        assert header['files']['sub']['files']['bin.dat']['unpacked'] is True
        assert 'offset' not in header['files']['sub']['files']['bin.dat']
        with AsarArchive('/out.asar') as reader:
            assert counts(reader) == (1, 1, 1, 0)
            assert bytes(reader.read_file('sub/bin.dat')) == b'PAYLOAD'
            assert bytes(reader.read_file('hello.txt')) == b'hello asar'

    def test_add_folder_stale_unpacked_is_kept(self, fs):
        """An old unpacked file is not removed, the caller owns the directory."""
        root = build_source_tree(fs)
        fs.create_file('/out.asar.unpacked/old.txt', contents=b'old')
        archive = AsarArchive()
        archive.add_folder(root)
        archive.write('/out.asar')
        assert file_read_bytes('/out.asar.unpacked/old.txt') == b'old'

    def test_add_folder_empty(self, fs):
        """An empty directory packs to an empty archive."""
        fs.create_dir('/empty')
        archive = AsarArchive()
        assert archive.add_folder('/empty') == 0
        archive.write('/out.asar')
        assert os.path.getsize('/out.asar') == 28
        assert file_read_bytes('/out.asar') == fixture.make_archive({'files': {}})

    def test_add_folder_unicode(self, fs):
        """Non ASCII names are stored as UTF-8."""
        fs.create_dir('/tree')
        fs.create_file('/tree/中文.txt', contents=b'text')
        archive = AsarArchive()
        archive.add_folder('/tree')
        archive.write('/out.asar')
        with AsarArchive('/out.asar') as reader:
            assert entry_paths(reader) == ['中文.txt']
            assert bytes(reader.read_file('中文.txt')) == b'text'


class TestUnpackedInheritance:
    def test_a_new_entry_follows_its_directory(self, fs):
        """A file added below an unpacked directory is stored unpacked as well."""
        root = build_source_tree(fs)
        archive = AsarArchive()
        archive.add_folder(root, unpack=True)
        info = archive.add_file(data=b'added', arc_path='sub/added.txt')
        assert info.unpacked is True

    def test_a_packed_tree_stays_packed(self, fs):
        """A file added below a packed directory stays packed."""
        root = build_source_tree(fs)
        archive = AsarArchive()
        archive.add_folder(root)
        info = archive.add_file(data=b'added', arc_path='sub/added.txt')
        assert info.unpacked is False

    def test_replacing_keeps_the_flag(self, fs):
        """A file that replaces an unpacked entry stays unpacked."""
        root = build_source_tree(fs)
        archive = AsarArchive()
        archive.add_folder(root)
        archive.add_file(f'{root}/sub/bin.dat', arc_path='sub/bin.dat', unpack=True)
        assert archive.entry('sub/bin.dat').unpacked is True
        info = archive.add_file(data=b'replaced', arc_path='sub/bin.dat')
        assert info.unpacked is True

    def test_the_flag_can_be_forced(self, fs):
        """An explicit flag wins over what the entry or its directory says."""
        archive = AsarArchive()
        archive.add_file(data=b'one', arc_path='packed/a.txt')
        archive.add_file(data=b'two', arc_path='packed/b.txt', unpack=True)
        assert archive.entry('packed/b.txt').unpacked is True
        archive.add_file(data=b'three', arc_path='packed/b.txt', unpack=False)
        assert archive.entry('packed/b.txt').unpacked is False

    def test_written_archive(self, fs):
        """The inherited flag reaches the archive and the .unpacked directory."""
        root = build_source_tree(fs)
        archive = AsarArchive()
        archive.add_folder(root, unpack=True)
        archive.add_file(data=b'added', arc_path='sub/added.txt')
        archive.write('/out.asar')
        header = stored_header('/out.asar')
        assert header['files']['sub']['files']['added.txt']['unpacked'] is True
        assert 'offset' not in header['files']['sub']['files']['added.txt']
        assert file_read_bytes('/out.asar.unpacked/sub/added.txt') == b'added'
        with AsarArchive('/out.asar') as reader:
            assert bytes(reader.read_file('sub/added.txt')) == b'added'


class TestMarkUnpack:
    def test_mark_a_file(self, fs):
        """A marked file keeps its content next to the archive."""
        root = build_source_tree(fs)
        archive = AsarArchive()
        archive.add_folder(root)
        assert archive.mark_unpack('sub/bin.dat') == 1
        assert archive.entry('sub/bin.dat').unpacked is True
        assert archive.entry('sub/bin.dat').offset is None
        assert archive.entry('hello.txt').unpacked is False
        archive.write('/out.asar')
        assert file_read_bytes('/out.asar.unpacked/sub/bin.dat') == b'PAYLOAD'
        with AsarArchive('/out.asar') as reader:
            assert counts(reader) == (1, 1, 1, 0)
            assert bytes(reader.read_file('sub/bin.dat')) == b'PAYLOAD'
            assert bytes(reader.read_file('hello.txt')) == b'hello asar'

    def test_mark_a_directory(self, fs):
        """A marked directory takes its whole subtree with it."""
        root = build_source_tree(fs)
        archive = AsarArchive()
        archive.add_folder(root)
        assert archive.mark_unpack('sub') == 2
        assert archive.entry('sub').unpacked is True
        assert archive.entry('sub/bin.dat').unpacked is True
        assert archive.entry('hello.txt').unpacked is False
        archive.write('/out.asar')
        header = stored_header('/out.asar')
        assert header['files']['sub']['unpacked'] is True
        assert header['files']['sub']['files']['bin.dat']['unpacked'] is True
        assert 'offset' not in header['files']['sub']['files']['bin.dat']

    def test_mark_a_link(self, fs):
        """A link below a marked directory carries the flag as well."""
        fs.create_file('/link.asar', contents=fixture.make_archive(
            {'files': {'dir': {'files': {
                'a.txt': {'size': 1, 'offset': '0'},
                'l.txt': {'link': 'dir/a.txt'},
            }}}},
            b'x',
        ))
        with AsarArchive('/link.asar') as archive:
            assert archive.mark_unpack('dir') == 3
            archive.write('/out.asar')
        header = stored_header('/out.asar')
        assert header['files']['dir']['unpacked'] is True
        assert header['files']['dir']['files']['a.txt']['unpacked'] is True
        assert header['files']['dir']['files']['l.txt']['unpacked'] is True
        assert file_read_bytes('/out.asar.unpacked/dir/a.txt') == b'x'

    def test_mark_twice(self, fs):
        """Marking what is unpacked already marks nothing."""
        root = build_source_tree(fs)
        archive = AsarArchive()
        archive.add_folder(root)
        assert archive.mark_unpack('sub') == 2
        assert archive.mark_unpack('sub') == 0
        assert archive.mark_unpack('sub/bin.dat') == 0

    def test_mark_a_missing_entry(self, fs):
        """Marking an entry that is not there is an error."""
        archive = AsarArchive()
        with pytest.raises(AsarEntryNotFoundError) as e:
            archive.mark_unpack('nope.txt')
        assert str(e.value) == 'Entry "nope.txt" does not exist in the archive'

    def test_mark_an_entry_of_an_archive(self, fs):
        """An entry of an archive is copied out of the body when it is marked."""
        fs.create_file('/tiny.asar', contents=fixture.tiny_341())
        with AsarArchive('/tiny.asar') as archive:
            assert archive.mark_unpack('hello.txt') == 1
            archive.write('/out.asar')
        assert file_read_bytes('/out.asar.unpacked/hello.txt') == b'hello asar'
        with AsarArchive('/out.asar') as reader:
            assert counts(reader) == (1, 1, 1, 0)
            assert bytes(reader.read_file('hello.txt')) == b'hello asar'
            assert bytes(reader.read_file('sub/bin.dat')) == b'PAYLOAD'


class TestMarkUnpacked:
    """The unpacked flag of the entries is settled before an archive is written."""
    def test_mark_subtree(self):
        """Every entry below an unpacked directory becomes unpacked."""
        files = {
            ('dir',): AsarFileInfo(kind=KIND_DIR, unpacked=True),
            ('dir', 'sub'): AsarFileInfo(kind=KIND_DIR),
            ('dir', 'a.txt'): AsarFileInfo(kind=KIND_FILE, size=1, offset=0),
            ('dir', 'sub', 'b.txt'): AsarFileInfo(kind=KIND_FILE, size=1, offset=1),
            ('other.txt',): AsarFileInfo(kind=KIND_FILE, size=1, offset=2),
        }
        mark_unpacked(files)
        assert files[('dir',)].unpacked is True
        assert files[('dir', 'a.txt')].unpacked is True
        assert files[('dir', 'a.txt')].offset is None
        # A directory inside an unpacked subtree carries the flag as well
        assert files[('dir', 'sub')].unpacked is True
        assert files[('dir', 'sub', 'b.txt')].unpacked is True
        assert files[('dir', 'sub', 'b.txt')].offset is None
        assert files[('other.txt',)].unpacked is False
        assert files[('other.txt',)].offset == 2

    def test_deep_subtree(self):
        """An entry three levels below the unpacked directory is caught as well."""
        files = {
            ('a',): AsarFileInfo(kind=KIND_DIR, unpacked=True),
            ('a', 'b'): AsarFileInfo(kind=KIND_DIR),
            ('a', 'b', 'c'): AsarFileInfo(kind=KIND_DIR),
            ('a', 'b', 'c', 'd.txt'): AsarFileInfo(kind=KIND_FILE, size=1, offset=5),
        }
        mark_unpacked(files)
        assert files[('a', 'b')].unpacked is True
        assert files[('a', 'b', 'c')].unpacked is True
        assert files[('a', 'b', 'c', 'd.txt')].unpacked is True
        assert files[('a', 'b', 'c', 'd.txt')].offset is None

    def test_the_order_of_the_table_does_not_matter(self):
        """An entry is marked whatever the order the entries are stored in."""
        # The content is stored before the directory it lives in, the flag is
        # looked up on the directories of the table, not on the ones that were
        # walked before it
        files = {
            ('dir', 'sub', 'deep.txt'): AsarFileInfo(kind=KIND_FILE, size=1, offset=3),
            ('dir', 'sub'): AsarFileInfo(kind=KIND_DIR),
            ('dir',): AsarFileInfo(kind=KIND_DIR, unpacked=True),
            ('dir', 'a.txt'): AsarFileInfo(kind=KIND_FILE, size=1, offset=0),
        }
        mark_unpacked(files)
        assert files[('dir', 'sub', 'deep.txt')].unpacked is True
        assert files[('dir', 'sub', 'deep.txt')].offset is None
        assert files[('dir', 'sub')].unpacked is True
        assert files[('dir', 'a.txt')].unpacked is True
        assert files[('dir', 'a.txt')].offset is None

    def test_two_subtrees(self):
        """Only the entries below an unpacked directory are marked."""
        files = {
            ('a',): AsarFileInfo(kind=KIND_DIR, unpacked=True),
            ('a', 'x.txt'): AsarFileInfo(kind=KIND_FILE, size=1, offset=0),
            ('b',): AsarFileInfo(kind=KIND_DIR),
            ('b', 'y.txt'): AsarFileInfo(kind=KIND_FILE, size=1, offset=1),
        }
        mark_unpacked(files)
        assert files[('a', 'x.txt')].unpacked is True
        assert files[('b', 'y.txt')].unpacked is False
        assert files[('b', 'y.txt')].offset == 1

    def test_nothing_to_do(self):
        """Without an unpacked directory the table is left untouched."""
        files = {
            ('dir',): AsarFileInfo(kind=KIND_DIR),
            ('dir', 'a.txt'): AsarFileInfo(kind=KIND_FILE, size=1, offset=0),
        }
        mark_unpacked(files)
        assert files[('dir', 'a.txt')].unpacked is False
        assert files[('dir', 'a.txt')].offset == 0
        assert files[('dir',)].unpacked is False

    def test_unpacked_file_keeps_its_flag(self):
        """An unpacked file that is already marked is not read again."""
        files = {
            ('dir',): AsarFileInfo(kind=KIND_DIR, unpacked=True),
            ('dir', 'a.txt'): AsarFileInfo(kind=KIND_FILE, size=1, unpacked=True),
        }
        mark_unpacked(files)
        assert files[('dir', 'a.txt')].unpacked is True

    def test_a_link_follows_its_directory(self):
        """A link below an unpacked directory carries the flag as well."""
        files = {
            ('dir',): AsarFileInfo(kind=KIND_DIR, unpacked=True),
            ('dir', 'l.txt'): AsarFileInfo(kind=KIND_LINK, link='dir/l.txt'),
        }
        mark_unpacked(files)
        assert files[('dir', 'l.txt')].unpacked is True


class TestDeleteEntries:
    def test_del_file(self, fs):
        """A file entry is removed, its directory is kept."""
        root = build_source_tree(fs)
        archive = AsarArchive()
        archive.add_folder(root)
        assert archive.del_file('hello.txt') == 1
        assert entry_paths(archive) == ['sub', 'sub/bin.dat']
        archive.write('/out.asar')
        assert stored_paths('/out.asar') == ['sub', 'sub/bin.dat']

    def test_del_file_missing(self, fs):
        """Deleting an entry that is not there is an error."""
        archive = AsarArchive()
        archive.add_folder(build_source_tree(fs))
        with pytest.raises(Exception) as e:
            archive.del_file('nope.txt')
        assert str(e.value) == 'Entry "nope.txt" does not exist in the archive'
        assert entry_paths(archive) == ['hello.txt', 'sub', 'sub/bin.dat']

    def test_del_file_is_a_directory(self, fs):
        """A directory is deleted with del_folder."""
        archive = AsarArchive()
        archive.add_folder(build_source_tree(fs))
        with pytest.raises(AsarPathError) as e:
            archive.del_file('sub')
        assert str(e.value) == 'Entry "sub" is a directory, use del_folder() to delete it'

    def test_del_folder(self, fs):
        """A directory is removed with its whole subtree, its parents are kept."""
        archive = AsarArchive()
        archive.add_folder(build_source_tree(fs))
        assert archive.del_folder('sub') == 2
        assert entry_paths(archive) == ['hello.txt']
        archive.write('/out.asar')
        assert stored_paths('/out.asar') == ['hello.txt']

    def test_del_folder_nested(self, fs):
        """The count of a deleted directory includes every entry below it."""
        archive = AsarArchive()
        archive.add_file(data=b'x', arc_path='a/b/c/d.txt')
        archive.add_file(data=b'x', arc_path='a/b/e.txt')
        assert archive.del_folder('a/b') == 4
        assert entry_paths(archive) == ['a']

    def test_del_folder_is_a_file(self, fs):
        """A file is deleted with del_file."""
        archive = AsarArchive()
        archive.add_folder(build_source_tree(fs))
        with pytest.raises(AsarPathError) as e:
            archive.del_folder('hello.txt')
        assert str(e.value) == 'Entry "hello.txt" is a file, use del_file() to delete it'

    def test_del_folder_missing(self, fs):
        """Deleting a directory that is not there is an error."""
        archive = AsarArchive()
        archive.add_folder(build_source_tree(fs))
        with pytest.raises(Exception) as e:
            archive.del_folder('nope')
        assert str(e.value) == 'Entry "nope" does not exist in the archive'

    def test_del_does_not_break_the_layout(self, fs):
        """Removing entries keeps the offsets of the others consistent."""
        archive = AsarArchive()
        archive.add_file(data=b'aaaa', arc_path='a.txt')
        archive.add_file(data=b'bbbb', arc_path='b.txt')
        archive.add_file(data=b'cccc', arc_path='c.txt')
        archive.del_file('b.txt')
        archive.write('/out.asar')
        header = stored_header('/out.asar')
        assert header['files']['a.txt']['offset'] == '0'
        assert header['files']['c.txt']['offset'] == '4'
        with AsarArchive('/out.asar') as reader:
            assert bytes(reader.read_file('c.txt')) == b'cccc'


class TestWrite:
    def test_deterministic(self, fs):
        """Packing the same tree twice gives the same bytes."""
        root = build_source_tree(fs)
        first = AsarArchive()
        first.add_folder(root)
        first.write('/first.asar')
        second = AsarArchive()
        second.add_folder(root)
        second.write('/second.asar')
        assert file_read_bytes('/first.asar') == file_read_bytes('/second.asar')

    def test_write_twice(self, fs):
        """Writing the same archive twice gives the same bytes."""
        root = build_source_tree(fs)
        archive = AsarArchive()
        archive.add_folder(root)
        archive.write('/first.asar')
        archive.write('/second.asar')
        assert file_read_bytes('/first.asar') == file_read_bytes('/second.asar')

    def test_canonical_order(self, fs):
        """The entries are stored in the canonical order, whatever was added first.

        The canonical order is a flat order: a directory comes before its
        content and ``.node`` files come last. The header nests the content of a
        directory below it, so reading the header back lists every entry right
        after its directory; the offsets of the data area show the flat order.
        """
        archive = AsarArchive()
        for path in ['b/x.node', 'a.txt', 'c/d.txt', 'e.node']:
            archive.add_file(data=path.encode(), arc_path=path)
        archive.write('/out.asar')
        assert stored_paths('/out.asar') == ['a.txt', 'b', 'b/x.node', 'c', 'c/d.txt', 'e.node']
        # The data area follows the flat order, the offsets are back to back and
        # the .node file is stored last
        header = stored_header('/out.asar')
        assert header['files']['a.txt']['offset'] == '0'
        assert header['files']['c']['files']['d.txt']['offset'] == str(len(b'a.txt'))
        assert header['files']['b']['files']['x.node']['offset'] == str(len(b'a.txt') + len(b'c/d.txt'))
        assert header['files']['e.node']['offset'] == str(
            len(b'a.txt') + len(b'c/d.txt') + len(b'b/x.node')
        )

    def test_order_does_not_depend_on_the_operations(self, fs):
        """Any sequence of operations that ends with the same entries writes them the same."""
        first = AsarArchive()
        first.add_file(data=b'one', arc_path='a.txt')
        first.add_file(data=b'two', arc_path='b.txt')
        first.add_file(data=b'three', arc_path='a/c.txt')
        first.del_file('b.txt')
        first.write('/first.asar')
        second = AsarArchive()
        second.add_file(data=b'three', arc_path='a/c.txt')
        second.add_file(data=b'one', arc_path='a.txt')
        second.add_file(data=b'ignored', arc_path='b.txt')
        second.del_file('b.txt')
        second.write('/second.asar')
        assert file_read_bytes('/first.asar') == file_read_bytes('/second.asar')

    def test_size_comes_from_content(self, fs, monkeypatch):
        """The header size is the byte count that was read, not os.stat()."""
        root = build_source_tree(fs)

        def no_stat(*args, **kwargs):
            raise AssertionError('the pack must not call os.stat()')

        monkeypatch.setattr(os, 'stat', no_stat)
        archive = AsarArchive()
        archive.add_folder(root)
        archive.write('/out.asar')
        header = stored_header('/out.asar')
        assert header['files']['hello.txt']['size'] == 10
        assert header['files']['sub']['files']['bin.dat']['size'] == 7

    def test_second_pass_checks_the_content(self, fs, monkeypatch):
        """A content that changes between the two passes fails the pack."""
        from alasio.codegen.asar import pack

        root = build_source_tree(fs)
        original = pack.iter_write_content

        def changed(keys, info, fd, cache, chunk_size=None):
            if keys == ('hello.txt',):
                yield b'hello asar and more'
                return
            for chunk in original(keys, info, fd, cache, chunk_size):
                yield chunk

        archive = AsarArchive()
        archive.add_folder(root)
        monkeypatch.setattr(pack, 'iter_write_content', changed)
        with pytest.raises(AsarError) as e:
            archive.write('/out.asar')
        assert str(e.value) == (
            'Content of "hello.txt" does not match, 10 bytes were expected but 19 bytes were written'
        )

    def test_second_pass_checks_the_hash(self, fs, monkeypatch):
        """A content of the same size but a different hash fails the pack."""
        from alasio.codegen.asar import pack

        root = build_source_tree(fs)
        original = pack.iter_write_content

        def changed(keys, info, fd, cache, chunk_size=None):
            if keys == ('hello.txt',):
                yield b'HELLO ASAR'
                return
            for chunk in original(keys, info, fd, cache, chunk_size):
                yield chunk

        archive = AsarArchive()
        archive.add_folder(root)
        monkeypatch.setattr(pack, 'iter_write_content', changed)
        with pytest.raises(AsarError) as e:
            archive.write('/out.asar')
        assert str(e.value) == (
            f'Content hash of "hello.txt" does not match, it is {sha256(b"HELLO ASAR")} '
            f'instead of {sha256(b"hello asar")}'
        )

    def test_failed_pack_leaves_nothing(self, fs, monkeypatch):
        """A failed pack removes its temporary file and keeps the archive usable."""
        from alasio.codegen.asar import pack

        root = build_source_tree(fs)
        original = pack.iter_write_content
        broken = {'on': True}

        def changed(keys, info, fd, cache, chunk_size=None):
            if broken['on'] and keys == ('hello.txt',):
                yield b'changed'
                return
            for chunk in original(keys, info, fd, cache, chunk_size):
                yield chunk

        archive = AsarArchive()
        archive.add_folder(root)
        monkeypatch.setattr(pack, 'iter_write_content', changed)
        with pytest.raises(AsarError):
            archive.write('/out.asar')
        assert not os.path.exists('/out.asar')
        leftovers = [name for name in fs._files if name.endswith('.tmp')]
        assert leftovers == []
        # The table and the sources survived the failure
        broken['on'] = False
        archive.write('/out.asar')
        assert file_read_bytes('/out.asar') == fixture.tiny_341()

    def test_no_integrity(self, fs):
        """Without integrity the header only stores the size and the offset."""
        root = build_source_tree(fs)
        archive = AsarArchive()
        archive.add_folder(root)
        archive.write('/out.asar', integrity=False)
        header = stored_header('/out.asar')
        assert header['files']['hello.txt'] == {'size': 10, 'offset': '0'}
        # A 3.4.1 reader accepts it as well
        with AsarArchive('/out.asar') as reader:
            assert reader.entry('hello.txt').integrity is None
            assert bytes(reader.read_file('hello.txt')) == b'hello asar'

    def test_too_large(self, fs, monkeypatch):
        """An entry larger than the format allows is refused."""
        from alasio.codegen.asar import pack

        root = build_source_tree(fs)
        monkeypatch.setattr(pack, 'UINT32_MAX', 4)
        archive = AsarArchive()
        archive.add_folder(root)
        with pytest.raises(AsarUnsupportedError) as e:
            archive.write('/out.asar')
        assert str(e.value) == 'Content is larger than the 4 bytes an asar entry can store'

    def test_written_header_is_valid(self, fs):
        """What a pack writes is a header a reader accepts.

        The encoded header is not checked while packing, the test suite checks
        it: a plain file, an unpacked file, an empty file and an empty
        directory all end up in a header a reader walks to the last entry (the
        fixtures of ``TestRoundTrip`` cover the links).
        """
        root = build_source_tree(fs)
        archive = AsarArchive()
        archive.add_folder(root, unpack=True)
        archive.add_file(data=b'', arc_path='empty.txt')
        archive.add_file(data=b'a' * 10, arc_path='nested/deep/big.bin')
        archive.write('/out.asar')
        # Walking the header is the check: every node is converted to the struct
        # of its kind and every entry of it is validated
        assert sorted(keys_path(keys) for keys, _, _ in iter_entries(stored_header('/out.asar'))) == [
            'empty.txt', 'hello.txt', 'nested', 'nested/deep', 'nested/deep/big.bin', 'sub', 'sub/bin.dat',
        ]

    def test_write_then_read(self, fs):
        """The entries of a written archive are re-read from the new file."""
        root = build_source_tree(fs)
        archive = AsarArchive()
        archive.add_folder(root)
        archive.write('/out.asar')
        assert archive.file == '/out.asar'
        assert archive.header_size == 524
        assert bytes(archive.read_file('hello.txt')) == b'hello asar'
        assert bytes(archive.read_file('sub/bin.dat')) == b'PAYLOAD'
        archive.extract_file('hello.txt', '/copy.txt')
        assert file_read_bytes('/copy.txt') == b'hello asar'

    def test_write_then_add(self, fs):
        """An entry added after a write is packed by the next write."""
        root = build_source_tree(fs)
        archive = AsarArchive()
        archive.add_folder(root)
        archive.write('/first.asar')
        archive.add_file(data=b'new', arc_path='new.txt')
        assert archive.entry('new.txt').offset is None
        archive.write('/second.asar')
        with AsarArchive('/second.asar') as reader:
            assert entry_paths(reader) == ['hello.txt', 'new.txt', 'sub', 'sub/bin.dat']
            assert bytes(reader.read_file('new.txt')) == b'new'
        # The first archive is untouched
        with AsarArchive('/first.asar') as reader:
            assert bytes(reader.read_file('hello.txt')) == b'hello asar'
            assert entry_paths(reader) == ['hello.txt', 'sub', 'sub/bin.dat']

    def test_write_without_dest_needs_a_file(self, fs):
        """An archive that lives in memory has no file to write back to."""
        archive = AsarArchive()
        archive.add_file(data=b'x', arc_path='a.txt')
        with pytest.raises(AsarError) as e:
            archive.write()
        assert str(e.value) == 'write() needs a dest path, this archive has no file'

    def test_self_write(self, fs):
        """write() without a dest replaces the archive it read from."""
        root = build_source_tree(fs)
        with AsarArchive() as archive:
            archive.add_folder(root)
            archive.write('/out.asar')
        with AsarArchive('/out.asar') as archive:
            archive.add_file(data=b'changed', arc_path='hello.txt')
            assert archive.write() is None
        assert file_read_bytes('/out.asar') != fixture.tiny_341()
        with AsarArchive('/out.asar') as reader:
            assert bytes(reader.read_file('hello.txt')) == b'changed'
            assert bytes(reader.read_file('sub/bin.dat')) == b'PAYLOAD'

    def test_self_write_is_a_fixed_point(self, fs):
        """Rewriting an archive that is already canonical keeps every byte."""
        root = build_source_tree(fs)
        with AsarArchive() as archive:
            archive.add_folder(root)
            archive.write('/out.asar')
        before = file_read_bytes('/out.asar')
        with AsarArchive('/out.asar') as archive:
            archive.write()
        assert file_read_bytes('/out.asar') == before

    def test_self_write_keeps_the_entries_readable(self, fs):
        """The archive is re-read from the file it just wrote."""
        root = build_source_tree(fs)
        with AsarArchive() as archive:
            archive.add_folder(root)
            archive.write('/out.asar')
        with AsarArchive('/out.asar') as archive:
            archive.write()
            assert bytes(archive.read_file('hello.txt')) == b'hello asar'
            archive.extract_file('hello.txt', '/copy.txt')
        assert file_read_bytes('/copy.txt') == b'hello asar'

    def test_write_updates_the_offsets(self, fs):
        """The entries describe the archive that was written."""
        archive = AsarArchive()
        archive.add_file(data=b'aaaa', arc_path='a.txt')
        archive.add_file(data=b'bbbb', arc_path='b.txt')
        assert archive.entry('a.txt').size is None
        archive.write('/out.asar')
        assert archive.entry('a.txt').size == 4
        assert archive.entry('a.txt').offset == 0
        assert archive.entry('b.txt').offset == 4


class TestRoundTrip:
    @pytest.mark.parametrize('archive_bytes, integrity', [
        (fixture.tiny_341(), True),
        (fixture.packthis_430(), True),
        # This one has no integrity, a byte identical repack can not add it
        (fixture.extractthis_430(), False),
        (fixture.packthis_unpack_430(), True),
        (fixture.packthis_symlink_430(), True),
    ])
    def test_read_write_unchanged(self, fs, archive_bytes, integrity):
        """Repacking an archive that was not modified keeps every byte."""
        fs.create_file('/in.asar', contents=archive_bytes)
        for name, content in fixture.unpacked_430_files().items():
            fs.create_file(f'/in.asar.unpacked/{name}', contents=content)
        with AsarArchive('/in.asar') as archive:
            archive.write('/out.asar', integrity=integrity)
        assert file_read_bytes('/out.asar') == archive_bytes

    def test_read_write_adds_integrity(self, fs):
        """By default a repack adds the integrity the source does not have."""
        fs.create_file('/in.asar', contents=fixture.extractthis_430())
        with AsarArchive('/in.asar') as archive:
            archive.write('/out.asar')
        assert os.path.getsize('/out.asar') > len(fixture.extractthis_430())
        with AsarArchive('/out.asar') as reader:
            reader.validate(verify_content=True)


class TestHashContent:
    def test_empty(self):
        """An empty content has one empty block, as 4.3.0 writes it."""
        size, integrity, cache = hash_content(iter([]))
        assert size == 0
        assert cache == bytearray()
        assert integrity == Integrity(
            algorithm='SHA256', hash=sha256(b''), blockSize=BLOCK_SIZE, blocks=[sha256(b'')],
        )

    def test_small(self):
        """A small content has a single block."""
        size, integrity, _ = hash_content(iter([b'hello']))
        assert size == 5
        assert integrity.hash == sha256(b'hello')
        assert integrity.blocks == [sha256(b'hello')]

    def test_exact_block(self):
        """A content of exactly one block has one block, not two.

        3.4.1 pushed an extra empty block here, 4.3.0 does not (the write side
        follows 4.3.0, see the plan section 3.3).
        """
        content = b'a' * BLOCK_SIZE
        size, integrity, _ = hash_content(iter([content]))
        assert size == BLOCK_SIZE
        assert integrity.blocks == [sha256(content)]

    def test_two_blocks(self):
        """A content over one block has a block per block size."""
        content = b'a' * (BLOCK_SIZE + 5)
        size, integrity, _ = hash_content(iter([content]))
        assert size == BLOCK_SIZE + 5
        assert integrity.blocks == [sha256(content[:BLOCK_SIZE]), sha256(content[BLOCK_SIZE:])]
        assert integrity.hash == sha256(content)

    def test_blocks_across_chunks(self):
        """Block boundaries are independent of the read chunk size."""
        chunks = [b'a' * (3 * 1024 * 1024), b'b' * (2 * 1024 * 1024), b'c' * 10]
        size, integrity, _ = hash_content(iter(chunks))
        content = b''.join(chunks)
        assert size == len(content) == 5 * 1024 * 1024 + 10
        assert integrity.blocks == [
            sha256(content[:BLOCK_SIZE]), sha256(content[BLOCK_SIZE:]),
        ]
        assert integrity.hash == sha256(content)

    def test_no_integrity(self):
        """Without integrity only the size is computed."""
        size, integrity, cache = hash_content(iter([b'hello']), integrity=False)
        assert size == 5
        assert integrity is None
        assert cache == bytearray(b'hello')

    def test_cache_dropped(self):
        """A content larger than the cache limit is not kept in memory."""
        content = b'a' * (CACHE_FILE_SIZE + 1)
        size, integrity, cache = hash_content(iter([content]))
        assert size == CACHE_FILE_SIZE + 1
        assert cache is None


class TestHelpers:
    def test_pack_sha256(self, fs):
        """The helper streams a file to hash it."""
        fs.create_file('/data.bin', contents=b'hello world')
        assert pack_sha256('/data.bin') == sha256(b'hello world')

    def test_write_content(self, fs):
        """Content chunks are written to their own file, atomically."""
        info = AsarFileInfo(
            kind=KIND_FILE, size=5,
            integrity={
                'algorithm': 'SHA256', 'hash': sha256(b'hello'),
                'blockSize': BLOCK_SIZE, 'blocks': [sha256(b'hello')],
            },
        )
        verifier = ContentVerifier('a.txt', info.size, info.integrity['hash'])
        write_content('/deep/a.txt', MemorySource(b'hello').iter_chunks(), verifier=verifier)
        assert file_read_bytes('/deep/a.txt') == b'hello'
        assert [name for name in fs._files if name.endswith('.tmp')] == []

    def test_write_content_checks(self, fs):
        """A content that does not match leaves no file behind."""
        verifier = ContentVerifier('a.txt', 5, sha256(b'other'))
        with pytest.raises(AsarError) as e:
            write_content('/deep/a.txt', MemorySource(b'hello').iter_chunks(), verifier=verifier)
        assert str(e.value) == (
            f'Content hash of "a.txt" does not match, it is {sha256(b"hello")} '
            f'instead of {sha256(b"other")}'
        )
        assert not os.path.exists('/deep/a.txt')
        assert [name for name in fs._files if name.endswith('.tmp')] == []

    def test_write_content_mode(self, fs):
        """The mode of an entry is set on the file that is written."""
        if os.name == 'nt':
            pytest.skip('Windows has no executable bit')
        info = AsarFileInfo(kind=KIND_FILE, size=2, executable=True)
        from alasio.codegen.asar.archive import entry_mode
        assert entry_mode(info) == 0o755
        write_content('/a.sh', MemorySource(b'hi').iter_chunks(), mode=entry_mode(info))
        assert os.stat('/a.sh').st_mode & 0o777 == 0o755

    def test_iter_entry_content_without_source(self, fs):
        """An entry that has no source can not be written to an archive."""
        from alasio.codegen.asar.pack import iter_entry_content
        info = AsarFileInfo(kind=KIND_FILE)
        with pytest.raises(AsarError) as e:
            list(iter_entry_content(('a.txt',), info))
        assert str(e.value) == (
            'Entry "a.txt" has no content source, it can not be written to an archive'
        )
