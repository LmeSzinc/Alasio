"""
The sources to warm up, by tier.

A release ships a `toolkit/python.exe` with its own site-packages, and the
mods of the project root. Every python source of them has a bytecode cache
that python writes on the first import, so the first start after an update
compiles thousands of files while the worker waits for its first device.
This module lists those sources without importing any of them, the cache
check itself is fresh.py.

Four tiers, in the order a warmup should run them:

    T1  pack     the distributions the pack flows installed, listed by the
                 index pack of their ledger, which carries a checksum
    T2  common   the distributions the device and the image stack pull in,
                 a fixed list of names
    T3  lock     the other pins of the requirements file of a mod
    M   mod      the sources of a mod tree

The tiers are ordered by what a run gets out of the time it is given: the
first two are the distributions the startup chain actually imports, the
last two are the bulk. A run that is interrupted, by a shutdown or by the
user, has warmed the files that matter.

A tier answers one ScopeResult per distribution and one per mod, so the
caller sees the scope of the run as the units the release is made of: a
distribution that is not installed is a result without a source, and the
caller reports it rather than guessing whether the tier was empty or the
distribution was missing.

The mods come from the loader of the backend, see Scope.mods: a mod
declares the location of its requirements file in its own declaration, so
the run reads the same mod_entry.yaml the backend reads instead of assuming
a layout.

Every path here is a posix path: the layers build absolute paths and pass
them to the scan, which normalizes them again for the disk.
"""
import csv
import os
import sys

from alasio.backport import removesuffix
from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.deploy.simple_pip.parse_req import Requirements
from alasio.deploy.simple_pip.pip_list import DIST_INFO_SUFFIX, normalize_name
from alasio.deploy.simple_pip.whl_record import RecordManager
from alasio.ext.cache import cached_property
from alasio.ext.path.atomic import atomic_read_bytes, atomic_read_text
from alasio.ext.path.calc import joinpath, to_posix

# The pack area of a deploy target, relative to its root, and the index
# pack a pack flow commits into the ledger folder of the target, see
# alasio.deploy.pack.job_base.DeployTarget
PACK_AREA = '.pack'
INDEX_PACK = 'index.pack'
# Suffix of a python source
SOURCE_SUFFIX = '.py'
# Names of the site-packages folders, the environment of the interpreter
SITE_DIRS = ('site-packages', 'dist-packages')

# Names of the tiers, the keys the report groups the results by
TIER_PACK = 'pack'
TIER_COMMON = 'common'
TIER_LOCK = 'lock'
TIER_MOD = 'mod'

# The distributions the device and the image stack import at startup, the
# names of the startup chain of a worker: numpy and opencv for the images,
# adbutils and uiautomator2 for the devices, trio and starlette for the
# backend, and the smaller dependencies of those. A name that is not
# installed is skipped, a distribution is not listed twice.
COMMON_DISTS = (
    # images and devices
    'numpy', 'scipy', 'opencv-python-headless', 'pillow', 'imageio',
    'adbutils', 'uiautomator2', 'uiautomator2cache', 'apkutils2', 'lxml',
    # network and processes
    'websockets', 'requests', 'urllib3', 'psutil',
    # data and logs
    'msgspec', 'msgspecerror', 'rich', 'pygments',
    # async stack
    'trio', 'starlette', 'anyio', 'attrs', 'sniffio', 'outcome',
    'sortedcontainers', 'exceptiongroup',
    # dependencies of the above
    'idna', 'charset-normalizer', 'certifi', 'filelock', 'logzero',
    'progress', 'retry', 'deprecated', 'wrapt', 'six', 'whichcraft',
    'pysocks', 'xmltodict', 'cached-property', 'decorator', 'deprecation',
    'cigam', 'colorama', 'typing-extensions', 'zstandard',
)


class ScopeResult:
    """
    The sources of one unit of the scope, and what the unit is.

    The unit is one distribution of the environment or one mod of the
    project. A unit the scope asked for and did not find, e.g. a name
    COMMON_DISTS lists that is not installed, is a result without any
    source: the caller reports it rather than reading an empty tier as a
    tier that had nothing to warm.

    Attributes:
        tier (str): Name of the tier, one of TIER_PACK, TIER_COMMON,
            TIER_LOCK and TIER_MOD
        target (str): The unit, a PEP 503 normalized distribution name,
            a mod name, or the path of a file that could not be read
        sources (list[str]): Absolute posix paths of the .py files to
            warm up
        folder (str): Folder the sources were listed from, the
            site-packages folder of a distribution
    """

    def __init__(self, tier, target, sources=None, folder=''):
        """
        Args:
            tier (str): Name of the tier
            target (str): The distribution, the mod, or the file
            sources (list[str]): Absolute posix paths of the sources,
                None for a unit without any
            folder (str): Folder the sources were listed from
        """
        self.tier = tier
        self.target = target
        self.sources = sources or []
        self.folder = folder

    def __repr__(self):
        return f'<ScopeResult {self.tier} "{self.target}" {len(self.sources)} sources>'


class Scope:
    """
    The scope of a warmup: every source the start of a release imports.

    The environment is the one of the running interpreter, and the mods are
    the ones the backend loads: a caller that runs a warmup on a release
    runs it with the interpreter of the release, and reads the mods of the
    project root, so nothing is derived from the layout of a release here,
    e.g. "toolkit/Lib/site-packages", which a virtual environment would
    answer wrong.

    Both are cached properties, and both are also the seam of the tests: a
    caller that hands its own folders and its own mods in never touches the
    disk of the machine.

    Attributes:
        site_packages (list[str]): Posix paths of the site-packages
            folders of the run
        mods (dict[str, Mod]): The mods of the project root, by mod name
    """

    def __init__(self, site_packages=None, mods=None):
        """
        Args:
            site_packages (list[str]): Posix paths of the site-packages
                folders to warm. Defaults to None, the folders of the
                running interpreter, see site_packages_dirs()
            mods (dict[str, Mod]): The mods to warm. Defaults to None, the
                mods the backend loads, see ModLoader.dict_mod
        """
        self._site_packages = site_packages
        self._mods = mods

    @cached_property
    def site_packages(self):
        """
        The site-packages folders of the run, in the order python searches
        them.

        Returns:
            list[str]: Posix paths of the folders that exist
        """
        if self._site_packages is not None:
            return self._site_packages
        return site_packages_dirs()

    @cached_property
    def mods(self):
        """
        The mods of the project root, by name.

        The mods are read through the loader of the backend rather than
        guessed from the folders of the project root: a mod declares the
        location of its requirements file and the folder of its tree in its
        own declaration, and a folder that declares no mod is not one.

        The loader is built on the project root of the run rather than
        taken from the module level MOD_LOADER of the loader, which is
        bound to the root in force when that module was imported: the
        warmup subprocess resolves its root from its own command line, and
        a test resolves one per case.

        The import is local on purpose, see the module docstring: the
        caller sets the project root before it reads the mods, and the
        import is what makes that order explicit.

        Returns:
            dict[str, Mod]: The mods of the project root, empty when the
                declaration of the root itself can not be read
        """
        if self._mods is not None:
            return self._mods
        from alasio.config.entry.loader import ModLoader
        from alasio.config.entry.mod_entry import ModEntryError
        from alasio.ext import env

        try:
            return ModLoader(env.PROJECT_ROOT).dict_mod
        except ModEntryError:
            # The declaration of the project root is unreadable. The backend
            # builds the same list at its startup and reports the reason
            # there, so a warmup is not a second place to fail: no mod of
            # the release can be warmed, the environment tiers still are
            return {}

    def tiers(self):
        """
        Walk the scope, tier by tier.

        The tiers come in the order a run should compile them, and the
        results of a tier come one per unit: a caller that consumes the
        generator as it goes starts compiling the first distribution while
        the next one is being listed.

        Yields:
            ScopeResult: One per distribution the environment installed and
                one per mod, in the order of the tiers
        """
        for folder in self.site_packages:
            infos = dist_infos(folder)
            # A distribution found by an earlier tier is not listed by a
            # later one: the tiers overlap, e.g. numpy is a pack
            # distribution and a common name, and warming it twice only
            # costs the scan of a tier
            covered = set()
            yield from self.pack_tier(folder, infos, covered)
            yield from self.common_tier(folder, infos, covered)
            for mod in self.mods.values():
                yield from self.lock_tier(folder, infos, mod, covered)
        yield from self.mod_tier()

    def pack_tier(self, folder, infos, covered):
        """
        The sources of the distributions the pack flows installed, T1.

        The ledger of a distribution is {site_packages}/.pack/{name}, its
        file list is the index pack of the ledger, the file a pack flow
        commits and whose checksum it validates. A ledger that can not be
        read falls back to the RECORD of the distribution, e.g. a
        distribution that pip installed before a pack flow took it over.

        Args:
            folder (str): Posix path of the site-packages folder
            infos (dict[str, str]): Metadata folder of every distribution,
                see dist_infos()
            covered (set[str]): Names an earlier tier resolved, and the
                names this tier resolved as it yields. A name in it is
                skipped, and a name this tier resolves is added before the
                result answers, so the tiers after it see the whole set

        Yields:
            ScopeResult: One per ledger of the pack area, sorted by name
        """
        try:
            with os.scandir(joinpath(folder, PACK_AREA)) as entries:
                names = sorted(
                    entry.name for entry in entries
                    if entry.is_dir()
                )
        except OSError:
            # No pack flow ever ran in this environment, the tier is empty
            return

        for name in names:
            key = normalize_name(name)
            if key in covered:
                continue
            sources = index_sources(folder, name)
            if not sources:
                # No index pack, a corrupt one, or one without any source:
                # the RECORD of the distribution is the next best list
                info = infos.get(key)
                sources = record_sources(folder, info) if info else []
            if sources:
                covered.add(key)
            yield ScopeResult(TIER_PACK, key, sources, folder)

    def common_tier(self, folder, infos, covered):
        """
        The sources of the distributions of COMMON_DISTS, T2.

        Args:
            folder (str): Posix path of the site-packages folder
            infos (dict[str, str]): Metadata folder of every distribution,
                see dist_infos()
            covered (set[str]): Names an earlier tier resolved, and the
                names this tier resolved as it yields, see pack_tier()

        Yields:
            ScopeResult: One per name of COMMON_DISTS, in the order of the
                list
        """
        for name in COMMON_DISTS:
            key = normalize_name(name)
            if key in covered:
                continue
            info = infos.get(key)
            sources = record_sources(folder, info) if info else []
            if sources:
                covered.add(key)
            yield ScopeResult(TIER_COMMON, key, sources, folder)

    def lock_tier(self, folder, infos, mod, covered):
        """
        The sources of the distributions the requirements file of a mod
        pins, T3.

        The file is the one the declaration of the mod names, every
        distribution in it is pinned to an exact version. A pin that is
        already covered, or that has no installed metadata folder, is
        skipped: the pins of the file are what the release should hold, the
        environment is what it does hold.

        Args:
            folder (str): Posix path of the site-packages folder
            infos (dict[str, str]): Metadata folder of every distribution,
                see dist_infos()
            mod (Mod): The mod whose requirements file is read
            covered (set[str]): Names an earlier tier resolved, and the
                names this tier resolved as it yields, see pack_tier()

        Yields:
            ScopeResult: One per pin of the file, in the order of the file.
                A file that can not be read answers one result without any
                source, named after the file
        """
        file = joinpath(to_posix(mod.root), mod.entry.requirements_file)
        try:
            content = atomic_read_text(file)
        except (OSError, UnicodeDecodeError):
            # The declaration names a file the tree does not hold, e.g. a
            # mod that never pinned its dependencies, or a file that is not
            # the UTF-8 the pins are written in, e.g. one an editor saved in
            # the local code page. Either way there are no pins to read, and
            # the environment tiers are still warmed
            yield ScopeResult(TIER_LOCK, file)
            return
        for key in Requirements(content).dict_pins:
            if key in covered:
                continue
            info = infos.get(key)
            sources = record_sources(folder, info) if info else []
            if sources:
                covered.add(key)
            yield ScopeResult(TIER_LOCK, key, sources, folder)

    def mod_tier(self):
        """
        The sources of the mod trees, M.

        A mod is read from the ledger of its project tree target, the index
        pack its updater commits: the files the last update left, deleted
        ones excluded. A mod without a usable ledger, e.g. a tree that was
        never updated, is skipped rather than walked: a walk would warm
        whatever the folder happens to hold, and a development tree holds
        thousands of files a run never imports.

        Yields:
            ScopeResult: One per mod, in the order of the loader, a mod
                without a ledger answers no source
        """
        for name, mod in self.mods.items():
            root = to_posix(mod.root)
            yield ScopeResult(TIER_MOD, name, index_sources(root), root)


def site_packages_dirs():
    """
    The site-packages folders of the running interpreter, in the order
    python searches them.

    The warmup subprocess runs the interpreter of the release, so its own
    folders are the folders the worker imports from: no path is derived
    from the layout of a release here, e.g. "toolkit/Lib/site-packages",
    which would be wrong for a virtual environment.

    Returns:
        list[str]: Posix paths of the folders that exist, duplicates
            removed
    """
    dirs = [
        entry for entry in sys.path
        if os.path.basename(entry) in SITE_DIRS
    ]
    try:
        import site
    except ImportError:
        # The interpreter was started with -S, sys.path is the truth
        pass
    else:
        try:
            # getsitepackages() answers the prefix too, e.g.
            # ['C:/envs/alasio', 'C:/envs/alasio/Lib/site-packages']
            dirs.extend(
                entry for entry in site.getsitepackages()
                if os.path.basename(entry) in SITE_DIRS
            )
        except (AttributeError, OSError):
            pass

    out = []
    seen = set()
    for path in dirs:
        path = to_posix(path).rstrip('/')
        if not path or path in seen:
            continue
        seen.add(path)
        if os.path.isdir(path):
            out.append(path)
    return out


def index_sources(root, name=''):
    """
    The sources a ledger lists.

    Args:
        root (str): Posix path of the target root
        name (str): Ledger key of the target. Defaults to '', the project
            tree target, whose ledger folder is {root}/.pack itself

    Returns:
        list[str] | None: Absolute posix paths of the .py files, None
            when the target has no index pack or one that can not be
            read, e.g. corrupt or written by an other format version
    """
    ledger = f'{PACK_AREA}/{name}' if name else PACK_AREA
    try:
        data = atomic_read_bytes(joinpath(joinpath(root, ledger), INDEX_PACK))
    except OSError:
        return None
    try:
        decoder = PackDecodeBase(data)
        decoder.validate_index()
    except PackDecodeError:
        # A file list that does not describe its target is no file list
        return None

    sources = []
    for path, info in decoder.fileinfo.items():
        if info.edit == 2:
            # A file the update removed, it is not part of the target
            continue
        if not path.endswith(SOURCE_SUFFIX):
            continue
        sources.append(joinpath(root, localize_path(path, name)))
    return sources


def localize_path(path, name):
    """
    Rewrite a pack area path into the ledger folder of a target, the
    mapping of DeployTarget.localize().

    Args:
        path (str): Path of a pack record, relative to the root
        name (str): Ledger key of the target, '' for the project tree

    Returns:
        str: Local path of the target
    """
    if not name or not path.startswith(f'{PACK_AREA}/'):
        return path
    return f'{PACK_AREA}/{name}{path[len(PACK_AREA):]}'


def dist_infos(site_packages):
    """
    The metadata folder of every distribution installed in a folder.

    The name is read from the folder name the way pip builds it:
    "trio-0.22.0.dist-info", "ruamel.yaml-0.17.21.dist-info". A version
    never holds a hyphen, the last one separates the name from the
    version.

    Args:
        site_packages (str): Posix path of the folder to scan

    Returns:
        dict[str, str]: PEP 503 normalized name to the posix path of the
            .dist-info folder, the first folder for a name installed
            twice
    """
    out = {}
    try:
        with os.scandir(site_packages) as entries:
            for entry in entries:
                name = entry.name
                if not name.endswith(DIST_INFO_SUFFIX):
                    continue
                key = normalize_name(removesuffix(name, DIST_INFO_SUFFIX).rpartition('-')[0])
                if not key:
                    continue
                out.setdefault(key, joinpath(site_packages, name))
    except OSError:
        # A folder that does not exist holds no distribution
        return {}
    return out


def record_sources(site_packages, dist_info):
    """
    The sources one distribution installed, from its RECORD.

    The RECORD is the file list of the installation, PEP 376, it is
    written by the installer: the files that are not in it, e.g. the
    bytecode caches and the files placed by hand, are never warmed up.
    A row is a path relative to site-packages by definition, so a row that
    leaves it, e.g. a script of the environment root, is not a file of the
    installation and is skipped.

    Args:
        site_packages (str): Posix path of the folder the RECORD paths
            are relative to
        dist_info (str): Posix path of the .dist-info folder

    Returns:
        list[str]: Absolute posix paths of the .py files, empty when the
            RECORD is missing or can not be decoded
    """
    try:
        content = atomic_read_bytes(joinpath(dist_info, 'RECORD'))
        record = RecordManager()
        record.load_bytes(content)
    except (OSError, UnicodeDecodeError, csv.Error):
        # A RECORD that is not there, not UTF-8, or not a csv at all, e.g.
        # one holding a NUL byte: the distribution has no file list to read,
        # and a broken list is never an error of the run
        return []

    sources = []
    for path in record.entries:
        if path.startswith('/') or '..' in path.split('/'):
            # Installed outside of site-packages, e.g. a script of the
            # environment root, the path does not resolve here. The same
            # holds for a row that climbs out in its middle, which no
            # installer writes and no installation holds
            continue
        if not path.endswith(SOURCE_SUFFIX):
            continue
        sources.append(joinpath(site_packages, path))
    return sources
