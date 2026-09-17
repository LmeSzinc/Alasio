"""
Tests of the write side: the entry table is turned into an archive file, the
content is read twice (scan and write) and both passes are compared.

The byte level expectations come from the reference implementation, see
``tests/codegen/asar/fixture.py``.
"""
import hashlib
import os

import pytest

from alasio.codegen.asar.archive import AsarArchive
from alasio.codegen.asar.errors import AsarError, AsarFormatError, AsarPathError, AsarUnsupportedError
from alasio.codegen.asar.format import BLOCK_SIZE, parse_header_pickle, parse_size_pickle
from alasio.codegen.asar.model import KIND_DIR, KIND_FILE, decode_header
from alasio.codegen.asar.pack import CACHE_FILE_SIZE, hash_content
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
    data = file_read_bytes(archive_path)
    return decode_header(parse_header_pickle(data[8:8 + parse_size_pickle(data[:8])]))


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
        assert (info.path, info.kind) == ('hello.txt', KIND_FILE)
        archive.add_file(f'{root}/sub/bin.dat', 'sub/bin.dat')
        result = archive.write_asar('/out.asar')
        assert file_read_bytes('/out.asar') == fixture.tiny_341()
        assert result.file_count == 2
        assert result.archive_size == 549
        assert result.header_size == 524
        assert result.data_size == 17
        assert result.sha256 == sha256(fixture.tiny_341())

    def test_add_file_arc_path(self, fs):
        """The archive path defaults to the file name and may be given."""
        root = build_source_tree(fs)
        archive = AsarArchive()
        archive.add_file(f'{root}/hello.txt', 'deep/nested/renamed.txt')
        archive.write_asar('/out.asar')
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
        archive.write_asar('/out.asar')
        assert file_read_bytes('/out.asar')[parse_size_pickle(file_read_bytes('/out.asar')[:8]) + 8:] == (
            b'{"name":"alasio"}'
        )
        reader = AsarArchive.read_asar('/out.asar')
        assert bytes(reader.read_file('package.json')) == b'{"name":"alasio"}'

    @pytest.mark.parametrize('kwargs, expected', [
        ({}, 'add_file() needs a path or a content'),
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
        assert list(archive.files) == ['hello.txt', 'sub', 'sub/bin.dat']
        archive.add_file(data=b'changed', arc_path='hello.txt')
        assert list(archive.files) == ['hello.txt', 'sub', 'sub/bin.dat']
        archive.write_asar('/out.asar')
        reader = AsarArchive.read_asar('/out.asar')
        assert list(reader.files) == ['hello.txt', 'sub', 'sub/bin.dat']
        assert bytes(reader.read_file('hello.txt')) == b'changed'
        assert bytes(reader.read_file('sub/bin.dat')) == b'PAYLOAD'

    def test_add_file_parent_directories(self, fs):
        """The parent directories of an added file are entries of the table."""
        archive = AsarArchive()
        archive.add_file(data=b'x', arc_path='deep/nested/a.txt')
        assert list(archive.files) == ['deep', 'deep/nested', 'deep/nested/a.txt']
        assert archive.files['deep'].kind == KIND_DIR
        archive.add_file(data=b'y', arc_path='deep/other.txt')
        assert list(archive.files) == ['deep', 'deep/nested', 'deep/nested/a.txt', 'deep/other.txt']

    def test_add_file_parent_is_a_file(self, fs):
        """A directory may not be created where a file already is."""
        archive = AsarArchive()
        archive.add_file(data=b'x', arc_path='deep')
        with pytest.raises(AsarPathError) as e:
            archive.add_file(data=b'y', arc_path='deep/nested.txt')
        assert str(e.value) == 'Archive path "deep" is already a file'


class TestAddFolder:
    def test_add_folder_tiny(self, fs):
        """A tree is added depth first, directories before their content."""
        root = build_source_tree(fs)
        archive = AsarArchive()
        assert archive.add_folder(root) == 3
        archive.write_asar('/out.asar')
        assert file_read_bytes('/out.asar') == fixture.tiny_341()

    def test_add_folder_include(self, fs):
        """Only the selected files are packed, their directories follow."""
        root = build_source_tree(fs)
        archive = AsarArchive()
        archive.add_folder(root, include=['sub/**'])
        archive.write_asar('/out.asar')
        reader = AsarArchive.read_asar('/out.asar')
        assert list(reader.files) == ['sub', 'sub/bin.dat']

    def test_add_folder_include_keeps_empty_dir(self, fs):
        """An empty directory that matches include is kept."""
        root = build_source_tree(fs)
        fs.create_dir(f'{root}/empty')
        archive = AsarArchive()
        archive.add_folder(root, include=['empty', 'hello.txt'])
        archive.write_asar('/out.asar')
        reader = AsarArchive.read_asar('/out.asar')
        assert list(reader.files) == ['empty', 'hello.txt']
        assert reader.files['empty'].kind == KIND_DIR

    def test_add_folder_exclude(self, fs):
        """Excluded entries are dropped, an excluded directory takes its subtree."""
        root = build_source_tree(fs)
        archive = AsarArchive()
        archive.add_folder(root, exclude=['sub'])
        archive.write_asar('/out.asar')
        reader = AsarArchive.read_asar('/out.asar')
        assert list(reader.files) == ['hello.txt']

    def test_add_folder_unpack(self, fs):
        """An unpacked file is stored next to the archive, not inside it."""
        root = build_source_tree(fs)
        archive = AsarArchive()
        archive.add_folder(root, unpack=['*.dat'])
        result = archive.write_asar('/out.asar')
        assert result.unpacked_count == 1
        assert file_read_bytes('/out.asar.unpacked/sub/bin.dat') == b'PAYLOAD'
        header = stored_header('/out.asar')
        assert header['files']['sub']['files']['bin.dat'] == {
            'size': 7,
            'unpacked': True,
            'integrity': {
                'algorithm': 'SHA256',
                'hash': sha256(b'PAYLOAD'),
                'blockSize': BLOCK_SIZE,
                'blocks': [sha256(b'PAYLOAD')],
            },
        }
        reader = AsarArchive.read_asar('/out.asar')
        assert bytes(reader.read_file('sub/bin.dat')) == b'PAYLOAD'

    def test_add_folder_unpack_dir(self, fs):
        """An unpacked directory takes its whole subtree with it."""
        root = build_source_tree(fs)
        archive = AsarArchive()
        archive.add_folder(root, unpack_dir=['sub'])
        result = archive.write_asar('/out.asar')
        assert result.unpacked_count == 1
        assert file_read_bytes('/out.asar.unpacked/sub/bin.dat') == b'PAYLOAD'
        header = stored_header('/out.asar')
        assert header['files']['sub']['unpacked'] is True
        assert header['files']['sub']['files']['bin.dat']['unpacked'] is True
        assert 'offset' not in header['files']['sub']['files']['bin.dat']
        assert header['files']['hello.txt']['offset'] == '0'

    def test_add_folder_stale_unpacked_is_kept(self, fs):
        """An old unpacked file is not removed, the caller owns the directory."""
        root = build_source_tree(fs)
        fs.create_file('/out.asar.unpacked/old.txt', contents=b'old')
        archive = AsarArchive()
        archive.add_folder(root)
        archive.write_asar('/out.asar')
        assert file_read_bytes('/out.asar.unpacked/old.txt') == b'old'

    def test_add_folder_empty(self, fs):
        """An empty directory packs to an empty archive."""
        fs.create_dir('/empty')
        archive = AsarArchive()
        assert archive.add_folder('/empty') == 0
        result = archive.write_asar('/out.asar')
        assert result.archive_size == 28
        assert file_read_bytes('/out.asar') == fixture.make_archive({'files': {}})

    def test_add_folder_unicode(self, fs):
        """Non ASCII names are stored as UTF-8."""
        fs.create_dir('/tree')
        fs.create_file('/tree/中文.txt', contents=b'text')
        archive = AsarArchive()
        archive.add_folder('/tree')
        archive.write_asar('/out.asar')
        reader = AsarArchive.read_asar('/out.asar')
        assert list(reader.files) == ['中文.txt']
        assert bytes(reader.read_file('中文.txt')) == b'text'


class TestWriteAsar:
    def test_deterministic(self, fs):
        """Packing the same tree twice gives the same bytes."""
        root = build_source_tree(fs)
        first = AsarArchive()
        first.add_folder(root)
        first.write_asar('/first.asar')
        second = AsarArchive()
        second.add_folder(root)
        second.write_asar('/second.asar')
        assert file_read_bytes('/first.asar') == file_read_bytes('/second.asar')

    def test_write_twice(self, fs):
        """Writing the same archive twice gives the same bytes."""
        root = build_source_tree(fs)
        archive = AsarArchive()
        archive.add_folder(root)
        archive.write_asar('/first.asar')
        archive.write_asar('/second.asar')
        assert file_read_bytes('/first.asar') == file_read_bytes('/second.asar')

    def test_size_comes_from_content(self, fs, monkeypatch):
        """The header size is the byte count that was read, not os.stat()."""
        root = build_source_tree(fs)

        def no_stat(*args, **kwargs):
            raise AssertionError('the pack must not call os.stat()')

        monkeypatch.setattr(os, 'stat', no_stat)
        archive = AsarArchive()
        archive.add_folder(root)
        archive.write_asar('/out.asar')
        header = stored_header('/out.asar')
        assert header['files']['hello.txt']['size'] == 10
        assert header['files']['sub']['files']['bin.dat']['size'] == 7

    def test_second_pass_checks_the_content(self, fs, monkeypatch):
        """A content that changes between the two passes fails the pack."""
        from alasio.codegen.asar import pack

        root = build_source_tree(fs)
        original = pack.iter_entry_chunks

        def changed(path, info, source, data_area, unpacked_root=None, chunk_size=None):
            if path == 'hello.txt':
                yield b'hello asar and more'
                return
            for chunk in original(path, info, source, data_area, unpacked_root, chunk_size):
                yield chunk

        archive = AsarArchive()
        archive.add_folder(root)
        monkeypatch.setattr(pack, 'iter_content_with_cache', changed)
        with pytest.raises(AsarError) as e:
            archive.write_asar('/out.asar')
        assert str(e.value) == (
            'Content of "hello.txt" does not match, 10 bytes were expected but 19 bytes were written'
        )

    def test_second_pass_checks_the_hash(self, fs, monkeypatch):
        """A content of the same size but a different hash fails the pack."""
        from alasio.codegen.asar import pack

        root = build_source_tree(fs)
        original = pack.iter_entry_chunks

        def changed(path, info, source, data_area, unpacked_root=None, chunk_size=None):
            if path == 'hello.txt':
                yield b'HELLO ASAR'
                return
            for chunk in original(path, info, source, data_area, unpacked_root, chunk_size):
                yield chunk

        archive = AsarArchive()
        archive.add_folder(root)
        monkeypatch.setattr(pack, 'iter_content_with_cache', changed)
        with pytest.raises(AsarError) as e:
            archive.write_asar('/out.asar')
        assert str(e.value) == (
            f'Content hash of "hello.txt" does not match, it is {sha256(b"HELLO ASAR")} '
            f'instead of {sha256(b"hello asar")}'
        )

    def test_failed_pack_leaves_nothing(self, fs, monkeypatch):
        """A failed pack removes its temporary file."""
        from alasio.codegen.asar import pack

        root = build_source_tree(fs)
        original = pack.iter_entry_chunks

        def changed(path, info, source, data_area, unpacked_root=None, chunk_size=None):
            if path == 'hello.txt':
                yield b'changed'
                return
            for chunk in original(path, info, source, data_area, unpacked_root, chunk_size):
                yield chunk

        archive = AsarArchive()
        archive.add_folder(root)
        monkeypatch.setattr(pack, 'iter_content_with_cache', changed)
        with pytest.raises(AsarError):
            archive.write_asar('/out.asar')
        assert not os.path.exists('/out.asar')
        leftovers = [name for name in fs._files if name.endswith('.tmp')]
        assert leftovers == []

    def test_no_integrity(self, fs):
        """Without integrity the header only stores the size and the offset."""
        root = build_source_tree(fs)
        archive = AsarArchive()
        archive.add_folder(root)
        archive.write_asar('/out.asar', integrity=False)
        header = stored_header('/out.asar')
        assert header['files']['hello.txt'] == {'size': 10, 'offset': '0'}
        # A 3.4.1 reader accepts it as well
        reader = AsarArchive.read_asar('/out.asar')
        assert reader.files['hello.txt'].integrity is None
        assert bytes(reader.read_file('hello.txt')) == b'hello asar'

    def test_too_large(self, fs, monkeypatch):
        """An entry larger than the format allows is refused."""
        from alasio.codegen.asar import pack

        root = build_source_tree(fs)
        monkeypatch.setattr(pack, 'UINT32_MAX', 4)
        archive = AsarArchive()
        archive.add_folder(root)
        with pytest.raises(AsarUnsupportedError) as e:
            archive.write_asar('/out.asar')
        assert str(e.value) == 'Content is larger than the 4 bytes an asar entry can store'

    def test_header_is_validated(self, fs, monkeypatch):
        """The encoded header is checked with the reference rules."""
        from alasio.codegen.asar import pack

        root = build_source_tree(fs)
        archive = AsarArchive()
        archive.add_folder(root)
        monkeypatch.setattr(pack, 'encode_header', lambda root: b'{"files":{"a/b":{}}}')
        with pytest.raises(AsarFormatError) as e:
            archive.write_asar('/out.asar')
        assert 'Invalid entry name' in str(e.value)

    def test_write_then_read(self, fs):
        """The entries of a written archive stay readable."""
        root = build_source_tree(fs)
        archive = AsarArchive()
        archive.add_folder(root)
        result = archive.write_asar('/out.asar')
        assert archive.archive_path == '/out.asar'
        assert archive.data is None
        assert archive.header_size == result.header_size
        assert bytes(archive.read_file('hello.txt')) == b'hello asar'
        assert bytes(archive.read_file('sub/bin.dat')) == b'PAYLOAD'
        archive.extract_file('hello.txt', '/copy.txt')
        assert file_read_bytes('/copy.txt') == b'hello asar'

    def test_write_then_add(self, fs):
        """An entry added after a write is packed by the next write."""
        root = build_source_tree(fs)
        archive = AsarArchive()
        archive.add_folder(root)
        archive.write_asar('/first.asar')
        archive.add_file(data=b'new', arc_path='new.txt')
        assert archive.files['new.txt'].offset is None
        archive.write_asar('/second.asar')
        reader = AsarArchive.read_asar('/second.asar')
        assert list(reader.files) == ['hello.txt', 'sub', 'sub/bin.dat', 'new.txt']
        assert bytes(reader.read_file('new.txt')) == b'new'
        # The first archive is untouched
        assert bytes(AsarArchive.read_asar('/first.asar').read_file('hello.txt')) == b'hello asar'


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
        archive = AsarArchive.read_asar('/in.asar')
        archive.write_asar('/out.asar', integrity=integrity)
        assert file_read_bytes('/out.asar') == archive_bytes

    def test_read_write_adds_integrity(self, fs):
        """By default a repack adds the integrity the source does not have."""
        fs.create_file('/in.asar', contents=fixture.extractthis_430())
        archive = AsarArchive.read_asar('/in.asar')
        result = archive.write_asar('/out.asar')
        assert result.archive_size > len(fixture.extractthis_430())
        AsarArchive.read_asar('/out.asar').validate(verify_content=True)


class TestHashContent:
    def test_empty(self):
        """An empty content has one empty block, as 4.3.0 writes it."""
        size, integrity, cache = hash_content(iter([]))
        assert size == 0
        assert cache == bytearray()
        assert integrity == {
            'algorithm': 'SHA256',
            'hash': sha256(b''),
            'blockSize': BLOCK_SIZE,
            'blocks': [sha256(b'')],
        }

    def test_small(self):
        """A small content has a single block."""
        size, integrity, _ = hash_content(iter([b'hello']))
        assert size == 5
        assert integrity['hash'] == sha256(b'hello')
        assert integrity['blocks'] == [sha256(b'hello')]

    def test_exact_block(self):
        """A content of exactly one block has one block, not two.

        3.4.1 pushed an extra empty block here, 4.3.0 does not (the write side
        follows 4.3.0, see the plan section 3.3).
        """
        content = b'a' * BLOCK_SIZE
        size, integrity, _ = hash_content(iter([content]))
        assert size == BLOCK_SIZE
        assert integrity['blocks'] == [sha256(content)]

    def test_two_blocks(self):
        """A content over one block has a block per block size."""
        content = b'a' * (BLOCK_SIZE + 5)
        size, integrity, _ = hash_content(iter([content]))
        assert size == BLOCK_SIZE + 5
        assert integrity['blocks'] == [sha256(content[:BLOCK_SIZE]), sha256(content[BLOCK_SIZE:])]
        assert integrity['hash'] == sha256(content)

    def test_blocks_across_chunks(self):
        """Block boundaries are independent of the read chunk size."""
        chunks = [b'a' * (3 * 1024 * 1024), b'b' * (2 * 1024 * 1024), b'c' * 10]
        size, integrity, _ = hash_content(iter(chunks))
        content = b''.join(chunks)
        assert size == len(content) == 5 * 1024 * 1024 + 10
        assert integrity['blocks'] == [
            sha256(content[:BLOCK_SIZE]), sha256(content[BLOCK_SIZE:]),
        ]
        assert integrity['hash'] == sha256(content)

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
        from alasio.codegen.asar import pack_sha256
        fs.create_file('/data.bin', contents=b'hello world')
        assert pack_sha256('/data.bin') == sha256(b'hello world')

    def test_write_entry(self, fs):
        """An entry is written to its own file and verified."""
        from alasio.codegen.asar.model import AsarFileInfo
        from alasio.codegen.asar.pack import write_entry
        info = AsarFileInfo(
            path='a.txt', kind=KIND_FILE, size=5,
            integrity={
                'algorithm': 'SHA256', 'hash': sha256(b'hello'),
                'blockSize': BLOCK_SIZE, 'blocks': [sha256(b'hello')],
            },
        )
        write_entry('/deep/a.txt', 'a.txt', info, b'hello')
        assert file_read_bytes('/deep/a.txt') == b'hello'
