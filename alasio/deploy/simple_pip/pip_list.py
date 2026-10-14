import os
import re

from alasio.backport import removesuffix
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_bytes
from alasio.ext.path.calc import joinpath
from alasio.logger import logger

# Suffix of the metadata directory of a modern installation, PEP 376
DIST_INFO_SUFFIX = '.dist-info'
# Suffix of the metadata entry of a legacy setuptools installation
EGG_INFO_SUFFIX = '.egg-info'
# Metadata file of a .dist-info directory, PEP 566
METADATA_FILE = 'METADATA'
# Metadata file of a .egg-info directory
PKG_INFO_FILE = 'PKG-INFO'
# Folder of the pack ledgers below a site-packages folder: the pack area of
# the deploy format, one ledger folder per distribution installed from a
# pack channel, see alasio.deploy.pack.job_base
PACK_AREA = '.pack'
# File of a pack ledger folder that records the applied version of its
# distribution: the last file a pack flow commits, so the version of a
# ledger is the version of a fully applied flow
INDEX_PACK = 'index.pack'


def normalize_name(name):
    """
    PEP 503 normalized name of a distribution, the dist-key of the pack files.

    The index url of a mirror and the folder of a pack use the same name,
    so a distribution named in a config with any case and separator style
    finds the same files, see Distribution.dist_key.

    Args:
        name (str): Name of a distribution, e.g. "ruamel.yaml"

    Returns:
        str: Normalized name, e.g. "ruamel-yaml"
    """
    # A run of "-", "_" and "." is a single "-", PEP 503. The compiled
    # pattern of a str pattern is cached by re, no global compile is needed
    return re.sub(r'[-_.]+', '-', name).lower()


class Distribution:
    """
    One distribution installed in a site-packages directory.

    Attributes:
        name (str): Name of the distribution, e.g. "ruamel.yaml", the Name
            header of its metadata. The name of the metadata entry when the
            metadata can not be read, the name is escaped there, PEP 427,
            e.g. "ruamel.yaml" is "ruamel_yaml"
        metadata_version (str): Version the metadata of the entry records,
            PEP 440, e.g. "0.18.6", the Version header of its metadata. The
            empty string when the metadata records no version, e.g. an
            empty or a broken ".dist-info" directory
        dist_version (str): Version the name of the metadata entry carries,
            PEP 427, e.g. "0.18.6" of "ruamel_yaml-0.18.6.dist-info". The
            empty string when the name carries none, e.g. a ".egg-info"
            entry, named after the distribution only
        version (str): Version of the distribution: the metadata_version,
            the dist_version as the fallback of an entry whose metadata
            records no version; the version of the pack ledger when the
            entry comes from one, see PipList.pack_list()
        info (PathStr): Path of the entry the distribution was read from:
            the .dist-info directory, or the .egg-info directory or file, of
            the site-packages directory; the index.pack of a pack ledger
            when the entry comes from one
    """

    def __init__(self, name, version, info, metadata_version='', dist_version=''):
        """
        Args:
            name (str): Name of the distribution
            version (str): Version of the distribution, see the class
                docstring
            info (PathStr): Path of the entry the distribution was read from
            metadata_version (str): Version the metadata of the entry
                records. Defaults to ''
            dist_version (str): Version the name of the metadata entry
                carries. Defaults to ''
        """
        self.name = name
        self.metadata_version = metadata_version
        self.dist_version = dist_version
        self.version = version
        self.info = info

    @property
    def dist_key(self):
        """
        PEP 503 normalized name of the distribution.

        Returns:
            str: Normalized name, e.g. "ruamel.yaml" -> "ruamel-yaml"
        """
        return normalize_name(self.name)

    def __repr__(self):
        """
        Returns:
            str: Representation of the distribution
        """
        return f'{type(self).__name__}({self.name!r}, {self.version!r}, {self.info!r})'


class PipList:
    """
    List the distributions installed in a site-packages directory.

    A "pip list" that runs no pip: the directory is scanned and the
    metadata of the installations in it is read, the interpreter of the
    environment is never probed nor imported, so the environment of an
    other device or of an other python version can be inspected. The pack
    ledgers of the deploy format are read next to the metadata entries: a
    distribution installed from a pack channel records the applied version
    in its ledger ({site_packages}/.pack/{dist-key}/index.pack), see
    pip_list() and pack_list().

    Three listings of the same directory:
        - pip_list() is the raw scan: one entry per metadata entry, a
          distribution with several entries is listed several times
        - pack_list() is the version of every distribution the mod updates
          by pack (mod.entry.pack_update_deps), read from its pack ledger
        - list() is the aggregate: one distribution per normalized name,
          keyed by it, the trusted version and the conflicts logged, see
          its docstring

    Read entries, the top level of the directory only, where a
    site-packages directory keeps its installations:
        - "{name}-{version}.dist-info/" directories with their METADATA
          file, PEP 376, the installations of pip
        - "{name}.egg-info" directories and files with their PKG-INFO
          file, the legacy installations of setuptools; the file of the
          file form holds the metadata itself

    The name of a distribution is the Name header of its metadata, the
    name of the metadata entry is the fallback; the version is the
    Version header of the metadata, the version of the directory name is
    the fallback (the "pip list" of pip 24.2 prefers the directory name
    for it, the metadata is the same value of a healthy installation).
    An entry that neither names is skipped with a warning: there is no
    name to report the distribution with, and an empty metadata directory
    is a residue of an interrupted installation rather than a
    distribution.

    Not read:
        - an entry that is a symbolic link, e.g. a .dist-info directory
          linked from an other tree: it is not an installation of this
          directory
        - ".egg" directories and ".egg-link" files, the easy_install and
          "setup.py develop" installations: the metadata of a link lives
          in the linked tree, not in the directory

    A distribution with several metadata entries, e.g. the directory of
    an old version left behind by an update, is listed once per entry by
    pip_list(); list() reports the distribution once and logs every entry
    it skips ("pip list" keeps the first one only).

    Attributes:
        site_packages (PathStr): Normalized path of the directory, posix
            style, e.g. the site-packages of an environment
        mod (Mod | None): The mounted mod whose entry declares the pack
            updated distributions (entry.pack_update_deps) that
            pack_list() reads the pack ledgers of, None to read no ledger
    """

    def __init__(self, site_packages, mod=None):
        """
        Args:
            site_packages (str | PathStr): Path of the site-packages
                directory to read, normalized to the posix style. A path
                that does not exist is not an error, list() reports no
                distribution of it
            mod (Mod, optional): The mounted mod whose entry declares the
                pack updated distributions (entry.pack_update_deps) that
                pack_list() reads the pack ledgers of, e.g. the mod the
                dependencies of the directory belong to. Defaults to None,
                no pack ledger is read
        """
        self.site_packages = PathStr.new(site_packages)
        self.mod = mod

    def list(self):
        """
        List the distributions installed in the directory, one entry per
        distribution, keyed by the normalized name.

        The aggregate of pip_list() and pack_list(): the metadata entries
        of the directory, with the versions the pack ledgers of the mod
        record. Every name appears once in the listing, so the entries are
        keyed by the PEP 503 normalized name (the dist-key, the identity
        of a distribution: any case and separator style of a name
        normalizes to one key). The conflicts of the two listings are
        resolved here, so the listing reports the trusted version of a
        distribution:

            - Two metadata entries of one name, e.g. the .dist-info
              directory of an old version left behind by an update: the
              first entry of pip_list() wins (the lowest path of the
              name, the same order as "pip list"), every other one is
              skipped and logged
            - A pack ledger and a metadata entry of one name that record
              different versions: the ledger wins. Its version is the one
              a pack flow applied -- the ledger is committed last, an
              interrupted update can already show the new metadata -- and
              the conflict is logged
            - A distribution a pack ledger alone records, its metadata
              gone: the entry comes from the ledger

        The directory and the ledgers are read on every call, the result
        is never cached, like pip_list().

        Returns:
            dict[str, Distribution]: The distributions, keyed by their
                normalized name (PEP 503), ordered by it. Empty when the
                directory does not exist or is not a directory
        """
        out = {}
        for dist in self.pip_list():
            dist_key = dist.dist_key
            kept = out.get(dist_key)
            if kept is not None:
                logger.warning(
                    f'Skipped a duplicate installation of "{dist_key}": '
                    f'{dist.info} ("{dist.version}"), using {kept.info} ("{kept.version}")'
                )
                continue
            out[dist_key] = dist
        for dist in self.pack_list():
            dist_key = dist.dist_key
            kept = out.get(dist_key)
            if kept is not None:
                if kept.version == dist.version:
                    # the ledger and the metadata agree on the version, the
                    # metadata entry names the installation
                    continue
                logger.warning(
                    f'The pack ledger version of "{dist_key}" wins over its metadata: '
                    f'"{dist.version}" ({dist.info}), the metadata has '
                    f'"{kept.version}" ({kept.info})'
                )
            out[dist_key] = dist
        return dict(sorted(out.items()))

    def pip_list(self):
        """
        List the metadata entries of the directory, the raw scan.

        The directory is read on every call: an installation or a removal
        in between is visible in the next listing, the result is never
        cached. A distribution with several metadata entries is listed
        once per entry, the conflicting entries are resolved by list().

        Returns:
            list[Distribution]: The distributions, sorted by their
                normalized name (PEP 503) then by the path of the metadata
                entry, so the order of a directory that does not change
                does not change. Empty when the directory does not exist
                or is not a directory
        """
        out = []
        try:
            entries = os.scandir(self.site_packages)
        except (FileNotFoundError, NotADirectoryError):
            return out
        with entries:
            for entry in entries:
                dist = self._read_entry(entry)
                if dist is not None:
                    out.append(dist)
        # The normalized name, so the case and the separator style of a name
        # do not decide the order, the same order as "pip list"
        out.sort(key=lambda dist: (dist.dist_key, dist.info))
        return out

    def pack_list(self):
        """
        List the distributions the pack ledgers of the mod record.

        The names come from mod.entry.pack_update_deps, the distributions
        the mod updates from its pack channels instead of pip. The ledger
        folder of a name is {site_packages}/.pack/{dist-key} (the PEP 503
        normalized name, the key of the channel and the ledger folder of
        the deploy flow), its index.pack records the version a pack flow
        applied, so the version of the record is the version of the files
        of a flow that ran to the end, see alasio.deploy.pack.job_base.
        The ledgers are read on every call, the result is never cached.

        A declared name the directory holds no ledger of is not listed:
        it was never applied from a pack channel, or its ledger was
        removed. A ledger that can not be read or records no version is
        skipped with a warning, the same rule as a metadata entry that
        names no distribution.

        A name declared twice, any case and separator style, is read once:
        the normalized name is the identity of the distribution, the first
        declaration names the entry, the rule of install_base.

        Returns:
            list[Distribution]: The distributions, sorted by their
                normalized name (PEP 503), every entry read from a pack
                ledger. Empty without a mod, and when no declared
                distribution has a ledger
        """
        if self.mod is None:
            return []
        names = {}
        for name in self.mod.entry.pack_update_deps:
            names.setdefault(normalize_name(name), name)
        out = []
        for dist_key, name in names.items():
            index_file = self.site_packages.joinpath(f'{PACK_AREA}/{dist_key}/{INDEX_PACK}')
            version = self._read_pack_version(index_file)
            if version is not None:
                out.append(Distribution(name, version, index_file))
        out.sort(key=lambda dist: dist.dist_key)
        return out

    def _read_entry(self, entry):
        """
        Read one entry of the site-packages directory.

        Args:
            entry (os.DirEntry): Entry of the site-packages directory

        Returns:
            Distribution | None: None if the entry is not the metadata
                entry of an installation, or names no distribution
        """
        entry_name = entry.name
        if entry_name.endswith(DIST_INFO_SUFFIX):
            # The name and the version of the directory are escaped, PEP 427:
            # a run of "-", "_" and "." became "_", so the name of a
            # distribution holds no "-" in it and the first "-" starts the
            # version, e.g. "importlib_metadata-7.0.0.dist-info"
            dir_name, sep, dir_version = removesuffix(entry_name, DIST_INFO_SUFFIX).partition('-')
            if not sep:
                # A name that holds no "-", e.g. "broken.dist-info": the
                # metadata has to name the distribution
                dir_name = dir_version = ''
            dist_info = True
        elif entry_name.endswith(EGG_INFO_SUFFIX):
            # The legacy installation of setuptools: the directory is named
            # after the distribution only, the version comes from the
            # metadata, e.g. "alasio.egg-info"
            dir_name = removesuffix(entry_name, EGG_INFO_SUFFIX).partition('-')[0]
            dir_version = ''
            dist_info = False
        else:
            return None
        path = self.site_packages.joinpath(entry_name)
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
        except FileNotFoundError:
            # The entry is removed between the listing and the read
            return None
        if is_dir:
            metadata = joinpath(path, METADATA_FILE if dist_info else PKG_INFO_FILE)
        elif dist_info:
            # A file named "*.dist-info" is not an installation: the metadata
            # of a distribution is a directory, PEP 376
            return None
        else:
            try:
                is_file = entry.is_file(follow_symlinks=False)
            except FileNotFoundError:
                return None
            if not is_file:
                # A symbolic link is not followed, a special file is not a
                # metadata file
                return None
            # The file of a legacy installation is the metadata itself
            metadata = path
        headers = _read_headers(metadata)
        name = headers.get('name') or dir_name
        # the version the metadata records, the version of the name of the
        # entry as the fallback, see Distribution
        metadata_version = headers.get('version', '')
        version = metadata_version or dir_version
        if not name:
            # A metadata entry that names no distribution can not be
            # reported, e.g. an empty ".dist-info" directory that holds
            # neither a name nor a version
            logger.warning(f'Skipped a metadata entry that names no distribution: {path}')
            return None
        return Distribution(
            name, version, path,
            metadata_version=metadata_version, dist_version=dir_version,
        )

    def _read_pack_version(self, index_file):
        """
        Read the version a pack ledger records.

        Only the version part of the index pack is decoded, the file
        records of the ledger are never built: it is what the pack flow
        reads of a ledger, see DeployJob._local_version().

        The decode never verifies the checksum of the ledger: a pack flow
        that works on the distribution verifies it and repairs it from the
        pack channel, this listing only reports the version a ledger still
        records, like the local version of a pack flow.

        Args:
            index_file (str | PathStr): Path of the index.pack of a ledger

        Returns:
            str | None: The version the ledger records, None when the
                folder holds no usable ledger: no ledger file of the name
                (the distribution was never applied from a pack channel),
                an unreadable or malformed record (logged), or a record
                that carries no version (logged)
        """
        try:
            data = atomic_read_bytes(index_file)
        except OSError:
            # Missing, unreadable, a directory, ...
            return None
        # The pack modules are imported here, not at the module level: the
        # listing is a cheap scan of a folder, the decoder of the ledgers
        # and its dependencies are only needed when a mod declares pack
        # updated distributions
        from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError

        try:
            decoder = PackDecodeBase(data)
        except PackDecodeError as e:
            logger.warning(f'Skipped a malformed pack ledger: {index_file}: {e}')
            return None
        version = decoder.current_version
        if not version:
            logger.warning(f'Skipped a pack ledger that records no version: {index_file}')
            return None
        return version


def _read_headers(file):
    """
    Read the headers of a metadata file, METADATA or PKG-INFO.

    Only the headers are read: the body of the file holds the description
    of a distribution and can be hundreds of KB, the listing needs two
    headers. The header block ends at the first empty line, RFC 822, so
    the read stops there and the body is never read.

    Args:
        file (str): Path of the metadata file

    Returns:
        dict[str, str]: Lower-cased name of a header -> value, e.g.
            {'name': 'ruamel.yaml', 'version': '0.18.6'}. The first
            occurrence of a header wins and the folded continuation lines
            of a value are joined with a space, RFC 822. Empty when the
            file can not be read, e.g. a missing or a broken file
    """
    headers = {}
    key = ''
    try:
        # The metadata of a distribution is UTF-8, PEP 566, a file written
        # by hand or edited by a tool may hold a byte order mark or other
        # bytes: the headers the listing needs are the first lines, a bad
        # byte in them must not fail the whole listing
        with open(file, 'r', encoding='utf-8-sig', errors='replace') as f:
            for line in f:
                if not line.strip():
                    # The empty line that ends the header block, RFC 822
                    break
                if line[0] in ' \t':
                    # A folded continuation of the value of the header above
                    if key:
                        headers[key] = ' '.join(part for part in (headers[key], line.strip()) if part)
                    continue
                key, sep, value = line.partition(':')
                key = key.strip().lower()
                if not sep or not key:
                    # A line that holds no header, e.g. of a broken metadata
                    # file: the next continuation folds into nothing
                    key = ''
                    continue
                if key not in headers:
                    headers[key] = value.strip()
    except OSError:
        # Missing, unreadable, a directory, ...
        return {}
    return headers
