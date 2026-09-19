"""
Data models of the asar header, and the converters between the header tree and
the flat entry table of an archive.

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

The entry table (``AsarArchive.files``) is flat: a key is the tuple of the path
segments of an entry and a value is an ``AsarFileInfo``. A directory is an entry
like any other, so an empty directory and the ``unpacked`` flag of a directory
have a place of their own and nothing has to be stored beside the entries.
``read_entries()`` builds that table from a decoded header and gives every file
entry the source its content is read from, so nothing has to walk the table
again to find it.

The segments are what the format is made of (every level of the header tree is a
name, a name can not contain a separator), so the segment tuple is the identity
of an entry and ``path_keys()`` / ``keys_path()`` are the only two places that
turn a path into it and back. The entry points of the module split a path once
and hand the segments to the helpers; a helper never receives a path and never
rebuilds one except to build a message, so a path of an operation is not split,
joined or walked twice.

An entry is not a node and can not be one: the header stores the offset of a
file as a decimal string while the table needs the number, an entry carries what
only lives in memory (its ``kind`` and the ``source`` of its content) and msgspec
would write those fields to the header, and an entry that was added to the
archive is not written yet (it has no size and no offset at all).
``build_node()`` and ``check_node()`` are the two directions of that mapping.
"""
import os
from typing import Any, Dict, List, Literal, Optional, Union

import msgspec
from msgspec import UNSET, UnsetType
from typing_extensions import Annotated

from alasio.ext.path.validate import validate_filename, validate_filepath

from .errors import AsarEntryNotFoundError, AsarError, AsarFormatError, AsarPathError
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

# Maximum number of links followed when resolving an entry, matches the
# SYMLOOP_MAX of the reference implementation
SYMLINK_MAX_DEPTH = 40


def path_keys(path):
    """
    Split an archive path into its segments, the key of an entry of the table.

    The only place of the module that splits a path: an entry point calls it
    once and every helper of the table takes the segments.

    Args:
        path (str): Archive path, POSIX separators, no leading '/'

    Returns:
        tuple: Path segments
    """
    return tuple(path.split('/'))


def keys_path(keys):
    """
    Join the segments of an entry back to an archive path.

    For a message, or for the entry points that hand a path to a caller. It is
    never used to look an entry up again: the table is keyed by the segments.

    Args:
        keys (tuple): Path segments

    Returns:
        str: Archive path
    """
    return '/'.join(keys)


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
    an entry carries what only lives in memory (its ``kind`` and the ``source``
    of its content) and msgspec would write those fields to the header, and an
    entry that was added to the archive is not written yet (it has no size and
    no offset at all). ``build_node()`` and ``check_node()`` are the two
    directions of that mapping.

    The path of an entry is its key in the table of the archive, an entry does
    not carry a copy of it: ``keys_path()`` joins the segments where a path is
    needed, in a message or in the entry points of the module.

    Attributes:
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
    kind: Literal['file', 'dir', 'link']
    size: Optional[int] = None
    offset: Optional[int] = None
    integrity: Optional[Integrity] = None
    unpacked: bool = False
    executable: bool = False
    link: Optional[str] = None
    source: Optional[ContentSource] = None

    def build_node(self, keys):
        """
        Build the node of this file or link entry for the header tree.

        Args:
            keys (tuple): Path segments of the entry, only used to build error
                messages

        Returns:
            FileNode | UnpackedFileNode | LinkNode: Node of the header tree

        Raises:
            AsarError: If the entry has no offset allocated
        """
        if self.kind == KIND_LINK:
            return LinkNode(
                unpacked=True if self.unpacked else UNSET,
                link=self.link,
            )
        integrity = self.integrity if self.integrity is not None else UNSET
        executable = True if self.executable else UNSET
        if self.unpacked:
            return UnpackedFileNode(
                size=self.size,
                unpacked=True,
                executable=executable,
                integrity=integrity,
            )
        if self.offset is None:
            raise AsarError(
                f'Entry "{keys_path(keys)}" has no offset, it was not written to an archive yet'
            )
        return FileNode(
            size=self.size,
            offset=str(self.offset),
            executable=executable,
            integrity=integrity,
        )


def check_name(name, keys):
    """
    Check a single path segment of the header tree.

    The name is checked once, when the entry enters the table: an entry the
    extraction can not create on every supported platform is refused here, so
    nothing below has to look at the path of an entry again.

    Args:
        name (str): Segment name
        keys (tuple): Path segments of the entry, only used to build the error
            message, which names the directory the name belongs to

    Raises:
        AsarFormatError: If the name contains a path separator or is a directory
            pointer, the header stores one segment per level
        AsarPathError: If the name is not a valid file name on every supported
            platform
    """
    if '/' in name or '\\' in name:
        raise AsarFormatError(
            f'Invalid entry name at "{keys_path(keys[:-1]) or "/"}", '
            f'a name must not contain a path separator: "{name}"'
        )
    if name == '.' or name == '..':
        raise AsarFormatError(f'Invalid entry name at "{keys_path(keys[:-1]) or "/"}": "{name}"')
    try:
        validate_filename(name)
    except ValueError as e:
        raise AsarPathError(
            f'Invalid entry name at "{keys_path(keys[:-1]) or "/"}": "{name}", {e}'
        )


def _convert_node(node, type_, keys):
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
        keys (tuple): Path segments of the node, only used to build error messages

    Returns:
        Struct: The node

    Raises:
        AsarFormatError: If a field of the node has the wrong type, or if a
            required one is missing
    """
    try:
        return msgspec.convert(node, type_)
    except msgspec.ValidationError as e:
        raise AsarFormatError(f'Invalid entry at "{keys_path(keys)}": {e}')


def check_node(node, keys):
    """
    Check one header node and classify it, following the reference
    ``validateHeader`` of @electron/asar.

    A node is converted to the struct of its kind, so the types of the fields
    and the fields the kind requires are checked by msgspec, and only the fields
    of that kind are looked at.

    Args:
        node (dict): Decoded header node
        keys (tuple): Path segments of the node, only used to build error messages

    Returns:
        tuple[str, Struct]: The kind of the node ('dir', 'file', 'unpacked' or
            'link'), and the node as the struct of that kind

    Raises:
        AsarFormatError: If the node is not a valid asar node
    """
    if not isinstance(node, dict):
        raise AsarFormatError(f'Invalid entry at "{keys_path(keys)}": entry must be an object')
    if 'link' in node:
        link = _convert_node(node, LinkNode, keys)
        if not link.link:
            raise AsarFormatError(f'Invalid entry at "{keys_path(keys)}": "link" must not be empty')
        return 'link', link
    if 'files' in node:
        return 'dir', _convert_node(node, DirNode, keys)
    if 'offset' in node:
        file = _convert_node(node, FileNode, keys)
        offset = file.offset
        # `str.isdecimal()` is true for the digits of every script, the
        # reference only knows the ASCII ones (`JSON.parse` goes through
        # `/^\d+$/`), and nothing longer than MAX_OFFSET_DIGITS can be a valid
        # offset
        if len(offset) > MAX_OFFSET_DIGITS or not (offset.isascii() and offset.isdecimal()):
            raise AsarFormatError(
                f'Invalid entry at "{keys_path(keys)}": "offset" must be a decimal string, got "{offset}"'
            )
        return 'file', file
    if node.get('unpacked') is True and 'size' in node:
        return 'unpacked', _convert_node(node, UnpackedFileNode, keys)
    raise AsarFormatError(
        f'Invalid entry at "{keys_path(keys)}": entry must be a directory (with "files"), '
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
        tuple[tuple[str, ...], str, Struct]: The path segments of an entry, its
            kind and the node as the struct of that kind, parents before their
            children, in the order the entries are stored in the header. The
            segments are the key the entry has in the entry table, see
            ``keys_path()`` and ``check_node()``.

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
    # the stored order. The key of a child is the key of its parent plus its
    # name, so a path is never joined to be split again, and the segments of a
    # directory are shared by all of its children
    stack = [((name,), name, node, 1) for name, node in reversed(list(root.items()))]
    while stack:
        keys, name, node, depth = stack.pop()
        check_name(name, keys)
        kind, node = check_node(node, keys)
        yield keys, kind, node
        if kind != 'dir':
            continue
        if depth >= MAX_PATH_DEPTH:
            raise AsarFormatError(f'Path "{keys_path(keys)}" is deeper than {MAX_PATH_DEPTH} segments')
        for child_name, child in reversed(list(node.files.items())):
            stack.append((keys + (child_name,), child_name, child, depth + 1))


def read_entries(header, archive_size, data_offset, unpacked_path):
    """
    Build the flat entry table of a decoded header.

    A key of the table is the tuple of the path segments of an entry and a value
    is an ``AsarFileInfo``; a directory is an entry like any other, so an empty
    directory is kept and the ``unpacked`` flag of a directory has a place of
    its own. The order of the entries is the order of the header, and every file
    entry holds the source its content is read from: the byte range of the
    archive for a packed file, the file of the unpacked directory for an
    unpacked one.

    Args:
        header (dict): Header JSON, as decoded by ``msgspec.json.decode()``
        archive_size (int): Total byte length of the archive
        data_offset (int): Offset of the data area, the second value of
            ``format.read_header()``
        unpacked_path (str): Directory the unpacked content of the archive lives
            in, ``<archive>.unpacked``

    Returns:
        dict[tuple[str, ...], AsarFileInfo]: The entries, in the order of the
            header

    Raises:
        AsarFormatError: If the header is invalid or an entry points outside of
            the archive
        AsarPathError: If an entry name or the target of a link is not a valid
            path
        AsarEntryNotFoundError: If a link points at an entry that is not there
    """
    files = {}
    data_size = archive_size - data_offset
    for keys, kind, node in iter_entries(header):
        if kind == 'dir':
            files[keys] = AsarFileInfo(kind=KIND_DIR, unpacked=node.unpacked is True)
            continue
        if kind == 'file':
            offset = int(node.offset)
            if offset + node.size > data_size:
                raise AsarFormatError(
                    f'Invalid entry at "{keys_path(keys)}": content is outside of the archive, '
                    f'offset {offset} + size {node.size} exceeds the data area of {data_size} bytes'
                )
            files[keys] = AsarFileInfo(
                kind=KIND_FILE,
                size=node.size,
                offset=offset,
                integrity=None if node.integrity is UNSET else node.integrity,
                executable=node.executable is True,
                source=RangeSource(data_offset + offset, node.size),
            )
        elif kind == 'unpacked':
            files[keys] = AsarFileInfo(
                kind=KIND_FILE,
                size=node.size,
                integrity=None if node.integrity is UNSET else node.integrity,
                unpacked=True,
                executable=node.executable is True,
                source=LocalFileSource(os.path.join(unpacked_path, *keys)),
            )
        else:
            files[keys] = AsarFileInfo(
                kind=KIND_LINK,
                unpacked=node.unpacked is True,
                link=node.link,
            )
    # The paths and the names are checked while the table is built, the links
    # are checked once it holds every entry they can point at
    check_links(files)
    return files


def check_links(files):
    """
    Check that every link of a table points at an entry of the same archive.

    A link is the only entry that names another one, so it is the only one that
    can be broken. It is checked once, when the table is built, so that nothing
    below (extraction, reading an entry) has to look at a link target again.

    Args:
        files (dict): Flat entry table, see ``read_entries()``

    Raises:
        AsarPathError: If the target of a link is not a valid archive path
        AsarEntryNotFoundError: If a link points at an entry that is not there
        AsarFormatError: If the links are circular or too deep
    """
    for keys, info in files.items():
        if info.kind != KIND_LINK:
            continue
        path = keys_path(keys)
        try:
            validate_filepath(info.link)
        except ValueError as e:
            raise AsarPathError(f'Invalid link target of "{path}": {e}')
        resolve_entry(files, path)


def resolve_entry(files, name):
    """
    Look up an entry, following the link entries of the path.

    A link may be the entry itself or an intermediate directory of the path,
    the reference implementation resolves both (a link to `A` makes
    `Current/real.txt` point at `A/real.txt`).

    Args:
        files (dict): Flat entry table, see ``read_entries()``
        name (str): Archive path

    Returns:
        tuple[str, AsarFileInfo]: Path and entry it points to

    Raises:
        AsarEntryNotFoundError: If the entry or a link target is missing
        AsarFormatError: If the links are circular or too deep
    """
    keys = path_keys(name)
    visited = set()
    while True:
        replaced = False
        # An intermediate segment may be a link to another directory
        for depth in range(1, len(keys)):
            info = files.get(keys[:depth])
            if info is None or info.kind != KIND_LINK:
                continue
            # The target of the link is followed and the rest of the path is
            # appended to it, the segments of the path stay segments
            keys = path_keys(_follow(keys_path(keys[:depth]), info, visited)) + keys[depth:]
            replaced = True
            break
        if replaced:
            continue
        info = files.get(keys)
        if info is None:
            raise AsarEntryNotFoundError(
                f'Entry "{keys_path(keys)}" does not exist in the archive'
            )
        if info.kind != KIND_LINK:
            return keys_path(keys), info
        keys = path_keys(_follow(keys_path(keys), info, visited))


def _follow(name, info, visited):
    """
    Check a link and get its target.

    Args:
        name (str): Archive path of the link
        info (AsarFileInfo): Entry of the link
        visited (set): Links already followed

    Returns:
        str: Target path of the link, relative to the archive root

    Raises:
        AsarFormatError: If the links are circular or too deep
    """
    if name in visited:
        raise AsarFormatError(f'Circular link at "{name}"')
    if len(visited) >= SYMLINK_MAX_DEPTH:
        raise AsarFormatError(f'Too many levels of links at "{name}"')
    visited.add(name)
    return info.link


def canonical_entries(files):
    """
    Sort the entry table into the canonical archive order.

    The order is the one electron-builder uses (``orderFileSet()``): the
    ``.node`` files come last, everything else is sorted by path. Only a file
    can be one: a directory named ``x.node`` is an ordinary directory and its
    content stays with it (the reference orders a set of files, where the
    directories are implicit). The comparison is done on the path segments
    instead of the path string, so it does not depend on the separator of the
    platform and can not be disturbed by a directory whose name is the prefix of
    a file name at the same level.

    Segment order already puts a parent before its content and keeps a subtree
    together, so the offsets, the data area and the header of an archive that is
    written share this single order: any sequence of operations gives the same
    bytes.

    Args:
        files (dict): Flat entry table

    Returns:
        list[tuple[tuple[str, ...], AsarFileInfo]]: The entries, in the order an
            archive stores them
    """
    entries = list(files.items())
    entries.sort(key=_canonical_key)
    return entries


def _canonical_key(entry):
    """
    Sort key of the canonical order.

    The ``.node`` flag is about the files the reference orders: a directory is
    never an addon (a flagged directory sorts after the content stored below
    it, which the header refuses), and neither is a file below a directory
    named ``*.node`` (its subtree stays in the normal group instead of being
    lifted away from its directory).

    Args:
        entry (tuple): ``(keys, info)`` of the flat entry table

    Returns:
        tuple: ``(is_a_native_module, tuple of the path segments)``
    """
    keys, info = entry
    is_node = (
        info.kind == KIND_FILE
        and keys[-1].endswith('.node')
        and not any(segment.endswith('.node') for segment in keys[:-1])
    )
    return is_node, keys


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
        children = built.get(keys[:-1])
        if children is None:
            # Only a hand made table can miss a directory: the writers of the
            # module always create the entries above an entry (``read_entries()``
            # walks the header, ``AsarArchive._ensure_parents()`` creates what is
            # missing)
            raise AsarError(
                f'Entry "{keys_path(keys)}" has no parent entry, '
                f'its directory is missing from the table'
            )
        if entry.kind == KIND_DIR:
            node = DirNode(
                unpacked=True if entry.unpacked else UNSET,
                files={},
            )
            built[keys] = node
        else:
            node = entry.build_node(keys)
        children.files[keys[-1]] = node
    return root
