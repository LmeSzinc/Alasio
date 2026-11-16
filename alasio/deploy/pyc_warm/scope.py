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
    T3  lock     the other pins of the requirements file of the mod
    M   mod      the sources of a mod tree

The tiers are ordered by what a run gets out of the time it is given: the
first two are the distributions the startup chain actually imports, the
last two are the bulk. A run that is interrupted, by a shutdown or by the
user, has warmed the files that matter.

Every path here is a posix path: the layers build absolute paths and pass
them to stale_sources(), which normalizes them again for the disk.
"""
import os
import sys

from alasio.backport import removesuffix
from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.deploy.simple_pip.parse_req import Requirements
from alasio.deploy.simple_pip.pip_list import DIST_INFO_SUFFIX, normalize_name
from alasio.deploy.simple_pip.whl_record import RecordManager
from alasio.ext.path.atomic import atomic_read_bytes, atomic_read_text
from alasio.ext.path.calc import joinpath, to_posix

# The pack area of a deploy target, relative to its root, and the index
# pack a pack flow commits into the ledger folder of the target, see
# alasio.deploy.pack.job_base.DeployTarget
PACK_AREA = '.pack'
INDEX_PACK = 'index.pack'
# The requirements file of a mod, the pins of its environment
LOCK_FILE = 'requirements.txt'
# Suffix of a python source
SOURCE_SUFFIX = '.py'
# Suffix of a file installed outside of site-packages, e.g. a script of
# the environment root, PEP 376 records it with a relative path
OUTSIDE = '..'
# Folders a walk of a tree never descends into: the bytecode cache, the
# ledger, the repository, the dependencies of the frontend, and an
# environment (a release keeps site-packages in the tree)
WALK_SKIP = frozenset((
    '__pycache__',
    '.git',
    PACK_AREA,
    'node_modules',
    'site-packages',
    'dist-packages',
))
# Names of the site-packages folders, the environment of the interpreter
SITE_DIRS = ('site-packages', 'dist-packages')

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
    The sources of one tier, and what the tier could not resolve.

    Attributes:
        sources (list[str]): Absolute posix paths of the .py files to
            warm up
        resolved (list[str]): PEP 503 normalized names of the
            distributions the tier found, empty for a tree
        unresolved (list[str]): Names the tier asked for and did not
            find, e.g. a distribution removed from the environment by
            hand, they are reported but never fail the run
    """

    def __init__(self, sources=None, resolved=None, unresolved=None):
        """
        Args:
            sources (list[str]): Absolute posix paths of the sources
            resolved (list[str]): Names of the distributions found
            unresolved (list[str]): Names of the distributions not found
        """
        self.sources = sources or []
        self.resolved = resolved or []
        self.unresolved = unresolved or []

    def __repr__(self):
        return (
            f'<ScopeResult {len(self.sources)} sources, '
            f'{len(self.resolved)} resolved, {len(self.unresolved)} unresolved>'
        )


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


def site_packages_tiers(site_packages, requirements_file=None):
    """
    The tiers of one site-packages folder, T1, T2 and T3, in order.

    A distribution found by an earlier tier is not listed by a later one:
    the tiers overlap, e.g. numpy is a pack distribution and a common
    name, and compiling it twice only costs the scan of a tier.

    Args:
        site_packages (str): Posix path of the folder
        requirements_file (str): Path of the requirements file of the
            mod. Defaults to None, T3 is empty

    Returns:
        list[tuple[str, ScopeResult]]: Tier name and its scope, the
            names are "pack", "common" and "lock"
    """
    infos = dist_infos(site_packages)
    covered = set()

    pack = pack_tier(site_packages, infos)
    covered.update(pack.resolved)

    common = common_tier(site_packages, infos, covered)
    covered.update(common.resolved)

    if requirements_file is None:
        lock = ScopeResult()
    else:
        lock = lock_tier(site_packages, infos, requirements_file, covered)

    return [('pack', pack), ('common', common), ('lock', lock)]


def pack_tier(site_packages, infos):
    """
    The sources of the distributions the pack flows installed, T1.

    The ledger of a distribution is {site_packages}/.pack/{name}, its file
    list is the index pack of the ledger, the file a pack flow commits and
    whose checksum it validates. A ledger that can not be read falls back
    to the RECORD of the distribution, e.g. a distribution that pip
    installed before a pack flow took it over.

    Args:
        site_packages (str): Posix path of the folder
        infos (dict[str, str]): Metadata folder of every distribution,
            see dist_infos()

    Returns:
        ScopeResult: The sources of the tier
    """
    try:
        with os.scandir(joinpath(site_packages, PACK_AREA)) as entries:
            names = sorted(
                entry.name for entry in entries
                if entry.is_dir()
            )
    except OSError:
        # No pack flow ever ran in this environment, the tier is empty
        return ScopeResult()

    files = []
    resolved = []
    unresolved = []
    for name in names:
        key = normalize_name(name)
        sources = index_sources(site_packages, name)
        if not sources:
            # No index pack, a corrupt one, or one without any source:
            # the RECORD of the distribution is the next best list
            info = infos.get(key)
            sources = record_sources(site_packages, info) if info else []
        if sources:
            files += sources
            resolved.append(key)
        else:
            unresolved.append(name)
    return ScopeResult(files, resolved, unresolved)


def common_tier(site_packages, infos, covered=()):
    """
    The sources of the distributions of COMMON_DISTS, T2.

    Args:
        site_packages (str): Posix path of the folder
        infos (dict[str, str]): Metadata folder of every distribution,
            see dist_infos()
        covered (set[str] | tuple[str]): Names an earlier tier resolved,
            they are skipped

    Returns:
        ScopeResult: The sources of the tier
    """
    files = []
    resolved = []
    unresolved = []
    for name in COMMON_DISTS:
        key = normalize_name(name)
        if key in covered:
            continue
        info = infos.get(key)
        sources = record_sources(site_packages, info) if info else []
        if not sources:
            unresolved.append(name)
            continue
        files += sources
        resolved.append(key)
    return ScopeResult(files, resolved, unresolved)


def lock_tier(site_packages, infos, requirements_file, covered=()):
    """
    The sources of the distributions the requirements file pins, T3.

    The file is the requirements file of a mod, every distribution of it
    is pinned to an exact version. A pin that is already covered, or that
    has no installed metadata folder, is skipped: the pins of the file are
    what the release should hold, the environment is what it does hold.

    Args:
        site_packages (str): Posix path of the folder
        infos (dict[str, str]): Metadata folder of every distribution,
            see dist_infos()
        requirements_file (str): Path of the requirements file
        covered (set[str] | tuple[str]): Names an earlier tier resolved,
            they are skipped

    Returns:
        ScopeResult: The sources of the tier, empty when the file is
            missing, e.g. in a development tree
    """
    try:
        content = atomic_read_text(requirements_file)
    except OSError:
        return ScopeResult()
    pins = Requirements(content).dict_pins

    files = []
    resolved = []
    unresolved = []
    for key in pins:
        if key in covered:
            continue
        info = infos.get(key)
        sources = record_sources(site_packages, info) if info else []
        if not sources:
            unresolved.append(key)
            continue
        files += sources
        resolved.append(key)
    return ScopeResult(files, resolved, unresolved)


def mod_sources(mod_root):
    """
    The sources of one mod tree, M.

    A ledger is the authority when the tree has one: the index pack of the
    project tree target lists the files the last update left, deleted ones
    excluded. A tree without a usable ledger, e.g. a development checkout,
    is walked instead: the mod is then read from the disk, which is only
    a slower way to the same files.

    Args:
        mod_root (str): Posix path of the mod tree

    Returns:
        list[str]: Absolute posix paths of the .py files
    """
    sources = index_sources(mod_root)
    if sources is None:
        return walk_sources(mod_root)
    return sources


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
    except (OSError, UnicodeDecodeError):
        return []

    sources = []
    for path in record.entries:
        if path.startswith(OUTSIDE):
            # Installed outside of site-packages, e.g. a script of the
            # environment root, the path does not resolve here
            continue
        if not path.endswith(SOURCE_SUFFIX):
            continue
        sources.append(joinpath(site_packages, path))
    return sources


def walk_sources(root):
    """
    The sources of a tree read from the disk.

    The fallback of a tree without a ledger, e.g. a development checkout.
    Two kinds of folders are never entered: the ones of WALK_SKIP, the
    bytecode caches and the ledgers, and the ones of the environment of
    the running interpreter, which holds the whole standard library and
    every installed distribution, thousands of sources that the
    environment tier already covers.

    Args:
        root (str): Posix path of the tree

    Returns:
        list[str]: Posix paths of the .py files
    """
    environment = environment_dirs()
    sources = []
    folders = [root]
    while folders:
        folder = folders.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    try:
                        is_dir = entry.is_dir()
                    except OSError:
                        continue
                    if not is_dir:
                        if entry.name.endswith(SOURCE_SUFFIX):
                            sources.append(joinpath(folder, entry.name))
                        continue
                    if entry.name in WALK_SKIP:
                        continue
                    path = joinpath(folder, entry.name)
                    if path in environment:
                        continue
                    folders.append(path)
        except OSError:
            continue
    return sources


def environment_dirs():
    """
    The folders of the environment of the running interpreter, the roots
    of its standard library and of its site-packages.

    Returns:
        frozenset[str]: Posix paths of the prefixes of the interpreter,
            e.g. a release keeps its interpreter in {root}/toolkit
    """
    dirs = set()
    for prefix in (getattr(sys, 'prefix', ''), getattr(sys, 'base_prefix', '')):
        if prefix:
            dirs.add(to_posix(prefix).rstrip('/'))
    return frozenset(dirs)
