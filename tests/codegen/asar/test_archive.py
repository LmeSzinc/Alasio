"""
Tests of reading, validating and extracting an archive.

The binary fixtures are the ones described in ``tests/codegen/asar/fixture.py``,
the hand made archives come from ``fixture.make_archive()`` and cover the
malformed inputs an archive from the network may contain.
"""
import os

import msgspec
import pytest

from alasio.codegen.asar import archive as archive_module, model as model_module
from alasio.codegen.asar.archive import DEFAULT_MAX_SIZE, AsarArchive, normalize_path
from alasio.codegen.asar.errors import (
    AsarEntryNotFoundError, AsarError, AsarFormatError, AsarPathError, AsarUnsupportedError
)
from alasio.codegen.asar.format import read_header
from alasio.codegen.asar.model import (
    KIND_DIR, KIND_FILE, KIND_LINK, AsarFileInfo, iter_entries as header_entries, keys_path
)
from alasio.ext.cache import InstanceCacheOperation
from alasio.ext.path.atomic import file_read_bytes
from alasio.testing.filesystem import fs  # noqa: F401
from tests.codegen.asar import fixture

HASH_A = 'a' * 64


def integrity(content, hash_hex=None):
    """
    Build the integrity of a content, as the reference implementation writes it.

    Args:
        content (bytes): Content of the entry
        hash_hex (str): Override the hash, to build a broken archive

    Returns:
        dict: Integrity object
    """
    import hashlib
    digest = hash_hex if hash_hex is not None else hashlib.sha256(content).hexdigest()
    return {'algorithm': 'SHA256', 'hash': digest, 'blockSize': 4194304, 'blocks': [digest]}


def simple_archive(data=b'hello world'):
    """
    Build an archive with one file and one directory.

    Args:
        data (bytes): Content of the file

    Returns:
        bytes: Archive bytes
    """
    header = {
        'files': {
            'hello.txt': {'size': len(data), 'offset': '0', 'integrity': integrity(data)},
            'dir': {'files': {}},
        },
    }
    return fixture.make_archive(header, data)


def parse_header(json_bytes):
    """
    Decode the header JSON of an archive, in the order it is stored in.

    Args:
        json_bytes (bytes): Header JSON

    Returns:
        dict: Decoded header
    """
    return msgspec.json.decode(json_bytes)


def stored_header(path):
    """
    Read the header JSON of an archive as it is stored in the file.

    Args:
        path (str): Archive path

    Returns:
        bytes: Header JSON, as the archive stores it
    """
    with open(path, 'rb') as f:
        return read_header(f)[0]


def paths_of(archive, kind):
    """
    Get the paths of the entries of one kind.

    Args:
        archive (AsarArchive): Archive to read
        kind (str): 'file', 'dir' or 'link'

    Returns:
        list: Paths, in the canonical order of the archive
    """
    return [path for path, info in archive.iter_entries() if info.kind == kind]


class TestOpen:
    def test_open_tiny(self, fs):
        """A 3.4.1 archive is parsed with its directories and offsets."""
        fs.create_file('/tiny.asar', contents=fixture.tiny_341())
        archive = AsarArchive('/tiny.asar')
        assert [path for path, _ in archive.iter_entries()] == ['hello.txt', 'sub', 'sub/bin.dat']
        assert archive.entry('hello.txt').size == 10
        assert archive.entry('hello.txt').offset == 0
        assert archive.entry('sub').kind == KIND_DIR
        assert archive.entry('sub').size is None
        assert archive.entry('sub').offset is None
        assert archive.entry('sub/bin.dat').offset == 10
        assert archive.header_size == 524
        assert archive.data_offset == 532
        assert bytes(archive.read_file('sub/bin.dat')) == b'PAYLOAD'

    def test_open_packthis(self, fs):
        """A 4.3.0 archive with an empty file and a hidden file is parsed."""
        fs.create_file('/packthis.asar', contents=fixture.packthis_430())
        archive = AsarArchive('/packthis.asar')
        assert paths_of(archive, KIND_FILE) == [
            '.hiddenfile.txt', 'dir1/file1.txt', 'dir2/file2.png', 'dir2/file3.txt',
            'emptyfile.txt', 'file0.txt',
        ]
        assert paths_of(archive, KIND_DIR) == ['dir1', 'dir2']
        # The empty file shares its offset with the next file, it holds no byte
        assert archive.entry('emptyfile.txt').size == 0
        assert archive.entry('emptyfile.txt').offset == archive.entry('file0.txt').offset == 213

    def test_open_without_integrity(self, fs):
        """An archive whose entries have no integrity is read, 4.3.0 fixture."""
        fs.create_file('/extractthis.asar', contents=fixture.extractthis_430())
        archive = AsarArchive('/extractthis.asar')
        assert archive.entry('file0.txt').integrity is None
        assert archive.entry('file0.txt').size == 13
        archive.validate()

    def test_open_unpacked(self, fs):
        """Unpacked entries have no offset, their content is next to the archive."""
        fs.create_file('/unpack.asar', contents=fixture.packthis_unpack_430())
        fs.create_file('/unpack.asar.unpacked/dir2/file2.png', contents=b'png')
        archive = AsarArchive('/unpack.asar')
        info = archive.entry('dir2/file2.png')
        assert info.unpacked is True
        assert info.offset is None
        assert info.size == 182
        assert archive.entry('dir2/file3.txt').unpacked is False
        # The content is read from <archive>.unpacked, not from the archive body
        assert bytes(archive.read_file('dir2/file2.png')) == b'png'
        assert bytes(archive.read_file('dir2/file3.txt')) == b'123'

    def test_read_file_content(self, fs):
        """read_file gives the content of the entry, whatever its source is."""
        fs.create_file('/simple.asar', contents=simple_archive())
        archive = AsarArchive('/simple.asar')
        assert bytes(archive.read_file('hello.txt')) == b'hello world'
        # Content that was added to the archive is a view on the content itself
        archive.add_file(data=b'added', arc_path='added.txt')
        content = archive.read_file('added.txt')
        assert content.readonly is True
        assert bytes(content) == b'added'

    def test_read_file_links(self, fs):
        """A link entry is followed to the entry it points at."""
        fs.create_file('/links.asar', contents=fixture.packthis_symlink_430())
        archive = AsarArchive('/links.asar')
        assert archive.entry('Current').kind == KIND_LINK
        assert archive.entry('Current').link == 'A'
        assert archive.resolve('Current') == ('A', archive.entry('A'))
        # `real.txt` links to a path that goes through the `Current` link
        assert archive.entry('real.txt').link == 'Current/real.txt'
        assert archive.resolve('real.txt')[0] == 'A/real.txt'
        assert bytes(archive.read_file('real.txt')) == bytes(archive.read_file('A/real.txt'))
        assert archive.entry('A/reverse-symlink.txt').link == 'B/reverse-symlink.txt'
        assert bytes(archive.read_file('A/reverse-symlink.txt')) == bytes(
            archive.read_file('B/reverse-symlink.txt')
        )

    def test_entry_missing(self, fs):
        """An unknown path is reported as a missing entry."""
        fs.create_file('/simple.asar', contents=simple_archive())
        archive = AsarArchive('/simple.asar')
        with pytest.raises(AsarEntryNotFoundError) as e:
            archive.entry('nope.txt')
        assert str(e.value) == 'Entry "nope.txt" does not exist in the archive'

    def test_read_file_directory(self, fs):
        """A directory has no content to read."""
        fs.create_file('/simple.asar', contents=simple_archive())
        archive = AsarArchive('/simple.asar')
        with pytest.raises(AsarError) as e:
            archive.read_file('dir')
        assert str(e.value) == 'Entry "dir" is a directory'

    def test_repr(self, fs):
        """The archive prints its path, and its entry count once they are read."""
        fs.create_file('/simple.asar', contents=simple_archive())
        archive = AsarArchive('/simple.asar')
        assert repr(archive) == "AsarArchive(file='/simple.asar')"
        with archive:
            assert repr(archive) == "AsarArchive(file='/simple.asar', entries=2)"

    def test_repr_of_a_broken_archive(self, fs):
        """Printing an archive never reads it, a broken one can still be shown."""
        fs.create_file('/broken.asar', contents=b'broken')
        assert repr(AsarArchive('/broken.asar')) == "AsarArchive(file='/broken.asar')"

    def test_unpacked_path(self, fs):
        """The unpacked directory sits next to the archive."""
        fs.create_file('/simple.asar', contents=simple_archive())
        assert AsarArchive('/simple.asar').unpacked_path == '/simple.asar.unpacked'
        assert AsarArchive().unpacked_path is None


class TestPathSeparators:
    """The separator of any platform names the same entry."""

    @pytest.mark.parametrize('path, expected', [
        ('a.txt', 'a.txt'),
        ('dir/a.txt', 'dir/a.txt'),
        ('dir\\a.txt', 'dir/a.txt'),
        ('dir\\deep/a.txt', 'dir/deep/a.txt'),
    ])
    def test_normalize_path(self, path, expected):
        """The path of a call is converted to POSIX separators."""
        assert normalize_path(path) == expected

    def test_the_entry_points_take_either_separator(self, fs):
        """Every entry point of the table normalizes the path it is given."""
        archive = AsarArchive()
        archive.add_file(data=b'x', arc_path='deep/nested/a.txt')
        assert archive.entry('deep\\nested\\a.txt') is archive.entry('deep/nested/a.txt')
        assert archive.resolve('deep\\nested\\a.txt')[0] == 'deep/nested/a.txt'
        assert bytes(archive.read_file('deep\\nested\\a.txt')) == b'x'
        assert bytes(b''.join(archive.iter_content('deep\\nested\\a.txt'))) == b'x'
        archive.extract_file('deep\\nested\\a.txt', '/out/a.txt')
        assert file_read_bytes('/out/a.txt') == b'x'
        assert archive.mark_unpack('deep\\nested\\a.txt') == 1
        assert archive.entry('deep\\nested\\a.txt').unpacked is True
        assert archive.del_file('deep\\nested\\a.txt') == 1
        assert ('deep', 'nested', 'a.txt') not in archive.files

    def test_del_folder_takes_either_separator(self, fs):
        """A whole subtree is deleted with the path of either separator."""
        archive = AsarArchive()
        archive.add_file(data=b'x', arc_path='deep/nested/a.txt')
        assert archive.del_folder('deep\\nested') == 2
        assert list(archive.files) == [('deep',)]

    def test_an_entry_of_an_archive(self, fs):
        """An entry of an archive that was read from a file is found as well."""
        fs.create_file('/tiny.asar', contents=fixture.tiny_341())
        with AsarArchive('/tiny.asar') as archive:
            assert bytes(archive.read_file('sub\\bin.dat')) == b'PAYLOAD'


class TestEnsureParents:
    """
    The parents of an entry, one pass over the prefixes of its path.

    The helper is a method of the archive because it works on the table of one:
    it creates the missing directories, refuses a file in the way, and reports
    the unpacked flag of the directories. The tests of the entry points that use
    it (``add_file()``, ``add_folder()``) are in test_pack.py.
    """
    @staticmethod
    def archive_with(files):
        """
        Build a memory archive whose table holds the given entries.

        Args:
            files (dict): Entries to put into the table

        Returns:
            AsarArchive: The archive
        """
        archive = AsarArchive()
        archive.files.update(files)
        return archive

    def test_below_the_root(self):
        """An entry directly below the root has no parent to create."""
        archive = self.archive_with({})
        assert archive._ensure_parents(('a.txt',)) is False
        assert archive.files == {}

    def test_creates_missing_levels(self):
        """Every missing level of the path is created as a directory."""
        archive = self.archive_with({})
        assert archive._ensure_parents(('a', 'b', 'c', 'd.txt')) is False
        assert archive.files == {
            ('a',): AsarFileInfo(kind=KIND_DIR),
            ('a', 'b'): AsarFileInfo(kind=KIND_DIR),
            ('a', 'b', 'c'): AsarFileInfo(kind=KIND_DIR),
        }

    def test_existing_is_kept(self):
        """A directory that already exists is not touched, its flag is reported."""
        archive = self.archive_with({
            ('a',): AsarFileInfo(kind=KIND_DIR),
            ('a', 'b'): AsarFileInfo(kind=KIND_DIR, unpacked=True),
        })
        assert archive._ensure_parents(('a', 'b', 'c.txt')) is True
        assert archive.files == {
            ('a',): AsarFileInfo(kind=KIND_DIR),
            ('a', 'b'): AsarFileInfo(kind=KIND_DIR, unpacked=True),
        }

    def test_the_entry_itself_is_not_an_ancestor(self):
        """The flag of the entry does not count, only a directory above it."""
        archive = self.archive_with({
            ('a',): AsarFileInfo(kind=KIND_DIR),
            ('a', 'b.txt'): AsarFileInfo(kind=KIND_FILE, unpacked=True),
        })
        assert archive._ensure_parents(('a', 'b.txt')) is False

    @pytest.mark.parametrize('keys, files, expected', [
        (('a', 'b'), {('a',): AsarFileInfo(kind=KIND_FILE)}, 'Archive path "a" is already a file'),
        (('a.txt', 'b'), {('a.txt',): AsarFileInfo(kind=KIND_FILE)},
         'Archive path "a.txt" is already a file'),
        (('a', 'b', 'c'), {('a',): AsarFileInfo(kind=KIND_FILE)}, 'Archive path "a" is already a file'),
    ])
    def test_parent_is_a_file(self, keys, files, expected):
        """A file can not be walked through, the table is left as it was."""
        archive = self.archive_with(files)
        with pytest.raises(AsarPathError) as e:
            archive._ensure_parents(keys)
        assert str(e.value) == expected
        assert list(archive.files) == [keys[:1]]

    def test_a_parent_is_created_before_the_error(self):
        """The levels before the one that fails are already created."""
        archive = self.archive_with({('a', 'b'): AsarFileInfo(kind=KIND_FILE)})
        with pytest.raises(AsarPathError) as e:
            archive._ensure_parents(('a', 'b', 'c', 'd.txt'))
        assert str(e.value) == 'Archive path "a/b" is already a file'
        assert archive.files == {
            ('a',): AsarFileInfo(kind=KIND_DIR),
            ('a', 'b'): AsarFileInfo(kind=KIND_FILE),
        }


class TestLifecycle:
    def test_fd_is_opened_once(self, fs, monkeypatch):
        """The file is opened on the first use, and only once."""
        fs.create_file('/simple.asar', contents=simple_archive())
        opened = []
        original = archive_module.atomic_open

        def counting_open(*args, **kwargs):
            handle = original(*args, **kwargs)
            opened.append(handle)
            return handle

        monkeypatch.setattr(archive_module, 'atomic_open', counting_open)
        archive = AsarArchive('/simple.asar')
        assert archive.fd is archive.fd
        assert archive.entry('hello.txt').size == 11
        assert archive.header is archive.header
        assert len(opened) == 1
        assert InstanceCacheOperation.has(archive, 'fd') is True
        assert InstanceCacheOperation.has(archive, 'header') is True
        archive.close()

    def test_fd_of_a_memory_archive(self, fs):
        """An archive that has no file has no handle."""
        archive = AsarArchive()
        assert archive.fd is None
        assert archive.files == {}
        assert archive.header_size == 0
        assert archive.data_offset == 0
        archive.close()

    def test_with_releases_the_handle(self, fs):
        """The handle is closed when the with block ends."""
        fs.create_file('/simple.asar', contents=simple_archive())
        with AsarArchive('/simple.asar') as archive:
            assert archive.entry('hello.txt').size == 11
            assert InstanceCacheOperation.has(archive, 'fd') is True
        assert InstanceCacheOperation.has(archive, 'fd') is False
        assert InstanceCacheOperation.has(archive, 'header') is False
        # Windows refuses to replace a file that is still open, this is the
        # proof that the handle of the block is gone
        fs.create_file('/other.asar', contents=b'other')
        os.replace('/other.asar', '/simple.asar')
        assert file_read_bytes('/simple.asar') == b'other'

    def test_used_without_with(self, fs):
        """The archive works without a with block, the caller closes it."""
        fs.create_file('/simple.asar', contents=simple_archive())
        archive = AsarArchive('/simple.asar')
        assert bytes(archive.read_file('hello.txt')) == b'hello world'
        archive.close()

    def test_close_keeps_nothing_behind(self, fs):
        """close() releases the handle and the table, the entries are re-read."""
        fs.create_file('/simple.asar', contents=simple_archive())
        archive = AsarArchive('/simple.asar')
        archive.add_file(data=b'new', arc_path='new.txt')
        assert archive.entry('new.txt').size is None
        archive.close()
        # The changes were not written, they are gone with the table
        with pytest.raises(AsarEntryNotFoundError):
            archive.entry('new.txt')
        assert archive.entry('hello.txt').size == 11
        archive.close()

    def test_close_is_idempotent(self, fs):
        """Closing an archive twice is fine."""
        fs.create_file('/simple.asar', contents=simple_archive())
        archive = AsarArchive('/simple.asar')
        with archive:
            pass
        archive.close()
        archive.close()

    def test_close_never_opened(self, fs):
        """Closing an archive that was never read does not open it."""
        fs.create_file('/simple.asar', contents=simple_archive())
        archive = AsarArchive('/simple.asar')
        archive.close()
        assert InstanceCacheOperation.has(archive, 'fd') is False

    def test_enter_is_fail_fast(self, fs):
        """Entering a broken archive reports it before anything is extracted."""
        fs.create_file('/broken.asar', contents=b'broken')
        with pytest.raises(AsarFormatError):
            with AsarArchive('/broken.asar'):
                pass

    def test_max_size(self, fs):
        """An archive larger than the limit is refused when it is entered."""
        fs.create_file('/simple.asar', contents=simple_archive())
        with pytest.raises(AsarUnsupportedError) as e:
            with AsarArchive('/simple.asar', max_size=10):
                pass
        assert str(e.value) == 'Archive is larger than the 10 bytes limit of this call'
        with AsarArchive('/simple.asar', max_size=1024) as archive:
            assert len(list(archive.iter_entries())) == 2

    def test_default_max_size(self, fs, monkeypatch):
        """The default limit is applied when the caller does not pass one."""
        fs.create_file('/simple.asar', contents=simple_archive())
        assert AsarArchive('/simple.asar').max_size == DEFAULT_MAX_SIZE
        # The size comes from the handle, so the limit is checked without an
        # archive of that size

        class Stat:
            st_size = DEFAULT_MAX_SIZE + 1

        monkeypatch.setattr(archive_module.os, 'fstat', lambda fd: Stat())
        with pytest.raises(AsarUnsupportedError) as e:
            with AsarArchive('/simple.asar'):
                pass
        assert str(e.value) == f'Archive is larger than the {DEFAULT_MAX_SIZE} bytes limit of this call'

    def test_no_max_size(self, fs, monkeypatch):
        """A caller that knows its archive may pass None to remove the limit."""
        fs.create_file('/simple.asar', contents=simple_archive())

        class Stat:
            st_size = DEFAULT_MAX_SIZE + 1

        monkeypatch.setattr(archive_module.os, 'fstat', lambda fd: Stat())
        with AsarArchive('/simple.asar', max_size=None) as archive:
            assert archive.max_size is None
            assert len(list(archive.iter_entries())) == 2


class TestErrors:
    @pytest.mark.parametrize('content, expected', [
        (b'', 'Archive is truncated, expected 8 bytes but got 0'),
        (b'\x04\x00\x00', 'Archive is truncated, expected 8 bytes but got 3'),
        (
            b'\x04\x00\x00\x00\x0c\x02\x00\x00',
            'Header size 524 exceeds the archive size of 8 bytes',
        ),
    ])
    def test_truncated(self, fs, content, expected):
        """A truncated archive is a format error."""
        fs.create_file('/broken.asar', contents=content)
        with pytest.raises(AsarFormatError) as e:
            with AsarArchive('/broken.asar'):
                pass
        assert str(e.value) == expected

    def test_broken_size_pickle(self, fs):
        """The first frame integer is a constant of the format."""
        fs.create_file('/broken.asar', contents=b'\x08\x00\x00\x00\x0c\x00\x00\x00' + b'\x00' * 32)
        with pytest.raises(AsarFormatError) as e:
            with AsarArchive('/broken.asar'):
                pass
        assert str(e.value) == 'Broken size pickle, expected a payload of 4 bytes, got 8'

    @pytest.mark.parametrize('header_size, expected', [
        (0, 'Header size 0 is smaller than the 8 bytes a header pickle needs'),
        (4, 'Header size 4 is smaller than the 8 bytes a header pickle needs'),
        (32 * 1024 * 1024, 'Header size 33554432 exceeds the 16777216 bytes limit'),
    ])
    def test_header_size_limits(self, fs, header_size, expected):
        """The header length is checked before any allocation."""
        content = fixture.make_archive({'files': {}}, header_size=header_size)
        fs.create_file('/broken.asar', contents=content)
        with pytest.raises(AsarFormatError) as e:
            with AsarArchive('/broken.asar'):
                pass
        assert str(e.value) == expected

    def test_header_outside_archive(self, fs):
        """A header that does not fit in the file is rejected."""
        header = {'files': {'a.txt': {'size': 1, 'offset': '0'}}}
        content = fixture.make_archive(header)
        header_size = int.from_bytes(content[4:8], 'little')
        # Cut the data area off, the header claims to be longer than the file
        fs.create_file('/broken.asar', contents=content[:16])
        with pytest.raises(AsarFormatError) as e:
            with AsarArchive('/broken.asar'):
                pass
        assert str(e.value) == (
            f'Header size {header_size} exceeds the archive size of 16 bytes'
        )

    @pytest.mark.parametrize('data, expected', [
        (b'', 'Header is not valid JSON: '),
        (b'{', 'Header is not valid JSON: '),
        (b'{"files":{}', 'Header is not valid JSON: '),
        (b'{"files":}', 'Header is not valid JSON: '),
        (b'{"files":{}}extra', 'Header is not valid JSON: '),
        (b'{"files":[]}', 'Header "files" must be a plain object'),
        (b'{}', 'Header must be a directory with a "files" property'),
        (b'[]', 'Header must be a JSON object'),
    ])
    def test_broken_header(self, fs, data, expected):
        """A header that is not a valid asar header is rejected."""
        fs.create_file('/broken.asar', contents=fixture.make_archive_bytes(data))
        with pytest.raises(AsarFormatError) as e:
            with AsarArchive('/broken.asar'):
                pass
        assert str(e.value).startswith(expected)

    @pytest.mark.parametrize('node, expected', [
        ({'size': 1, 'offset': 0}, '$.offset'),
        ({'size': 1, 'offset': '+0'}, '"offset" must be a decimal string'),
        ({'size': -1, 'offset': '0'}, '$.size'),
        ({}, 'entry must be a directory (with "files")'),
        ({'link': ''}, '"link" must not be empty'),
    ])
    def test_broken_entry(self, fs, node, expected):
        """An invalid entry is rejected while the header is parsed."""
        content = fixture.make_archive({'files': {'a.txt': node}})
        fs.create_file('/broken.asar', contents=content)
        with pytest.raises(AsarFormatError) as e:
            with AsarArchive('/broken.asar'):
                pass
        assert expected in str(e.value)

    @pytest.mark.parametrize('name', ['dir/a.txt', 'a\\b.txt', '.', '..'])
    def test_broken_entry_name(self, fs, name):
        """Entry names are single path segments, the format stores a tree."""
        content = fixture.make_archive({'files': {name: {'files': {}}}})
        fs.create_file('/broken.asar', contents=content)
        with pytest.raises(AsarFormatError) as e:
            with AsarArchive('/broken.asar'):
                pass
        assert 'Invalid entry name at "/"' in str(e.value)

    def test_entry_out_of_bounds(self, fs):
        """An entry that points outside of the data area is rejected."""
        header = {'files': {'a.txt': {'size': 100, 'offset': '0'}}}
        content = fixture.make_archive(header, b'x' * 10)
        fs.create_file('/broken.asar', contents=content)
        with pytest.raises(AsarFormatError) as e:
            with AsarArchive('/broken.asar'):
                pass
        assert str(e.value) == (
            'Invalid entry at "a.txt": content is outside of the archive, '
            'offset 0 + size 100 exceeds the data area of 10 bytes'
        )

    def test_too_many_entries(self, fs, monkeypatch):
        """An archive whose header holds more entries than the limit is refused."""
        monkeypatch.setattr(model_module, 'MAX_ENTRY_COUNT', 3)
        header = {'files': {f'f{i}.txt': {'size': 0, 'offset': '0'} for i in range(4)}}
        fs.create_file('/many.asar', contents=fixture.make_archive(header, b''))
        with pytest.raises(AsarFormatError) as e:
            with AsarArchive('/many.asar'):
                pass
        assert str(e.value) == 'Archive holds more than 3 entries'

    def test_deep_header(self, fs):
        """A deeply nested header is a format error, not a crash."""
        content = b'{"files":' * 2000 + b'{}' + b'}' * 2000
        fs.create_file('/broken.asar', contents=fixture.make_archive_bytes(content))
        with pytest.raises(AsarFormatError) as e:
            with AsarArchive('/broken.asar'):
                pass
        assert str(e.value) == 'Header is nested too deeply, over 256 segments'

    def test_missing_file(self, fs):
        """An archive that does not exist reports the file system error."""
        with pytest.raises(FileNotFoundError):
            with AsarArchive('/nope.asar'):
                pass


class TestValidate:
    def test_validate_fixtures(self, fs):
        """The fixtures of both versions pass the structure check."""
        for name, content in (
            ('/tiny.asar', fixture.tiny_341()),
            ('/packthis.asar', fixture.packthis_430()),
            ('/extractthis.asar', fixture.extractthis_430()),
            ('/unpack.asar', fixture.packthis_unpack_430()),
            ('/links.asar', fixture.packthis_symlink_430()),
        ):
            fs.create_file(name, contents=content)
            AsarArchive(name).validate()

    def test_validate_content(self, fs):
        """Every content hash of a 4.3.0 fixture matches."""
        fs.create_file('/packthis.asar', contents=fixture.packthis_430())
        AsarArchive('/packthis.asar').validate(verify_content=True)

    def test_validate_content_mismatch(self, fs):
        """A content that does not match its hash is reported."""
        content = fixture.make_archive(
            {'files': {'a.txt': {'size': 5, 'offset': '0', 'integrity': integrity(b'hello', HASH_A)}}},
            b'hello',
        )
        fs.create_file('/broken.asar', contents=content)
        with pytest.raises(AsarFormatError) as e:
            AsarArchive('/broken.asar').validate(verify_content=True)
        assert str(e.value) == (
            f'Content hash of "a.txt" does not match, it is '
            f'2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824 instead of {HASH_A}'
        )

    def test_validate_content_missing_hash(self, fs):
        """Verifying an archive without integrity is an error."""
        fs.create_file('/extractthis.asar', contents=fixture.extractthis_430())
        with pytest.raises(AsarFormatError) as e:
            AsarArchive('/extractthis.asar').validate(verify_content=True)
        assert str(e.value) == 'Entry "dir1/file1.txt" has no integrity hash to verify'

    @pytest.mark.parametrize('name', ['CON', 'a:b', 'trailing.', 'trailing '])
    def test_entry_name_that_can_not_be_created(self, fs, name):
        """A name that no extraction could create is refused while reading."""
        # The name can not be created on every platform, so the entry is refused
        # when the table is built: the extraction never has to look at the path
        # of an entry again
        content = fixture.make_archive({'files': {name: {'size': 0, 'offset': '0'}}})
        fs.create_file('/broken.asar', contents=content)
        with pytest.raises(AsarPathError) as e:
            with AsarArchive('/broken.asar'):
                pass
        assert str(e.value).startswith(f'Invalid entry name at "/": "{name}"')

    @pytest.mark.parametrize('link', ['../escape', '/absolute', 'dir/../../escape'])
    def test_link_target_outside_the_archive(self, fs, link):
        """A link that leaves the archive is refused while the table is built."""
        content = fixture.make_archive({'files': {'l.txt': {'link': link}}})
        fs.create_file('/broken.asar', contents=content)
        with pytest.raises(AsarPathError) as e:
            with AsarArchive('/broken.asar'):
                pass
        assert str(e.value).startswith('Invalid link target of "l.txt": ')

    def test_link_target_missing(self, fs):
        """A link to an entry that is not there is refused while the table is built."""
        content = fixture.make_archive({'files': {'l.txt': {'link': 'nope.txt'}}})
        fs.create_file('/broken.asar', contents=content)
        with pytest.raises(AsarEntryNotFoundError) as e:
            with AsarArchive('/broken.asar'):
                pass
        assert str(e.value) == 'Entry "nope.txt" does not exist in the archive'

    def test_link_circular(self, fs):
        """Two links pointing at each other are refused while the table is built."""
        content = fixture.make_archive({
            'files': {
                'a.txt': {'link': 'b.txt'},
                'b.txt': {'link': 'a.txt'},
            },
        })
        fs.create_file('/broken.asar', contents=content)
        with pytest.raises(AsarFormatError) as e:
            with AsarArchive('/broken.asar'):
                pass
        assert str(e.value) == 'Circular link at "a.txt"'

    def test_link_self(self, fs):
        """A link pointing at itself is refused while the table is built."""
        content = fixture.make_archive({'files': {'a.txt': {'link': 'a.txt'}}})
        fs.create_file('/broken.asar', contents=content)
        with pytest.raises(AsarFormatError) as e:
            with AsarArchive('/broken.asar'):
                pass
        assert str(e.value) == 'Circular link at "a.txt"'

    def test_validate_after_a_target_is_deleted(self, fs):
        """validate() checks the links of the table as it is now."""
        fs.create_file('/links.asar', contents=fixture.packthis_symlink_430())
        with AsarArchive('/links.asar') as archive:
            archive.del_folder('A')
            with pytest.raises(AsarEntryNotFoundError) as e:
                archive.validate()
        assert str(e.value) == 'Entry "A" does not exist in the archive'

    def test_add_repairs_the_target_of_a_link(self, fs):
        """The call that adds what a link points at is the one that repairs it."""
        fs.create_file('/links.asar', contents=fixture.packthis_symlink_430())
        with AsarArchive('/links.asar') as archive:
            archive.del_file('A/real.txt')
            # The links are checked once the entry is in the table, so the
            # intermediate state above is not an error of its own
            archive.add_file(data=b'repaired', arc_path='A/real.txt')
            assert archive.resolve('Current/real.txt')[0] == 'A/real.txt'
            assert bytes(archive.read_file('Current/real.txt')) == b'repaired'

    def test_add_reports_a_link_without_a_target(self, fs):
        """Adding an entry reports a link the table holds without a target."""
        fs.create_file('/links.asar', contents=fixture.packthis_symlink_430())
        with AsarArchive('/links.asar') as archive:
            archive.del_folder('A')
            with pytest.raises(AsarEntryNotFoundError) as e:
                archive.add_file(data=b'x', arc_path='new.txt')
        assert str(e.value) == 'Entry "A" does not exist in the archive'

    def test_add_folder_reports_a_link_without_a_target(self, fs):
        """A tree is added whole before the links of the table are checked."""
        fs.create_file('/links.asar', contents=fixture.packthis_symlink_430())
        fs.create_dir('/tree')
        fs.create_file('/tree/a.txt', contents=b'a')
        with AsarArchive('/links.asar') as archive:
            archive.del_folder('A')
            with pytest.raises(AsarEntryNotFoundError) as e:
                archive.add_folder('/tree')
        assert str(e.value) == 'Entry "A" does not exist in the archive'

    def test_write_refuses_a_broken_link(self, fs):
        """An archive whose links do not resolve is not written."""
        fs.create_file('/links.asar', contents=fixture.packthis_symlink_430())
        with AsarArchive('/links.asar') as archive:
            archive.del_folder('A')
            with pytest.raises(AsarEntryNotFoundError) as e:
                archive.write('/out.asar')
        assert str(e.value) == 'Entry "A" does not exist in the archive'
        assert not os.path.exists('/out.asar')


class TestExtractAll:
    def test_extract_all(self, fs):
        """The whole tree is extracted, empty directories included."""
        fs.create_file('/packthis.asar', contents=fixture.packthis_430())
        with AsarArchive('/packthis.asar') as archive:
            assert len(paths_of(archive, KIND_FILE)) == 6
            assert len(paths_of(archive, KIND_DIR)) == 2
            archive.extract_all('/out')
        assert file_read_bytes('/out/file0.txt') == b'file0 content'
        assert file_read_bytes('/out/emptyfile.txt') == b''
        assert file_read_bytes('/out/dir1/file1.txt') == b'file one.'
        assert len(file_read_bytes('/out/dir2/file2.png')) == 182
        assert file_read_bytes('/out/.hiddenfile.txt') == b'This file is hidden'

    def test_extract_all_unpacked(self, fs):
        """Unpacked entries are read from the .unpacked directory."""
        fs.create_file('/unpack.asar', contents=fixture.packthis_unpack_430())
        for name, content in fixture.unpacked_430_files().items():
            fs.create_file(f'/unpack.asar.unpacked/{name}', contents=content)
        with AsarArchive('/unpack.asar') as archive:
            archive.extract_all('/out')
        assert file_read_bytes('/out/dir2/file2.png') == fixture.unpacked_430_files()['dir2/file2.png']
        assert file_read_bytes('/out/dir2/file3.txt') == b'123'

    def test_extract_all_empty_archive(self, fs):
        """An archive without entry extracts to an empty directory."""
        fs.create_file('/empty.asar', contents=fixture.make_archive({'files': {}}))
        with AsarArchive('/empty.asar') as archive:
            assert list(archive.iter_entries()) == []
            archive.extract_all('/out')
        assert os.path.isdir('/out')
        assert os.listdir('/out') == []

    @pytest.mark.parametrize('name, expected', [
        ('../escape.txt', 'Invalid entry name at "/", a name must not contain a path separator: "../escape.txt"'),
        ('dir/../../escape.txt', 'Invalid entry name at "/", a name must not contain a path separator'),
    ])
    def test_extract_all_escape(self, fs, name, expected):
        """An entry that escapes the target directory never reaches extraction."""
        content = fixture.make_archive({'files': {name: {'size': 1, 'offset': '0'}}}, b'x')
        fs.create_file('/broken.asar', contents=content)
        with pytest.raises(AsarFormatError) as e:
            with AsarArchive('/broken.asar') as archive:
                archive.extract_all('/out')
        assert expected in str(e.value)

    def test_extract_file_target_path(self, fs):
        """extract_file takes a file path and creates its parent directory."""
        fs.create_file('/simple.asar', contents=simple_archive())
        with AsarArchive('/simple.asar') as archive:
            archive.extract_file('hello.txt', '/deep/nested/hello.txt')
        assert file_read_bytes('/deep/nested/hello.txt') == b'hello world'

    @pytest.mark.skipif(os.name == 'nt', reason='symbolic links need elevation on Windows')
    def test_extract_all_links(self, fs):
        """A link is created as a relative symbolic link on POSIX."""
        fs.create_file('/links.asar', contents=fixture.packthis_symlink_430())
        with AsarArchive('/links.asar') as archive:
            assert paths_of(archive, KIND_LINK) == ['Current', 'real.txt']
            archive.extract_all('/out')
        assert os.path.islink('/out/Current')
        assert os.readlink('/out/Current') == 'A'
        assert os.readlink('/out/real.txt') == 'Current/real.txt'


class TestRealArchive:
    """
    Tests against the archive electron-builder produced for the desktop client,
    they are skipped when the release build is not present.
    """
    ARCHIVE = os.path.join('webapp', 'release', 'app.asar')

    def test_read_real_archive(self):
        """The real archive has 38 entries: 28 files and 10 directories."""
        if not os.path.isfile(self.ARCHIVE):
            pytest.skip('webapp/release/app.asar is not built')
        with AsarArchive(self.ARCHIVE) as archive:
            files = [info for _, info in archive.iter_entries() if info.kind == KIND_FILE]
            dirs = [info for _, info in archive.iter_entries() if info.kind == KIND_DIR]
            assert len(files) == 28
            assert len(dirs) == 10
            assert sum(info.size for info in files) == 448793
            assert archive.header_size == 7332
            assert archive.data_offset == 7340
            archive.validate(verify_content=True)

    def test_repack_real_archive(self, fs):
        """Repacking an untouched archive keeps its entries, in the canonical order.

        The shipping archive is written in the traversal order of the file
        system, the canonical order is what this module writes, so the bytes of
        a repack differ from the original (`fed47aad...` shipped, the canonical
        repack of the 2026-09-17 build is `e857d551...`). What must hold is that
        no content changes, that the entries are stored in the canonical order
        and that writing the repack again changes nothing.
        """
        # The release archive is read at import time: this test runs under the
        # in-memory filesystem, which serves every path from memory and never
        # touches the real disk (see fixture.release_archive_bytes).
        original = fixture.release_archive_bytes()
        if original is None:
            pytest.skip(f'{fixture.RELEASE_ARCHIVE} is not built')
        fs.create_file('/app.asar', contents=original)
        with AsarArchive('/app.asar') as archive:
            expected = {
                path: bytes(archive.read_file(path))
                for path, info in archive.iter_entries() if info.kind == KIND_FILE
            }
            canonical_paths = [path for path, _ in archive.iter_entries()]
            archive.write('/app.repacked.asar')
            # The entries are re-read from the archive that was just written
            archive.write('/app.repacked2.asar')
        with AsarArchive('/app.repacked.asar') as archive:
            assert {
                path: bytes(archive.read_file(path))
                for path, info in archive.iter_entries() if info.kind == KIND_FILE
            } == expected
            # The stored header order is the canonical order of the entries
            stored = [keys_path(keys) for keys, _, _ in header_entries(
                parse_header(stored_header('/app.repacked.asar'))
            )]
            assert stored == canonical_paths
        assert file_read_bytes('/app.repacked.asar') == file_read_bytes('/app.repacked2.asar')
