"""
Data models of the asar header, and the converters between the header tree and
the nested entry table of an archive.

Writing uses one ``msgspec.Struct`` per kind of node, so that the field set and
the field order of every kind are fixed by a class definition and always match
the reference implementation::

    directory       : unpacked?, files
    file            : size, offset, executable?, integrity?
    unpacked file   : size, unpacked, executable?, integrity?
    link            : unpacked?, link

The same structs are the schema of the reader: a node is converted to the struct
of its kind, so msgspec checks the types and the fields the kind requires (a
directory has ``files``, a file has a ``size`` and an ``offset``, an unpacked
file has the flag) and only the fields of that kind are looked at, which is what
the reference does (it takes the branch of the field it finds first and never
looks at the fields of the others).

The kind of a node is decided by ``check_node()``, which also enforces the rules
the format adds on top of the types (the decimal offset, a link with a target).
msgspec refuses a union of several untagged structs, so the four kinds cannot be
dispatched on by a single conversion.

The entry table (``AsarArchive.files``) mirrors that tree: a directory is a
dict of its children, the root table is the root directory, and a file or a
link is an ``AsarFileInfo``. The attributes a directory may have of its own are
stored under the ``None`` key, so a plain directory is just ``{name: child}``
and only an unpacked directory carries an extra entry. ``read_entries()`` builds
that table from a decoded header and gives every file entry the source its
content is read from, so nothing has to walk the table again to find it.

An entry is not a node and can not be one: the header stores the offset of a
file as a decimal string while the table needs the number, an entry carries what
only lives in memory (its ``path``, its ``kind`` and the ``source`` of its
content) and msgspec would write those fields to the header, and an entry that
was added to the archive is not written yet (it has no size and no offset at
all). ``_build_file_node()`` and ``check_node()`` are the two directions of that
mapping.
"""
import os
from typing import Any, Dict, List, Literal, Optional, Union

import msgspec
from msgspec import UNSET, UnsetType
from typing_extensions import Annotated

from .errors import AsarError, AsarFormatError, AsarPathError
from .format import BLOCK_SIZE, MAX_PATH_DEPTH, UINT32_MAX
from .source import ContentSource, LocalFileSource, RangeSource

# Entry kinds, the header only stores 'dir' as a node with 'files' and 'file'
# as a node with 'size' and either 'offset' or 'unpacked'
KIND_FILE = 'file'
KIND_DIR = 'dir'
KIND_LINK = 'link'

# `offset` is a decimal string on the wire, the check of it uses str.isascii()
# and str.isdecimal() rather than a regex: it is the same rule (JSON.parse plus
# /^\d+$/ rejects unicode digits as well) and about three times faster
# A larger offset can not be valid, it also bounds the cost of int() conversion
MAX_OFFSET_DIGITS = 20
ALGORITHM = 'SHA256'


class Integrity(msgspec.Struct):
    """
    Integrity object of a file node,
    ``{'algorithm': 'SHA256', 'hash': ..., 'blockSize': 4194304, 'blocks': [...]}``.

    The field order of the struct is the order of the wire.
    """
    algorithm: str
    hash: str
    # The reference refuses a file larger than a uint32 (filesystem.js, "file
    # size can not be larger than 4.2GB"), so a block can not be larger either
    blockSize: Annotated[int, msgspec.Meta(gt=0, le=UINT32_MAX)]
    blocks: List[str]

    @classmethod
    def new(cls, file_hash, block_hashes):
        """
        Build the integrity of a content, in the wire field order.

        Args:
            file_hash (str): SHA256 of the whole file, hex digest
            block_hashes (list): SHA256 of every block, hex digests

        Returns:
            Integrity: Algorithm, hash, block size and block hashes
        """
        return cls(
            algorithm=ALGORITHM,
            hash=file_hash,
            blockSize=BLOCK_SIZE,
            blocks=list(block_hashes),
        )


class DirNode(msgspec.Struct, kw_only=True):
    """
    Directory node, ``{'unpacked': true, 'files': {...}}``.

    Keyword only, because msgspec requires every required field to come after
    the optional ones, while the wire order is ``unpacked`` then ``files``.
    """
    unpacked: Union[bool, UnsetType] = UNSET
    files: Dict[str, Any]


class FileNode(msgspec.Struct, kw_only=True):
    """
    File node, ``{'size': 1, 'offset': '0', 'integrity': {...}}``.

    The size of a file can not be larger than the archive that stores it, which
    a uint32 holds many times over, and the offset is the decimal string of the
    wire (the entry table stores it as an int).
    """
    size: Annotated[int, msgspec.Meta(ge=0, le=UINT32_MAX)]
    offset: str
    executable: Union[bool, UnsetType] = UNSET
    integrity: Union[Integrity, UnsetType] = UNSET
    # A packed file may carry the flag, the reference checks it as well, the
    # writer never writes it (a packed file has an offset instead)
    unpacked: Union[bool, UnsetType] = UNSET


class UnpackedFileNode(msgspec.Struct, kw_only=True):
    """
    Unpacked file node, ``{'size': 1, 'unpacked': true, 'integrity': {...}}``.

    The content is not stored in the archive body, it is copied next to the
    archive as ``<archive>.unpacked/<path>``.
    """
    size: Annotated[int, msgspec.Meta(ge=0, le=UINT32_MAX)]
    unpacked: bool
    executable: Union[bool, UnsetType] = UNSET
    integrity: Union[Integrity, UnsetType] = UNSET


class LinkNode(msgspec.Struct, kw_only=True):
    """
    Symbolic link node, ``{'link': 'dir1/file1.txt'}``.

    Keyword only, because msgspec requires every required field to come after
    the optional ones, while the wire order is ``unpacked`` then ``link``.
    """
    unpacked: Union[bool, UnsetType] = UNSET
    link: str


class AsarFileInfo(msgspec.Struct):
    """
    One entry of an archive: a file, a directory or a link.

    An entry is not a node of the header and can not be one: the header stores
    the offset of a file as a decimal string while the table needs the number,
    an entry carries what only lives in memory (its ``path``, its ``kind`` and
    the ``source`` of its content) and msgspec would write those fields to the
    header, and an entry that was added to the archive is not written yet (it
    has no size and no offset at all). ``_build_file_node()`` and
    ``check_node()`` are the two directions of that mapping.

    Attributes:
        path (str): Path inside the archive, POSIX separators, no leading '/'
        kind (str): 'file', 'dir' or 'link', see the type of the field
        size (int): Content byte length, None for directories
        offset (int): Offset of the content in the data area, None for
            directories and unpacked files, and for local files that have not
            been written to an archive yet
        integrity (Integrity): Algorithm, hash, block size and block hashes of
            the content, None when the archive was packed without integrity
        unpacked (bool): True when the content lives in '<archive>.unpacked/'
            instead of the archive body
        executable (bool): POSIX executable bit
        link (str): Link target inside the archive, for kind='link'
        source (ContentSource): Where the content of a file comes from, it is
            only known in memory and never stored in the header. A directory
            and a link hold no content, an entry that was added holds what was
            handed over, and an entry that was read from an archive holds the
            range of the archive or the file of its unpacked directory
    """
    path: str
    kind: Literal['file', 'dir', 'link']
    size: Optional[int] = None
    offset: Optional[int] = None
    integrity: Optional[Integrity] = None
    unpacked: bool = False
    executable: bool = False
    link: Optional[str] = None
    source: Optional[ContentSource] = None


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


def _convert_node(node, type_, path):
    """
    Convert a decoded node to the struct of its kind.

    The structs of this module are the schema of the header: msgspec checks the
    type of every field they declare and the fields they require, which is what
    the reference ``validateHeader`` of @electron/asar checks as well. Only the
    fields of the kind of node are looked at, a field of another kind (a size on
    a link, for instance) is ignored exactly like the reference ignores it.

    Args:
        node (dict): Decoded header node
        type_ (type): Struct of the kind of node
        path (str): Path of the node, only used to build error messages

    Returns:
        Struct: The node

    Raises:
        AsarFormatError: If a field of the node has the wrong type, or if a
            required one is missing
    """
    try:
        return msgspec.convert(node, type_)
    except msgspec.ValidationError as e:
        raise AsarFormatError(f'Invalid entry at "{path}": {e}')


def check_node(node, path):
    """
    Check one header node and classify it, following the reference
    ``validateHeader`` of @electron/asar.

    A node is converted to the struct of its kind, so the types of the fields
    and the fields the kind requires are checked by msgspec, and only the fields
    of that kind are looked at.

    Args:
        node (dict): Decoded header node
        path (str): Path of the node, only used to build error messages

    Returns:
        (str, Struct): The kind of the node ('dir', 'file', 'unpacked' or
            'link'), and the node as the struct of that kind

    Raises:
        AsarFormatError: If the node is not a valid asar node
    """
    if not isinstance(node, dict):
        raise AsarFormatError(f'Invalid entry at "{path}": entry must be an object')
    if 'link' in node:
        link = _convert_node(node, LinkNode, path)
        if not link.link:
            raise AsarFormatError(f'Invalid entry at "{path}": "link" must not be empty')
        return 'link', link
    if 'files' in node:
        return 'dir', _convert_node(node, DirNode, path)
    if 'offset' in node:
        file = _convert_node(node, FileNode, path)
        offset = file.offset
        # `str.isdecimal()` is true for the digits of every script, the
        # reference only knows the ASCII ones (`JSON.parse` goes through
        # `/^\d+$/`), and nothing longer than MAX_OFFSET_DIGITS can be a valid
        # offset
        if len(offset) > MAX_OFFSET_DIGITS or not (offset.isascii() and offset.isdecimal()):
            raise AsarFormatError(
                f'Invalid entry at "{path}": "offset" must be a decimal string, got "{offset}"'
            )
        return 'file', file
    if node.get('unpacked') is True and 'size' in node:
        return 'unpacked', _convert_node(node, UnpackedFileNode, path)
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
        (str, str, Struct): ``(path, kind, node)`` triples, parents before their
            children, in the order the entries are stored in the header. The
            node is the struct of its kind, see ``check_node()``.

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
        kind, node = check_node(node, path)
        yield path, kind, node
        if kind != 'dir':
            continue
        if depth >= MAX_PATH_DEPTH:
            raise AsarFormatError(f'Path "{path}" is deeper than {MAX_PATH_DEPTH} segments')
        for child_name, child in reversed(list(node.files.items())):
            stack.append((path, child_name, child, depth + 1))


def read_entries(header, archive_size, data_offset, unpacked_path):
    """
    Build the nested entry table of a decoded header.

    The table mirrors the header tree, the order of the entries is the order of
    the header, and every file entry holds the source its content is read from:
    the byte range of the archive for a packed file, the file of the unpacked
    directory for an unpacked one.

    Args:
        header (dict): Header JSON, as decoded by ``msgspec.json.decode()``
        archive_size (int): Total byte length of the archive
        data_offset (int): Offset of the data area, the second value of
            ``format.read_header()``
        unpacked_path (str): Directory the unpacked content of the archive lives
            in, ``<archive>.unpacked``

    Returns:
        dict: ``{name: dict | AsarFileInfo}``, the root directory

    Raises:
        AsarFormatError: If the header is invalid or an entry points outside of
            the archive
    """
    files = {}
    # The table itself is the root directory, every directory is registered
    # while it is walked so a child always finds its parent
    directories = {'': files}
    data_size = archive_size - data_offset
    for path, kind, node in iter_entries(header):
        parent, _, name = path.rpartition('/')
        children = directories[parent]
        if kind == 'dir':
            child = {}
            children[name] = child
            directories[path] = child
            if node.unpacked is True:
                child[None] = AsarFileInfo(path=path, kind=KIND_DIR, unpacked=True)
            continue
        if kind == 'file':
            offset = int(node.offset)
            if offset + node.size > data_size:
                raise AsarFormatError(
                    f'Invalid entry at "{path}": content is outside of the archive, '
                    f'offset {offset} + size {node.size} exceeds the data area of {data_size} bytes'
                )
            children[name] = AsarFileInfo(
                path=path,
                kind=KIND_FILE,
                size=node.size,
                offset=offset,
                integrity=None if node.integrity is UNSET else node.integrity,
                executable=node.executable is True,
                source=RangeSource(data_offset + offset, node.size),
            )
        elif kind == 'unpacked':
            children[name] = AsarFileInfo(
                path=path,
                kind=KIND_FILE,
                size=node.size,
                integrity=None if node.integrity is UNSET else node.integrity,
                unpacked=True,
                executable=node.executable is True,
                source=LocalFileSource(os.path.join(unpacked_path, *path.split('/'))),
            )
        else:
            children[name] = AsarFileInfo(
                path=path,
                kind=KIND_LINK,
                unpacked=node.unpacked is True,
                link=node.link,
            )
    return files


def flatten_entries(files):
    """
    Flatten the nested entry table to a list of entries.

    The walk is iterative, so a deep tree is fine, and every entry is reported
    including the directories (a directory is a dict and would be skipped by a
    walk that only reports the leaves, which would lose an empty directory).

    Args:
        files (dict): Nested entry table

    Returns:
        list: ``[(keys, entry)]``, ``keys`` is the list of path segments, in no
            particular order
    """
    entries = []
    stack = [([], files)]
    while stack:
        keys, children = stack.pop()
        for name, child in children.items():
            if name is None:
                # The attributes of the directory itself, not an entry
                continue
            child_keys = keys + [name]
            entries.append((child_keys, child))
            if type(child) is dict:
                stack.append((child_keys, child))
    return entries


def canonical_entries(files):
    """
    Flatten the nested entry table and sort it into the canonical archive order.

    The order is the one electron-builder uses (``orderFileSet()``): the
    ``.node`` files come last, everything else is sorted by path. The comparison
    is done on the path segments instead of the path string, so it does not
    depend on the separator of the platform and can not be disturbed by a
    directory whose name is the prefix of a file name at the same level.

    Segment order already puts a parent before its content and keeps a subtree
    together, so the offsets, the data area and the header of an archive that is
    written share this single order: any sequence of operations gives the same
    bytes.

    Args:
        files (dict): Nested entry table

    Returns:
        list: ``[(keys, entry)]``, in the order an archive stores them
    """
    entries = flatten_entries(files)
    entries.sort(key=_canonical_key)
    return entries


def _canonical_key(entry):
    """
    Sort key of the canonical order.

    Args:
        entry (tuple): ``(keys, entry)``

    Returns:
        tuple: ``(is_a_native_module, tuple of the path segments)``
    """
    keys = entry[0]
    return keys[-1].endswith('.node'), tuple(keys)


def ensure_dir(files, path):
    """
    Get a directory of the nested table, creating it and its parents.

    Args:
        files (dict): Nested entry table, modified in place
        path (str): Archive path of the directory, an empty path is the root

    Returns:
        dict: The children of the directory, ``files`` itself for the root

    Raises:
        AsarPathError: If the path or one of its parents is used by a file
    """
    if not path:
        return files
    children = files
    end = 0
    length = len(path)
    while end < length:
        separator = path.find('/', end)
        if separator == -1:
            separator = length
        name = path[end:separator]
        child = children.get(name)
        if child is None:
            child = {}
            children[name] = child
        elif type(child) is not dict:
            raise AsarPathError(f'Archive path "{path[:separator]}" is already a file')
        children = child
        end = separator + 1
    return children


def set_leaf(files, path, info):
    """
    Put a file or a link entry into the nested table, creating its parents.

    An entry that is already at the path is replaced, an entry that is a
    directory is refused: a file and a directory can not share a path.

    Args:
        files (dict): Nested entry table, modified in place
        path (str): Archive path of the entry
        info (AsarFileInfo): Entry to store

    Raises:
        AsarPathError: If the path or one of its parents is a directory
    """
    parent, _, name = path.rpartition('/')
    children = ensure_dir(files, parent)
    if type(children.get(name)) is dict:
        raise AsarPathError(f'Archive path "{path}" is already a directory')
    children[name] = info


def has_unpacked_ancestor(files, keys):
    """
    Check whether an entry lives inside a directory that is stored unpacked.

    Args:
        files (dict): Nested entry table
        keys (list[str]): Path segments of the entry, the entry itself is not
            looked at

    Returns:
        bool: True if a directory above the entry is unpacked
    """
    children = files
    for name in keys[:-1]:
        child = children.get(name)
        if type(child) is not dict:
            return False
        info = child.get(None)
        if info is not None and info.unpacked:
            return True
        children = child
    return False


def mark_unpacked(files):
    """
    Mark every entry that lives inside an unpacked directory as unpacked.

    The reference implementation marks the directories matched by ``unpackDir``
    and every entry below them, so an entry that was added later under such a
    directory must inherit the flag as well. Unpacked files are copied next to
    the archive instead of being stored in it, so they have no offset.

    Args:
        files (dict): Nested entry table, modified in place
    """
    stack = [('', files, False)]
    while stack:
        path, children, unpacked = stack.pop()
        info = children.get(None)
        if info is not None and info.unpacked:
            unpacked = True
        elif unpacked and path:
            # A directory inside an unpacked subtree carries the flag as well,
            # its own attributes are stored under the None key
            if info is None:
                info = AsarFileInfo(path=path, kind=KIND_DIR)
                children[None] = info
            info.unpacked = True
        for name, child in children.items():
            if name is None:
                continue
            child_path = f'{path}/{name}' if path else name
            if type(child) is dict:
                stack.append((child_path, child, unpacked))
                continue
            if not unpacked or child.unpacked:
                continue
            child.unpacked = True
            if child.kind == KIND_FILE:
                child.offset = None


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


def _build_file_node(info, path):
    """
    Build the header node of one file or link entry.

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


def build_header(entries):
    """
    Build the header tree of an archive from its entries.

    The entries have to come from ``canonical_entries()``: a parent is built
    before its content, and the node order of every directory is the order in
    which the entries are stored, which is also the order of the data area.

    Args:
        entries (list): ``[(keys, entry)]`` in canonical order

    Returns:
        DirNode: Root node of the header tree

    Raises:
        AsarError: If a file entry has no offset and is not unpacked
    """
    root = DirNode(files={})
    # ``keys`` of a directory, the root is the empty tuple, so a child always
    # finds the node of its parent
    built = {(): root}
    for keys, entry in entries:
        children = built[tuple(keys[:-1])]
        if type(entry) is dict:
            info = entry.get(None)
            node = DirNode(
                unpacked=True if info is not None and info.unpacked else UNSET,
                files={},
            )
            built[tuple(keys)] = node
        else:
            node = _build_file_node(entry, entry.path)
        children.files[keys[-1]] = node
    return root


def encode_header(root):
    """
    Encode a header tree to the JSON bytes stored in the archive.

    msgspec.json emits the same bytes as ``JSON.stringify``
    (``json.dumps(separators=(',', ':'), ensure_ascii=False)`` as well), and the
    field order of every node comes from the struct of its kind.

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
