"""
Tests of the header models: node serialization order, header validation and the
conversion between the header tree and the flat entry table.

The validation rules mirror ``validateHeader()`` of @electron/asar 4.3.0
(``src/disk.ts``), every rejected case below is one of its checks.
"""
import msgspec
import pytest

from alasio.codegen.asar.errors import AsarError, AsarFormatError
from alasio.codegen.asar.format import BLOCK_SIZE, UINT32_MAX
from alasio.codegen.asar.model import (
    AsarFileInfo, DirNode, FileNode, LinkNode, UnpackedFileNode, build_header, check_node, decode_header, encode_header,
    has_parent, iter_entries, new_integrity, propagate_unpacked, read_entries, validate_header
)

HASH_A = 'a' * 64
HASH_B = 'b' * 64


def integrity(hash_hex=HASH_A, blocks=None):
    """Build an integrity object, blocks default to the single block of a small file."""
    return new_integrity(hash_hex, [HASH_B] if blocks is None else blocks)


class TestNewIntegrity:
    def test_new_integrity(self):
        """The integrity object has the wire field order of the reference."""
        assert new_integrity(HASH_A, [HASH_B]) == {
            'algorithm': 'SHA256',
            'hash': HASH_A,
            'blockSize': 4194304,
            'blocks': [HASH_B],
        }
        assert list(new_integrity(HASH_A, [])) == ['algorithm', 'hash', 'blockSize', 'blocks']

    def test_new_integrity_block_order(self):
        """Every block hash is kept in order, the input list is not shared."""
        blocks = [HASH_A, HASH_B]
        result = new_integrity(HASH_A, blocks)
        assert result['blocks'] == blocks
        assert result['blocks'] is not blocks


class TestNodeEncoding:
    def test_file_node_order(self):
        """A file node is size, offset, executable, integrity."""
        node = FileNode(size=10, offset='0', executable=True, integrity=integrity())
        assert encode_header(DirNode(files={'a': node})) == (
            b'{"files":{"a":{"size":10,"offset":"0","executable":true,"integrity":'
            b'{"algorithm":"SHA256","hash":"' + HASH_A.encode() + b'","blockSize":4194304,"blocks":["'
            + HASH_B.encode() + b'"]}}}}'
        )

    def test_file_node_without_optional_fields(self):
        """executable and integrity are omitted when unset."""
        assert encode_header(DirNode(files={'a': FileNode(size=7, offset='10')})) == (
            b'{"files":{"a":{"size":7,"offset":"10"}}}'
        )

    def test_unpacked_file_node_order(self):
        """An unpacked file node is size, unpacked, integrity, without offset."""
        node = UnpackedFileNode(size=3, unpacked=True, integrity=integrity(HASH_B, []))
        assert encode_header(DirNode(files={'a': node})) == (
            b'{"files":{"a":{"size":3,"unpacked":true,"integrity":{"algorithm":"SHA256","hash":"'
            + HASH_B.encode() + b'","blockSize":4194304,"blocks":[]}}}}'
        )

    def test_dir_node_order(self):
        """A directory node is files, or unpacked then files."""
        assert encode_header(DirNode(files={})) == b'{"files":{}}'
        assert encode_header(DirNode(files={'a': DirNode(files={})})) == b'{"files":{"a":{"files":{}}}}'
        assert encode_header(DirNode(files={'a': DirNode(unpacked=True, files={})})) == (
            b'{"files":{"a":{"unpacked":true,"files":{}}}}'
        )

    def test_link_node_order(self):
        """A link node is link, or unpacked then link."""
        assert encode_header(DirNode(files={'a': LinkNode(link='b.txt')})) == (
            b'{"files":{"a":{"link":"b.txt"}}}'
        )
        assert encode_header(DirNode(files={'a': LinkNode(unpacked=True, link='b.txt')})) == (
            b'{"files":{"a":{"unpacked":true,"link":"b.txt"}}}'
        )

    def test_encode_header_no_ascii_escape(self):
        """Non ASCII names are stored as UTF-8, not as \\u escapes."""
        assert encode_header(DirNode(files={'dir/sub.txt': DirNode(files={})})) == (
            b'{"files":{"dir/sub.txt":{"files":{}}}}'
        )
        assert encode_header(DirNode(files={'中文.txt': DirNode(files={})})) == (
            '{"files":{"中文.txt":{"files":{}}}}'.encode('utf-8')
        )


class TestCheckName:
    @pytest.mark.parametrize('name, expected', [
        ('a/b', 'Invalid entry name at "/", a name must not contain a path separator: "a/b"'),
        ('a\\b', 'Invalid entry name at "/", a name must not contain a path separator: "a\\b"'),
        ('\\', 'Invalid entry name at "/", a name must not contain a path separator: "\\"'),
        ('.', 'Invalid entry name at "/": "."'),
        ('..', 'Invalid entry name at "/": ".."'),
    ])
    def test_check_name_invalid(self, name, expected):
        """Names with a separator or a directory pointer are rejected."""
        header = {'files': {name: {'files': {}}}}
        with pytest.raises(AsarFormatError) as e:
            validate_header(header)
        assert str(e.value) == expected

    @pytest.mark.parametrize('name, expected', [
        ('a/b', 'Invalid entry name at "dir", a name must not contain a path separator: "a/b"'),
        ('a\\b', 'Invalid entry name at "dir", a name must not contain a path separator: "a\\b"'),
        ('..', 'Invalid entry name at "dir": ".."'),
        ('.', 'Invalid entry name at "dir": "."'),
    ])
    def test_check_name_invalid_nested(self, name, expected):
        """The error message names the parent directory."""
        header = {'files': {'dir': {'files': {name: {'files': {}}}}}}
        with pytest.raises(AsarFormatError) as e:
            validate_header(header)
        assert str(e.value) == expected

    @pytest.mark.parametrize('name', [
        'a', 'a.txt', '.hidden', '..hidden', 'a.', 'a ', 'a-b_c.d', '中文.txt', '__proto__',
        'constructor', 'a\u2028.txt',
    ])
    def test_check_name_valid(self, name):
        """Names that are not a separator nor a directory pointer are accepted."""
        validate_header({'files': {name: {'files': {}}}})


class TestCheckNode:
    @pytest.mark.parametrize('node, expected', [
        # Not an object
        ([], 'Invalid entry at "a": entry must be an object'),
        ('x', 'Invalid entry at "a": entry must be an object'),
        (None, 'Invalid entry at "a": entry must be an object'),
        # offset
        ({'size': 1, 'offset': 0}, 'Invalid entry at "a": "offset" must be a string'),
        ({'size': 1, 'offset': '+0'}, 'Invalid entry at "a": "offset" must be a decimal string, got "+0"'),
        ({'size': 1, 'offset': '-1'}, 'Invalid entry at "a": "offset" must be a decimal string, got "-1"'),
        ({'size': 1, 'offset': ' 0'}, 'Invalid entry at "a": "offset" must be a decimal string, got " 0"'),
        ({'size': 1, 'offset': '0x10'}, 'Invalid entry at "a": "offset" must be a decimal string, got "0x10"'),
        ({'size': 1, 'offset': '1.0'}, 'Invalid entry at "a": "offset" must be a decimal string, got "1.0"'),
        ({'size': 1, 'offset': '١٢'}, 'Invalid entry at "a": "offset" must be a decimal string, got "١٢"'),
        # size
        ({'offset': '0'}, 'Invalid entry at "a": "size" must be a number'),
        ({'size': True, 'offset': '0'}, 'Invalid entry at "a": "size" must be a number'),
        ({'size': '1', 'offset': '0'}, 'Invalid entry at "a": "size" must be a number'),
        ({'size': -1, 'offset': '0'}, 'Invalid entry at "a": "size" must be a non-negative number'),
        ({'size': 1.5, 'offset': '0'}, 'Invalid entry at "a": "size" must be an integer'),
        ({'size': float('inf'), 'offset': '0'}, 'Invalid entry at "a": "size" must be a non-negative number'),
        ({'size': float('nan'), 'offset': '0'}, 'Invalid entry at "a": "size" must be a non-negative number'),
        ({'size': UINT32_MAX + 1, 'offset': '0'}, 'Invalid entry at "a": "size" must not exceed 4294967295 bytes'),
        # Empty node
        ({}, 'Invalid entry at "a": entry must be a directory (with "files"), '
             'a file (with "offset" or "unpacked"), or a link (with "link")'),
        ({'unpacked': True}, 'Invalid entry at "a": entry must be a directory (with "files"), '
                             'a file (with "offset" or "unpacked"), or a link (with "link")'),
        ({'size': 1}, 'Invalid entry at "a": entry must be a directory (with "files"), '
                      'a file (with "offset" or "unpacked"), or a link (with "link")'),
        # link
        ({'link': 1}, 'Invalid entry at "a": "link" must be a string'),
        ({'link': ''}, 'Invalid entry at "a": "link" must not be empty'),
        # files
        ({'files': []}, 'Invalid entry at "a": "files" must be a plain object'),
        ({'files': None}, 'Invalid entry at "a": "files" must be a plain object'),
        # booleans
        ({'offset': '0', 'size': 1, 'unpacked': 1}, 'Invalid entry at "a": "unpacked" must be a boolean'),
        ({'offset': '0', 'size': 1, 'executable': 1}, 'Invalid entry at "a": "executable" must be a boolean'),
        ({'files': {}, 'unpacked': 'yes'}, 'Invalid entry at "a": "unpacked" must be a boolean'),
        ({'link': 'x', 'unpacked': 'yes'}, 'Invalid entry at "a": "unpacked" must be a boolean'),
        # integrity
        ({'offset': '0', 'size': 1, 'integrity': 1}, 'Invalid entry at "a": "integrity" must be an object'),
        ({'offset': '0', 'size': 1, 'integrity': []}, 'Invalid entry at "a": "integrity" must be an object'),
        ({'offset': '0', 'size': 1, 'integrity': {'hash': HASH_A, 'blockSize': 1, 'blocks': []}},
         'Invalid entry at "a": "integrity.algorithm" must be a string'),
        ({'offset': '0', 'size': 1, 'integrity': {'algorithm': 'SHA256', 'blockSize': 1, 'blocks': []}},
         'Invalid entry at "a": "integrity.hash" must be a string'),
        ({'offset': '0', 'size': 1, 'integrity': {'algorithm': 'SHA256', 'hash': HASH_A, 'blocks': []}},
         'Invalid entry at "a": "integrity.blockSize" must be a positive number'),
        ({'offset': '0', 'size': 1,
          'integrity': {'algorithm': 'SHA256', 'hash': HASH_A, 'blockSize': 0, 'blocks': []}},
         'Invalid entry at "a": "integrity.blockSize" must be a positive number'),
        ({'offset': '0', 'size': 1, 'integrity': {'algorithm': 'SHA256', 'hash': HASH_A, 'blockSize': 1}},
         'Invalid entry at "a": "integrity.blocks" must be an array'),
        ({'offset': '0', 'size': 1,
          'integrity': {'algorithm': 'SHA256', 'hash': HASH_A, 'blockSize': 1, 'blocks': [1]}},
         'Invalid entry at "a": "integrity.blocks[0]" must be a string'),
    ])
    def test_check_node_invalid(self, node, expected):
        """Every malformed node is rejected with the reference message."""
        with pytest.raises(AsarFormatError) as e:
            validate_header({'files': {'a': node}})
        assert str(e.value) == expected

    @pytest.mark.parametrize('node', [
        # A file without integrity is valid, the JS fixture input/extractthis.asar
        # stores exactly this shape
        {'size': 9, 'offset': '0'},
        # An unpacked file has no offset
        {'size': 9, 'unpacked': True},
        # zero byte files are valid
        {'size': 0, 'offset': '0'},
        # A directory, with and without the unpacked flag
        {'files': {}},
        {'files': {}, 'unpacked': True},
        {'files': {}, 'unpacked': False},
        # A link
        {'link': 'a.txt'},
        {'link': 'a.txt', 'unpacked': True},
        # Unknown fields are ignored, as the JS implementation does
        {'size': 1, 'offset': '0', 'unknown': {'a': 1}},
        # Size as an integral float is accepted
        {'size': 1.0, 'offset': '0'},
    ])
    def test_check_node_valid(self, node):
        """Valid nodes pass the check and report their kind."""
        assert check_node(node, 'a') in ('dir', 'file', 'unpacked', 'link')


class TestIterEntries:
    def test_iter_entries_order(self):
        """Entries are yielded depth first, in the order they are stored."""
        header = {
            'files': {
                'b.txt': {'size': 1, 'offset': '0'},
                'a': {
                    'files': {
                        'c.txt': {'size': 2, 'offset': '1'},
                        'd': {'files': {'e.txt': {'size': 3, 'offset': '3'}}},
                    },
                },
                'f.txt': {'size': 4, 'offset': '6'},
            },
        }
        assert [path for path, _, _ in iter_entries(header)] == [
            'b.txt', 'a', 'a/c.txt', 'a/d', 'a/d/e.txt', 'f.txt',
        ]
        assert [kind for _, kind, _ in iter_entries(header)] == [
            'file', 'dir', 'file', 'dir', 'file', 'file',
        ]

    def test_iter_entries_unpacked_kind(self):
        """An unpacked file is reported with its own kind."""
        header = {'files': {'a': {'size': 1, 'unpacked': True}, 'b': {'size': 1, 'offset': '0'}}}
        assert [(path, kind) for path, kind, _ in iter_entries(header)] == [
            ('a', 'unpacked'), ('b', 'file'),
        ]

    @pytest.mark.parametrize('header, expected', [
        ([], 'Header must be a JSON object'),
        ('x', 'Header must be a JSON object'),
        (None, 'Header must be a JSON object'),
        ({}, 'Header must be a directory with a "files" property'),
        ({'files': None}, 'Header "files" must be a plain object'),
        ({'files': []}, 'Header "files" must be a plain object'),
    ])
    def test_iter_entries_root_invalid(self, header, expected):
        """The root header must be a directory."""
        with pytest.raises(AsarFormatError) as e:
            list(iter_entries(header))
        assert str(e.value) == expected

    def test_iter_entries_deep_path(self):
        """A path deeper than the limit is rejected instead of crashing."""
        node = {'files': {}}
        for _ in range(300):
            node = {'files': {'d': node}}
        with pytest.raises(AsarFormatError) as e:
            validate_header(node)
        assert str(e.value) == 'Path "%s" is deeper than 256 segments' % '/'.join(['d'] * 256)

    def test_iter_entries_max_depth(self):
        """A path of exactly the maximum depth is accepted."""
        node = {'files': {}}
        for _ in range(255):
            node = {'files': {'d': node}}
        validate_header(node)
        assert len(list(iter_entries(node))) == 255


class TestDecodeHeader:
    def test_decode_header(self):
        """A header is decoded to plain objects."""
        header = decode_header(b'{"files":{"a":{"size":1,"offset":"0"}}}')
        assert header == {'files': {'a': {'size': 1, 'offset': '0'}}}
        assert type(header) is dict
        assert type(header['files']['a']) is dict

    @pytest.mark.parametrize('data', [
        b'',
        b'{',
        b'{"files":}',
        b'{"files":{}}extra',
    ])
    def test_decode_header_invalid_json(self, data):
        """Broken JSON is a format error, not a decoder error."""
        with pytest.raises(AsarFormatError) as e:
            decode_header(data)
        assert str(e.value).startswith('Header is not valid JSON: ')

    def test_decode_header_deep_json(self):
        """A deeply nested JSON is reported as a format error, not a crash."""
        data = b'{"files":' * 2000 + b'{}' + b'}' * 2000
        with pytest.raises(AsarFormatError) as e:
            decode_header(data)
        assert str(e.value) == 'Header is nested too deeply, over 256 segments'


class TestReadEntries:
    def test_read_entries(self):
        """The flat table follows the header order and keeps every field."""
        header = {
            'files': {
                'a.txt': {'size': 9, 'offset': '0', 'integrity': integrity()},
                'b': {'files': {'c.txt': {'size': 1, 'offset': '9'}}},
                'd.txt': {'size': 0, 'offset': '10'},
                'e.txt': {'size': 3, 'unpacked': True, 'executable': True},
                'l.txt': {'link': 'a.txt'},
            },
        }
        files = read_entries(header, 30, 20)
        assert list(files) == ['a.txt', 'b', 'b/c.txt', 'd.txt', 'e.txt', 'l.txt']
        assert files['a.txt'] == AsarFileInfo(
            path='a.txt', kind='file', size=9, offset=0, integrity=integrity(),
        )
        assert files['b'] == AsarFileInfo(path='b', kind='dir')
        assert files['b/c.txt'].offset == 9
        assert files['d.txt'].size == 0
        assert files['e.txt'] == AsarFileInfo(
            path='e.txt', kind='file', size=3, unpacked=True, executable=True,
        )
        assert files['l.txt'] == AsarFileInfo(path='l.txt', kind='link', link='a.txt')

    def test_read_entries_out_of_bounds(self):
        """An entry that points outside of the archive is rejected."""
        header = {'files': {'a.txt': {'size': 11, 'offset': '0'}}}
        with pytest.raises(AsarFormatError) as e:
            read_entries(header, 30, 20)
        assert str(e.value) == (
            'Invalid entry at "a.txt": content is outside of the archive, '
            'offset 0 + size 11 exceeds the data area of 10 bytes'
        )

    def test_read_entries_out_of_bounds_at_end(self):
        """An entry that ends exactly at the end of the data area is accepted."""
        header = {'files': {'a.txt': {'size': 10, 'offset': '0'}}}
        assert read_entries(header, 30, 20)['a.txt'].size == 10
        header = {'files': {'a.txt': {'size': 10, 'offset': '1'}}}
        with pytest.raises(AsarFormatError):
            read_entries(header, 30, 20)

    def test_read_entries_reused_offset(self):
        """Several entries may share an offset, 4.3.0 deduplicates contents."""
        header = {
            'files': {
                'a.txt': {'size': 5, 'offset': '0'},
                'b.txt': {'size': 5, 'offset': '0'},
                'c.txt': {'size': 0, 'offset': '0'},
            },
        }
        files = read_entries(header, 25, 20)
        assert [(i.size, i.offset) for i in files.values()] == [(5, 0), (5, 0), (0, 0)]

    def test_read_entries_keeps_unknown_integrity(self):
        """An integrity object is passed through unchanged."""
        value = {'algorithm': 'SHA256', 'hash': HASH_A, 'blockSize': BLOCK_SIZE, 'blocks': [HASH_B, HASH_A]}
        header = {'files': {'a.txt': {'size': 5, 'offset': '0', 'integrity': value}}}
        assert read_entries(header, 25, 20)['a.txt'].integrity == value


class TestBuildHeader:
    def test_build_header_golden_bytes(self):
        """A flat table is rebuilt to the exact bytes of the reference."""
        files = {
            'hello.txt': AsarFileInfo(
                path='hello.txt', kind='file', size=10, offset=0,
                integrity=new_integrity(HASH_A, [HASH_A]),
            ),
            'sub': AsarFileInfo(path='sub', kind='dir'),
            'sub/bin.dat': AsarFileInfo(
                path='sub/bin.dat', kind='file', size=7, offset=10,
                integrity=new_integrity(HASH_B, [HASH_B]),
            ),
        }
        assert encode_header(build_header(files)) == (
            b'{"files":{"hello.txt":{"size":10,"offset":"0","integrity":{"algorithm":"SHA256","hash":"'
            + HASH_A.encode() + b'","blockSize":4194304,"blocks":["' + HASH_A.encode() + b'"]}},'
            b'"sub":{"files":{"bin.dat":{"size":7,"offset":"10","integrity":{"algorithm":"SHA256","hash":"'
            + HASH_B.encode() + b'","blockSize":4194304,"blocks":["' + HASH_B.encode() + b'"]}}}}}}'
        )

    def test_build_header_implicit_dirs(self):
        """A parent directory that is not in the table is created in place."""
        files = {
            'a.txt': AsarFileInfo(path='a.txt', kind='file', size=1, offset=0),
            'dir/sub/b.txt': AsarFileInfo(path='dir/sub/b.txt', kind='file', size=1, offset=1),
            'c.txt': AsarFileInfo(path='c.txt', kind='file', size=1, offset=2),
        }
        assert encode_header(build_header(files)) == (
            b'{"files":{"a.txt":{"size":1,"offset":"0"},"dir":{"files":{"sub":{"files":'
            b'{"b.txt":{"size":1,"offset":"1"}}}}},"c.txt":{"size":1,"offset":"2"}}}'
        )

    def test_build_header_unpacked_dir(self):
        """An unpacked directory marks itself and its subtree."""
        files = {
            'dir': AsarFileInfo(path='dir', kind='dir', unpacked=True),
            'dir/sub': AsarFileInfo(path='dir/sub', kind='dir', unpacked=True),
            'dir/sub/a.txt': AsarFileInfo(
                path='dir/sub/a.txt', kind='file', size=5, unpacked=True, integrity=integrity(),
            ),
        }
        assert encode_header(build_header(files)) == (
            b'{"files":{"dir":{"unpacked":true,"files":{"sub":{"unpacked":true,"files":'
            b'{"a.txt":{"size":5,"unpacked":true,"integrity":{"algorithm":"SHA256","hash":"'
            + HASH_A.encode() + b'","blockSize":4194304,"blocks":["' + HASH_B.encode() + b'"]}}}}}}}}'
        )

    def test_build_header_link(self):
        """A link entry becomes a link node."""
        files = {
            'a.txt': AsarFileInfo(path='a.txt', kind='file', size=1, offset=0),
            'l.txt': AsarFileInfo(path='l.txt', kind='link', link='a.txt'),
        }
        assert encode_header(build_header(files)) == (
            b'{"files":{"a.txt":{"size":1,"offset":"0"},"l.txt":{"link":"a.txt"}}}'
        )

    def test_build_header_file_without_offset(self):
        """A file that was never written to an archive can not be stored."""
        files = {'a.txt': AsarFileInfo(path='a.txt', kind='file', size=1)}
        with pytest.raises(AsarError) as e:
            build_header(files)
        assert str(e.value) == 'Entry "a.txt" has no offset, it was not written to an archive yet'

    def test_build_header_file_dir_conflict(self):
        """A path may not be used by both a file and a directory."""
        files = {
            'a': AsarFileInfo(path='a', kind='file', size=1, offset=0),
            'a/b.txt': AsarFileInfo(path='a/b.txt', kind='file', size=1, offset=1),
        }
        with pytest.raises(AsarError) as e:
            build_header(files)
        assert str(e.value) == 'Path "a/b.txt" is used by both a file and a directory'

    def test_build_header_file_dir_conflict(self):
        """A file may not be used as the directory of a deeper entry."""
        files = {
            'a': AsarFileInfo(path='a', kind='file', size=1, offset=0),
            'a/b.txt': AsarFileInfo(path='a/b.txt', kind='file', size=1, offset=0),
        }
        with pytest.raises(AsarError) as e:
            build_header(files)
        assert str(e.value) == 'Path "a/b.txt" is used by both a file and a directory'

    def test_build_header_dir_file_conflict(self):
        """A file may not be placed below a path that already holds a subtree."""
        files = {}
        files['a/b.txt'] = AsarFileInfo(path='a/b.txt', kind='file', size=1, offset=0)
        files['a'] = AsarFileInfo(path='a', kind='file', size=1, offset=0)
        with pytest.raises(AsarError) as e:
            build_header(files)
        assert str(e.value) == 'Path "a" is already used by another entry'

    def test_build_header_empty(self):
        """An empty archive has an empty root."""
        assert encode_header(build_header({})) == b'{"files":{}}'

    def test_build_header_deep_path(self):
        """A deep path is built iteratively and does not hit the recursion limit."""
        path = '/'.join(['d'] * 200 + ['a.txt'])
        files = {path: AsarFileInfo(path=path, kind='file', size=1, offset=0)}
        header = build_header(files)
        validate_header(msgspec.json.decode(encode_header(header)))

    def test_build_header_round_trip(self):
        """Build -> encode -> decode -> read gives back the same table."""
        files = {
            'dir': AsarFileInfo(path='dir', kind='dir'),
            'dir/a.txt': AsarFileInfo(
                path='dir/a.txt', kind='file', size=3, offset=0, integrity=integrity(),
            ),
            'dir/empty': AsarFileInfo(path='dir/empty', kind='dir'),
            'b.txt': AsarFileInfo(path='b.txt', kind='file', size=0, offset=3),
        }
        json_bytes = encode_header(build_header(files))
        header = decode_header(json_bytes)
        assert read_entries(header, 8 + 16 + len(json_bytes) + 3, 16 + len(json_bytes)) == files


class TestPropagateUnpacked:
    def test_propagate_unpacked(self):
        """Every entry below an unpacked directory becomes unpacked."""
        files = {
            'dir': AsarFileInfo(path='dir', kind='dir', unpacked=True),
            'dir/a.txt': AsarFileInfo(path='dir/a.txt', kind='file', size=1, offset=0),
            'dir/sub': AsarFileInfo(path='dir/sub', kind='dir'),
            'dir/sub/b.txt': AsarFileInfo(path='dir/sub/b.txt', kind='file', size=1, offset=1),
            'other.txt': AsarFileInfo(path='other.txt', kind='file', size=1, offset=2),
        }
        propagate_unpacked(files)
        assert [(path, info.unpacked, info.offset) for path, info in files.items()] == [
            ('dir', True, None),
            ('dir/a.txt', True, None),
            ('dir/sub', True, None),
            ('dir/sub/b.txt', True, None),
            ('other.txt', False, 2),
        ]

    def test_propagate_unpacked_nothing_to_do(self):
        """Without an unpacked directory the table is left untouched."""
        files = {
            'dir': AsarFileInfo(path='dir', kind='dir'),
            'dir/a.txt': AsarFileInfo(path='dir/a.txt', kind='file', size=1, offset=0),
        }
        propagate_unpacked(files)
        assert [(path, info.unpacked, info.offset) for path, info in files.items()] == [
            ('dir', False, None), ('dir/a.txt', False, 0),
        ]

    def test_propagate_unpacked_unpacked_file_in_dir(self):
        """An unpacked file keeps its own flag."""
        files = {
            'dir': AsarFileInfo(path='dir', kind='dir', unpacked=True),
            'dir/a.txt': AsarFileInfo(path='dir/a.txt', kind='file', size=1, unpacked=True),
        }
        propagate_unpacked(files)
        assert files['dir/a.txt'].unpacked is True

    @pytest.mark.parametrize('path, unpacked_dirs, expected', [
        ('a/b/c.txt', set(), False),
        ('a/b/c.txt', {'a'}, True),
        ('a/b/c.txt', {'a/b'}, True),
        ('a/b/c.txt', {'a/b/c.txt'}, False),
        ('a/b/c.txt', {'ab'}, False),
        ('a/b/c.txt', {'a/b/c'}, False),
        ('a.txt', {'a'}, False),
        ('a.txt', {'b'}, False),
    ])
    def test_has_parent(self, path, unpacked_dirs, expected):
        """Only real parent directories count as ancestors."""
        assert has_parent(path, unpacked_dirs) is expected
