"""
Tests of the header models: node serialization order, header validation and the
conversion between the header tree and the flat entry table.

The validation rules mirror ``validateHeader()`` of @electron/asar 4.3.0
(``src/disk.ts``), every rejected case below is one of its checks.
"""
import os

import msgspec
import pytest

from alasio.codegen.asar import model as model_module
from alasio.codegen.asar.errors import AsarError, AsarFormatError, AsarPathError
from alasio.codegen.asar.format import BLOCK_SIZE, UINT32_MAX
from alasio.codegen.asar.model import (
    MAX_OFFSET_DIGITS, AsarFileInfo, DirNode, FileNode, Integrity, LinkNode, UnpackedFileNode, build_header,
    canonical_entries, check_node, iter_entries, keys_path, path_keys, read_entries
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


def entry(**kwargs):
    """
    Build a file entry of a table.

    The path of an entry is the key it is stored at, an entry does not carry a
    copy of it.

    Args:
        **kwargs: Fields of the entry

    Returns:
        AsarFileInfo: Entry
    """
    kwargs.setdefault('kind', 'file')
    return AsarFileInfo(**kwargs)


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
        assert msgspec.json.encode(DirNode(files={'a': node})) == (
            b'{"files":{"a":{"size":10,"offset":"0","executable":true,"integrity":'
            b'{"algorithm":"SHA256","hash":"' + HASH_A.encode() + b'","blockSize":4194304,"blocks":["'
            + HASH_B.encode() + b'"]}}}}'
        )

    def test_file_node_without_optional_fields(self):
        """executable and integrity are omitted when unset."""
        assert msgspec.json.encode(DirNode(files={'a': FileNode(size=7, offset='10')})) == (
            b'{"files":{"a":{"size":7,"offset":"10"}}}'
        )

    def test_unpacked_file_node_order(self):
        """An unpacked file node is size, unpacked, integrity, without offset."""
        node = UnpackedFileNode(size=3, unpacked=True, integrity=integrity(HASH_B, []))
        assert msgspec.json.encode(DirNode(files={'a': node})) == (
            b'{"files":{"a":{"size":3,"unpacked":true,"integrity":{"algorithm":"SHA256","hash":"'
            + HASH_B.encode() + b'","blockSize":4194304,"blocks":[]}}}}'
        )

    def test_dir_node_order(self):
        """A directory node is files, or unpacked then files."""
        assert msgspec.json.encode(DirNode(files={})) == b'{"files":{}}'
        assert msgspec.json.encode(DirNode(files={'a': DirNode(files={})})) == b'{"files":{"a":{"files":{}}}}'
        assert msgspec.json.encode(DirNode(files={'a': DirNode(unpacked=True, files={})})) == (
            b'{"files":{"a":{"unpacked":true,"files":{}}}}'
        )

    def test_link_node_order(self):
        """A link node is link, or unpacked then link."""
        assert msgspec.json.encode(DirNode(files={'a': LinkNode(link='b.txt')})) == (
            b'{"files":{"a":{"link":"b.txt"}}}'
        )
        assert msgspec.json.encode(DirNode(files={'a': LinkNode(unpacked=True, link='b.txt')})) == (
            b'{"files":{"a":{"unpacked":true,"link":"b.txt"}}}'
        )

    def test_no_ascii_escape(self):
        """Non ASCII names are stored as UTF-8, not as \\u escapes."""
        assert msgspec.json.encode(DirNode(files={'dir/sub.txt': DirNode(files={})})) == (
            b'{"files":{"dir/sub.txt":{"files":{}}}}'
        )
        assert msgspec.json.encode(DirNode(files={'中文.txt': DirNode(files={})})) == (
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

    @pytest.mark.parametrize('name, expected', [
        ('a:b', 'Invalid entry name at "/": "a:b", Filename should not contain character: ":"'),
        ('CON', 'Invalid entry name at "/": "CON", Filename cannot be reserved system name: CON'),
        ('a.', 'Invalid entry name at "/": "a.", Filename cannot end with a <dot>'),
        ('a ', 'Invalid entry name at "/": "a ", Filename cannot end with a <space>'),
        ('.. ', 'Invalid entry name at "/": ".. ", Filename cannot end with a <space>'),
    ])
    def test_check_name_not_creatable(self, name, expected):
        """A name that no extraction could create is refused while reading."""
        header = {'files': {name: {'files': {}}}}
        with pytest.raises(AsarPathError) as e:
            validate_header(header)
        assert str(e.value) == expected

    def test_check_name_not_creatable_nested(self):
        """The error message names the directory the name belongs to."""
        header = {'files': {'dir': {'files': {'a:b': {'size': 1, 'offset': '0'}}}}}
        with pytest.raises(AsarPathError) as e:
            validate_header(header)
        assert str(e.value) == (
            'Invalid entry name at "dir": "a:b", Filename should not contain character: ":"'
        )

    @pytest.mark.parametrize('name', [
        'a', 'a.txt', '.hidden', '..hidden', 'a-b_c.d', '中文.txt', '__proto__',
        'constructor', 'a\u2028.txt',
    ])
    def test_check_name_valid(self, name):
        """A name that is a valid file name everywhere is accepted."""
        validate_header({'files': {name: {'files': {}}}})


class TestPathKeys:
    """The splitter of an archive path, only a slash separates."""

    @pytest.mark.parametrize('path, expected', [
        ('a.txt', ('a.txt',)),
        ('dir/a.txt', ('dir', 'a.txt')),
        ('dir/deep/a.txt', ('dir', 'deep', 'a.txt')),
    ])
    def test_path_keys(self, path, expected):
        """A path is split on a slash."""
        assert path_keys(path) == expected

    def test_a_backslash_is_not_converted(self):
        """The splitter never converts, only the path of a call is normalized."""
        assert path_keys('dir\\a.txt') == ('dir\\a.txt',)


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
        kind, converted = check_node(node, ('a',))
        assert kind in ('dir', 'file', 'unpacked', 'link')
        assert converted is not None

    def test_converted_node(self):
        """The node is converted to the struct of its kind, its fields are typed."""
        kind, node = check_node({'size': 1, 'offset': '0'}, ('a',))
        assert kind == 'file'
        assert node == FileNode(size=1, offset='0')
        # A wrong type is reported with the field of the JSON it is stored in
        with pytest.raises(AsarFormatError) as e:
            check_node({'size': 1, 'offset': 3}, ('a',))
        assert '$.offset' in str(e.value)

    def test_a_field_of_another_kind_is_ignored(self):
        """A node is checked like the reference does: only its own kind's fields."""
        # The reference takes the branch of the field it finds first and never
        # looks at the fields of the others, so a link with a broken size is a
        # valid link
        kind, _ = check_node({'link': 'a.txt', 'size': 'nonsense'}, ('a',))
        assert kind == 'link'

    def test_the_kind_decides_the_required_fields(self):
        """Every kind requires its own fields, the struct of the kind checks them."""
        # A file needs its size and its offset
        with pytest.raises(AsarFormatError) as e:
            check_node({'offset': '0'}, ('a',))
        assert 'missing required field `size`' in str(e.value)
        # A size alone is not a node
        with pytest.raises(AsarFormatError) as e:
            check_node({'size': 1}, ('a',))
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
        assert [keys_path(keys) for keys, _, _ in iter_entries(header)] == [
            'b.txt', 'a', 'a/c.txt', 'a/d', 'a/d/e.txt', 'f.txt',
        ]
        assert [kind for _, kind, _ in iter_entries(header)] == [
            'file', 'dir', 'file', 'dir', 'file', 'file',
        ]

    def test_iter_entries_unpacked_kind(self):
        """An unpacked file is reported with its own kind."""
        header = {'files': {'a': {'size': 1, 'unpacked': True}, 'b': {'size': 1, 'offset': '0'}}}
        assert [(keys_path(keys), kind) for keys, kind, _ in iter_entries(header)] == [
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
        files = read_entries(header, 30, 20, UNPACKED_PATH)
        assert [keys_path(keys) for keys in files] == [
            'a.txt', 'b', 'b/c.txt', 'd.txt', 'e.txt', 'l.txt',
        ]
        info = files[('a.txt',)]
        assert (info.kind, info.size, info.offset, info.integrity) == ('file', 9, 0, integrity())
        # A directory is an entry of its own, whatever it holds
        assert files[('b',)] == AsarFileInfo(kind='dir')
        assert files[('b', 'c.txt')].offset == 9
        assert files[('d.txt',)].size == 0
        info = files[('e.txt',)]
        assert (info.kind, info.size, info.unpacked, info.executable) == ('file', 3, True, True)
        assert files[('l.txt',)] == AsarFileInfo(kind='link', link='a.txt')

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
        source = files[('packed.txt',)].source
        assert type(source) is RangeSource
        assert (source.offset, source.size) == (23, 4)
        assert files[('packed.txt',)] == AsarFileInfo(
            kind='file', size=4, offset=3, source=RangeSource(23, 4),
        )
        source = files[('sub', 'deep.txt')].source
        assert (source.offset, source.size) == (27, 2)
        # An empty entry holds no byte of the archive
        empty = files[('empty.txt',)].source
        assert (empty.offset, empty.size) == (27, 0)
        # An unpacked file is not stored in the archive, it is a file of the
        # unpacked directory of the archive
        source = files[('gone.txt',)].source
        assert type(source) is LocalFileSource
        assert source.file == os.path.join(UNPACKED_PATH, 'gone.txt')
        assert files[('gone.txt',)] == AsarFileInfo(
            kind='file', size=68, unpacked=True,
            source=LocalFileSource(os.path.join(UNPACKED_PATH, 'gone.txt')),
        )
        assert files[('sub', 'gone.bin')].source.file == os.path.join(UNPACKED_PATH, 'sub', 'gone.bin')
        # A directory and a link hold no content at all
        assert files[('plain',)].source is None
        assert files[('sub',)].source is None
        assert files[('link.txt',)].source is None

    def test_read_entries_directories(self):
        """A directory is an entry of its own, its attributes are part of it."""
        header = {
            'files': {
                'plain': {'files': {'a.txt': {'size': 1, 'offset': '0'}}},
                'hidden': {'files': {}, 'unpacked': True},
            },
        }
        files = read_entries(header, 21, 20, UNPACKED_PATH)
        assert files[('plain',)] == AsarFileInfo(kind='dir')
        # An empty directory is kept, and so is the flag of a directory
        assert files[('hidden',)] == AsarFileInfo(kind='dir', unpacked=True)
        assert files[('plain', 'a.txt')].offset == 0

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
        assert read_entries(header, 30, 20, UNPACKED_PATH)[('a.txt',)].size == 10
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
        assert read_entries(header, 25, 20, UNPACKED_PATH)[('a.txt',)].integrity == value

    def test_drops_unknown_integrity_fields(self):
        """A field the format does not know is dropped, like the node fields around it."""
        header = {'files': {'a.txt': {'size': 5, 'offset': '0', 'integrity': {
            'algorithm': 'SHA256', 'hash': HASH_A, 'blockSize': BLOCK_SIZE, 'blocks': [], 'custom': 1,
        }}}}
        assert read_entries(header, 25, 20, UNPACKED_PATH)[('a.txt',)].integrity == Integrity(
            algorithm='SHA256', hash=HASH_A, blockSize=BLOCK_SIZE, blocks=[],
        )

    def test_too_many_entries(self, monkeypatch):
        """A header that describes more entries than the limit is refused."""
        monkeypatch.setattr(model_module, 'MAX_ENTRY_COUNT', 3)
        header = {'files': {f'f{i}.txt': {'size': 0, 'offset': '0'} for i in range(4)}}
        with pytest.raises(AsarFormatError) as e:
            read_entries(header, 1024, 16, UNPACKED_PATH)
        assert str(e.value) == 'Archive holds more than 3 entries'
        # A header that holds the limit itself is not a problem
        header = {'files': {f'f{i}.txt': {'size': 0, 'offset': '0'} for i in range(3)}}
        assert len(read_entries(header, 1024, 16, UNPACKED_PATH)) == 3


class TestBuildHeader:
    def test_build_header_golden_bytes(self):
        """A table is rebuilt to the exact bytes of the reference."""
        files = {
            ('hello.txt',): entry(size=10, offset=0, integrity=Integrity.new(HASH_A, [HASH_A])),
            ('sub',): AsarFileInfo(kind='dir'),
            ('sub', 'bin.dat'): entry(size=7, offset=10, integrity=Integrity.new(HASH_B, [HASH_B])),
        }
        assert msgspec.json.encode(build_header(canonical_entries(files))) == (
            b'{"files":{"hello.txt":{"size":10,"offset":"0","integrity":{"algorithm":"SHA256","hash":"'
            + HASH_A.encode() + b'","blockSize":4194304,"blocks":["' + HASH_A.encode() + b'"]}},'
            b'"sub":{"files":{"bin.dat":{"size":7,"offset":"10","integrity":{"algorithm":"SHA256","hash":"'
            + HASH_B.encode() + b'","blockSize":4194304,"blocks":["' + HASH_B.encode() + b'"]}}}}}}'
        )

    def test_build_header_directories(self):
        """A directory is a node of its own, its children follow it."""
        files = {
            ('a.txt',): entry(size=1, offset=0),
            ('c.txt',): entry(size=1, offset=2),
            ('dir',): AsarFileInfo(kind='dir'),
            ('dir', 'sub'): AsarFileInfo(kind='dir'),
            ('dir', 'sub', 'b.txt'): entry(size=1, offset=1),
        }
        assert msgspec.json.encode(build_header(canonical_entries(files))) == (
            b'{"files":{"a.txt":{"size":1,"offset":"0"},"c.txt":{"size":1,"offset":"2"},'
            b'"dir":{"files":{"sub":{"files":{"b.txt":{"size":1,"offset":"1"}}}}}}}'
        )

    def test_build_header_empty_directory(self):
        """An empty directory is an entry of its own, it is not lost."""
        files = {('empty',): AsarFileInfo(kind='dir')}
        assert msgspec.json.encode(build_header(canonical_entries(files))) == (
            b'{"files":{"empty":{"files":{}}}}'
        )

    def test_build_header_unpacked_dir(self):
        """A directory and its subtree are written with the unpacked flag."""
        # An entry that lives inside an unpacked directory is marked by
        # ``mark_unpacked()`` before an archive is written, see test_pack.py
        files = {
            ('dir',): AsarFileInfo(kind='dir', unpacked=True),
            ('dir', 'sub'): AsarFileInfo(kind='dir', unpacked=True),
            ('dir', 'sub', 'a.txt'): entry(size=5, unpacked=True, integrity=integrity()),
        }
        assert msgspec.json.encode(build_header(canonical_entries(files))) == (
            b'{"files":{"dir":{"unpacked":true,"files":{"sub":{"unpacked":true,"files":'
            b'{"a.txt":{"size":5,"unpacked":true,"integrity":{"algorithm":"SHA256","hash":"'
            + HASH_A.encode() + b'","blockSize":4194304,"blocks":["' + HASH_B.encode() + b'"]}}}}}}}}'
        )

    def test_build_header_link(self):
        """A link entry becomes a link node."""
        files = {
            ('a.txt',): entry(size=1, offset=0),
            ('l.txt',): AsarFileInfo(kind='link', link='a.txt'),
        }
        assert msgspec.json.encode(build_header(canonical_entries(files))) == (
            b'{"files":{"a.txt":{"size":1,"offset":"0"},"l.txt":{"link":"a.txt"}}}'
        )

    def test_build_header_file_without_offset(self):
        """A file that was never written to an archive can not be stored."""
        files = {('a.txt',): entry(size=1)}
        with pytest.raises(AsarError) as e:
            build_header(canonical_entries(files))
        assert str(e.value) == 'Entry "a.txt" has no offset, it was not written to an archive yet'

    def test_build_header_empty(self):
        """An empty archive has an empty root."""
        assert msgspec.json.encode(build_header(canonical_entries({}))) == b'{"files":{}}'

    def test_build_header_deep_path(self):
        """A deep path is built iteratively and does not hit the recursion limit."""
        keys = path_keys('/'.join(['d'] * 200 + ['a.txt']))
        # Every directory above the file is an entry of the table, as the
        # writers of the module build it
        files = {keys[:depth]: AsarFileInfo(kind='dir') for depth in range(1, len(keys))}
        files[keys] = entry(size=1, offset=0)
        header = build_header(canonical_entries(files))
        validate_header(msgspec.json.decode(msgspec.json.encode(header)))

    def test_build_header_missing_parent(self):
        """A table that misses a directory is refused, not silently nested."""
        files = {('a', 'b', 'c.txt'): entry(size=1, offset=0)}
        with pytest.raises(AsarError) as e:
            build_header(canonical_entries(files))
        assert str(e.value) == (
            'Entry "a/b/c.txt" has no parent entry, its directory is missing from the table'
        )

    def test_build_header_round_trip(self):
        """Build -> encode -> decode -> read gives back the same header bytes."""
        files = {
            ('dir',): AsarFileInfo(kind='dir'),
            ('dir', 'a.txt'): entry(size=3, offset=0, integrity=integrity()),
            ('dir', 'empty'): AsarFileInfo(kind='dir'),
            ('b.txt',): entry(size=0, offset=3),
        }
        json_bytes = msgspec.json.encode(build_header(canonical_entries(files)))
        entries = read_entries(
            msgspec.json.decode(json_bytes), 8 + 16 + len(json_bytes) + 3, 16 + len(json_bytes), UNPACKED_PATH,
        )
        # The entries of the read table are the entries of the built one: the
        # header they build is the header of the archive, byte for byte
        assert [keys_path(keys) for keys in entries] == ['b.txt', 'dir', 'dir/a.txt', 'dir/empty']
        assert entries[('b.txt',)].offset == 3
        assert entries[('dir', 'a.txt')].size == 3
        assert entries[('dir', 'empty')] == AsarFileInfo(kind='dir')
        assert msgspec.json.encode(build_header(canonical_entries(entries))) == json_bytes


class TestCanonicalEntries:
    def test_order(self):
        """A directory comes before its content, the rest is sorted by path."""
        files = {
            ('c.txt',): entry(),
            ('a.txt',): entry(),
            ('a',): AsarFileInfo(kind='dir'),
            ('a', 'b.txt'): entry(),
            ('a', 'z.txt'): entry(),
        }
        assert [keys_path(keys) for keys, _ in canonical_entries(files)] == [
            'a', 'a/b.txt', 'a/z.txt', 'a.txt', 'c.txt',
        ]

    def test_native_modules_last(self):
        """``.node`` files are stored last, as electron-builder does."""
        files = {
            ('a.txt',): entry(),
            ('b',): AsarFileInfo(kind='dir'),
            ('b', 'y.txt'): entry(),
            ('b', 'x.node'): entry(),
            ('e.node',): entry(),
        }
        assert [keys_path(keys) for keys, _ in canonical_entries(files)] == [
            'a.txt', 'b', 'b/y.txt', 'b/x.node', 'e.node',
        ]

    def test_a_directory_named_node_is_not_an_addon(self):
        """A directory named ``*.node`` is an ordinary directory.

        The flag is about files: a directory that carries the name used to
        jump into the addon group, which stored its content -- not an addon by
        name -- before the directory itself and made ``build_header()`` refuse
        every table holding one.
        """
        files = {
            ('plain.txt',): entry(size=5, offset=0),
            ('x.node',): AsarFileInfo(kind='dir'),
            ('x.node', 'child.txt'): entry(size=3, offset=5),
        }
        assert [keys_path(keys) for keys, _ in canonical_entries(files)] == [
            'plain.txt', 'x.node', 'x.node/child.txt',
        ]
        assert msgspec.json.encode(build_header(canonical_entries(files))) == (
            b'{"files":{"plain.txt":{"size":5,"offset":"0"},'
            b'"x.node":{"files":{"child.txt":{"size":3,"offset":"5"}}}}}'
        )

    def test_nested_directories_named_node(self):
        """Nested ``*.node`` directories keep the flat order of their paths."""
        files = {
            ('a.node',): AsarFileInfo(kind='dir'),
            ('a.node', 'b.node'): AsarFileInfo(kind='dir'),
            ('a.node', 'b.node', 'c.txt'): entry(),
            ('z.node',): entry(),
        }
        assert [keys_path(keys) for keys, _ in canonical_entries(files)] == [
            'a.node', 'a.node/b.node', 'a.node/b.node/c.txt', 'z.node',
        ]

    def test_a_node_file_below_a_node_directory_stays_with_it(self):
        """A ``*.node`` file below a ``*.node`` directory is not an addon.

        The subtree of a ``*.node`` directory stays in the normal group: the
        file would otherwise be lifted away from its directory to the end of
        the archive.
        """
        files = {
            ('a.node',): AsarFileInfo(kind='dir'),
            ('a.node', 'b.node'): entry(),
            ('m.txt',): entry(),
        }
        assert [keys_path(keys) for keys, _ in canonical_entries(files)] == [
            'a.node', 'a.node/b.node', 'm.txt',
        ]

    def test_stable(self):
        """Sorting the entries of a table in canonical order keeps that order."""
        files = {path_keys(path): entry() for path in ['b/x.node', 'a.txt', 'c/d.txt', 'e.node']}
        files[('b',)] = AsarFileInfo(kind='dir')
        files[('c',)] = AsarFileInfo(kind='dir')
        # The entries in canonical order, taken from the table itself
        again = {keys: info for keys, info in canonical_entries(files)}
        assert canonical_entries(again) == canonical_entries(files)
