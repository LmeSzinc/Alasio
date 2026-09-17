"""
Reading, building, changing and extracting asar archives.

``AsarArchive`` is the single entry point of the module. It holds the entry
table of an archive, the source of the content of every entry, and — when the
archive was read from or written to a file — the open handle of that file::

    # Open an archive that exists
    with AsarArchive('app.asar') as asar:
        asar.extract_all('output')

    # Build one from scratch
    with AsarArchive() as asar:
        asar.add_folder('webapp', include=['dist/**', 'package.json'])
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
import hashlib
import os

from alasio.ext.cache import InstanceCacheOperation, cached_property
from alasio.ext.deep import deep_get, deep_pop
from alasio.ext.path.atomic import CHUNK_SIZE, atomic_open, file_write
from alasio.ext.path.validate import validate_filename, validate_filepath, validate_resolve_filepath

from .crawl import crawl_folder
from .errors import AsarEntryNotFoundError, AsarError, AsarFormatError, AsarPathError, AsarUnsupportedError
from .format import read_header
from .model import (
    KIND_DIR, KIND_FILE, KIND_LINK, AsarFileInfo, canonical_entries, decode_header, ensure_dir, has_unpacked_ancestor,
    read_entries, set_leaf
)
from .pack import ContentVerifier, hash_file, pack_archive, write_content
from .scan import REGION_BUDGET, Member, build_regions, scan_regions
from .source import LocalFileSource, MemorySource, RangeSource

# Maximum number of links followed when resolving an entry, matches the
# SYMLOOP_MAX of the reference implementation
SYMLINK_MAX_DEPTH = 40
# Hash of an empty content, an empty file must be the only entry with it
EMPTY_SHA256 = hashlib.sha256(b'').hexdigest()


class _Header:
    """
    Everything the header of an archive gives: the layout of the file and the
    entry table it describes.

    Attributes:
        data_offset (int): Offset of the first content byte, the header is what
            comes before it, the 8 bytes of the frame of the archive included
        files (dict): Nested entry table, see ``AsarArchive.files``
    """
    __slots__ = ('data_offset', 'files')

    def __init__(self, data_offset, files):
        self.data_offset = data_offset
        self.files = files

    def __repr__(self):
        return f'_Header({len(self.files)} entries)'


def check_archive_path(path):
    """
    Check the path of a local entry that is about to be added to an archive.

    Every segment must be a valid filename on every supported platform, so that
    an archive we write can always be extracted again.

    Args:
        path (str): Path inside the archive, POSIX separators

    Returns:
        str: Normalized archive path

    Raises:
        AsarPathError: If the path is absolute, empty or has an invalid segment
    """
    if not isinstance(path, str) or not path:
        raise AsarPathError(f'Archive path must be a non empty string, got "{path}"')
    normalized = path.replace('\\', '/')
    if normalized.startswith('/'):
        raise AsarPathError(f'Archive path must be relative, got "{path}"')
    for part in normalized.split('/'):
        try:
            validate_filename(part)
        except ValueError as e:
            raise AsarPathError(f'Invalid archive path "{path}": {e}')
    return normalized


def check_target_path(root, path):
    """
    Resolve an archive path below a target directory.

    Args:
        root (str): Target directory
        path (str): Archive path of the entry

    Returns:
        str: Absolute path of the entry inside the target directory

    Raises:
        AsarPathError: If the path is unsafe or escapes the target directory
    """
    if '\\' in path:
        raise AsarPathError(f'Invalid archive path "{path}": a path must not contain a backslash')
    try:
        return validate_resolve_filepath(root, path)
    except ValueError as e:
        raise AsarPathError(f'Invalid archive path "{path}": {e}')


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


def create_link(archive, name, dest, target):
    """
    Create the link of an entry.

    The link and its target are both inside the extracted tree, so the link is
    created with the relative path the header stores. Windows needs elevation to
    create a symbolic link, a link to a file is materialized as a copy there and
    a link to a directory can not be extracted at all.

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
    if os.name != 'nt':
        os.makedirs(os.path.dirname(target), exist_ok=True)
        os.symlink(info.link, target)
        return
    _, target_info = archive.resolve(name)
    if target_info.kind == KIND_DIR:
        raise AsarUnsupportedError(
            f'Link "{name}" points to a directory, which can not be extracted on Windows'
        )
    archive.extract_file(name, target)


def entry_hash(path, info, verify):
    """
    Get the integrity hash of an entry.

    Args:
        path (str): Archive path, only used in error messages
        info (AsarFileInfo): Entry
        verify (bool): Whether a content check was requested

    Returns:
        str: SHA256 hex digest of the entry, None when not verifying

    Raises:
        AsarFormatError: If a content check was requested but the entry has no hash
    """
    if not verify:
        return None
    expected = info.integrity.hash if info.integrity else None
    if expected is None:
        raise AsarFormatError(f'Entry "{path}" has no integrity hash to verify')
    return expected


def check_empty_content(path, info, verify):
    """
    Check the integrity of an empty file, there is no content to read.

    Args:
        path (str): Archive path, only used in error messages
        info (AsarFileInfo): Entry
        verify (bool): Check the hash

    Raises:
        AsarFormatError: If the stored hash is not the hash of an empty content
    """
    expected = entry_hash(path, info, verify)
    if expected is not None and expected != EMPTY_SHA256:
        raise AsarFormatError(
            f'Content hash of "{path}" does not match, it is empty but its integrity '
            f'hash is {expected} instead of {EMPTY_SHA256}'
        )


class AsarArchive:
    """
    An asar archive: the entry table, the source of every content, and the file
    the archive lives in when there is one.

    Attributes:
        file (str): Path of the archive, None for an archive that was built in
            memory and was never written
        max_size (int): Refuse an archive larger than this, in bytes, None to
            accept any size. An archive may come from the network, so the update
            flow should pass the expected size limit
        files (dict): Nested entry table, a directory is a dict of its children
            and a file or a link is an ``AsarFileInfo``. The attributes of a
            directory itself are stored under the ``None`` key, so a plain
            directory is just ``{name: child}``
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
        f = atomic_open(file, 'rb', buffering=0)
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
        # The table carries the source of every content, and the source of an
        # unpacked entry is the file it is copied to next to the archive
        files = read_entries(decode_header(json_bytes), archive_size, data_offset, self.unpacked_path)
        return _Header(data_offset, files)

    @property
    def files(self):
        """
        Get the nested entry table of the archive.

        Returns:
            dict: ``{name: dict | AsarFileInfo}``, see the class documentation
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
            AsarFileInfo: Entry, the attributes of a directory that has none of
                its own are built on the fly

        Raises:
            AsarEntryNotFoundError: If there is no such entry
        """
        node = deep_get(self.files, name.split('/'))
        if node is None:
            raise AsarEntryNotFoundError(f'Entry "{name}" does not exist in the archive')
        if type(node) is dict:
            info = node.get(None)
            if info is not None:
                return info
            return AsarFileInfo(path=name, kind=KIND_DIR)
        return node

    def iter_entries(self):
        """
        Iter every entry of the archive, the directories included.

        Yields:
            (str, AsarFileInfo): Path and entry, in the canonical order of an
                archive this module writes, a directory comes before its content
        """
        for keys, node in canonical_entries(self.files):
            path = '/'.join(keys)
            if type(node) is dict:
                info = node.get(None)
                if info is None:
                    info = AsarFileInfo(path=path, kind=KIND_DIR)
                yield path, info
                continue
            yield path, node

    def resolve(self, name):
        """
        Look up an entry, following the link entries of the path.

        A link may be the entry itself or an intermediate directory of the path,
        the reference implementation resolves both (a link to `A` makes
        `Current/real.txt` point at `A/real.txt`).

        Args:
            name (str): Archive path

        Returns:
            (str, AsarFileInfo): Path and entry it points to

        Raises:
            AsarEntryNotFoundError: If the entry or a link target is missing
            AsarFormatError: If the links are circular or too deep
        """
        visited = set()
        while True:
            parts = name.split('/')
            replaced = False
            # An intermediate segment may be a link to another directory
            for index in range(len(parts) - 1):
                info = deep_get(self.files, parts[:index + 1])
                if info is None or type(info) is dict or info.kind != KIND_LINK:
                    continue
                name = f'{self._follow("/".join(parts[:index + 1]), info, visited)}/{"/".join(parts[index + 1:])}'
                replaced = True
                break
            if replaced:
                continue
            info = self.entry(name)
            if info.kind != KIND_LINK:
                return name, info
            name = self._follow(name, info, visited)

    def _follow(self, name, info, visited):
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

    # -------------------------------------------------------------- changing

    def add_file(self, path=None, arc_path=None, data=None, unpacked=None):
        """
        Add a single file to the archive.

        The content of a file on disk is not read now, it is read when the
        archive is written, so a large file never sits in memory.

        Args:
            path (str): Local file path
            arc_path (str): Path inside the archive, defaults to the file name of
                `path`
            data (bytes): Content, added instead of reading `path`, for a file
                that does not exist on disk (a generated package.json and the like)
            unpacked (bool): Store the content next to the archive instead of
                inside it, None to keep the flag of the entry that is replaced,
                or the flag of the directory the entry is added to

        Returns:
            AsarFileInfo: The added entry

        Raises:
            ValueError: If neither `path` nor `data` is given
            AsarPathError: If `arc_path` is not a valid archive path, or if it
                is used by a directory
        """
        if path is None and data is None:
            raise ValueError('add_file() needs a path or a content')
        if arc_path is None:
            if path is None:
                raise ValueError('add_file() needs an arc_path when there is no path')
            arc_path = os.path.basename(path)
        arc_path = check_archive_path(arc_path)
        keys = arc_path.split('/')
        previous = deep_get(self.files, keys)
        if previous is not None and type(previous) is dict:
            raise AsarPathError(f'Archive path "{arc_path}" is already a directory')
        if unpacked is None:
            if previous is not None:
                unpacked = previous.unpacked
            else:
                # A new entry follows the directory it is added to, so that a
                # native module added below an unpacked directory is stored the
                # same way as its neighbours
                unpacked = has_unpacked_ancestor(self.files, keys)
        info = AsarFileInfo(
            path=arc_path,
            kind=KIND_FILE,
            unpacked=bool(unpacked),
            source=MemorySource(data) if data is not None else LocalFileSource(path),
        )
        set_leaf(self.files, arc_path, info)
        return info

    def add_folder(self, root, include=None, exclude=None, unpack=None, unpack_dir=None):
        """
        Add a directory tree to the archive.

        Args:
            root (str): Root directory, its content is added below the archive root
            include (list): Glob patterns of the entries to add, None to add every
                file of the tree
            exclude (list): Glob patterns of the entries to drop, dropping a
                directory drops its whole subtree
            unpack (list): Glob patterns of the files to store next to the archive
            unpack_dir (list): Patterns of the directories to store next to the
                archive, their whole content follows

        Returns:
            int: Number of added entries

        Raises:
            AsarError: If the directory can not be listed
            AsarPathError: If a path of the tree is not a valid archive path
        """
        entries = crawl_folder(
            root, include=include, exclude=exclude, unpack=unpack, unpack_dir=unpack_dir,
        )
        for arc_path, local_path, kind, unpacked in entries:
            arc_path = check_archive_path(arc_path)
            if kind != KIND_DIR:
                self.add_file(path=local_path, arc_path=arc_path, unpacked=unpacked)
                continue
            children = ensure_dir(self.files, arc_path)
            if unpacked and children.get(None) is None:
                children[None] = AsarFileInfo(path=arc_path, kind=KIND_DIR, unpacked=True)
        return len(entries)

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
        node = deep_get(self.files, name.split('/'))
        if node is None:
            raise AsarEntryNotFoundError(f'Entry "{name}" does not exist in the archive')
        if type(node) is dict:
            raise AsarPathError(f'Entry "{name}" is a directory, use del_folder() to delete it')
        deep_pop(self.files, name.split('/'))
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
        node = deep_get(self.files, name.split('/'))
        if node is None:
            raise AsarEntryNotFoundError(f'Entry "{name}" does not exist in the archive')
        if type(node) is not dict:
            raise AsarPathError(f'Entry "{name}" is a file, use del_file() to delete it')
        # The subtree is counted before it is removed, and it is removed in one
        # pop, never while a caller iterates the table
        count = 1
        stack = [node]
        while stack:
            for child_name, child in stack.pop().items():
                if child_name is None:
                    continue
                count += 1
                if type(child) is dict:
                    stack.append(child)
        deep_pop(self.files, name.split('/'))
        return count

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

    def extract_file(self, name, dest):
        """
        Extract one entry to a file path.

        Args:
            name (str): Archive path, links are followed
            dest (str): Target file path, the parent directory is created

        Raises:
            AsarError: If the entry can not be read
        """
        name, info = self.resolve(name)
        if info.kind == KIND_DIR:
            os.makedirs(dest, exist_ok=True)
            return
        write_content(dest, self.iter_content(name), mode=entry_mode(info))

    def extract_all(self, dest, verify=False, region_budget=REGION_BUDGET, chunk_size=CHUNK_SIZE):
        """
        Extract the whole archive to a directory.

        The entries that are stored in the archive are extracted with a single
        sequential pass over the data area, the other ones are copied from their
        own source. Directories are created first, then the content, then the
        links, so that a link target always exists when the link is created. On
        Windows a link to a file is materialized as a copy, a link to a
        directory is not supported because Windows needs elevation to create a
        symlink.

        Args:
            dest (str): Target directory
            verify (bool): Compare every content that is stored in the archive
                with the integrity of the header while extracting, it costs no
                extra I/O because the content is read anyway
            region_budget (int): Regions up to this size are read in one piece, 0
                streams everything
            chunk_size (int): Read chunk size of a streamed region

        Raises:
            AsarPathError: If an entry would escape the target directory
            AsarFormatError: If the archive is malformed, or if a content hash
                does not match
            AsarUnsupportedError: If a directory link is extracted on Windows
        """
        entries = list(self.iter_entries())
        os.makedirs(dest, exist_ok=True)
        for path, info in entries:
            if info.kind == KIND_DIR:
                os.makedirs(check_target_path(dest, path), exist_ok=True)

        # The content that lives in the archive is read in one pass over the
        # data area, whatever the order of the entries is
        members = []
        for path, info in entries:
            source = info.source
            if info.kind != KIND_FILE or not isinstance(source, RangeSource):
                continue
            target = check_target_path(dest, path)
            if info.size == 0:
                check_empty_content(path, info, verify)
                file_write(target, b'')
                continue
            verifier = None
            expected = entry_hash(path, info, verify)
            if expected is not None:
                verifier = ContentVerifier(path, info.size, expected, error=AsarFormatError)
            members.append(Member(
                source.offset, source.offset + source.size, path, target, verifier, entry_mode(info),
            ))
        if members:
            members.sort(key=lambda member: (member.start, -member.end, member.path))
            regions = build_regions(members)
            f = self.fd
            if f.tell() != regions[0].start:
                # A second extraction of the same archive starts where the first
                # one ended, this is the only seek of a real archive
                f.seek(regions[0].start)
            scan_regions(f, regions, region_budget=region_budget, chunk_size=chunk_size)

        # The content that has a source of its own is copied entry by entry, it
        # is not part of the archive and has nothing to be checked against
        for path, info in entries:
            source = info.source
            if info.kind != KIND_FILE or isinstance(source, RangeSource):
                continue
            verifier = None
            if verify and info.integrity is not None:
                verifier = ContentVerifier(
                    path, info.size, info.integrity.hash, error=AsarFormatError,
                )
            write_content(
                check_target_path(dest, path), source.iter_chunks(self.fd, chunk_size),
                verifier=verifier, mode=entry_mode(info),
            )

        for path, info in entries:
            if info.kind == KIND_LINK:
                create_link(self, path, dest, check_target_path(dest, path))

    def validate(self, verify_content=False):
        """
        Check the archive structure, and optionally its content.

        Args:
            verify_content (bool): Stream every file, recalculate its SHA256 and
                compare it with the integrity of the header

        Raises:
            AsarFormatError: If an entry can not be read, or if a content hash
                does not match
            AsarPathError: If an entry path can not be extracted safely
        """
        for path, info in self.iter_entries():
            try:
                validate_filepath(path)
            except ValueError as e:
                raise AsarPathError(f'Invalid archive path "{path}": {e}')
            if info.kind == KIND_LINK:
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
            hasher = hashlib.sha256()
            for chunk in self.iter_content(path):
                hasher.update(chunk)
            digest = hasher.hexdigest()
            if digest != expected:
                raise AsarFormatError(
                    f'Content hash of "{path}" does not match, '
                    f'it is {digest} instead of {expected}'
                )

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
