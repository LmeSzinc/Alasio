"""
Tests of reading, validating and extracting an archive that is loaded in memory.

The binary fixtures are the ones described in ``tests/codegen/asar/fixture.py``,
the hand made archives come from ``fixture.make_archive()`` and cover the
malformed inputs an archive from the network may contain.
"""
import os

import pytest

from alasio.codegen.asar.archive import AsarArchive
from alasio.codegen.asar.errors import (
    AsarEntryNotFoundError, AsarError, AsarFormatError, AsarPathError, AsarUnsupportedError
)
from alasio.codegen.asar.format import calc_header_size, pack_header_pickle, pack_size_pickle, parse_size_pickle
from alasio.codegen.asar.model import KIND_DIR, KIND_FILE, KIND_LINK
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


class TestReadAsar:
    def test_read_tiny(self, fs):
        """A 3.4.1 archive is parsed with its directories and offsets."""
        fs.create_file('/tiny.asar', contents=fixture.tiny_341())
        archive = AsarArchive.read_asar('/tiny.asar')
        assert list(archive.files) == ['hello.txt', 'sub', 'sub/bin.dat']
        assert archive.files['hello.txt'].size == 10
        assert archive.files['hello.txt'].offset == 0
        assert archive.files['sub'].kind == KIND_DIR
        assert archive.files['sub'].size is None
        assert archive.files['sub'].offset is None
        assert archive.files['sub/bin.dat'].offset == 10
        assert archive.header_size == 524
        assert archive.data_offset == 532
        assert bytes(archive.data) == fixture.tiny_341()

    def test_read_packthis(self, fs):
        """A 4.3.0 archive with an empty file and a hidden file is parsed."""
        fs.create_file('/packthis.asar', contents=fixture.packthis_430())
        archive = AsarArchive.read_asar('/packthis.asar')
        assert [path for path, info in archive.files.items() if info.kind == KIND_FILE] == [
            '.hiddenfile.txt', 'dir1/file1.txt', 'dir2/file2.png', 'dir2/file3.txt',
            'emptyfile.txt', 'file0.txt',
        ]
        assert [path for path, info in archive.files.items() if info.kind == KIND_DIR] == [
            'dir1', 'dir2',
        ]
        # The empty file shares its offset with the next file, it holds no byte
        assert archive.files['emptyfile.txt'].size == 0
        assert archive.files['emptyfile.txt'].offset == archive.files['file0.txt'].offset == 213

    def test_read_without_integrity(self, fs):
        """An archive whose entries have no integrity is read, 4.3.0 fixture."""
        fs.create_file('/extractthis.asar', contents=fixture.extractthis_430())
        archive = AsarArchive.read_asar('/extractthis.asar')
        assert archive.files['file0.txt'].integrity is None
        assert archive.files['file0.txt'].size == 13
        archive.validate()

    def test_read_unpacked(self, fs):
        """Unpacked entries have no offset, their content is next to the archive."""
        fs.create_file('/unpack.asar', contents=fixture.packthis_unpack_430())
        fs.create_file('/unpack.asar.unpacked/dir2/file2.png', contents=b'png')
        archive = AsarArchive.read_asar('/unpack.asar')
        info = archive.files['dir2/file2.png']
        assert info.unpacked is True
        assert info.offset is None
        assert info.size == 182
        assert archive.files['dir2/file3.txt'].unpacked is False
        # The content is read from <archive>.unpacked, not from the archive body
        assert bytes(archive.read_file('dir2/file2.png')) == b'png'

    def test_read_file_is_a_view(self, fs):
        """read_file slices the loaded archive, it does not copy it."""
        fs.create_file('/simple.asar', contents=simple_archive())
        archive = AsarArchive.read_asar('/simple.asar')
        content = archive.read_file('hello.txt')
        assert content.readonly is True
        assert content.obj is archive.data.obj
        assert bytes(content) == b'hello world'

    def test_read_file_links(self, fs):
        """A link entry is followed to the entry it points at."""
        fs.create_file('/links.asar', contents=fixture.packthis_symlink_430())
        archive = AsarArchive.read_asar('/links.asar')
        assert archive.files['Current'].kind == KIND_LINK
        assert archive.files['Current'].link == 'A'
        assert archive.resolve('Current') == ('A', archive.files['A'])
        # `real.txt` links to a path that goes through the `Current` link
        assert archive.files['real.txt'].link == 'Current/real.txt'
        assert archive.resolve('real.txt')[0] == 'A/real.txt'
        assert bytes(archive.read_file('real.txt')) == bytes(archive.read_file('A/real.txt'))
        assert archive.files['A/reverse-symlink.txt'].link == 'B/reverse-symlink.txt'
        assert bytes(archive.read_file('A/reverse-symlink.txt')) == bytes(
            archive.read_file('B/reverse-symlink.txt')
        )

    def test_entry_missing(self, fs):
        """An unknown path is reported as a missing entry."""
        fs.create_file('/simple.asar', contents=simple_archive())
        archive = AsarArchive.read_asar('/simple.asar')
        with pytest.raises(AsarEntryNotFoundError) as e:
            archive.entry('nope.txt')
        assert str(e.value) == 'Entry "nope.txt" does not exist in the archive'

    def test_read_file_directory(self, fs):
        """A directory has no content to read."""
        fs.create_file('/simple.asar', contents=simple_archive())
        archive = AsarArchive.read_asar('/simple.asar')
        with pytest.raises(AsarError) as e:
            archive.read_file('dir')
        assert str(e.value) == 'Entry "dir" is a directory'

    def test_repr(self, fs):
        """The archive prints its entry count and path."""
        fs.create_file('/simple.asar', contents=simple_archive())
        archive = AsarArchive.read_asar('/simple.asar')
        assert repr(archive) == "AsarArchive(entries=2, path='/simple.asar')"


class TestReadAsarErrors:
    @pytest.mark.parametrize('content, expected', [
        (b'', 'Archive is truncated, expected 8 bytes of size pickle, got 0'),
        (b'\x04\x00\x00', 'Archive is truncated, expected 8 bytes of size pickle, got 3'),
        (
            b'\x04\x00\x00\x00\x0c\x02\x00\x00',
            'Header size 524 exceeds the archive size of 8 bytes',
        ),
    ])
    def test_truncated(self, fs, content, expected):
        """A truncated archive is a format error."""
        fs.create_file('/broken.asar', contents=content)
        with pytest.raises(AsarFormatError) as e:
            AsarArchive.read_asar('/broken.asar')
        assert str(e.value) == expected

    def test_broken_size_pickle(self, fs):
        """The first frame integer is a constant of the format."""
        fs.create_file('/broken.asar', contents=b'\x08\x00\x00\x00\x0c\x00\x00\x00' + b'\x00' * 32)
        with pytest.raises(AsarFormatError) as e:
            AsarArchive.read_asar('/broken.asar')
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
            AsarArchive.read_asar('/broken.asar')
        assert str(e.value) == expected

    def test_header_outside_archive(self, fs):
        """A header that does not fit in the file is rejected."""
        header = {'files': {'a.txt': {'size': 1, 'offset': '0'}}}
        content = fixture.make_archive(header)
        header_size = parse_size_pickle(content[:8])
        # Cut the data area off, the header claims to be longer than the file
        fs.create_file('/broken.asar', contents=content[:16])
        with pytest.raises(AsarFormatError) as e:
            AsarArchive.read_asar('/broken.asar')
        assert str(e.value) == (
            f'Header size {header_size} exceeds the archive size of 16 bytes'
        )

    @pytest.mark.parametrize('data, expected', [
        (b'{"files":{}', 'Header is not valid JSON: '),
        (b'{"files":[]}', 'Header "files" must be a plain object'),
        (b'{}', 'Header must be a directory with a "files" property'),
        (b'[]', 'Header must be a JSON object'),
    ])
    def test_broken_header(self, fs, data, expected):
        """A header that is not a valid asar header is rejected."""
        json_bytes = data
        content = (
            b'\x04\x00\x00\x00'
            + (8 + ((len(json_bytes) + 3) // 4) * 4).to_bytes(4, 'little')
            + (4 + ((len(json_bytes) + 3) // 4) * 4).to_bytes(4, 'little')
            + len(json_bytes).to_bytes(4, 'little')
            + json_bytes
            + b'\x00' * (((len(json_bytes) + 3) // 4) * 4 - len(json_bytes))
        )
        fs.create_file('/broken.asar', contents=content)
        with pytest.raises(AsarFormatError) as e:
            AsarArchive.read_asar('/broken.asar')
        assert str(e.value).startswith(expected)

    @pytest.mark.parametrize('node, expected', [
        ({'size': 1, 'offset': 0}, '"offset" must be a string'),
        ({'size': 1, 'offset': '+0'}, '"offset" must be a decimal string, got "+0"'),
        ({'size': -1, 'offset': '0'}, '"size" must be a non-negative number'),
        ({}, 'entry must be a directory (with "files")'),
        ({'link': ''}, '"link" must not be empty'),
    ])
    def test_broken_entry(self, fs, node, expected):
        """An invalid entry is rejected while the header is parsed."""
        content = fixture.make_archive({'files': {'a.txt': node}})
        fs.create_file('/broken.asar', contents=content)
        with pytest.raises(AsarFormatError) as e:
            AsarArchive.read_asar('/broken.asar')
        assert expected in str(e.value)

    @pytest.mark.parametrize('name', ['dir/a.txt', 'a\\b.txt', '.', '..'])
    def test_broken_entry_name(self, fs, name):
        """Entry names are single path segments, the format stores a tree."""
        content = fixture.make_archive({'files': {name: {'files': {}}}})
        fs.create_file('/broken.asar', contents=content)
        with pytest.raises(AsarFormatError) as e:
            AsarArchive.read_asar('/broken.asar')
        assert 'Invalid entry name at "/"' in str(e.value)

    def test_entry_out_of_bounds(self, fs):
        """An entry that points outside of the data area is rejected."""
        header = {'files': {'a.txt': {'size': 100, 'offset': '0'}}}
        content = fixture.make_archive(header, b'x' * 10)
        fs.create_file('/broken.asar', contents=content)
        with pytest.raises(AsarFormatError) as e:
            AsarArchive.read_asar('/broken.asar')
        assert str(e.value) == (
            'Invalid entry at "a.txt": content is outside of the archive, '
            'offset 0 + size 100 exceeds the data area of 10 bytes'
        )

    def test_deep_header(self, fs):
        """A deeply nested header is a format error, not a crash."""
        content = b'{"files":' * 2000 + b'{}' + b'}' * 2000
        archive = pack_size_pickle(calc_header_size(len(content))) + pack_header_pickle(content)
        fs.create_file('/broken.asar', contents=archive)
        with pytest.raises(AsarFormatError) as e:
            AsarArchive.read_asar('/broken.asar')
        assert str(e.value) == 'Header is nested too deeply, over 256 segments'

    def test_max_size(self, fs):
        """An archive larger than the limit is refused before being read."""
        fs.create_file('/simple.asar', contents=simple_archive())
        with pytest.raises(AsarUnsupportedError) as e:
            AsarArchive.read_asar('/simple.asar', max_size=10)
        assert str(e.value) == 'Archive is larger than the 10 bytes limit of this call'
        archive = AsarArchive.read_asar('/simple.asar', max_size=1024)
        assert len(archive.files) == 2


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
            AsarArchive.read_asar(name).validate()

    def test_validate_content(self, fs):
        """Every content hash of a 4.3.0 fixture matches."""
        fs.create_file('/packthis.asar', contents=fixture.packthis_430())
        AsarArchive.read_asar('/packthis.asar').validate(verify_content=True)

    def test_validate_content_mismatch(self, fs):
        """A content that does not match its hash is reported."""
        content = fixture.make_archive(
            {'files': {'a.txt': {'size': 5, 'offset': '0', 'integrity': integrity(b'hello', HASH_A)}}},
            b'hello',
        )
        fs.create_file('/broken.asar', contents=content)
        with pytest.raises(AsarFormatError) as e:
            AsarArchive.read_asar('/broken.asar').validate(verify_content=True)
        assert str(e.value) == (
            f'Content hash of "a.txt" does not match, it is '
            f'2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824 instead of {HASH_A}'
        )

    def test_validate_content_missing_hash(self, fs):
        """Verifying an archive without integrity is an error."""
        fs.create_file('/extractthis.asar', contents=fixture.extractthis_430())
        with pytest.raises(AsarFormatError) as e:
            AsarArchive.read_asar('/extractthis.asar').validate(verify_content=True)
        assert str(e.value) == 'Entry "dir1/file1.txt" has no integrity hash to verify'

    @pytest.mark.parametrize('name, expected', [
        ('CON', 'Invalid archive path "CON": Path component cannot be reserved system name: CON'),
        ('a:b', 'Invalid archive path "a:b": Path component should not contain character: ":"'),
        ('trailing.', 'Invalid archive path "trailing.": Path component cannot end with a <dot>'),
        ('trailing ', 'Invalid archive path "trailing ": Path component cannot end with a <space>'),
    ])
    def test_validate_bad_path(self, fs, name, expected):
        """An entry that can not be extracted on every platform is reported."""
        content = fixture.make_archive({'files': {name: {'size': 0, 'offset': '0'}}})
        fs.create_file('/broken.asar', contents=content)
        with pytest.raises(AsarPathError) as e:
            AsarArchive.read_asar('/broken.asar').validate()
        assert str(e.value) == expected

    @pytest.mark.parametrize('link', ['../escape', '/absolute', 'dir/../../escape'])
    def test_validate_link_escape(self, fs, link):
        """A link that leaves the archive is reported."""
        content = fixture.make_archive({'files': {'l.txt': {'link': link}}})
        fs.create_file('/broken.asar', contents=content)
        with pytest.raises(AsarPathError) as e:
            AsarArchive.read_asar('/broken.asar').validate()
        assert str(e.value).startswith('Invalid link target of "l.txt": ')

    def test_validate_link_missing(self, fs):
        """A link to a missing entry is reported."""
        content = fixture.make_archive({'files': {'l.txt': {'link': 'nope.txt'}}})
        fs.create_file('/broken.asar', contents=content)
        with pytest.raises(AsarEntryNotFoundError) as e:
            AsarArchive.read_asar('/broken.asar').validate()
        assert str(e.value) == 'Entry "nope.txt" does not exist in the archive'

    def test_validate_link_circular(self, fs):
        """Two links pointing at each other are reported."""
        content = fixture.make_archive({
            'files': {
                'a.txt': {'link': 'b.txt'},
                'b.txt': {'link': 'a.txt'},
            },
        })
        fs.create_file('/broken.asar', contents=content)
        with pytest.raises(AsarFormatError) as e:
            AsarArchive.read_asar('/broken.asar').validate()
        assert str(e.value) == 'Circular link at "a.txt"'

    def test_validate_link_self(self, fs):
        """A link pointing at itself is reported."""
        content = fixture.make_archive({'files': {'a.txt': {'link': 'a.txt'}}})
        fs.create_file('/broken.asar', contents=content)
        with pytest.raises(AsarFormatError) as e:
            AsarArchive.read_asar('/broken.asar').validate()
        assert str(e.value) == 'Circular link at "a.txt"'


class TestExtractAll:
    def test_extract_all(self, fs):
        """The whole tree is extracted, empty directories included."""
        fs.create_file('/packthis.asar', contents=fixture.packthis_430())
        archive = AsarArchive.read_asar('/packthis.asar')
        result = archive.extract_all('/out')
        assert result.file_count == 6
        assert result.dir_count == 2
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
        archive = AsarArchive.read_asar('/unpack.asar')
        result = archive.extract_all('/out')
        assert result.unpacked_count == 1
        assert file_read_bytes('/out/dir2/file2.png') == fixture.unpacked_430_files()['dir2/file2.png']
        assert file_read_bytes('/out/dir2/file3.txt') == b'123'

    def test_extract_all_empty_archive(self, fs):
        """An archive without entry extracts to an empty directory."""
        fs.create_file('/empty.asar', contents=fixture.make_archive({'files': {}}))
        result = AsarArchive.read_asar('/empty.asar').extract_all('/out')
        assert (result.file_count, result.dir_count) == (0, 0)
        assert os.path.isdir('/out')

    @pytest.mark.parametrize('name, expected', [
        ('../escape.txt', 'Invalid entry name at "/", a name must not contain a path separator: "../escape.txt"'),
        ('dir/../../escape.txt', 'Invalid entry name at "/", a name must not contain a path separator'),
    ])
    def test_extract_all_escape(self, fs, name, expected):
        """An entry that escapes the target directory never reaches extraction."""
        content = fixture.make_archive({'files': {name: {'size': 1, 'offset': '0'}}}, b'x')
        fs.create_file('/broken.asar', contents=content)
        with pytest.raises(AsarFormatError) as e:
            AsarArchive.read_asar('/broken.asar').extract_all('/out')
        assert expected in str(e.value)

    def test_extract_all_escape_after_read(self, fs):
        """An entry that escapes the target directory is refused when read."""
        # `a:b` passes the format check of the header, but can not be created on
        # every platform, so extraction must still refuse it
        content = fixture.make_archive({'files': {'a:b': {'size': 1, 'offset': '0'}}}, b'x')
        fs.create_file('/broken.asar', contents=content)
        archive = AsarArchive.read_asar('/broken.asar')
        assert list(archive.files) == ['a:b']
        with pytest.raises(AsarPathError) as e:
            archive.extract_all('/out')
        assert str(e.value).startswith('Invalid archive path "a:b"')

    def test_extract_file_target_path(self, fs):
        """extract_file takes a file path and creates its parent directory."""
        fs.create_file('/simple.asar', contents=simple_archive())
        archive = AsarArchive.read_asar('/simple.asar')
        archive.extract_file('hello.txt', '/deep/nested/hello.txt')
        assert file_read_bytes('/deep/nested/hello.txt') == b'hello world'

    @pytest.mark.skipif(os.name == 'nt', reason='symbolic links need elevation on Windows')
    def test_extract_all_links(self, fs):
        """A link is created as a relative symbolic link on POSIX."""
        fs.create_file('/links.asar', contents=fixture.packthis_symlink_430())
        archive = AsarArchive.read_asar('/links.asar')
        result = archive.extract_all('/out')
        assert result.link_count == 2
        assert os.path.islink('/out/A')
        assert os.readlink('/out/A') == archive.files['A'].link


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
        archive = AsarArchive.read_asar(self.ARCHIVE)
        files = [info for info in archive.files.values() if info.kind == KIND_FILE]
        dirs = [info for info in archive.files.values() if info.kind == KIND_DIR]
        assert len(files) == 28
        assert len(dirs) == 10
        assert sum(info.size for info in files) == 448793
        assert archive.header_size == 7332
        assert archive.data_offset == 7340
        archive.validate(verify_content=True)

    def test_repack_real_archive(self, fs):
        """Repacking an untouched archive writes the same bytes."""
        # The release archive is read at import time: this test runs under the
        # in-memory filesystem, which serves every path from memory and never
        # touches the real disk (see fixture.release_archive_bytes).
        original = fixture.release_archive_bytes()
        if original is None:
            pytest.skip(f'{fixture.RELEASE_ARCHIVE} is not built')
        fs.create_file('/app.asar', contents=original)
        archive = AsarArchive.read_asar('/app.asar')
        result = archive.write_asar('/app.repacked.asar')
        assert file_read_bytes('/app.repacked.asar') == original
        assert result.archive_size == len(original)
