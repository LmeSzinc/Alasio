"""
Reading, building and extracting asar archives.

``AsarArchive`` is the entry point of the module: it holds the flat entry table
of an archive, the content of the archive when it was read from a file, and the
local sources of the entries that were added to it.

Extraction of a whole archive uses a single sequential scan of the data area
(see ``unpack()``), while random access by path uses the loaded content or the
file the archive was written to.
"""
import hashlib
import os

import msgspec

from alasio.ext.path.atomic import CHUNK_SIZE, atomic_open, file_read_bytes, file_write
from alasio.ext.path.validate import validate_filename, validate_filepath, validate_resolve_filepath

from .crawl import crawl_folder
from .errors import AsarEntryNotFoundError, AsarError, AsarFormatError, AsarPathError, AsarUnsupportedError
from .format import MAX_HEADER_SIZE, parse_header_pickle, parse_size_pickle
from .model import KIND_DIR, KIND_FILE, KIND_LINK, AsarFileInfo, decode_header, read_entries
from .pack import AtomicChunkWriter, ContentVerifier, FileRange, hash_file, iter_entry_chunks, pack_archive, write_entry

# Regions up to this size are read in one piece, it is about the cost of one
# seek on a mechanical disk (~1 MB of sequential reading)
REGION_BUDGET = 1048576
# Maximum number of links followed when resolving an entry, matches the
# SYMLOOP_MAX of the reference implementation
SYMLINK_MAX_DEPTH = 40
# Hash of an empty content, an empty file must be the only entry with it
EMPTY_SHA256 = hashlib.sha256(b'').hexdigest()


class UnpackResult(msgspec.Struct):
    """
    Statistics of an extraction, returned by ``unpack()`` and
    ``AsarArchive.extract_all()``.

    ``region_count`` and ``seek_count`` only describe the sequential scan of
    ``unpack()``, the in memory reader writes its entries directly and always
    reports 0 for both.

    Attributes:
        dest (str): Target directory
        file_count (int): Number of file entries extracted
        dir_count (int): Number of directory entries created
        unpacked_count (int): Number of files read from ``<archive>.unpacked/``
        link_count (int): Number of link entries created
        data_size (int): Number of bytes read from the data area
        region_count (int): Number of regions the data area was split into
        seek_count (int): Number of seeks the scan needed, 0 for a well formed
            archive (the entries are stored back to back)
    """
    dest: str
    file_count: int
    dir_count: int
    unpacked_count: int
    link_count: int
    data_size: int
    region_count: int
    seek_count: int


class _Member:
    """
    One entry inside a region of the data area.
    """
    __slots__ = ('start', 'end', 'path', 'target', 'verifier', 'writer')

    def __init__(self, start, end, path, target, verifier=None):
        self.start = start
        self.end = end
        self.path = path
        self.target = target
        self.verifier = verifier
        self.writer = None


class _Region:
    """
    A part of the data area that is read in one go.
    """
    __slots__ = ('start', 'end', 'members')

    def __init__(self, start, end, members):
        self.start = start
        self.end = end
        self.members = members


def check_header_size(header_size, archive_size):
    """
    Check the header length of an archive against its size.

    Args:
        header_size (int): Header pickle length, from the frame
        archive_size (int): Total byte length of the archive

    Raises:
        AsarFormatError: If the header can not be inside the archive
    """
    if header_size < 8:
        raise AsarFormatError(
            f'Header size {header_size} is smaller than the 8 bytes a header pickle needs'
        )
    if header_size > MAX_HEADER_SIZE:
        raise AsarFormatError(
            f'Header size {header_size} exceeds the {MAX_HEADER_SIZE} bytes limit'
        )
    if header_size + 8 > archive_size:
        raise AsarFormatError(
            f'Header size {header_size} exceeds the archive size of {archive_size} bytes'
        )


def read_exact(file, size):
    """
    Read exactly a number of bytes.

    Args:
        file (io.IOBase): Binary file object
        size (int): Byte count to read

    Returns:
        bytes: The bytes read

    Raises:
        AsarFormatError: If the file ends before the requested size
    """
    data = file.read(size)
    if len(data) == size:
        return data
    chunks = [data]
    remaining = size - len(data)
    while remaining > 0:
        chunk = file.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    if remaining:
        raise AsarFormatError(
            f'Archive is truncated, expected {size} bytes but got {size - remaining}'
        )
    return b''.join(chunks)


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


def build_regions(members):
    """
    Merge the entries of the data area into regions.

    Entries that overlap or touch each other are merged, so that the content of
    duplicated entries (4.3.0 writes identical content once), of entries that
    share a prefix and of nested entries is read once. Entries of a real archive
    are stored back to back, so the whole data area is usually a single region.

    Args:
        members (list): ``_Member`` list, sorted by start offset

    Returns:
        list: ``_Region`` list, in offset order
    """
    regions = []
    for member in members:
        if regions and member.start <= regions[-1].end:
            region = regions[-1]
            if member.end > region.end:
                region.end = member.end
            region.members.append(member)
        else:
            regions.append(_Region(member.start, member.end, [member]))
    return regions


def finish_member(member):
    """
    Close the file of a member and check its content.

    Args:
        member (_Member): Member whose content was fully written

    Raises:
        AsarError: If the content does not match the header
    """
    member.writer.close()
    member.writer = None
    if member.verifier is not None:
        member.verifier.check()


def write_member(member, content):
    """
    Write a member from a buffer.

    Args:
        member (_Member): Member to write
        content (memoryview): Whole content of the member
    """
    member.writer = AtomicChunkWriter(member.target)
    if len(content):
        member.writer.write(content)
        if member.verifier is not None:
            member.verifier.update(content)
    finish_member(member)


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
    expected = info.integrity.get('hash') if info.integrity else None
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
    An asar archive: the flat entry table, plus the archive content when it was
    read from a file.

    Attributes:
        files (dict): ``{path: AsarFileInfo}``, the insertion order is the order
            of the entries in the archive, which is also the order in which the
            offsets are allocated when the archive is written
        data (memoryview): Content of the archive that was read, None for an
            archive that was built from local files or written already
        archive_path (str): Path of the archive, None when it was never read
            from or written to a file
        header_size (int): Header pickle length, the data area starts at 8 + it
        data_offset (int): Offset of the first content byte
    """

    def __init__(self):
        self.files = {}
        self.data = None
        self.archive_path = None
        self.header_size = 0
        self.data_offset = 0
        # {path: local file path or content} of the entries that have a source
        # of their own, the other entries are read from `data`
        self._sources = {}

    def __repr__(self):
        return f'AsarArchive(entries={len(self.files)}, path={self.archive_path!r})'

    @property
    def unpacked_path(self):
        """
        Directory of the unpacked content of this archive.

        Returns:
            str: Path of ``<archive>.unpacked``, None when there is no archive file
        """
        return f'{self.archive_path}.unpacked' if self.archive_path else None

    @property
    def data_area(self):
        """
        Data area of the loaded archive, where the entry offsets point.

        Returns:
            memoryview: View starting at the first content byte, None when no
                archive is loaded
        """
        if self.data is None:
            return None
        if not self.data_offset:
            return self.data
        return self.data[self.data_offset:]

    @classmethod
    def read_asar(cls, file, max_size=None):
        """
        Load a whole archive into memory and parse it.

        Args:
            file (str): Path of the archive
            max_size (int): Refuse an archive larger than this, in bytes, None to
                accept any size. An archive may come from the network, so the
                update flow should pass the expected size limit.

        Returns:
            AsarArchive: Archive with its entries and its content loaded

        Raises:
            AsarFormatError: If the file is not a valid asar archive
            AsarUnsupportedError: If the archive is larger than `max_size`
        """
        with open(file, 'rb') as f:
            data = f.read() if max_size is None else f.read(max_size + 1)
        if max_size is not None and len(data) > max_size:
            raise AsarUnsupportedError(
                f'Archive is larger than the {max_size} bytes limit of this call'
            )
        archive = cls()
        archive.archive_path = str(file)
        archive.data = memoryview(data)
        header_size = parse_size_pickle(archive.data[:8])
        check_header_size(header_size, len(data))
        archive.header_size = header_size
        archive.data_offset = 8 + header_size
        header = decode_header(parse_header_pickle(archive.data[8:archive.data_offset]))
        archive.files = read_entries(header, len(data), archive.data_offset)
        return archive

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
        try:
            return self.files[name]
        except KeyError:
            raise AsarEntryNotFoundError(f'Entry "{name}" does not exist in the archive')

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
                prefix = '/'.join(parts[:index + 1])
                info = self.files.get(prefix)
                if info is None or info.kind != KIND_LINK:
                    continue
                name = f'{self._follow(prefix, info, visited)}/{"/".join(parts[index + 1:])}'
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

    # ------------------------------------------------------------------ build

    def _ensure_parents(self, arc_path):
        """
        Make sure every parent directory of a path is an entry.

        A directory is added before its content, so the entry order of the table
        still decides the order of the header and of the content.

        Args:
            arc_path (str): Archive path of an entry

        Raises:
            AsarPathError: If a parent path is already used by a file
        """
        parts = arc_path.split('/')[:-1]
        for index in range(1, len(parts) + 1):
            parent = '/'.join(parts[:index])
            info = self.files.get(parent)
            if info is None:
                self.files[parent] = AsarFileInfo(path=parent, kind=KIND_DIR)
            elif info.kind != KIND_DIR:
                raise AsarPathError(f'Archive path "{parent}" is already a file')

    def add_file(self, path=None, arc_path=None, data=None, unpacked=False):
        """
        Add a single file to the archive.

        The file is not read yet, its content is read when the archive is
        written, so a large file never sits in memory.

        Args:
            path (str): Local file path
            arc_path (str): Path inside the archive, defaults to the file name of
                `path`
            data (bytes): Content, added instead of reading `path`, for a file
                that does not exist on disk (a generated package.json and the like)
            unpacked (bool): Store the content next to the archive instead of
                inside it

        Returns:
            AsarFileInfo: The added entry

        Raises:
            ValueError: If neither `path` nor `data` is given
            AsarPathError: If `arc_path` is not a valid archive path
        """
        if path is None and data is None:
            raise ValueError('add_file() needs a path or a content')
        if arc_path is None:
            if path is None:
                raise ValueError('add_file() needs an arc_path when there is no path')
            arc_path = os.path.basename(path)
        arc_path = check_archive_path(arc_path)
        self._ensure_parents(arc_path)
        info = self.files.get(arc_path)
        if info is not None and info.kind != KIND_FILE:
            raise AsarPathError(f'Archive path "{arc_path}" is already a directory')
        if info is None:
            info = AsarFileInfo(path=arc_path, kind=KIND_FILE)
            self.files[arc_path] = info
        # The entry keeps its position, only its content changes
        info.size = None
        info.offset = None
        info.integrity = None
        info.unpacked = bool(unpacked)
        info.executable = False
        info.link = None
        self._sources[arc_path] = data if data is not None else str(path)
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
            if kind == KIND_DIR:
                if self.files.get(arc_path) is not None and self.files[arc_path].kind != KIND_DIR:
                    raise AsarPathError(f'Archive path "{arc_path}" is already a file')
                info = AsarFileInfo(path=arc_path, kind=KIND_DIR, unpacked=unpacked)
                self.files[arc_path] = info
                continue
            self.add_file(path=local_path, arc_path=arc_path, unpacked=unpacked)
        return len(entries)

    def write_asar(self, dest, integrity=True):
        """
        Write the archive to a file.

        The content of every entry is read from its source, its real byte count
        and its hashes are calculated, then the content is written and checked
        against the first pass, so a source that changed in between fails the
        pack instead of producing an archive that lies about its content.

        Args:
            dest (str): Target archive path
            integrity (bool): Write the per file SHA256 integrity of the reference
                implementation, disable it to save the hashing time

        Returns:
            PackResult: Statistics of the written archive

        Raises:
            AsarError: If an entry has no content source, or if a source changed
                while packing
            AsarUnsupportedError: If an entry is larger than the format allows
        """
        result = pack_archive(
            self.files,
            self._sources,
            dest,
            data_area=self.data_area,
            integrity=integrity,
            unpacked_root=self.unpacked_path,
        )
        self.archive_path = str(dest)
        self.header_size = result.header_size
        self.data_offset = 8 + result.header_size
        # The entries now live in the written file, the archive that was read
        # before is neither their source nor an up to date view of them
        for path, info in self.files.items():
            if path in self._sources or info.kind != KIND_FILE or info.unpacked:
                continue
            self._sources[path] = FileRange(dest, self.data_offset + info.offset, info.size)
        self.data = None
        return result

    # ---------------------------------------------------------------- reading

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
        for chunk in iter_entry_chunks(
            name, info, self._sources.get(name), self.data_area, self.unpacked_path,
            chunk_size=chunk_size,
        ):
            yield chunk

    def read_file(self, name):
        """
        Read the content of one entry.

        Args:
            name (str): Archive path, links are followed

        Returns:
            memoryview: Content, a read only view when it is already in memory

        Raises:
            AsarError: If the entry is not a file, or if its content is missing
        """
        name, info = self.resolve(name)
        if info.kind != KIND_FILE:
            raise AsarError(f'Entry "{name}" is a directory')
        source = self._sources.get(name)
        if source is not None and not isinstance(source, (str, FileRange)):
            # Content that was added to the archive, no copy needed
            return memoryview(source)
        if source is None:
            if info.unpacked:
                return memoryview(file_read_bytes(self._unpacked_target(name)))
            if self.data_area is not None:
                return self.data_area[info.offset:info.offset + info.size]
        return memoryview(b''.join(self.iter_content(name)))

    def _unpacked_target(self, name):
        """
        Path of the unpacked content of an entry.

        Args:
            name (str): Archive path

        Returns:
            str: Path inside ``<archive>.unpacked/``, validated
        """
        root = self.unpacked_path
        if root is None:
            raise AsarError(f'Entry "{name}" is unpacked but the archive has no file path')
        return check_target_path(root, name)

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
        writer = AtomicChunkWriter(dest)
        try:
            for chunk in self.iter_content(name):
                writer.write(chunk)
            writer.close()
        except BaseException:
            writer.abort()
            raise

    def extract_all(self, dest):
        """
        Extract the whole archive to a directory.

        Directories are created first, then the content, then the links, so that
        a link target always exists when the link is created. On Windows a link
        to a file is materialized as a copy, a link to a directory is not
        supported because Windows needs elevation to create a symlink.

        Args:
            dest (str): Target directory

        Returns:
            UnpackResult: Statistics of the extraction

        Raises:
            AsarPathError: If an entry would escape the target directory
            AsarUnsupportedError: If a directory link is extracted on Windows
        """
        os.makedirs(dest, exist_ok=True)
        dir_count = 0
        file_count = 0
        link_count = 0
        for path, info in self.files.items():
            if info.kind != KIND_DIR:
                continue
            os.makedirs(check_target_path(dest, path), exist_ok=True)
            dir_count += 1
        for path, info in self.files.items():
            if info.kind != KIND_FILE:
                continue
            self.extract_file(path, check_target_path(dest, path))
            file_count += 1
        for path, info in self.files.items():
            if info.kind != KIND_LINK:
                continue
            create_link(self, path, check_target_path(dest, path))
            link_count += 1
        return UnpackResult(
            dest=dest,
            file_count=file_count,
            dir_count=dir_count,
            unpacked_count=sum(1 for info in self.files.values() if info.kind == KIND_FILE and info.unpacked),
            link_count=link_count,
            data_size=sum(info.size for info in self.files.values() if info.kind == KIND_FILE),
            region_count=0,
            seek_count=0,
        )

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
        for path, info in self.files.items():
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
        for path, info in self.files.items():
            if info.kind != KIND_FILE:
                continue
            expected = info.integrity.get('hash') if info.integrity else None
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


def archive_of_entries(archive_path, files, header_size, data_offset):
    """
    Build an archive object over the entries of an archive that is on disk.

    The content of the packed entries is read from the archive file itself, so
    that a helper of the class (resolving a link, extracting one entry) can be
    reused without loading the whole archive.

    Args:
        archive_path (str): Path of the archive
        files (dict): ``{path: AsarFileInfo}``
        header_size (int): Header pickle length
        data_offset (int): Offset of the data area

    Returns:
        AsarArchive: Archive whose content is read from the file
    """
    archive = AsarArchive()
    archive.files = files
    archive.archive_path = archive_path
    archive.header_size = header_size
    archive.data_offset = data_offset
    for path, info in files.items():
        if info.kind == KIND_FILE and not info.unpacked:
            archive._sources[path] = FileRange(
                archive_path, data_offset + info.offset, info.size,
            )
    return archive


def create_link(archive, name, target):
    """
    Create the link of an entry.

    Args:
        archive (AsarArchive): Archive holding the entry
        name (str): Archive path of the link
        target (str): Target path of the link on disk

    Raises:
        AsarUnsupportedError: If a directory link is extracted on Windows
    """
    if os.name != 'nt':
        # A relative symlink, the link and its target are both inside the tree
        os.makedirs(os.path.dirname(target), exist_ok=True)
        os.symlink(archive.files[name].link, target)
        return
    # Windows needs elevation to create a symbolic link, materialize the content
    _, info = archive.resolve(name)
    if info.kind == KIND_DIR:
        raise AsarUnsupportedError(
            f'Link "{name}" points to a directory, which can not be extracted on Windows'
        )
    archive.extract_file(name, target)


def unpack(archive, dest, region_budget=REGION_BUDGET, chunk_size=CHUNK_SIZE, verify=False):
    """
    Extract a whole archive to a directory with a single sequential scan.

    The entries are merged into regions of the data area which are processed in
    offset order, so the data area is read once from start to end: no seek, no
    content kept between regions, and the memory usage is bounded by
    `region_budget` (or by `chunk_size` for a larger region). Entries that share
    their content, a prefix or a whole range share the same read.

    Args:
        archive (str): Path of the archive
        dest (str): Target directory
        region_budget (int): Regions up to this size are read in one piece, 0
            streams everything
        chunk_size (int): Read chunk size of a streamed region
        verify (bool): Compare the content of every entry with the integrity of
            the header while extracting, it costs no extra I/O

    Returns:
        UnpackResult: Statistics of the extraction

    Raises:
        AsarFormatError: If the archive is malformed, or if a content hash does
            not match
        AsarPathError: If an entry would escape the target directory
    """
    archive = str(archive)
    dest = str(dest)
    f = atomic_open(archive, 'rb', buffering=0)
    try:
        archive_size = f.seek(0, 2)
        f.seek(0)
        header_size = parse_size_pickle(read_exact(f, 8))
        check_header_size(header_size, archive_size)
        data_offset = 8 + header_size
        header = decode_header(parse_header_pickle(read_exact(f, header_size)))
        files = read_entries(header, archive_size, data_offset)

        os.makedirs(dest, exist_ok=True)
        dir_count = 0
        for path, info in files.items():
            if info.kind != KIND_DIR:
                continue
            os.makedirs(check_target_path(dest, path), exist_ok=True)
            dir_count += 1

        # Empty and unpacked entries hold no byte of the data area, they are
        # written before the scan and never enter a region
        scan = []
        file_count = 0
        unpacked_count = 0
        for path, info in files.items():
            if info.kind != KIND_FILE:
                continue
            target = check_target_path(dest, path)
            file_count += 1
            if info.unpacked:
                entry_hash(path, info, verify)
                source = os.path.join(f'{archive}.unpacked', *path.split('/'))
                write_entry(target, path, info, source, verify=verify, error=AsarFormatError)
                unpacked_count += 1
                continue
            if info.size == 0:
                check_empty_content(path, info, verify)
                file_write(target, b'')
                continue
            expected = entry_hash(path, info, verify)
            verifier = None
            if expected is not None:
                verifier = ContentVerifier(path, info.size, expected, error=AsarFormatError)
            scan.append(_Member(info.offset, info.offset + info.size, path, target, verifier))

        # The offsets of a well formed archive are back to back and sorted, the
        # sort also keeps a hand made or deduplicated archive in offset order
        scan.sort(key=lambda member: (member.start, -member.end, member.path))
        regions = build_regions(scan)

        position = data_offset
        seek_count = 0
        data_size = 0
        for region in regions:
            length = region.end - region.start
            data_size += length
            start = data_offset + region.start
            if start != position:
                # Only entries that are not back to back need this, a real
                # archive never does
                f.seek(start)
                position = start
                seek_count += 1
            if length <= region_budget:
                # The whole region fits in the budget, read it once and slice it
                buffer = read_exact(f, length)
                position += length
                view = memoryview(buffer)
                for member in region.members:
                    write_member(member, view[member.start - region.start:member.end - region.start])
                continue
            # Larger than the budget, stream it and feed every member that
            # overlaps the current chunk
            members = region.members
            active = []
            index = 0
            offset_in_region = 0
            while offset_in_region < length:
                take = min(chunk_size, length - offset_in_region)
                chunk = read_exact(f, take)
                position += take
                chunk_start = region.start + offset_in_region
                chunk_end = chunk_start + take
                view = memoryview(chunk)
                while index < len(members) and members[index].start < chunk_end:
                    member = members[index]
                    member.writer = AtomicChunkWriter(member.target)
                    active.append(member)
                    index += 1
                remaining = []
                for member in active:
                    piece_start = max(chunk_start, member.start)
                    piece_end = min(chunk_end, member.end)
                    if piece_start < piece_end:
                        piece = view[piece_start - chunk_start:piece_end - chunk_start]
                        member.writer.write(piece)
                        if member.verifier is not None:
                            member.verifier.update(piece)
                    if member.end <= chunk_end:
                        finish_member(member)
                    else:
                        remaining.append(member)
                active = remaining
                offset_in_region += take

        link_count = 0
        if any(info.kind == KIND_LINK for info in files.values()):
            # Links are created last, a real archive has few of them
            archive_object = archive_of_entries(archive, files, header_size, data_offset)
            for path, info in files.items():
                if info.kind != KIND_LINK:
                    continue
                target = check_target_path(dest, path)
                create_link(archive_object, path, target)
                link_count += 1
    finally:
        f.close()
    return UnpackResult(
        dest=dest,
        file_count=file_count,
        dir_count=dir_count,
        unpacked_count=unpacked_count,
        link_count=link_count,
        data_size=data_size,
        region_count=len(regions),
        seek_count=seek_count,
    )


def pack_sha256(path):
    """
    Calculate the SHA256 of a file, for the update flow.

    Args:
        path (str): File path, the file is streamed

    Returns:
        str: SHA256 hex digest
    """
    return hash_file(path)
