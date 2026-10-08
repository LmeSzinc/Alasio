"""
Build the full pack of the install tree of a wheel.

The dist channel of the pack server packs python distributions: a version of a
distribution is a wheel, and the files a client ends up with are the files an
installation writes into site-packages, not the members of the wheel archive.
PackWheel reads a wheel and encodes that install tree as a full pack:

    wheel ──▶ install tree ──▶ full pack (index + data)
                               index_pack: the ledger of the version

The tree is built by the rules of SimplePip, the installer of the client side:
the .dist-info directory is found the same way, the .data/purelib and
.data/platlib members install below site-packages with the category prefix
stripped, the executable bit of a member is kept, and the RECORD is the one of
an installation (RecordManager: the installed paths, no pyc rows, the RECORD
itself without a hash) with INSTALLER written as "pip". A wheel that ships
bytecode (__pycache__/**, *.pyc) or a .data category that installs outside of
site-packages (.data/scripts, .data/headers, .data/data) is not a pack target
and raises ValueError: the footprint of a version is the source level install
(a pyc embeds the machine of the install and changes on every recompile, and
an index that recorded one could never stay consistent with the disk), and a
pack rooted at site-packages cannot carry the files of the other roots of the
install scheme. Every install path (the markers included) passes the cross
platform rules of the pack format (PackCache.validate_record_path: a relative
path, no traversal, no name or length that a platform rejects, see
_validate_path), so a record of a wheel is never a path that cannot be
unpacked somewhere. The
pure python gate of the channel (no compiled extension, pure wheel tags) is
the business of the pack server, not of this encoder.

The tree is materialized in memory (a wheel is a zip of some MB): nothing is
written to the disk, there is no staging directory to clean up, and the build
of a version is a pure function of the wheel bytes. The records are the ones
of the git pipeline (PackFull) with the dist policy:

- eol is 2 (binary) for every file: the footprint is the installed bytes, a
  line ending rule of a repository must never rewrite a third party file
- a folder that holds a .py file and ships no ``__init__.py`` gets a D
  (deleted) record for it: the folder is a namespace package of the
  distribution, python must not import an ``__init__.py`` that appeared from
  anywhere else (the same rule the git source applies to a project tree, see
  PackFull.fileinfo)
- no ``.pack/history.pack`` extra: the commit history is a concept of the git
  source and no side of the dist flow reads it
- the contents are encoded with the raw / lzma rules of the index of a full
  pack, so the index pack of a wheel version is the same kind of artifact as
  the index pack of a commit

The encodings are cached by the content sha1 in WHEEL_CACHE, the cache of the
wheel pipeline: a content an earlier build of the run carried is encoded once,
every build that follows takes the bytes from the cache. The validated install
paths are cached there too (PackCache.validate_record_path): the paths of a
wheel pass the rules once, the materialization and the assembly of a later
build look them up. The cache is an instance of its own, separate from
``_pack_cache.PACK_CACHE``: the two pipelines key their entries by different
identities (a wheel record by the content sha1, a git record by the git blob
sha1), and one pack server run builds both kinds of packs in one process.

Usage:
    from alasio.deploy_dev.pack.pack_wheel import PackWheel
    from alasio.ext.path.atomic import atomic_write, atomic_write_stream

    pack = PackWheel('wheel/httpx/0.28.1/httpx-0.28.1-py3-none-any.whl')
    atomic_write_stream('packdep/httpx/0.28.1/full.pack', pack.iter_pack_data())
    atomic_write('packdep/httpx/0.28.1/index.pack', pack.index_pack)
"""

import zipfile
from functools import partial
from hashlib import sha1

from msgspec import Struct

from alasio.backport import removeprefix, removesuffix
from alasio.deploy.pack.pack_model import FileInfo
from alasio.deploy_dev.pack._pack_cache import PACK_POOL, PackCache, PlainCache
from alasio.deploy_dev.pack.encode_base import PACK_AREA, PACK_AREA_DIR, PackEncodeBase
from alasio.deploy_dev.pack.pack_full import PackFull, _dfs_path_key, apply_encoding
from alasio.deploy_dev.simple_pip.simple_pip import DATA_CATEGORIES, SimplePip, zip_item_is_executable
from alasio.deploy_dev.simple_pip.whl_record import RecordManager
from alasio.ext.cache import cached_property
from alasio.ext.path import PathStr

# The cache of the wheel pipeline: the same PackCache the git pipeline uses
# (_pack_cache.PACK_CACHE), but an instance of its own. The tables of the two
# pipelines are keyed by different identities -- a wheel record by the content
# sha1, a git record by the git blob sha1, a path by the path itself -- and a
# pack server run builds the packs of the project tree (git) and of the
# dependencies (wheel) in one process: separate instances keep the tables, the
# per key locks and the statistics of the two pipelines apart. Every PackWheel
# / PackWheelUpdate of the process shares this one, so the versions of a run
# reuse the encodings of the contents they share; a caller that wants a run
# scoped cache (e.g. the pack server of one run) passes its own instance to the
# constructors.
WHEEL_CACHE = PackCache()


class WheelFile(Struct):
    """
    One file of the install tree of a wheel.

    Attributes:
        content (bytes): Content of the file, the bytes an installation
            writes
        mode (int): Executable bit of the file, 1 for a member with the
            executable permission (the record mode 755 of the pack), 0 for
            the others (644)
        sha1 (bytes): sha1 digest of the content, the identity the records
            carry and the cache of the pipeline is keyed by
    """

    content: bytes
    mode: int
    sha1: bytes


def _parse_metadata_headers(content, wheel):
    """
    Parse the headers of the METADATA of a wheel.

    The file is an email style header block (PEP 566): one ``Name: value``
    per line, the block ends at the first empty line, and a line that starts
    with a space or a tab is a folded continuation of the header before it.
    The names of the headers are lowercased, the values are stripped.

    Args:
        content (bytes): Content of the METADATA file
        wheel (str): Path of the wheel, for the error message

    Returns:
        dict[str, str]: {lowercase header name: value}, {} for a file
            without a header

    Raises:
        ValueError: If the content is not UTF-8
    """
    try:
        text = content.decode('utf-8')
    except UnicodeDecodeError as e:
        raise ValueError(f'Invalid wheel, the METADATA is not UTF-8: "{wheel}", {e}') from e

    headers = {}
    for line in text.splitlines():
        if not line.strip():
            # the header block ends at the first empty line
            break
        if line[0] in ' \t':
            # a folded continuation of the header before
            continue
        name, sep, value = line.partition(':')
        if not sep:
            continue
        headers[name.strip().lower()] = value.strip()
    return headers


class PackWheel(PackEncodeBase):
    """
    Full pack of the install tree of a wheel, see the module docstring.

    Attributes:
        wheel (PathStr): Path of the .whl file being packed
        dist_info (str): Name of the .dist-info directory of the wheel,
            e.g. 'httpx-0.28.1.dist-info'
        name (str): Name of the distribution, the Name header of the
            METADATA (the folder name when the header is missing); not
            normalized, the dist-key of the channel is the PEP 503
            normalized form of it
        cache (PackCache): Cache of the pack (the encodings and the validated
            paths), WHEEL_CACHE by default
    """

    def __init__(self, wheel, pack_version=None, cache=None):
        """
        Args:
            wheel (str | PathStr): Path of the .whl file to pack
            pack_version (int, optional): Pack format version to encode with,
                0~255. Defaults to None, the current version of
                PackEncodeBase; an already published pack must be rebuilt with
                the format version it was encoded with, see PackWheelUpdate
            cache (PackCache, optional): Cache of the pack (the encodings and
                the validated paths). Defaults to None, WHEEL_CACHE of this
                module

        The install tree of the wheel is materialized here (see tree): a
        wheel that is not a pack target fails at construction, with the name
        of its member, instead of at the assembly of the pack.

        Raises:
            ValueError: If the wheel is not a zip file of a distribution, its
                METADATA carries no Version, or a member is not part of a
                footprint (see the module docstring)
            FileNotFoundError: If the wheel does not exist
            zipfile.BadZipFile: If the wheel is not a zip file
        """
        super().__init__()
        self.wheel = PathStr.new(wheel)
        if pack_version is not None:
            self.pack_version = pack_version
        # the cache of the wheel pipeline replaces the process cache bound by
        # PackEncodeBase.__init__: the encodings and the validated paths of the
        # wheels live on an instance of their own, see the module docstring
        self.cache = WHEEL_CACHE if cache is None else cache
        # the identity of the version, read from the wheel now: a caller that
        # read the version of the pack reads it before anything is encoded
        self.dist_info, self.name, self.current_version = self._read_metadata()
        _ = self.tree

    def _read_metadata(self):
        """
        Read the identity of the distribution from the wheel.

        The .dist-info directory is found like the installer finds it
        (SimplePip._find_dist_info: exactly one, with a METADATA), the name
        of the distribution is the Name header of the METADATA and the
        version of the pack is its Version header (the display version of
        the distribution, PEP 440): a client reads the same value with
        importlib.metadata and compares it against its ledger, and the
        channel names the products of a version by it.

        Returns:
            tuple[str, str, str]: (name of the .dist-info directory, name of
                the distribution, display version)

        Raises:
            ValueError: If the wheel holds no or several .dist-info
                directories, no METADATA, or a METADATA without a Version
        """
        with zipfile.ZipFile(self.wheel, 'r') as zf:
            dist_info = SimplePip._find_dist_info(self.wheel, zf)
            content = zf.read(f'{dist_info}/METADATA')
        headers = _parse_metadata_headers(content, self.wheel)

        version = headers.get('version')
        if not version:
            raise ValueError(f'Invalid wheel, no Version in the METADATA: "{self.wheel}"')
        # the folder of a .dist-info is "{name}-{version}", both fields may
        # carry separators the name escapes: the Name header is authoritative
        folder, _, _ = removesuffix(dist_info, '.dist-info').rpartition('-')
        return dist_info, headers.get('name') or folder, version

    @cached_property
    def tree(self) -> "dict[str, WheelFile]":
        """
        The install tree of the wheel, in DFS path order.

        The members are installed by the rules of SimplePip, see
        _member_path, the RECORD of the wheel is not installed (an
        installation writes its own) and INSTALLER is written with the
        content of an installation, "pip". The RECORD of the installation
        lists every file of the tree with its checksum, RecordManager
        writes it (LF, sorted rows, the RECORD row itself without a hash).

        The paths are validated while the tree is built, before anything is
        encoded: the pack encoder would reject an unsafe path at the
        assembly of the pack, this fails with the name of the member
        instead. The order is the DFS order of the pack records (files of a
        folder before its subfolders), the order of the copy detection and
        of the records of the full pack.

        Returns:
            dict[str, WheelFile]: {path relative to site-packages: file}

        Raises:
            ValueError: If the wheel is invalid (no or several .dist-info
                directories, no METADATA), a member is not installable or is
                not part of a footprint (see the module docstring), or two
                members install to the same path
        """
        tree = {}
        with zipfile.ZipFile(self.wheel, 'r') as zf:
            dist_info = self.dist_info
            data_folder = f'{removesuffix(dist_info, ".dist-info")}.data'
            for member in zf.infolist():
                if member.is_dir():
                    continue
                rel_path = member.filename
                if rel_path == f'{dist_info}/RECORD':
                    # the RECORD of the wheel describes the archive, not the
                    # installation: the file of the tree is written below
                    continue
                path = self._member_path(rel_path, data_folder)
                if path is None:
                    continue
                self._check_member(path, rel_path)
                if path in tree:
                    raise ValueError(
                        f'Invalid wheel, two members install to the same path "{path}": '
                        f'"{self.wheel}"')
                content = zf.read(member)
                tree[path] = WheelFile(
                    content=content,
                    mode=1 if zip_item_is_executable(member) else 0,
                    sha1=sha1(content).digest(),
                )

        # INSTALLER is written by the installation, not taken from the
        # archive: an installation overwrites a member of the same name
        installer_path = f'{dist_info}/INSTALLER'
        installer_content = b'pip\n'
        tree[installer_path] = WheelFile(
            content=installer_content, mode=0, sha1=sha1(installer_content).digest())

        record = RecordManager()
        for path, file in tree.items():
            record.add_content(path, file.content)
        # the RECORD itself is listed without a hash and a size, pip does the
        # same and derives its rows from the other ones
        record.add_content(f'{dist_info}/RECORD', None)
        record_content = record.dump_bytes()
        tree[f'{dist_info}/RECORD'] = WheelFile(
            content=record_content, mode=0, sha1=sha1(record_content).digest())

        return {path: tree[path] for path in sorted(tree, key=_dfs_path_key)}

    def _member_path(self, rel_path, data_folder):
        """
        Install path of a wheel member, relative to site-packages.

        The mapping is the one of SimplePip._member_target, the installer
        that runs on a device: a member outside of .data installs to its own
        path, a member of .data/purelib or .data/platlib installs below
        site-packages with the category prefix stripped, and the other
        categories of .data raise (see the module docstring: they install
        outside of site-packages).

        Args:
            rel_path (str): Name of the member in the wheel
            data_folder (str): Name of the .data directory of the wheel,
                "{name}-{version}.data"

        Returns:
            str | None: Path of the member below site-packages, None for a
                member the installer does not install (a file directly under
                .data: the categories are folders of their own)

        Raises:
            ValueError: If the member holds an unknown .data category, or a
                category whose files install outside of site-packages
        """
        if not rel_path.startswith(f'{data_folder}/'):
            return rel_path
        content_path = removeprefix(rel_path, f'{data_folder}/')
        category, sep, subpath = content_path.partition('/')
        if not sep:
            # a file directly under .data, the categories are folders of
            # their own, SimplePip does not install it either
            return None
        if category == 'purelib' or category == 'platlib':
            return subpath
        if category in DATA_CATEGORIES:
            raise ValueError(
                f'Invalid wheel for a pack, the member "{rel_path}" installs to the '
                f'"{category}" directory of the environment, outside of site-packages; '
                f'install the distribution with pip instead: "{self.wheel}"')
        raise ValueError(
            f'Invalid wheel, unsupported .data directory "{category}", '
            f'expected one of {", ".join(DATA_CATEGORIES)}: "{self.wheel}"')

    def _check_member(self, path, rel_path):
        """
        Check that a member can be a file of the footprint.

        Args:
            path (str): Install path of the member, relative to site-packages
            rel_path (str): Name of the member in the wheel, for the messages

        Raises:
            ValueError: If the path is not a valid path of the pack format
                (see _validate_path), or is a bytecode file
        """
        if '\\' in path:
            # a wheel path is posix, a backslash of a member name would
            # install to a path of its own on one platform and to a folder
            # separator on another (validate_filepath normalizes the
            # separator away and would not see it)
            raise ValueError(
                f'Invalid wheel, the member "{rel_path}" carries a backslash: "{self.wheel}"')
        self._validate_path(path, f'the member "{rel_path}"')
        if path == PACK_AREA_DIR or path.startswith(PACK_AREA):
            # a footprint carries no pack area path at all: the client maps
            # every .pack path into the ledger folder of its target, a file
            # there would alias the ledger, the workspace or another pack
            # file of the target
            raise ValueError(
                f'Invalid wheel, the member "{rel_path}" lies in the pack area '
                f'"{PACK_AREA_DIR}" of the pack format: "{self.wheel}"')
        if path.endswith('.pyc') or '__pycache__' in path.split('/'):
            # the footprint of a version is the source level install: a pyc
            # embeds the machine of the install and changes on every
            # recompile, an index that recorded one could never stay
            # consistent with the disk, see the module docstring
            raise ValueError(
                f'Invalid wheel for a pack, the member "{rel_path}" is a bytecode file; '
                f'a distribution that ships bytecode is not a pack target: "{self.wheel}"')

    def _validate_path(self, path, what):
        """
        Validate an install path of the pack, through the cache of the encoder.

        The rules are the cross platform ones of the pack format
        (PackCache.validate_record_path: validate_filepath and
        validate_pack_area), the same rules the client decoder applies to every
        record of a pack, so a path of a wheel never reaches a device in a form
        some platform cannot unpack. The verdict is cached in the cache of the
        encoder (self.cache, WHEEL_CACHE by default): a path the wheel
        validated before (an earlier version, an earlier build of the same
        wheel, a file the same wheel carries twice) costs a set lookup here,
        and the paths validated here are a set lookup again when the pack
        encoder walks the records at the assembly
        (PackEncodeBase.iter_index_data).

        Args:
            path (str): Install path of a record, relative to site-packages
            what (str): What the path belongs to, for the error message,
                e.g. 'the member "pkg/mod.py"' or 'the marker of "pkg/mod.py"'

        Raises:
            ValueError: If the path violates the rules of the pack format
        """
        try:
            self.cache.validate_record_path(path)
        except ValueError as e:
            raise ValueError(f'Invalid wheel, {what} installs to "{path}": {e}') from e

    @cached_property
    def fileinfo(self) -> "dict[str, FileInfo]":
        """
        Records of the install tree, in the encoded order.

        Every file of the tree is an A (added) record with the content of the
        wheel; the records that share a content are converted to C (copied)
        records by the copy detection of the full pack, which references the
        earlier record instead of storing the bytes again. The contents are
        encoded with the rules of the index, see _populate_data.

        A folder that holds a .py file and ships no ``__init__.py`` gets a D
        (deleted) record for it (``<folder>/__init__.py``): the folder is a
        namespace package of the distribution, the record tells the client
        that an ``__init__.py`` of that folder must not exist, so a stray
        file that appeared from anywhere else (an older version, another
        distribution, a hand made one) is removed by an unpack and repaired
        by a validation instead of being imported by python as a package that
        the distribution does not have. The rule is the one of the git source
        (PackFull.fileinfo), a folder that ships an ``__init__.py`` is a
        package and is left alone; a folder of no .py file is not claimed,
        no import of the distribution runs through it.

        Returns:
            dict[str, FileInfo]: {filepath: FileInfo}
        """
        out = {}
        for path, file in self.tree.items():
            # the footprint is binary: eol 2 decodes and checks the file as
            # it is written, no line ending rule applies to a distribution
            out[path] = FileInfo(
                path=path, mode=file.mode, eol=2,
                size=len(file.content), sha1=file.sha1)
        # if folder does not have __init__.py, add __init__.py and mark as
        # deleted: this keeps python from importing unknown code, because
        # python will auto import __init__.py of a folder it imports a
        # module from
        for path in self.tree:
            if not path.endswith('.py'):
                continue
            folder, sep, _ = path.rpartition('/')
            while sep:
                init = f'{folder}/__init__.py'
                if init not in out:
                    # the marker is a record of the pack like any other, its
                    # path passes the same cross platform rules
                    self._validate_path(init, f'the marker of "{path}"')
                    out[init] = PackFull._new_deleted(init)
                folder, sep, _ = folder.rpartition('/')
        # sort by path, the marker of a folder lands before the files of it
        out = {path: out[path] for path in sorted(out, key=_dfs_path_key)}
        PackFull._populate_edit_copied(dict_fileinfo=out)
        self._populate_data(out)
        # a C (copied) record carries no data of its own: the encoder stores
        # none of it and a decoder restores the info from the source record.
        # Restore it here too, so a caller reads the same records from a
        # wheel pack as from a git pack, see PackFull._populate_data
        records = list(out.values())
        for index, info in enumerate(records):
            if info.edit == 0 and info.source_lookback:
                source = records[index - info.source_lookback]
                info.sha1, info.size = source.sha1, source.size
        return out

    def _populate_data(self, dict_fileinfo: "dict[str, FileInfo]"):
        """
        Encode the content of every record that carries data.

        A content is encoded with the rules of the full pack (raw / lzma),
        through the cache of the pipeline: an encoding the cache already
        holds (the same content of a build before it) is applied to the
        record, the others are compressed by the tasks of PACK_POOL, one
        for each, see PackFull._populate_data. A C (copied) record carries
        no data of its own, the decoder restores it from the source record.

        Args:
            dict_fileinfo (dict[str, FileInfo]): Records to encode, in place
        """
        cache = self.cache
        with PACK_POOL.wait_jobs() as pool:
            for path, info in dict_fileinfo.items():
                if info.edit == 2 or (info.edit == 0 and info.source_lookback):
                    continue
                file = self.tree[path]
                key = file.sha1.hex()
                cached = cache.get(cache.content_index, key)
                if cached is not None:
                    apply_encoding(info, cached.info)
                else:
                    pool.start_thread_soon(
                        cache._compute_entry, cache.content_index, key,
                        partial(PackWheel._encode_content, info=info, data=file.content))

    @staticmethod
    def _encode_content(cached, info, data):
        """
        Encode one content with the rules of the full pack, the entry of the cache.

        The callback of PackCache._compute_entry: cached is the stored entry
        of the content (see PlainCache), None when the cache has none. It
        fills the record either way and returns the entry to keep. The rules
        are raw / lzma, the ones of the index of a full pack, and the entry
        is keyed by the content sha1, the identity of the content itself
        (the git pipeline keys its entries by the git blob sha1 instead).

        Args:
            cached (PlainCache | None): Stored entry of the content, None
                when the cache has none
            info (FileInfo): Record to fill
            data (bytes): Content to compress

        Returns:
            PlainCache: The entry to keep
        """
        if cached is not None:
            apply_encoding(info, cached.info)
            return cached
        PackFull._load_data(info, data, zstd=False)
        return PlainCache(info=FileInfo(
            path=info.path, algo=info.algo, size=info.size,
            data_size=info.data_size, sha1=info.sha1, data=info.data))

    def encode_content(self, info, data):
        """
        Encode a content that is not a file of the tree, through the cache.

        The rules of the full pack (raw / lzma) are applied to the content
        and the encoding is cached like the one of a file, keyed by the
        content sha1. PackWheelUpdate encodes the index pack of the version
        with them (the ledger the client patches), and the caller owns the
        record: the content is not added to the tree.

        Args:
            info (FileInfo): Record to fill
            data (bytes): Content to encode

        Returns:
            PlainCache: The cache entry of the content, the caller passes it
                to PackFull._load_data as the cache_info of a later encode
                (the plain candidates of a patch comparison)
        """
        cache = self.cache
        key = sha1(data).hexdigest()
        cached = cache.get(cache.content_index, key)
        if cached is not None:
            apply_encoding(info, cached.info)
            return cached
        return cache._compute_entry(
            cache.content_index, key,
            partial(PackWheel._encode_content, info=info, data=data))

    @cached_property
    def index_pack(self) -> bytes:
        """
        Index pack bytes of the version: header plus index section.

        The index pack is the ledger of the version: the client writes it to
        {ledger}/index.pack, the channel publishes its checksum in
        latest.pack, and the update pack of the version patches the ledger
        of the old version to it. The bytes are the prefix of the full pack,
        a PackDecodeBase of either one decodes the same records.

        Returns:
            bytes: Index pack bytes
        """
        return b''.join(self.iter_packidx_data())
