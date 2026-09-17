"""
Data models of the asar header, and the converters between the header tree and
the flat entry table.

Writing uses one ``msgspec.Struct`` per node type, so that the field order of
the emitted JSON is fixed by the class definition and always matches the
reference implementation::

    directory       : unpacked?, files
    file            : size, offset, executable?, integrity?
    unpacked file   : size, unpacked, executable?, integrity?
    link            : unpacked?, link

msgspec refuses an untagged union of multiple structs, so the reader and the
validation work on the plain objects produced by ``msgspec.json.decode()``
instead, which is exactly the shape the JS implementation validates. Reading is
lenient where the format allows it (unknown fields, missing integrity) and
strict where safety depends on it (entry names, sizes, offsets).
"""
import math
import re
from typing import Any, Dict, Optional, Union

import msgspec
from msgspec import UNSET, UnsetType

from .errors import AsarError, AsarFormatError
from .format import BLOCK_SIZE, MAX_PATH_DEPTH, UINT32_MAX

# Entry kinds, the header only stores 'dir' as a node with 'files' and 'file'
# as a node with 'size' and either 'offset' or 'unpacked'
KIND_FILE = 'file'
KIND_DIR = 'dir'
KIND_LINK = 'link'

# `offset` is a decimal string on the wire, the regex is ASCII only so that
# unicode digits (which JSON.parse plus /^\d+$/ would reject as well) fail here
REGEX_OFFSET = re.compile(r'^\d+$', re.ASCII)
# A larger offset can not be valid, it also bounds the cost of int() conversion
MAX_OFFSET_DIGITS = 20
ALGORITHM = 'SHA256'


class AsarFileInfo(msgspec.Struct):
    """
    One entry of an archive: a file, a directory or a link.

    Attributes:
        path (str): Path inside the archive, POSIX separators, no leading '/'
        kind (str): 'file', 'dir' or 'link'
        size (int): Content byte length, None for directories
        offset (int): Offset of the content in the data area, None for
            directories and unpacked files, and for local files that have not
            been written to an archive yet
        integrity (dict): {'algorithm', 'hash', 'blockSize', 'blocks'}, None
            when the archive was packed without integrity
        unpacked (bool): True when the content lives in '<archive>.unpacked/'
            instead of the archive body
        executable (bool): POSIX executable bit
        link (str): Link target inside the archive, for kind='link'
    """
    path: str
    kind: str
    size: "Optional[int]" = None
    offset: "Optional[int]" = None
    integrity: "Optional[Dict[str, Any]]" = None
    unpacked: bool = False
    executable: bool = False
    link: "Optional[str]" = None


class DirNode(msgspec.Struct):
    """
    Directory node, ``{'unpacked': true, 'files': {...}}``.
    """
    unpacked: "Union[bool, UnsetType]" = UNSET
    files: "Dict[str, Any]" = UNSET


class FileNode(msgspec.Struct):
    """
    File node, ``{'size': 1, 'offset': '0', 'integrity': {...}}``.
    """
    size: int
    offset: str
    executable: "Union[bool, UnsetType]" = UNSET
    integrity: "Union[Dict[str, Any], UnsetType]" = UNSET


class UnpackedFileNode(msgspec.Struct):
    """
    Unpacked file node, ``{'size': 1, 'unpacked': true, 'integrity': {...}}``.

    The content is not stored in the archive body, it is copied next to the
    archive as ``<archive>.unpacked/<path>``.
    """
    size: int
    unpacked: bool
    executable: "Union[bool, UnsetType]" = UNSET
    integrity: "Union[Dict[str, Any], UnsetType]" = UNSET


class LinkNode(msgspec.Struct, kw_only=True):
    """
    Symbolic link node, ``{'link': 'dir1/file1.txt'}``.

    Keyword only, because msgspec requires every required field to come after
    the optional ones, while the wire order is ``unpacked`` then ``link``.
    """
    unpacked: "Union[bool, UnsetType]" = UNSET
    link: str


def new_integrity(file_hash, block_hashes):
    """
    Build an integrity object in the wire field order.

    Args:
        file_hash (str): SHA256 of the whole file, hex digest
        block_hashes (list): SHA256 of every block, hex digests

    Returns:
        dict: {'algorithm', 'hash', 'blockSize', 'blocks'}
    """
    return {
        'algorithm': ALGORITHM,
        'hash': file_hash,
        'blockSize': BLOCK_SIZE,
        'blocks': list(block_hashes),
    }


def check_name(name, path):
    """
    Check a single path segment of the header tree.

    Args:
        name (str): Segment name
        path (str): Parent path, only used to build the error message

    Raises:
        AsarFormatError: If the name contains a path separator or is a directory
            pointer, the header stores one segment per level
    """
    if '/' in name or '\\' in name:
        raise AsarFormatError(
            f'Invalid entry name at "{path}", a name must not contain a path separator: "{name}"'
        )
    if name == '.' or name == '..':
        raise AsarFormatError(f'Invalid entry name at "{path}": "{name}"')


def _check_bool(node, key, path):
    """
    Check an optional boolean field.
    """
    if key in node and not isinstance(node[key], bool):
        raise AsarFormatError(f'Invalid entry at "{path}": "{key}" must be a boolean')


def _check_size(node, path):
    """
    Check the 'size' field of a file node and return it as an int.
    """
    size = node.get('size')
    if isinstance(size, bool) or not isinstance(size, (int, float)):
        raise AsarFormatError(f'Invalid entry at "{path}": "size" must be a number')
    if not math.isfinite(size) or size < 0:
        raise AsarFormatError(f'Invalid entry at "{path}": "size" must be a non-negative number')
    if int(size) != size:
        raise AsarFormatError(f'Invalid entry at "{path}": "size" must be an integer')
    if size > UINT32_MAX:
        raise AsarFormatError(
            f'Invalid entry at "{path}": "size" must not exceed {UINT32_MAX} bytes'
        )
    return int(size)


def _check_offset(node, path):
    """
    Check the 'offset' field of a file node and return it as an int.
    """
    offset = node['offset']
    if not isinstance(offset, str):
        raise AsarFormatError(f'Invalid entry at "{path}": "offset" must be a string')
    if len(offset) > MAX_OFFSET_DIGITS or not REGEX_OFFSET.match(offset):
        raise AsarFormatError(
            f'Invalid entry at "{path}": "offset" must be a decimal string, got "{offset}"'
        )
    return int(offset)


def _check_integrity(node, path):
    """
    Check the 'integrity' field of a file node, see the reference validateIntegrity.
    """
    if 'integrity' not in node:
        return
    integrity = node['integrity']
    if not isinstance(integrity, dict):
        raise AsarFormatError(f'Invalid entry at "{path}": "integrity" must be an object')
    if not isinstance(integrity.get('algorithm'), str):
        raise AsarFormatError(f'Invalid entry at "{path}": "integrity.algorithm" must be a string')
    if not isinstance(integrity.get('hash'), str):
        raise AsarFormatError(f'Invalid entry at "{path}": "integrity.hash" must be a string')
    block_size = integrity.get('blockSize')
    if isinstance(block_size, bool) or not isinstance(block_size, (int, float)):
        raise AsarFormatError(
            f'Invalid entry at "{path}": "integrity.blockSize" must be a positive number'
        )
    if not math.isfinite(block_size) or block_size <= 0:
        raise AsarFormatError(
            f'Invalid entry at "{path}": "integrity.blockSize" must be a positive number'
        )
    blocks = integrity.get('blocks')
    if not isinstance(blocks, list):
        raise AsarFormatError(f'Invalid entry at "{path}": "integrity.blocks" must be an array')
    for index, block in enumerate(blocks):
        if not isinstance(block, str):
            raise AsarFormatError(
                f'Invalid entry at "{path}": "integrity.blocks[{index}]" must be a string'
            )


def check_node(node, path):
    """
    Validate one header node and classify it, following the reference
    ``validateHeader`` of @electron/asar.

    Args:
        node (dict): Decoded header node
        path (str): Path of the node, only used to build error messages

    Returns:
        str: 'dir', 'file' for a normal file, 'unpacked' for an unpacked file,
            or 'link'

    Raises:
        AsarFormatError: If the node is not a valid asar node
    """
    if not isinstance(node, dict):
        raise AsarFormatError(f'Invalid entry at "{path}": entry must be an object')
    if 'link' in node:
        link = node['link']
        if not isinstance(link, str):
            raise AsarFormatError(f'Invalid entry at "{path}": "link" must be a string')
        if not link:
            raise AsarFormatError(f'Invalid entry at "{path}": "link" must not be empty')
        _check_bool(node, 'unpacked', path)
        return 'link'
    if 'files' in node:
        if not isinstance(node['files'], dict):
            raise AsarFormatError(f'Invalid entry at "{path}": "files" must be a plain object')
        _check_bool(node, 'unpacked', path)
        return 'dir'
    if 'offset' in node:
        _check_offset(node, path)
        _check_size(node, path)
        _check_bool(node, 'unpacked', path)
        _check_bool(node, 'executable', path)
        _check_integrity(node, path)
        return 'file'
    if node.get('unpacked') is True and 'size' in node:
        _check_size(node, path)
        _check_bool(node, 'executable', path)
        _check_integrity(node, path)
        return 'unpacked'
    raise AsarFormatError(
        f'Invalid entry at "{path}": entry must be a directory (with "files"), '
        f'a file (with "offset" or "unpacked"), or a link (with "link")'
    )


def iter_entries(header):
    """
    Walk a decoded header, validate every entry and yield them in header order.

    The walk is iterative on purpose: an archive is untrusted input, and a
    recursive walk would break the interpreter stack on a deep tree long before
    the depth limit is reached.

    Args:
        header (dict): Header JSON, as decoded by ``msgspec.json.decode()``

    Yields:
        (str, str, dict): ``(path, kind, node)`` triples, parents before their
            children, in the order the entries are stored in the header

    Raises:
        AsarFormatError: If the header is not a valid asar header
    """
    if not isinstance(header, dict):
        raise AsarFormatError('Header must be a JSON object')
    if 'files' not in header:
        raise AsarFormatError('Header must be a directory with a "files" property')
    root = header['files']
    if not isinstance(root, dict):
        raise AsarFormatError('Header "files" must be a plain object')
    # The stack pops the last item first, children are pushed reversed to keep
    # the stored order
    stack = [('', name, node, 1) for name, node in reversed(list(root.items()))]
    while stack:
        parent, name, node, depth = stack.pop()
        check_name(name, parent if parent else '/')
        path = f'{parent}/{name}' if parent else name
        kind = check_node(node, path)
        yield path, kind, node
        if kind != 'dir':
            continue
        if depth >= MAX_PATH_DEPTH:
            raise AsarFormatError(f'Path "{path}" is deeper than {MAX_PATH_DEPTH} segments')
        for child_name, child in reversed(list(node['files'].items())):
            stack.append((path, child_name, child, depth + 1))


def validate_header(header):
    """
    Check a decoded header against the reference ``validateHeader`` rules.

    Args:
        header (dict): Header JSON, as decoded by ``msgspec.json.decode()``

    Raises:
        AsarFormatError: If the header is not a valid asar header
    """
    # The generator validates while walking
    for _ in iter_entries(header):
        pass


def read_entries(header, archive_size, data_offset):
    """
    Build the flat entry table of a decoded header.

    Entry order is the header order, which is also the order in which the
    contents are stored, so re-packing an untouched archive reproduces the same
    offsets.

    Args:
        header (dict): Header JSON, as decoded by ``msgspec.json.decode()``
        archive_size (int): Total byte length of the archive
        data_offset (int): Offset of the data area, see ``calc_data_offset()``

    Returns:
        dict: ``{path: AsarFileInfo}``

    Raises:
        AsarFormatError: If the header is invalid or an entry points outside of
            the archive
    """
    files = {}
    data_size = archive_size - data_offset
    for path, kind, node in iter_entries(header):
        if kind == 'file':
            size = _check_size(node, path)
            offset = _check_offset(node, path)
            if offset + size > data_size:
                raise AsarFormatError(
                    f'Invalid entry at "{path}": content is outside of the archive, '
                    f'offset {offset} + size {size} exceeds the data area of {data_size} bytes'
                )
            files[path] = AsarFileInfo(
                path=path,
                kind=KIND_FILE,
                size=size,
                offset=offset,
                integrity=node.get('integrity'),
                executable=node.get('executable', False),
            )
        elif kind == 'unpacked':
            files[path] = AsarFileInfo(
                path=path,
                kind=KIND_FILE,
                size=_check_size(node, path),
                integrity=node.get('integrity'),
                unpacked=True,
                executable=node.get('executable', False),
            )
        elif kind == 'dir':
            files[path] = AsarFileInfo(
                path=path,
                kind=KIND_DIR,
                unpacked=node.get('unpacked') is True,
            )
        else:
            files[path] = AsarFileInfo(
                path=path,
                kind=KIND_LINK,
                unpacked=node.get('unpacked') is True,
                link=node['link'],
            )
    return files


def has_parent(path, parents):
    """
    Check whether any parent directory of a path is in a set of directories.

    Args:
        path (str): Archive path, POSIX separators
        parents (set): Archive paths of the directories to look for

    Returns:
        bool: True if a parent of the path is in the set
    """
    if not parents:
        return False
    start = 0
    while True:
        separator = path.find('/', start)
        if separator == -1:
            return False
        if path[:separator] in parents:
            return True
        start = separator + 1


def propagate_unpacked(files):
    """
    Mark every entry that lives inside an unpacked directory as unpacked.

    The reference implementation marks the directories matched by ``unpackDir``
    and every file below them, so a file added later under such a directory must
    inherit the flag as well. Unpacked files are copied next to the archive, so
    they have no offset.

    Args:
        files (dict): ``{path: AsarFileInfo}``, modified in place
    """
    unpacked_dirs = {
        path for path, info in files.items() if info.kind == KIND_DIR and info.unpacked
    }
    if not unpacked_dirs:
        return
    for path, info in files.items():
        if info.unpacked:
            continue
        if not has_parent(path, unpacked_dirs):
            continue
        info.unpacked = True
        if info.kind == KIND_FILE:
            info.offset = None


def _build_file_node(info, path):
    """
    Build a file or link node of the header tree.

    Args:
        info (AsarFileInfo): Entry to convert
        path (str): Archive path, only used to build error messages

    Returns:
        FileNode | UnpackedFileNode | LinkNode: Node of the header tree

    Raises:
        AsarError: If a file entry has no offset allocated
    """
    if info.kind == KIND_LINK:
        return LinkNode(
            unpacked=True if info.unpacked else UNSET,
            link=info.link,
        )
    integrity = info.integrity if info.integrity is not None else UNSET
    executable = True if info.executable else UNSET
    if info.unpacked:
        return UnpackedFileNode(
            size=info.size,
            unpacked=True,
            executable=executable,
            integrity=integrity,
        )
    if info.offset is None:
        raise AsarError(f'Entry "{path}" has no offset, it was not written to an archive yet')
    return FileNode(
        size=info.size,
        offset=str(info.offset),
        executable=executable,
        integrity=integrity,
    )


def build_header(files):
    """
    Build the header tree of an archive from the flat entry table.

    Entry order decides the node order of every directory, and the offset
    allocation order follows it, so the flat table is the single source of
    truth of the layout. ``propagate_unpacked()`` must have been called before,
    directories marked as unpacked put their whole subtree next to the archive.

    Args:
        files (dict): ``{path: AsarFileInfo}``, insertion order is the archive
            order, see ``AsarArchive.files``

    Returns:
        DirNode: Root node of the header tree

    Raises:
        AsarError: If a path is used by both a file and a directory, or if a
            file entry has no offset and is not unpacked
    """
    tree = {}
    for path, info in files.items():
        parts = path.split('/')
        children = tree
        for part in parts[:-1]:
            child = children.get(part)
            if child is None:
                child = children[part] = {}
            elif not isinstance(child, dict):
                raise AsarError(f'Path "{path}" is used by both a file and a directory')
            children = child
        name = parts[-1]
        child = children.get(name)
        if info.kind == KIND_DIR:
            # A directory is a container, its own fields are looked up from the
            # flat table by path
            if child is None:
                children[name] = {}
            elif not isinstance(child, dict):
                raise AsarError(f'Path "{path}" is used by both a file and a directory')
            continue
        if child is not None:
            raise AsarError(f'Path "{path}" is already used by another entry')
        children[name] = info
    # Collect the directories, a parent is always collected before its children,
    # so building the nodes in reverse order builds every child first
    order = []
    stack = [('', tree)]
    while stack:
        path, children = stack.pop()
        order.append((path, children))
        prefix = f'{path}/' if path else ''
        for name, child in children.items():
            if isinstance(child, dict):
                stack.append((prefix + name, child))
    built = {}
    for path, children in reversed(order):
        nodes = {}
        prefix = f'{path}/' if path else ''
        for name, child in children.items():
            if isinstance(child, dict):
                nodes[name] = built[id(child)]
            else:
                nodes[name] = _build_file_node(child, prefix + name)
        info = files.get(path)
        unpacked = True if info is not None and info.unpacked else UNSET
        built[id(children)] = DirNode(unpacked=unpacked, files=nodes)
    return built[id(tree)]


def encode_header(root):
    """
    Encode a header tree to the JSON bytes stored in the archive.

    msgspec.json emits the same bytes as ``JSON.stringify``
    (``json.dumps(separators=(',', ':'), ensure_ascii=False)`` as well), and the
    field order of every node comes from the struct definition.

    Args:
        root (DirNode): Root node, see ``build_header()``

    Returns:
        bytes: Header JSON, UTF-8 encoded
    """
    return msgspec.json.encode(root)


def decode_header(data):
    """
    Decode the header JSON of an archive.

    Args:
        data (bytes): Header JSON, UTF-8 encoded

    Returns:
        dict: Decoded header

    Raises:
        AsarFormatError: If the JSON is malformed, or nested too deeply
    """
    try:
        return msgspec.json.decode(data)
    except msgspec.DecodeError as e:
        raise AsarFormatError(f'Header is not valid JSON: {e}')
    except RecursionError:
        # msgspec raises RecursionError instead of DecodeError on deep nesting,
        # see MAX_PATH_DEPTH
        raise AsarFormatError(f'Header is nested too deeply, over {MAX_PATH_DEPTH} segments')
