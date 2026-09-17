"""
Tests of the header models: node serialization order, header validation and the
conversion between the header tree and the flat entry table.

The validation rules mirror ``validateHeader()`` of @electron/asar 4.3.0
(``src/disk.ts``), every rejected case below is one of its checks.
"""
import os

import msgspec
import pytest

from alasio.codegen.asar.errors import AsarError, AsarFormatError, AsarPathError
from alasio.codegen.asar.format import BLOCK_SIZE, UINT32_MAX
from alasio.codegen.asar.model import (
    MAX_OFFSET_DIGITS, AsarFileInfo, DirNode, FileNode, Integrity, LinkNode, UnpackedFileNode, build_header,
    canonical_entries, check_node, decode_header, encode_header, ensure_dir, flatten_entries, has_parent,
    has_unpacked_ancestor, iter_entries, mark_unpacked, read_entries, set_leaf
)
from alasio.codegen.asar.source import LocalFileSource, RangeSource

HASH_A = 'a' * 64
HASH_B = 'b' * 64
# Directory the content of an unpacked entry is read from, the table only keeps
# the path of it
UNPACKED_PATH = '/app.asar.unpacked'


def integrity(hash_hex=HASH_A, blocks=None):
    """Build an integrity object, blocks default to the single block of a small file."""
    return Integrity.new(hash_hex, [HASH_B] if blocks is None else blocks)


def validate_header(header):
    """
    Check a decoded header against the reference ``validateHeader`` rules.

    The reader checks every node while it walks the header, so draining the
    generator is the check itself.

    Args:
        header (dict): Header JSON, as decoded by ``msgspec.json.decode()``

    Raises:
        AsarFormatError: If the header is not a valid asar header
    """
    for _ in iter_entries(header):
        pass


def entry(path, **kwargs):
    """
    Build a file entry of a table.

    Args:
        path (str): Archive path of the entry
        **kwargs: Fields to override

    Returns:
        AsarFileInfo: Entry
    """
    kwargs.setdefault('kind', 'file')
    return AsarFileInfo(path=path, **kwargs)


class TestIntegrityNew:
    def test_new(self):
        """The integrity object has the wire field order of the reference."""
        assert Integrity.new(HASH_A, [HASH_B]) == Integrity(
            algorithm='SHA256', hash=HASH_A, blockSize=4194304, blocks=[HASH_B],
        )
        # The field order of the struct is the order of the wire, which is what
        # the header JSON is written in
        assert msgspec.json.encode(Integrity.new(HASH_A, [HASH_B])) == (
            b'{"algorithm":"SHA256","hash":"' + HASH_A.encode() + b'","blockSize":4194304,"blocks":["'
            + HASH_B.encode() + b'"]}'
        )

    def test_block_order(self):
        """Every block hash is kept in order, the input list is not shared."""
        blocks = [HASH_A, HASH_B]
        result = Integrity.new(HASH_A, blocks)
        assert result.blocks == blocks
        assert result.blocks is not blocks


class TestNodeEncoding:
    """
    One struct per kind of node: its fields and their order are the shape the
    reference implementation writes and validates.
    """
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
    """
    The rules a node has to follow, on top of the types of its fields.

    A node is converted to the struct of its kind, so msgspec reports a field
    that has the wrong type or a field the kind requires (the message names the
    JSON path of the field, such as ``$.offset``) and this module reports the
    rules the format adds on top of the types.
    """
    @pytest.mark.parametrize('node, expected', [
        # Not an object
        ([], 'Invalid entry at "a": entry must be an object'),
        ('x', 'Invalid entry at "a": entry must be an object'),
        (None, 'Invalid entry at "a": entry must be an object'),
        # offset, a decimal string of at most MAX_OFFSET_DIGITS digits
        ({'size': 1, 'offset': 0}, '$.offset'),
        ({'size': 1, 'offset': '+0'}, '"offset" must be a decimal string, got "+0"'),
        ({'size': 1, 'offset': '-1'}, '"offset" must be a decimal string, got "-1"'),
        ({'size': 1, 'offset': ' 0'}, '"offset" must be a decimal string, got " 0"'),
        ({'size': 1, 'offset': '0x10'}, '"offset" must be a decimal string, got "0x10"'),
        ({'size': 1, 'offset': '1.0'}, '"offset" must be a decimal string, got "1.0"'),
        ({'size': 1, 'offset': '١٢'}, '"offset" must be a decimal string'),
        ({'size': 1, 'offset': '1_0'}, '"offset" must be a decimal string'),
        ({'size': 1, 'offset': '1' * (MAX_OFFSET_DIGITS + 1)}, '"offset" must be a decimal string'),
        # size, a number the format stores in a uint32
        ({'offset': '0'}, 'missing required field `size`'),
        ({'size': True, 'offset': '0'}, '$.size'),
        ({'size': '1', 'offset': '0'}, '$.size'),
        # A float is not a size, JS has no int/float distinction but no tool
        # writes `1.0` either (JSON.stringify never does, msgspec never does)
        ({'size': 1.0, 'offset': '0'}, '$.size'),
        ({'size': 1.5, 'offset': '0'}, '$.size'),
        ({'size': float('inf'), 'offset': '0'}, '$.size'),
        ({'size': float('nan'), 'offset': '0'}, '$.size'),
        ({'size': -1, 'offset': '0'}, '$.size'),
        ({'size': UINT32_MAX + 1, 'offset': '0'}, '$.size'),
        # Empty node
        ({}, 'Invalid entry at "a": entry must be a directory (with "files"), '
             'a file (with "offset" or "unpacked"), or a link (with "link")'),
        ({'unpacked': True}, 'Invalid entry at "a": entry must be a directory (with "files"), '
                             'a file (with "offset" or "unpacked"), or a link (with "link")'),
        ({'size': 1}, 'Invalid entry at "a": entry must be a directory (with "files"), '
                      'a file (with "offset" or "unpacked"), or a link (with "link")'),
        # link
        ({'link': 1}, '$.link'),
        ({'link': ''}, 'Invalid entry at "a": "link" must not be empty'),
        # files
        ({'files': []}, '$.files'),
        ({'files': None}, '$.files'),
        # booleans
        ({'offset': '0', 'size': 1, 'unpacked': 1}, '$.unpacked'),
        ({'offset': '0', 'size': 1, 'executable': 1}, '$.executable'),
        ({'files': {}, 'unpacked': 'yes'}, '$.unpacked'),
        ({'link': 'x', 'unpacked': 'yes'}, '$.unpacked'),
        # integrity
        ({'offset': '0', 'size': 1, 'integrity': 1}, '$.integrity'),
        ({'offset': '0', 'size': 1, 'integrity': []}, '$.integrity'),
        ({'offset': '0', 'size': 1, 'integrity': {'hash': HASH_A, 'blockSize': 1, 'blocks': []}},
         'missing required field `algorithm`'),
        ({'offset': '0', 'size': 1, 'integrity': {'algorithm': 'SHA256', 'blockSize': 1, 'blocks': []}},
         'missing required field `hash`'),
        ({'offset': '0', 'size': 1, 'integrity': {'algorithm': 'SHA256', 'hash': HASH_A, 'blocks': []}},
         'missing required field `blockSize`'),
        ({'offset': '0', 'size': 1,
          'integrity': {'algorithm': 'SHA256', 'hash': HASH_A, 'blockSize': 0, 'blocks': []}},
         '$.integrity.blockSize'),
        ({'offset': '0', 'size': 1,
          'integrity': {'algorithm': 'SHA256', 'hash': HASH_A, 'blockSize': -1, 'blocks': []}},
         '$.integrity.blockSize'),
        # A block larger than the archive that stores it, and a float, which no
        # tool writes either
        ({'offset': '0', 'size': 1,
          'integrity': {'algorithm': 'SHA256', 'hash': HASH_A, 'blockSize': UINT32_MAX + 1, 'blocks': []}},
         '$.integrity.blockSize'),
        ({'offset': '0', 'size': 1,
          'integrity': {'algorithm': 'SHA256', 'hash': HASH_A, 'blockSize': 4194304.0, 'blocks': []}},
         '$.integrity.blockSize'),
        ({'offset': '0', 'size': 1, 'integrity': {'algorithm': 'SHA256', 'hash': HASH_A, 'blockSize': 1}},
         'missing required field `blocks`'),
        ({'offset': '0', 'size': 1,
          'integrity': {'algorithm': 'SHA256', 'hash': HASH_A, 'blockSize': 1, 'blocks': [1]}},
         '$.integrity.blocks[0]'),
    ])
    def test_check_node_invalid(self, node, expected):
        """Every malformed node is rejected, the message names the rule or the field."""
        with pytest.raises(AsarFormatError) as e:
            validate_header({'files': {'a': node}})
        assert expected in str(e.value)

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
        # An offset of the largest length the format allows
        {'size': 1, 'offset': '1' * MAX_OFFSET_DIGITS},
    ])
    def test_check_node_valid(self, node):
        """Valid nodes pass the check and report their kind."""
        kind, converted = check_node(node, 'a')
        assert kind in ('dir', 'file', 'unpacked', 'link')
        assert converted is not None

    def test_converted_node(self):
        """The node is converted to the struct of its kind, its fields are typed."""
        kind, node = check_node({'size': 1, 'offset': '0'}, 'a')
        assert kind == 'file'
        assert node == FileNode(size=1, offset='0')
        # A wrong type is reported with the field of the JSON it is stored in
        with pytest.raises(AsarFormatError) as e:
            check_node({'size': 1, 'offset': 3}, 'a')
        assert '$.offset' in str(e.value)

    def test_a_field_of_another_kind_is_ignored(self):
        """A node is checked like the reference does: only its own kind's fields."""
        # The reference takes the branch of the field it finds first and never
        # looks at the fields of the others, so a link with a broken size is a
        # valid link
        kind, _ = check_node({'link': 'a.txt', 'size': 'nonsense'}, 'a')
        assert kind == 'link'

    def test_the_kind_decides_the_required_fields(self):
        """Every kind requires its own fields, the struct of the kind checks them."""
        # A file needs its size and its offset
        with pytest.raises(AsarFormatError) as e:
            check_node({'offset': '0'}, 'a')
        assert 'missing required field `size`' in str(e.value)
        # A size alone is not a node
        with pytest.raises(AsarFormatError) as e:
            check_node({'size': 1}, 'a')
        assert 'entry must be a directory' in str(e.value)
        # A directory needs the children it holds
        with pytest.raises(AsarFormatError) as e:
            validate_header({'files': {'a': {'files': None}}})
        assert '$.files' in str(e.value)


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
        """The nested table follows the header order and keeps every field."""
        header = {
            'files': {
                'a.txt': {'size': 9, 'offset': '0', 'integrity': integrity()},
                'b': {'files': {'c.txt': {'size': 1, 'offset': '9'}}},
                'd.txt': {'size': 0, 'offset': '10'},
                'e.txt': {'size': 3, 'unpacked': True, 'executable': True},
                'l.txt': {'link': 'a.txt'},
            },
        }
        files = read_entries(header, 30, 20, UNPACKED_PATH)
        assert list(files) == ['a.txt', 'b', 'd.txt', 'e.txt', 'l.txt']
        info = files['a.txt']
        assert (info.path, info.kind, info.size, info.offset, info.integrity) == (
            'a.txt', 'file', 9, 0, integrity(),
        )
        # A directory is a dict of its children, not an entry of the table
        assert type(files['b']) is dict
        assert list(files['b']) == ['c.txt']
        assert files['b']['c.txt'].offset == 9
        assert files['d.txt'].size == 0
        info = files['e.txt']
        assert (info.path, info.kind, info.size, info.unpacked, info.executable) == (
            'e.txt', 'file', 3, True, True,
        )
        assert files['l.txt'] == AsarFileInfo(path='l.txt', kind='link', link='a.txt')

    def test_read_entries_sources(self):
        """Every file entry knows where its content is read from."""
        header = {
            'files': {
                'packed.txt': {'size': 4, 'offset': '3'},
                'empty.txt': {'size': 0, 'offset': '7'},
                'gone.txt': {'size': 68, 'unpacked': True},
                'sub': {'files': {
                    'deep.txt': {'size': 2, 'offset': '7'},
                    'gone.bin': {'size': 5, 'unpacked': True},
                }},
                'link.txt': {'link': 'packed.txt'},
                'plain': {'files': {}},
            },
        }
        files = read_entries(header, 30, 20, UNPACKED_PATH)
        # A packed file is a byte range of the archive, at the offset of the data
        # area plus the offset the header stores
        source = files['packed.txt'].source
        assert type(source) is RangeSource
        assert (source.offset, source.size) == (23, 4)
        assert files['packed.txt'] == AsarFileInfo(
            path='packed.txt', kind='file', size=4, offset=3, source=RangeSource(23, 4),
        )
        source = files['sub']['deep.txt'].source
        assert (source.offset, source.size) == (27, 2)
        # An empty entry holds no byte of the archive
        assert (files['empty.txt'].source.offset, files['empty.txt'].source.size) == (27, 0)
        # An unpacked file is not stored in the archive, it is a file of the
        # unpacked directory of the archive
        source = files['gone.txt'].source
        assert type(source) is LocalFileSource
        assert source.file == os.path.join(UNPACKED_PATH, 'gone.txt')
        assert files['gone.txt'] == AsarFileInfo(
            path='gone.txt', kind='file', size=68, unpacked=True,
            source=LocalFileSource(os.path.join(UNPACKED_PATH, 'gone.txt')),
        )
        assert files['sub']['gone.bin'].source.file == os.path.join(UNPACKED_PATH, 'sub', 'gone.bin')
        # A directory and a link hold no content at all
        assert files['plain'].get(None) is None
        assert files['link.txt'].source is None
        assert files['sub'].get(None) is None

    def test_read_entries_directory_attributes(self):
        """Only a directory that has attributes of its own carries an entry."""
        header = {
            'files': {
                'plain': {'files': {'a.txt': {'size': 1, 'offset': '0'}}},
                'hidden': {'files': {}, 'unpacked': True},
            },
        }
        files = read_entries(header, 21, 20, UNPACKED_PATH)
        assert None not in files['plain']
        assert files['hidden'][None] == AsarFileInfo(path='hidden', kind='dir', unpacked=True)

    def test_read_entries_out_of_bounds(self):
        """An entry that points outside of the archive is rejected."""
        header = {'files': {'a.txt': {'size': 11, 'offset': '0'}}}
        with pytest.raises(AsarFormatError) as e:
            read_entries(header, 30, 20, UNPACKED_PATH)
        assert str(e.value) == (
            'Invalid entry at "a.txt": content is outside of the archive, '
            'offset 0 + size 11 exceeds the data area of 10 bytes'
        )

    def test_read_entries_out_of_bounds_at_end(self):
        """An entry that ends exactly at the end of the data area is accepted."""
        header = {'files': {'a.txt': {'size': 10, 'offset': '0'}}}
        assert read_entries(header, 30, 20, UNPACKED_PATH)['a.txt'].size == 10
        header = {'files': {'a.txt': {'size': 10, 'offset': '1'}}}
        with pytest.raises(AsarFormatError):
            read_entries(header, 30, 20, UNPACKED_PATH)

    def test_read_entries_reused_offset(self):
        """Several entries may share an offset, 4.3.0 deduplicates contents."""
        header = {
            'files': {
                'a.txt': {'size': 5, 'offset': '0'},
                'b.txt': {'size': 5, 'offset': '0'},
                'c.txt': {'size': 0, 'offset': '0'},
            },
        }
        files = read_entries(header, 25, 20, UNPACKED_PATH)
        assert [(i.size, i.offset) for i in files.values()] == [(5, 0), (5, 0), (0, 0)]

    def test_keeps_integrity(self):
        """The integrity of a file entry is the object the wire stores."""
        value = Integrity(algorithm='SHA256', hash=HASH_A, blockSize=BLOCK_SIZE, blocks=[HASH_B, HASH_A])
        header = {'files': {'a.txt': {'size': 5, 'offset': '0', 'integrity': {
            'algorithm': 'SHA256', 'hash': HASH_A, 'blockSize': BLOCK_SIZE, 'blocks': [HASH_B, HASH_A],
        }}}}
        assert read_entries(header, 25, 20, UNPACKED_PATH)['a.txt'].integrity == value

    def test_drops_unknown_integrity_fields(self):
        """A field the format does not know is dropped, like the node fields around it."""
        header = {'files': {'a.txt': {'size': 5, 'offset': '0', 'integrity': {
            'algorithm': 'SHA256', 'hash': HASH_A, 'blockSize': BLOCK_SIZE, 'blocks': [], 'custom': 1,
        }}}}
        assert read_entries(header, 25, 20, UNPACKED_PATH)['a.txt'].integrity == Integrity(
            algorithm='SHA256', hash=HASH_A, blockSize=BLOCK_SIZE, blocks=[],
        )


class TestBuildHeader:
    def test_build_header_golden_bytes(self):
        """A table is rebuilt to the exact bytes of the reference."""
        files = {
            'hello.txt': entry(
                'hello.txt', size=10, offset=0, integrity=Integrity.new(HASH_A, [HASH_A]),
            ),
            'sub': {
                'bin.dat': entry(
                    'sub/bin.dat', size=7, offset=10, integrity=Integrity.new(HASH_B, [HASH_B]),
                ),
            },
        }
        assert encode_header(build_header(canonical_entries(files))) == (
            b'{"files":{"hello.txt":{"size":10,"offset":"0","integrity":{"algorithm":"SHA256","hash":"'
            + HASH_A.encode() + b'","blockSize":4194304,"blocks":["' + HASH_A.encode() + b'"]}},'
            b'"sub":{"files":{"bin.dat":{"size":7,"offset":"10","integrity":{"algorithm":"SHA256","hash":"'
            + HASH_B.encode() + b'","blockSize":4194304,"blocks":["' + HASH_B.encode() + b'"]}}}}}}'
        )

    def test_build_header_directories(self):
        """A directory is a node of its own, its children follow it."""
        files = {}
        set_leaf(files, 'a.txt', entry('a.txt', size=1, offset=0))
        set_leaf(files, 'dir/sub/b.txt', entry('dir/sub/b.txt', size=1, offset=1))
        set_leaf(files, 'c.txt', entry('c.txt', size=1, offset=2))
        assert encode_header(build_header(canonical_entries(files))) == (
            b'{"files":{"a.txt":{"size":1,"offset":"0"},"c.txt":{"size":1,"offset":"2"},'
            b'"dir":{"files":{"sub":{"files":{"b.txt":{"size":1,"offset":"1"}}}}}}}'
        )

    def test_build_header_unpacked_dir(self):
        """An unpacked directory marks itself and its subtree."""
        files = {}
        set_leaf(files, 'dir/sub/a.txt', entry(
            'dir/sub/a.txt', size=5, unpacked=True, integrity=integrity(),
        ))
        ensure_dir(files, 'dir')[None] = AsarFileInfo(path='dir', kind='dir', unpacked=True)
        mark_unpacked(files)
        assert encode_header(build_header(canonical_entries(files))) == (
            b'{"files":{"dir":{"unpacked":true,"files":{"sub":{"unpacked":true,"files":'
            b'{"a.txt":{"size":5,"unpacked":true,"integrity":{"algorithm":"SHA256","hash":"'
            + HASH_A.encode() + b'","blockSize":4194304,"blocks":["' + HASH_B.encode() + b'"]}}}}}}}}'
        )

    def test_build_header_link(self):
        """A link entry becomes a link node."""
        files = {}
        set_leaf(files, 'a.txt', entry('a.txt', size=1, offset=0))
        set_leaf(files, 'l.txt', AsarFileInfo(path='l.txt', kind='link', link='a.txt'))
        assert encode_header(build_header(canonical_entries(files))) == (
            b'{"files":{"a.txt":{"size":1,"offset":"0"},"l.txt":{"link":"a.txt"}}}'
        )

    def test_build_header_file_without_offset(self):
        """A file that was never written to an archive can not be stored."""
        files = {'a.txt': entry('a.txt', size=1)}
        with pytest.raises(AsarError) as e:
            build_header(canonical_entries(files))
        assert str(e.value) == 'Entry "a.txt" has no offset, it was not written to an archive yet'

    def test_build_header_empty(self):
        """An empty archive has an empty root."""
        assert encode_header(build_header(canonical_entries({}))) == b'{"files":{}}'

    def test_build_header_deep_path(self):
        """A deep path is built iteratively and does not hit the recursion limit."""
        path = '/'.join(['d'] * 200 + ['a.txt'])
        files = {}
        set_leaf(files, path, entry(path, size=1, offset=0))
        header = build_header(canonical_entries(files))
        validate_header(msgspec.json.decode(encode_header(header)))

    def test_build_header_round_trip(self):
        """Build -> encode -> decode -> read gives back the same header bytes."""
        files = {}
        set_leaf(files, 'dir/a.txt', entry('dir/a.txt', size=3, offset=0, integrity=integrity()))
        set_leaf(files, 'b.txt', entry('b.txt', size=0, offset=3))
        ensure_dir(files, 'dir/empty')
        json_bytes = encode_header(build_header(canonical_entries(files)))
        entries = read_entries(
            decode_header(json_bytes), 8 + 16 + len(json_bytes) + 3, 16 + len(json_bytes), UNPACKED_PATH,
        )
        # The entries of the read table are the entries of the built one: the
        # header they build is the header of the archive, byte for byte
        assert list(entries) == ['b.txt', 'dir']
        assert entries['b.txt'].offset == 3
        assert entries['dir']['a.txt'].size == 3
        assert type(entries['dir']['empty']) is dict
        assert encode_header(build_header(canonical_entries(entries))) == json_bytes


class TestFlattenEntries:
    def test_every_entry(self):
        """Every entry is reported, the directories included."""
        files = {
            'a.txt': entry('a.txt'),
            'dir': {'b.txt': entry('dir/b.txt'), 'sub': {}},
        }
        assert sorted('/'.join(keys) for keys, _ in flatten_entries(files)) == [
            'a.txt', 'dir', 'dir/b.txt', 'dir/sub',
        ]

    def test_empty_directory(self):
        """An empty directory is an entry of its own, a leaves only walk loses it."""
        assert list(flatten_entries({'empty': {}})) == [(['empty'], {})]

    def test_directory_attributes_are_not_an_entry(self):
        """The attributes of a directory itself are not an entry."""
        files = {'dir': {'a.txt': entry('dir/a.txt'), None: AsarFileInfo(
            path='dir', kind='dir', unpacked=True,
        )}}
        assert [keys for keys, _ in flatten_entries(files)] == [['dir'], ['dir', 'a.txt']]


class TestCanonicalEntries:
    def test_order(self):
        """A directory comes before its content, the rest is sorted by path."""
        files = {}
        for path in ['c.txt', 'a/b.txt', 'a/z.txt', 'a.txt']:
            set_leaf(files, path, entry(path))
        assert ['/'.join(keys) for keys, _ in canonical_entries(files)] == [
            'a', 'a/b.txt', 'a/z.txt', 'a.txt', 'c.txt',
        ]

    def test_native_modules_last(self):
        """``.node`` files are stored last, as electron-builder does."""
        files = {}
        for path in ['b/x.node', 'a.txt', 'e.node', 'b/y.txt']:
            set_leaf(files, path, entry(path))
        assert ['/'.join(keys) for keys, _ in canonical_entries(files)] == [
            'a.txt', 'b', 'b/y.txt', 'b/x.node', 'e.node',
        ]

    def test_stable(self):
        """A table built in the canonical order sorts to the same order."""
        files = {}
        for path in ['b/x.node', 'a.txt', 'c/d.txt', 'e.node']:
            set_leaf(files, path, entry(path))
        # The files in canonical order, the directories are created on the way
        order = [
            '/'.join(keys) for keys, node in canonical_entries(files) if type(node) is not dict
        ]
        again = {}
        for path in order:
            set_leaf(again, path, entry(path))
        assert canonical_entries(again) == canonical_entries(files)


class TestEnsureDir:
    def test_root(self):
        """The root of the table is the root directory of the archive."""
        files = {}
        assert ensure_dir(files, '') is files

    def test_creates_missing_levels(self):
        """Every missing level of the path is created."""
        files = {}
        children = ensure_dir(files, 'a/b/c')
        assert files == {'a': {'b': {'c': {}}}}
        assert children is files['a']['b']['c']

    def test_existing_is_kept(self):
        """A directory that already exists is returned as it is."""
        files = {'a': {'b.txt': entry('a/b.txt')}}
        children = ensure_dir(files, 'a')
        assert children is files['a']

    @pytest.mark.parametrize('path, files, expected', [
        ('a/b', {'a': AsarFileInfo(path='a', kind='file')}, 'Archive path "a" is already a file'),
        ('a.txt/b', {'a.txt': AsarFileInfo(path='a.txt', kind='file')},
         'Archive path "a.txt" is already a file'),
        ('a/b/c', {'a': AsarFileInfo(path='a', kind='file')}, 'Archive path "a" is already a file'),
    ])
    def test_parent_is_a_file(self, path, files, expected):
        """A file can not be walked through, and the table is left as it was."""
        with pytest.raises(AsarPathError) as e:
            ensure_dir(files, path)
        assert str(e.value) == expected
        assert list(files) == [path.split('/')[0]]


class TestSetLeaf:
    def test_creates_parents(self):
        """The parents of a leaf are created, the leaf is stored."""
        files = {}
        info = entry('a/b.txt')
        set_leaf(files, 'a/b.txt', info)
        assert files == {'a': {'b.txt': info}}

    def test_replaces_and_keeps_the_position(self):
        """An existing entry is replaced without moving it."""
        files = {}
        set_leaf(files, 'a.txt', entry('a.txt', size=1))
        set_leaf(files, 'b.txt', entry('b.txt', size=1))
        set_leaf(files, 'a.txt', entry('a.txt', size=2))
        assert list(files) == ['a.txt', 'b.txt']
        assert files['a.txt'].size == 2

    def test_directory_conflict(self):
        """A file can not take the path of a directory."""
        files = {'a': {}}
        with pytest.raises(AsarPathError) as e:
            set_leaf(files, 'a', entry('a'))
        assert str(e.value) == 'Archive path "a" is already a directory'

    def test_parent_is_a_file(self):
        """The parents of a leaf are checked the same way."""
        files = {'a': AsarFileInfo(path='a', kind='file')}
        with pytest.raises(AsarPathError) as e:
            set_leaf(files, 'a/b.txt', entry('a/b.txt'))
        assert str(e.value) == 'Archive path "a" is already a file'


class TestHasUnpackedAncestor:
    @pytest.mark.parametrize('path, unpacked_dirs, expected', [
        ('a/b/c.txt', [], False),
        ('a/b/c.txt', ['a'], True),
        ('a/b/c.txt', ['a/b'], True),
        ('a/b/c.txt', ['ab'], False),
        ('a/b/c.txt', ['a/b/c'], False),
        ('a.txt', ['a'], False),
        ('a.txt', ['b'], False),
    ])
    def test_has_unpacked_ancestor(self, path, unpacked_dirs, expected):
        """Only real directories above the entry count as ancestors."""
        files = {}
        set_leaf(files, path, entry(path))
        for directory in unpacked_dirs:
            ensure_dir(files, directory)[None] = AsarFileInfo(path=directory, kind='dir', unpacked=True)
        assert has_unpacked_ancestor(files, path.split('/')) is expected

    def test_unpacked_entry_is_not_an_ancestor(self):
        """The flag of the entry itself does not count, only a directory above it."""
        files = {'a': {'b.txt': entry('a/b.txt', unpacked=True)}}
        assert has_unpacked_ancestor(files, ['a', 'b.txt']) is False


class TestMarkUnpacked:
    def test_mark_subtree(self):
        """Every entry below an unpacked directory becomes unpacked."""
        files = {}
        set_leaf(files, 'dir/a.txt', entry('dir/a.txt', size=1, offset=0))
        set_leaf(files, 'dir/sub/b.txt', entry('dir/sub/b.txt', size=1, offset=1))
        set_leaf(files, 'other.txt', entry('other.txt', size=1, offset=2))
        ensure_dir(files, 'dir')[None] = AsarFileInfo(path='dir', kind='dir', unpacked=True)
        mark_unpacked(files)
        assert files['dir'][None].unpacked is True
        assert files['dir']['a.txt'].unpacked is True
        assert files['dir']['a.txt'].offset is None
        # A directory inside an unpacked subtree carries the flag as well
        assert files['dir']['sub'][None].unpacked is True
        assert files['dir']['sub']['b.txt'].unpacked is True
        assert files['dir']['sub']['b.txt'].offset is None
        assert files['other.txt'].unpacked is False
        assert files['other.txt'].offset == 2

    def test_nothing_to_do(self):
        """Without an unpacked directory the table is left untouched."""
        files = {}
        set_leaf(files, 'dir/a.txt', entry('dir/a.txt', size=1, offset=0))
        mark_unpacked(files)
        assert files['dir']['a.txt'].unpacked is False
        assert files['dir']['a.txt'].offset == 0
        assert None not in files['dir']

    def test_unpacked_file_keeps_its_flag(self):
        """An unpacked file that is already marked is not read again."""
        files = {}
        set_leaf(files, 'dir/a.txt', entry('dir/a.txt', size=1, unpacked=True))
        ensure_dir(files, 'dir')[None] = AsarFileInfo(path='dir', kind='dir', unpacked=True)
        mark_unpacked(files)
        assert files['dir']['a.txt'].unpacked is True


class TestHasParent:
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
