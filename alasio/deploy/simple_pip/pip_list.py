import os
import re

from alasio.backport import removesuffix
from alasio.ext.path import PathStr
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
        version (str): Version of the distribution, PEP 440, e.g. "0.18.6",
            the Version header of its metadata. The version of the directory
            name when the metadata can not be read, the empty string when
            neither carries one, e.g. a .egg-info directory without a
            PKG-INFO file
        info (PathStr): Path of the metadata entry the distribution was read
            from: the .dist-info directory, or the .egg-info directory or
            file, of the site-packages directory
    """

    def __init__(self, name, version, info):
        """
        Args:
            name (str): Name of the distribution
            version (str): Version of the distribution
            info (PathStr): Path of the metadata entry
        """
        self.name = name
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
    other device or of an other python version can be inspected.

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
    an old version left behind by an update, is listed once per entry:
    the listing reports the directory as it is, the caller decides which
    entry it takes ("pip list" keeps the first one only).

    Attributes:
        site_packages (PathStr): Normalized path of the directory, posix
            style, e.g. the site-packages of an environment
    """

    def __init__(self, site_packages):
        """
        Args:
            site_packages (str | PathStr): Path of the site-packages
                directory to read, normalized to the posix style. A path
                that does not exist is not an error, list() reports no
                distribution of it
        """
        self.site_packages = PathStr.new(site_packages)

    def list(self):
        """
        List the distributions installed in the directory.

        The directory is read on every call: an installation or a removal
        in between is visible in the next listing, the result is never
        cached.

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

    def get(self, name):
        """
        Find the distribution of a name.

        Args:
            name (str): Name of the distribution, e.g. "ruamel.yaml", any
                case and separator style, the compare is on the normalized
                name (PEP 503): "Ruamel.Yaml" finds "ruamel-yaml"

        Returns:
            Distribution | None: The first distribution of the name in
                the order of list(), None if the directory holds no
                distribution of the name
        """
        key = normalize_name(name)
        for dist in self.list():
            if dist.dist_key == key:
                return dist
        return None

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
        version = headers.get('version') or dir_version
        if not name:
            # A metadata entry that names no distribution can not be
            # reported, e.g. an empty ".dist-info" directory that holds
            # neither a name nor a version
            logger.warning(f'Skipped a metadata entry that names no distribution: {path}')
            return None
        return Distribution(name, version, path)


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
