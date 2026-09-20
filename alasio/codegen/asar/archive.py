"""
Reading, building, changing and extracting asar archives.

``AsarArchive`` is the single entry point of the module. It holds the entry
table of an archive, the source of the content of every entry, and — when the
archive was read from or written to a file — the open handle of that file::

    # Open an archive that exists
    with AsarArchive('app.asar') as asar:
        asar.extract_all('output')

    # Build one from scratch: the source tree mirrors the archive
    with AsarArchive() as asar:
        # dist/main.js is stored as dist/main.js
        asar.add_folder('build/app')
        asar.add_file(data=b'{"name":"alasio"}', arc_path='build.json')
        asar.write('app.asar')

An archive that was opened can be changed and written back, the content of an
entry that was not touched is read from the archive it came from::

    with AsarArchive('app.asar') as asar:
        asar.add_file('new-main.js', 'dist/main.js')
        asar.del_file('dist/main.js.map')
        asar.write()                       # replaces app.asar, atomically

The handle is opened on demand and released by ``close()``, which the ``with``
block calls, so an archive is never left open by accident.
"""
import os

import msgspec

from alasio.ext.cache import InstanceCacheOperation, cached_property
from alasio.ext.path.atomic import CHUNK_SIZE, atomic_open
from alasio.ext.path.validate import validate_filename, validate_filepath, validate_resolve_filepath

from .crawl import crawl_tree
from .errors import AsarEntryNotFoundError, AsarError, AsarFormatError, AsarPathError, AsarUnsupportedError
from .format import MAX_PATH_DEPTH, read_header
from .model import (
    KIND_DIR, KIND_FILE, KIND_LINK, AsarFileInfo, canonical_entries, check_links, keys_path, path_keys, read_entries,
    resolve_entry
)
from .pack import ContentVerifier, ContentWriter, hash_file, pack_archive, write_content
from .source import LocalFileSource, MemorySource, RangeSource

# Whether the file system can hold a symbolic link: Windows needs elevation to
# create one, so the extraction materializes a link as a copy of its target there
CAN_SYMLINK = os.name != 'nt'


class _Header:
    """
    Everything the header of an archive gives: the layout of the file and the
    entry table it describes.

    Attributes:
        data_offset (int): Offset of the first content byte, the header is what
            comes before it, the 8 bytes of the frame of the archive included
        files (dict): Flat entry table, see ``AsarArchive.files``
    """
    __slots__ = ('data_offset', 'files')

    def __init__(self, data_offset, files):
        self.data_offset = data_offset
        self.files = files

    def __repr__(self):
        return f'_Header({len(self.files)} entries)'


def normalize_path(path):
    """
    Normalize the separators of an archive path that a caller gives.

    A caller may use the separator of its platform, so the path of a call is
    converted to POSIX separators at the entry points of the module, and only
    there: a path that already flows inside (built by ``keys_path()``, or read
    from a header) is not converted a second time. No name of a table holds a
    backslash, it is refused when the table is built and when an entry is
    added, so the two separators can never name different entries.

    Args:
        path (str): Archive path of a call, either separator

    Returns:
        str: The same path with POSIX separators
    """
    return path.replace('\\', '/')


def check_archive_path(path):
    """
    Check the path of a local entry that is about to be added to an archive.

    Every segment must be a valid filename on every supported platform, so that
    an archive we write can always be extracted again.

    Args:
        path (str): Path inside the archive; a backslash is a separator as well
            and is normalized to '/'

    Returns:
        tuple: Path segments, the key of the entry in the table

    Raises:
        AsarPathError: If the path is absolute, empty or has an invalid segment
    """
    if not isinstance(path, str) or not path:
        raise AsarPathError(f'Archive path must be a non empty string, got "{path}"')
    normalized = normalize_path(path)
    if normalized.startswith('/'):
        raise AsarPathError(f'Archive path must be relative, got "{path}"')
    # The only split of a path that is added: everything below takes the segments
    keys = path_keys(normalized)
    for name in keys:
        try:
            validate_filename(name)
        except ValueError as e:
            raise AsarPathError(f'Invalid archive path "{path}": {e}')
    return keys


def entry_mode(info):
    """
    Get the POSIX mode a file entry is extracted with.

    Args:
        info (AsarFileInfo): Entry

    Returns:
        int: 0o755 for an executable entry, None to keep the default mode,
            always None on Windows, which has no executable bit
    """
    if info.executable and os.name != 'nt':
        return 0o755
    return None


def check_link_target(root, name, link):
    """
    Check the target of a link entry before the link is created.

    Args:
        root (str): Root of the extraction
        name (str): Archive path of the link, only used in error messages
        link (str): Target of the link, relative to the archive root

    Raises:
        AsarPathError: If the target is unsafe or leaves the extraction
            directory
    """
    try:
        validate_filepath(link)
        validate_resolve_filepath(root, link)
    except ValueError as e:
        raise AsarPathError(f'Invalid link target of "{name}": {e}')


def relative_link_target(dest, target, link):
    """
    Get the text of the symbolic link that stands for an entry.

    The link of the header is relative to the root of the archive, while a
    symbolic link of a file system is relative to the directory that holds it:
    the target is rebased on the directory of the link, the same way the
    reference implementation does it (``path.relative()`` between the directory
    of the target and the directory of the link).

    Args:
        dest (str): Root of the extraction
        target (str): Path the link is created at
        link (str): Target of the link, relative to the archive root

    Returns:
        str: Target of the link, relative to the directory of the link
    """
    return os.path.relpath(os.path.join(dest, *path_keys(link)), os.path.dirname(target))


def create_link(archive, name, dest, target):
    """
    Create the link of an entry.

    The link and its target are both inside the extracted tree, so the link is
    created with the path the header stores, rebased on the directory of the
    link. Windows needs elevation to create a symbolic link, a link to a file is
    materialized as a copy there and a link to a directory can not be extracted
    at all.

    Args:
        archive (AsarArchive): Archive holding the entry
        name (str): Archive path of the link
        dest (str): Root of the extraction
        target (str): Path of the link on disk

    Raises:
        AsarPathError: If the target of the link leaves the extraction directory
        AsarUnsupportedError: If a directory link is extracted on Windows
    """
    info = archive.entry(name)
    check_link_target(dest, name, info.link)
    if CAN_SYMLINK:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        # The tree of a previous extraction holds the link already, and a
        # symbolic link is never created over an existing path: the file of the
        # last extraction is replaced, a directory in the way is an error
        try:
            os.unlink(target)
        except FileNotFoundError:
            pass
        os.symlink(relative_link_target(dest, target, info.link), target)
        return
    _, target_info = archive.resolve(name)
    if target_info.kind == KIND_DIR:
        raise AsarUnsupportedError(
            f'Link "{name}" points to a directory, which can not be extracted on Windows'
        )
    archive.extract_file(name, target)


def content_verifier(path, info, verify):
    """
    Build the checker of the content of one entry.

    Args:
        path (str): Archive path, only used in error messages
        info (AsarFileInfo): Entry
        verify (bool): Whether a content check was requested

    Returns:
        ContentVerifier: Checker of the content, None when there is nothing to
            check

    Raises:
        AsarFormatError: If the entry is stored in the archive and a content
            check was requested but the entry has no hash to check it against
    """
    if not verify:
        return None
    if isinstance(info.source, RangeSource):
        # What an entry that is stored in the archive holds is described by the
        # header, which was written by another pass of the module: a mismatch is
        # reported instead of extracting content the header does not describe
        expected = info.integrity.hash if info.integrity else None
        if expected is None:
            raise AsarFormatError(f'Entry "{path}" has no integrity hash to verify')
    elif info.integrity is not None:
        expected = info.integrity.hash
    else:
        # Content that was handed over has no hash before it is written into an
        # archive, there is nothing to compare it with
        return None
    return ContentVerifier(path, info.size, expected, error=AsarFormatError)


class AsarArchive:
    """
    An asar archive: the entry table, the source of every content, and the file
    the archive lives in when there is one.

    An archive path of a call (the ``name`` of the entry points, the ``arc_path``
    of ``add_file()``) uses POSIX separators; a backslash is a separator as well,
    so a path that came from another platform names the same entry.

    Attributes:
        file (str): Path of the archive, None for an archive that was built in
            memory and was never written
        max_size (int): Refuse an archive larger than this, in bytes, None to
            accept any size. An archive may come from the network, so the update
            flow should pass the expected size limit
        files (dict): Flat entry table, ``{keys: AsarFileInfo}`` where ``keys``
            is the tuple of the path segments of an entry. A directory is an
            entry like any other, so an empty directory and the unpacked flag
            of a directory are part of the table. ``entry(path)`` and
            ``iter_entries()`` are the ways to read it
        header_size (int): Header pickle length, the data area starts at 8 + it
        data_offset (int): Offset of the first content byte
        fd (io.IOBase): Open handle of the archive file, None for an archive
            that has no file
    """

    def __init__(self, file=None, max_size=None):
        """
        Args:
            file (str): Path of the archive, None to build a new archive
            max_size (int): Refuse an archive larger than this, in bytes
        """
        # A path is kept as a plain string, the entry points of the module work
        # with str and the caller may pass any path like object
        self.file = None if file is None else str(file)
        self.max_size = max_size

    def __repr__(self):
        # No IO, a debugger may print an archive that can not be read
        header = InstanceCacheOperation.get(self, 'header')
        if header is None:
            return f'AsarArchive(file={self.file!r})'
        return f'AsarArchive(file={self.file!r}, entries={len(header.files)})'

    @cached_property
    def fd(self):
        """
        Open the archive file, on demand.

        Returns:
            io.IOBase: Handle of the archive, None when the archive has no file

        Raises:
            AsarUnsupportedError: If the archive is larger than `max_size`
        """
        file = self.file
        if file is None:
            return None
        f = atomic_open(file, 'rb')
        if self.max_size is not None:
            size = os.fstat(f.fileno()).st_size
            if size > self.max_size:
                f.close()
                raise AsarUnsupportedError(
                    f'Archive is larger than the {self.max_size} bytes limit of this call'
                )
        return f

    @cached_property
    def header(self):
        """
        Read and parse the header of the archive, on demand.

        Returns:
            _Header: Header of the archive, an empty one for an archive that has
                no file yet

        Raises:
            AsarFormatError: If the file is not a valid asar archive
        """
        f = self.fd
        if f is None:
            # No file, no header: the table of the archive is the archive
            return _Header(0, {})
        # The size is needed to check that every entry points inside the archive,
        # it comes from the handle so that the position stays where reading the
        # header leaves it, on the first content byte
        archive_size = os.fstat(f.fileno()).st_size
        json_bytes, data_offset = read_header(f)
        # The header JSON is the input of the module that comes from the outside,
        # so its errors are translated to the module ones here
        try:
            header = msgspec.json.decode(json_bytes)
        except msgspec.DecodeError as e:
            raise AsarFormatError(f'Header is not valid JSON: {e}')
        except RecursionError:
            # msgspec raises RecursionError instead of DecodeError on a deep
            # nesting, see MAX_PATH_DEPTH
            raise AsarFormatError(f'Header is nested too deeply, over {MAX_PATH_DEPTH} segments')
        # The table carries the source of every content, and the source of an
        # unpacked entry is the file it is copied to next to the archive
        files = read_entries(header, archive_size, data_offset, self.unpacked_path)
        return _Header(data_offset, files)

    @property
    def files(self):
        """
        Get the flat entry table of the archive.

        Returns:
            dict[tuple[str, ...], AsarFileInfo]: The entries of the archive, see
                the class documentation
        """
        return self.header.files

    @property
    def header_size(self):
        """
        Get the header pickle length of the archive.

        Returns:
            int: Header length, the data area starts at 8 + it, 0 for an archive
                that has no file
        """
        # The 8 bytes of the frame of the archive come first, the header is what
        # the data area comes after
        data_offset = self.header.data_offset
        return data_offset - 8 if data_offset else 0

    @property
    def data_offset(self):
        """
        Get the offset of the first content byte of the archive.

        Returns:
            int: Offset of the data area
        """
        return self.header.data_offset

    @property
    def unpacked_path(self):
        """
        Get the directory of the unpacked content of this archive.

        Returns:
            str: Path of ``<archive>.unpacked``, None when there is no archive file
        """
        return f'{self.file}.unpacked' if self.file else None

    def close(self):
        """
        Release the archive file and drop everything that was read from it.

        The entries are re-read the next time they are used, so an archive that
        is changed and then closed loses the changes that were not written, and
        an archive whose file was replaced meanwhile is never read through stale
        metadata. An archive without a file only holds its entry table, closing
        it does nothing.

        Can be called more than once.
        """
        f = InstanceCacheOperation.pop(self, 'fd')
        if f is None:
            # Nothing was opened, or the archive has no file: the entries are
            # the archive, they can not be dropped
            return
        f.close()
        # The handle and the table live and die together, a table without its
        # handle would read the content of the old file at the new offsets
        InstanceCacheOperation.pop(self, 'header')

    def __enter__(self):
        # Read the header here, so that a broken archive is reported where it is
        # opened instead of at the first use of its entries
        InstanceCacheOperation.warm(self, 'header')
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

    # ------------------------------------------------------------------ table

    def entry(self, name):
        """
        Get one entry of the archive.

        Args:
            name (str): Archive path

        Returns:
            AsarFileInfo: Entry

        Raises:
            AsarEntryNotFoundError: If there is no such entry
        """
        info = self.files.get(path_keys(normalize_path(name)))
        if info is None:
            raise AsarEntryNotFoundError(f'Entry "{name}" does not exist in the archive')
        return info

    def iter_entries(self):
        """
        Iter every entry of the archive, the directories included.

        Yields:
            tuple[str, AsarFileInfo]: Path and entry, in the canonical order of
                an archive this module writes, a directory comes before its
                content
        """
        for keys, info in canonical_entries(self.files):
            yield keys_path(keys), info

    def resolve(self, name):
        """
        Look up an entry, following the link entries of the path.

        A link may be the entry itself or an intermediate directory of the path,
        the reference implementation resolves both (a link to `A` makes
        `Current/real.txt` point at `A/real.txt`).

        Args:
            name (str): Archive path

        Returns:
            tuple[str, AsarFileInfo]: Path and entry it points to

        Raises:
            AsarEntryNotFoundError: If the entry or a link target is missing
            AsarFormatError: If the links are circular or too deep
        """
        return resolve_entry(self.files, normalize_path(name))

    # -------------------------------------------------------------- changing

    def add_file(self, path=None, arc_path=None, data=None, unpack=None):
        """
        Add a single file to the archive.

        The content of a file on disk is not read now, it is read when the
        archive is written, so a large file never sits in memory.

        The links of the table are checked once the entry is added, so a link
        that points at the entry is accepted while a link that is left without a
        target is reported.

        Args:
            path (str): Local file path
            arc_path (str): Path inside the archive, defaults to the file name of
                `path`
            data (bytes): Content, added instead of reading `path`, for a file
                that does not exist on disk (a generated package.json and the like)
            unpack (bool): Store the content next to the archive instead of
                inside it, for a file that has to be a real file on disk (a
                native module and the like). None to keep the flag of the entry
                that is replaced, or to follow the directory the entry is added to

        Returns:
            AsarFileInfo: The added entry

        Raises:
            ValueError: If neither `path` nor `data` is given
            AsarPathError: If `arc_path` is not a valid archive path, or if it
                is used by a directory
        """
        if path is None and data is None:
            raise ValueError('add_file() needs a path or a content, use mark_unpack() to mark an entry')
        if arc_path is None:
            if path is None:
                raise ValueError('add_file() needs an arc_path when there is no path')
            arc_path = os.path.basename(path)
        keys = check_archive_path(arc_path)
        info = self._set_file(keys, MemorySource(data) if data is not None else LocalFileSource(path), unpack)
        # The links are checked once the entry is in the table: a link may well
        # point at the entry this call adds
        check_links(self.files)
        return info

    def _set_file(self, keys, source, unpack):
        """
        Put a file entry into the table, creating the directories above it.

        Args:
            keys (tuple): Path segments of the entry
            source (ContentSource): Where the content of the file comes from
            unpack (bool): Store the content next to the archive, None to inherit
                the flag of the entry that is replaced or of the directory the
                entry is added to

        Returns:
            AsarFileInfo: The entry

        Raises:
            AsarPathError: If the path is used by a directory, or if a parent of
                it is used by a file
        """
        previous = self.files.get(keys)
        if previous is not None and previous.kind == KIND_DIR:
            raise AsarPathError(f'Archive path "{keys_path(keys)}" is already a directory')
        # One pass over the parents: the missing ones are created, a file in the
        # way is refused, and a directory that is stored unpacked is reported
        inherited = self._ensure_parents(keys)
        if unpack is None:
            # A new entry follows the directory it is added to, so that a native
            # module added below an unpacked directory is stored the same way as
            # its neighbours
            unpack = inherited if previous is None else previous.unpacked
        info = AsarFileInfo(kind=KIND_FILE, unpacked=bool(unpack), source=source)
        # Replacing an entry keeps its position: assigning a key of a dict a
        # second time does not move it
        self.files[keys] = info
        return info

    def _ensure_parents(self, keys):
        """
        Create the directories above an entry and check the path it is put at.

        Missing levels are created as directories, a level that is used by a
        file is refused, and the unpacked flag of the directories is reported so
        that the caller can let the entry inherit it. One pass over the prefixes
        of the path: a caller that has to look the path up anyway (the entry it
        replaces, the conflict of its name) does it on its own, but the path is
        never walked a second time.

        Args:
            keys (tuple): Path segments of the entry, the entry itself is not
                looked at

        Returns:
            bool: True when a directory above the entry is stored unpacked

        Raises:
            AsarPathError: If a parent of the entry is used by a file
        """
        unpacked = False
        parent_keys = ()
        for name in keys[:-1]:
            parent_keys += (name,)
            parent = self.files.get(parent_keys)
            if parent is None:
                self.files[parent_keys] = AsarFileInfo(kind=KIND_DIR)
            elif parent.kind != KIND_DIR:
                raise AsarPathError(f'Archive path "{keys_path(parent_keys)}" is already a file')
            elif parent.unpacked:
                unpacked = True
        return unpacked

    def add_folder(self, root, unpack=False):
        """
        Add a directory tree to the archive.

        Every entry of the tree is added, the archive paths are the paths
        relative to `root`, so the caller points the method at a tree that
        mirrors what the archive should hold. A mixed tree (a source root that
        also holds what must not be packed) is not filtered here: the part to
        pack has to be given on its own.

        The links of the table are checked once the whole tree is added, so a
        link that points at an entry of the tree is accepted while a link that
        is left without a target is reported.

        Args:
            root (str): Root directory, its content is added below the archive root
            unpack (bool): Store the content of the tree next to the archive
                instead of inside it, for the entries that have to be real files
                (native modules and the like). A directory that is unpacked
                already keeps its flag

        Returns:
            int: Number of added entries

        Raises:
            AsarError: If the directory can not be listed
            AsarPathError: If a path of the tree is not a valid archive path
        """
        entries = crawl_tree(root)
        for arc_path, local_path, kind in entries:
            # The path is checked once here, the entry is put into the table with
            # the segments it gives: the tree of the directory is not validated
            # a second time by the file entries
            keys = check_archive_path(arc_path)
            if kind != KIND_DIR:
                self._set_file(keys, LocalFileSource(local_path), unpack)
                continue
            # The parents of a directory are entries of the crawl as well, the
            # archive may already hold them (an archive that was opened)
            self._ensure_parents(keys)
            info = self.files.get(keys)
            if info is None:
                self.files[keys] = AsarFileInfo(kind=KIND_DIR, unpacked=unpack)
            elif info.kind != KIND_DIR:
                raise AsarPathError(f'Archive path "{keys_path(keys)}" is already a file')
            elif unpack:
                info.unpacked = True
        # The links are checked once the whole tree is in the table: a link may
        # well point at an entry of the tree this call adds
        check_links(self.files)
        return len(entries)

    def mark_unpack(self, name):
        """
        Mark an entry and everything below it as unpacked.

        An unpacked entry keeps its content next to the archive instead of
        inside it, which is what a file that has to be a real file on disk
        needs (a native module and the like). A directory takes its whole
        subtree with it, a file or a link is marked on its own. The content of
        an entry that was read from an archive is copied out of it when the
        archive is written.

        Args:
            name (str): Archive path of the entry

        Returns:
            int: Number of entries that were packed before this call and are
                unpacked now, the entry itself included

        Raises:
            AsarEntryNotFoundError: If there is no such entry
        """
        keys = path_keys(normalize_path(name))
        if keys not in self.files:
            raise AsarEntryNotFoundError(f'Entry "{name}" does not exist in the archive')
        # The subtree of a path is every key the path is a prefix of, at any
        # depth: a directory is marked by the same pass as the entries below it
        depth = len(keys)
        marked = 0
        for entry_keys, entry in self.files.items():
            if entry_keys[:depth] != keys or entry.unpacked:
                continue
            entry.unpacked = True
            if entry.kind == KIND_FILE:
                # The content does not live in the body any more, the offset
                # belongs to a layout the entry is not part of
                entry.offset = None
            marked += 1
        return marked

    def del_file(self, name):
        """
        Delete a file or a link from the archive.

        Args:
            name (str): Archive path

        Returns:
            int: Number of deleted entries, always 1

        Raises:
            AsarEntryNotFoundError: If there is no such entry
            AsarPathError: If the entry is a directory, use ``del_folder()``
        """
        keys = path_keys(normalize_path(name))
        info = self.files.get(keys)
        if info is None:
            raise AsarEntryNotFoundError(f'Entry "{name}" does not exist in the archive')
        if info.kind == KIND_DIR:
            raise AsarPathError(f'Entry "{name}" is a directory, use del_folder() to delete it')
        del self.files[keys]
        return 1

    def del_folder(self, name):
        """
        Delete a directory and its whole content from the archive.

        The parents of the directory are kept, an empty directory is a valid
        part of an archive.

        Args:
            name (str): Archive path

        Returns:
            int: Number of deleted entries, the directory included

        Raises:
            AsarEntryNotFoundError: If there is no such entry
            AsarPathError: If the entry is a file, use ``del_file()``
        """
        keys = path_keys(normalize_path(name))
        info = self.files.get(keys)
        if info is None:
            raise AsarEntryNotFoundError(f'Entry "{name}" does not exist in the archive')
        if info.kind != KIND_DIR:
            raise AsarPathError(f'Entry "{name}" is a file, use del_file() to delete it')
        # The subtree is counted and collected before it is removed, never while
        # the table is iterated, and one key of the table is one pop
        depth = len(keys)
        removed = [entry_keys for entry_keys in self.files if entry_keys[:depth] == keys]
        for entry_keys in removed:
            del self.files[entry_keys]
        return len(removed)

    # --------------------------------------------------------------- reading

    def iter_content(self, name, chunk_size=CHUNK_SIZE):
        """
        Stream the content of a file entry.

        Args:
            name (str): Archive path, links are followed
            chunk_size (int): Read chunk size of a file on disk

        Yields:
            bytes | memoryview: Content chunks

        Raises:
            AsarError: If the entry is not a file, or if its content is missing
        """
        name, info = self.resolve(name)
        if info.kind != KIND_FILE:
            raise AsarError(f'Entry "{name}" is a directory')
        source = info.source
        if source is None:
            raise AsarError(f'Entry "{name}" has no content source')
        yield from source.iter_chunks(fd=self.fd, chunk_size=chunk_size)

    def read_file(self, name):
        """
        Read the content of one entry.

        Args:
            name (str): Archive path, links are followed

        Returns:
            memoryview: Content, a read only view when it is kept in memory

        Raises:
            AsarError: If the entry is not a file, or if its content is missing
        """
        name, info = self.resolve(name)
        if info.kind != KIND_FILE:
            raise AsarError(f'Entry "{name}" is a directory')
        source = info.source
        if isinstance(source, MemorySource):
            # Content that was added to the archive, no copy needed
            return memoryview(source.data)
        return memoryview(b''.join(self.iter_content(name)))

    def extract_file(self, name, dest, verify=True):
        """
        Extract one entry to a file path.

        Args:
            name (str): Archive path, links are followed
            dest (str): Target file path, the parent directory is created
            verify (bool): Compare the content with the integrity of the header
                while it is written, it costs no extra I/O because the content
                is read anyway. An entry whose archive has no integrity can not
                be verified, it is extracted with ``verify=False``

        Raises:
            AsarError: If the entry can not be read
            AsarFormatError: If the content does not match the header
        """
        name, info = self.resolve(name)
        if info.kind == KIND_DIR:
            os.makedirs(dest, exist_ok=True)
            return
        write_content(
            dest, self.iter_content(name),
            verifier=content_verifier(name, info, verify), mode=entry_mode(info),
        )

    def extract_all(self, dest, verify=True, chunk_size=CHUNK_SIZE):
        """
        Extract the whole archive to a directory.

        The content that is stored in the archive is read in the order of the
        data area: the entries of an archive are stored back to back, so the
        reads follow each other and the buffer of the handle is reused. The
        reads run in this thread, the write of every file is one task on the
        thread pool (see `ContentWriter`), and the writer is waited for before
        the links are created, so that a link target is always on the disk when
        the link is created. Directories are created first, then the content,
        then the links. On Windows a link to a file is materialized as a copy, a
        link to a directory is not supported because Windows needs elevation to
        create a symlink. A table that holds a file entry without a content
        source is refused before the target is touched.

        Every entry of the table holds a path that was checked when it entered
        the table (added by the caller, or read from the header of an archive),
        so the target of an entry is its path below `dest`.

        Args:
            dest (str): Target directory
            verify (bool): Compare every content that is stored in the archive
                with the integrity of the header while extracting, it costs no
                extra I/O because the content is read anyway. An archive whose
                entries have no integrity can not be verified, it is extracted
                with ``verify=False``
            chunk_size (int): Read chunk size of an entry

        Raises:
            AsarError: If a file entry has no content source
            AsarPathError: If the target of a link leaves the target directory
            AsarFormatError: If the archive is truncated, or if a content hash
                does not match
            AsarUnsupportedError: If a directory link is extracted on Windows
        """
        entries = canonical_entries(self.files)

        def iter_files():
            """
            Yield the file entries of the table, checked while it is walked.

            A file entry that has no content source at all can not be extracted,
            and a table that can not be extracted as a whole must not leave a
            half extracted tree behind: it is refused here, before the target is
            touched.

            Yields:
                tuple: ``(keys, info)`` of a file entry

            Raises:
                AsarError: If a file entry has no content source
            """
            for keys, info in entries:
                if info.kind != KIND_FILE:
                    continue
                if info.source is None:
                    raise AsarError(
                        f'Entry "{keys_path(keys)}" has no content source, it can not be extracted'
                    )
                yield keys, info

        # The generator is consumed while `stored` is built, so the table is
        # walked once and every file entry is checked before the target is
        # touched. The entries that live in the archive are collected in the
        # order of the data area: the reads of a real archive follow each other
        # and the buffer of the handle is reused, and a source seeks to the
        # offset of its own entry
        stored = [
            (info.source.offset, keys, info)
            for keys, info in iter_files()
            if isinstance(info.source, RangeSource)
        ]
        stored.sort(key=lambda entry: entry[0])

        os.makedirs(dest, exist_ok=True)
        for keys, info in entries:
            if info.kind == KIND_DIR:
                os.makedirs(os.path.join(dest, *keys), exist_ok=True)

        # Every file is written by its own task on the thread pool, the writer is
        # waited for below
        with ContentWriter() as writer:
            for _, keys, info in stored:
                path = keys_path(keys)
                writer.write(
                    os.path.join(dest, *keys), info.source.iter_chunks(self.fd, chunk_size),
                    verifier=content_verifier(path, info, verify), mode=entry_mode(info),
                )

            # The content that has a source of its own is copied entry by entry,
            # it is not part of the archive and has nothing to be checked against
            for keys, info in entries:
                source = info.source
                if info.kind != KIND_FILE or isinstance(source, RangeSource):
                    continue
                writer.write(
                    os.path.join(dest, *keys), source.iter_chunks(self.fd, chunk_size),
                    verifier=content_verifier(keys_path(keys), info, verify), mode=entry_mode(info),
                )

        # Every file is on the disk now, the links of the archive can be created
        for keys, info in entries:
            if info.kind == KIND_LINK:
                create_link(self, keys_path(keys), dest, os.path.join(dest, *keys))

    def validate(self, verify_content=False):
        """
        Check the archive structure, and optionally its content.

        The paths of the entries were checked when they entered the table, so
        what is left is the target of a link (the header does not keep it inside
        the archive) and, on demand, the content of every file.

        Args:
            verify_content (bool): Stream every file, recalculate its SHA256 and
                compare it with the integrity of the header

        Raises:
            AsarFormatError: If an entry can not be read, or if a content hash
                does not match
            AsarPathError: If the target of a link can not be extracted safely
        """
        for path, info in self.iter_entries():
            if info.kind != KIND_LINK:
                continue
            try:
                validate_filepath(info.link)
            except ValueError as e:
                raise AsarPathError(f'Invalid link target of "{path}": {e}')
            # Dangling and circular links break extraction
            self.resolve(path)
        if not verify_content:
            return
        for path, info in self.iter_entries():
            if info.kind != KIND_FILE:
                continue
            expected = info.integrity.hash if info.integrity else None
            if expected is None:
                raise AsarFormatError(f'Entry "{path}" has no integrity hash to verify')
            verifier = ContentVerifier(path, info.size, expected, error=AsarFormatError)
            for chunk in self.iter_content(path):
                verifier.update(chunk)
            verifier.check()

    # --------------------------------------------------------------- writing

    def write(self, dest=None, integrity=True):
        """
        Write the archive to a file.

        The content of every entry is read from its source, its real byte count
        and its hashes are calculated, then the content is written and checked
        against the first pass, so a source that changed in between fails the
        pack instead of producing an archive that lies about its content. The
        entries are stored in the canonical order, so any sequence of operations
        that ends with the same entries writes the same bytes.

        Without `dest` the archive is written back to its own file: the content
        of the entries that were not touched is read from that file, the archive
        replaces it only once it is complete, and the handle this archive holds
        is released just before, which Windows requires.

        The links of the table are checked first: an archive whose links do not
        resolve is one this module could not read back, it is never written.

        Args:
            dest (str): Target archive path, None to write to ``self.file``
            integrity (bool): Write the per file SHA256 integrity of the
                reference implementation, disable it to save the hashing time

        Raises:
            AsarError: If there is no target path, if an entry has no content
                source, or if a source changed while packing
            AsarUnsupportedError: If an entry is larger than the format allows
        """
        file = self.file
        if dest is None:
            if file is None:
                raise AsarError('write() needs a dest path, this archive has no file')
            dest = file
        else:
            dest = str(dest)
        # An archive whose links do not resolve is one this module could not read
        # back, it is never written
        check_links(self.files)
        # The handle is taken before anything is written, and released after the
        # last content was read: a file can not be replaced while it is open on
        # Windows, and the handle would point at the old file afterwards
        fd = self.fd

        def release():
            handle = InstanceCacheOperation.pop(self, 'fd')
            if handle is not None:
                handle.close()

        pack_archive(self.files, dest, integrity=integrity, fd=fd, release=release)
        # Everything from the old file is stale now, the entries are re-read
        # from the archive that was just written
        InstanceCacheOperation.pop(self, 'header')
        self.file = dest


def pack_sha256(path):
    """
    Calculate the SHA256 of a file, for the update flow.

    Args:
        path (str): File path, the file is streamed

    Returns:
        str: SHA256 hex digest
    """
    return hash_file(path)
