"""
Freshness of the bytecode cache of a python source file, PEP 3147.

Python reuses the pyc file of a source only when the header of the pyc
file matches the source, see importlib._bootstrap_external. The header
of a timestamp based pyc file is the same on python 3.7 to 3.14:

    [0:4]   magic number, the bytes of the interpreter version
    [4:8]   flags, bit 0 hash based, bit 1 check source, no other bit
    [8:12]  mtime of the source, int() of the stat, unsigned 32 bits
    [12:16] size of the source in bytes, unsigned 32 bits
    [16:]   the marshalled code object

The name of the pyc file holds the cache tag of the interpreter and the
manual optimization, see pyc_path(): a pyc file of an other interpreter
is never looked at here, the magic number would reject it anyway.

The checks run twice, and both have to pass to skip the compilation:

    - the pyc file is not older than its source, stricter than python:
      the header holds the mtime truncated to seconds, so a source
      replaced in the same second by a file of the same size leaves a
      header behind that python can not tell from a valid one, while
      the pyc file itself dates from the former source
    - the header matches the stat of the source, exactly like python

Being stricter only compiles a file that python would have reused, the
other way around would run the bytecode of a former source.

The order matters as much as the checks. The mtime of the pyc file is
read without opening it, from the stat of the source folder listing and
of the __pycache__ folder listing, and a pyc file older than its source
is stale right there: no byte of it is read. That is the state of every
source of a release that was just updated, so the scan of a cold tree
opens no file at all. Only a pyc file dated after its source is opened,
and then only for the 16 bytes of the header, with a raw file object so
python reads no more than that.

The scan is split along that line. plan() walks the scope and yields one
work item per source, ensure() answers whether the item has to be
compiled. The two halves are the io half and the other io half, and they
are separable so a caller with a pool of threads runs the ensures
elsewhere: listing a folder is one os.scandir() per folder whatever the
number of sources it holds, so a folder is never cut between two tasks,
and the ensures are one per source, so they split freely.

Where the stat of a directory entry is taken depends on the platform,
see DIRENT_STAT_IS_FREE. Windows reads it from the enumeration, so the
listing takes it and a pyc file older than its source is answered stale
without a single open. POSIX answers the names only, so a stat there is
a syscall on the one thread of the run that is not parallel: it travels
to the worker, which opens the pyc file anyway and reads the stat of its
descriptor instead of calling fstatat() by path first.

The listings come in one order. The pyc file of a source is named after
the source alone, so __pycache__ is listed first: a source without a pyc
file is stale there and then, and no stat of it is taken anywhere. The
size of a source is never carried through the scan, only the path, which
is all a run needs.

This module is imported by the warmup subprocess before anything else:
it only imports the standard library and pyc_path(), it must never
import the logger nor the mod.
"""
import os
import time
from importlib.util import MAGIC_NUMBER

from alasio.deploy.simple_pip.pyc import PYCACHE, pyc_path
from alasio.ext.path.atomic import atomic_open
from alasio.ext.path.calc import joinpath, to_posix

# Size of the header of a timestamp based pyc file, python 3.7 and later
HEADER_SIZE = 16
# Size of the magic number, the first field of the header
MAGIC_SIZE = 4
# Flags of the header, bit 0 hash based, bit 1 check source
FLAGS_MASK = 0b11
HASH_BASED = 0b1
# The mtime and the size of a header are unsigned 32 bits
STAT_MASK = 0xFFFFFFFF
# Whether the stat of a directory entry is free on this platform.
#
# Windows reads it from the enumeration itself: FindFirstFileW answers the
# size and the three timestamps, so DirEntry.stat() is a lookup in memory.
# POSIX answers the name and the type only, so DirEntry.stat() is an
# fstatat() syscall.
#
# The listing runs on the calling thread, the one thread of a run that is
# not parallel, so a stat is only taken there where it costs nothing. It is
# not about the total work: fstatat() by path is cheaper than the open that
# the mtime gate saves. It is about the thread, which has one thread of
# budget while the workers have several.
DIRENT_STAT_IS_FREE = os.name == 'nt'


def parse_header(header):
    """
    Parse the stat of the source that the header of a pyc file holds.

    The rules mirror importlib._bootstrap_external.SourceLoader: the
    magic number of the running interpreter and the flags, and a hash
    based pyc file is refused as a whole. Its header holds a hash of
    the source rather than its stat, and the hash is not checked here:
    compiling the source again is the conservative answer, this module
    only ever decides whether a compilation may be skipped.

    The header holds the mtime of the source truncated to seconds and
    its size as unsigned 32 bits, and both are answered as recorded: the
    comparison with the stat of the source is left to the caller.

    Args:
        header (bytes): First bytes of a pyc file, at least
            HEADER_SIZE of them

    Returns:
        tuple[int, int] | None: The mtime and the size of the source, in
            the shape of the item of ensure(), both unsigned 32 bits.
            None when the header can not be the header of a timestamp
            based pyc file of this interpreter
    """
    if len(header) < HEADER_SIZE:
        # A file shorter than a header can not hold any bytecode
        return None
    if header[:MAGIC_SIZE] != MAGIC_NUMBER:
        # A pyc file of an other python version, e.g. left by an update
        # of the interpreter
        return None
    flags = int.from_bytes(header[4:8], 'little')
    if flags & ~FLAGS_MASK:
        # Python raises ImportError on an unknown flag
        return None
    if flags & HASH_BASED:
        # A hash based pyc file, e.g. written with SOURCE_DATE_EPOCH or
        # CHECKED_HASH: no interpreter writes one by default
        return None
    return (
        int.from_bytes(header[8:12], 'little'),
        int.from_bytes(header[12:16], 'little'),
    )


def stale_sources(py_files):
    """
    Find the source files whose bytecode cache has to be compiled.

    This is the serial reference of the module, and the scope of a dry
    run: it walks the generator of plan() on the calling thread, so it
    reads every answer itself rather than handing the work to a pool.

    Only the pyc file of the running interpreter is looked at, the pyc
    files of the other interpreters and of the manual optimizations are
    left alone, see pyc_path().

    Args:
        py_files (Iterable[str]): Paths of the .py source files, in any
            order, the same file may be listed more than once

    Returns:
        list[str]: Paths of the sources to compile, sorted
    """
    stale = [file for file in map(ensure, plan(py_files)) if file is not None]
    stale.sort()
    return stale


def plan(py_files, now=None):
    """
    Walk a scope and yield one work item per source, folder by folder.

    This is the single traversal of the module: a caller batches what it
yields and hands the batches to a pool, and the work of a batch is
    ensure() over its items. The generator lists a folder and yields its
    items before the folder after it is listed, so a caller that compiles
    the batches as they come overlaps the listings with the compilations.

    The listing is the unit that must not be cut: a folder listed twice
    pays twice, whatever the number of items a batch keeps, so the caller
    batches the items and never splits a listing.

    Args:
        py_files (Iterable[str]): Paths of the .py source files, in any
            order, the same file may be listed more than once
        now (float): Current time. Defaults to None, time.time()

    Yields:
        tuple[str, str | None, tuple | None, float]: The work item of one
            source, see ensure(): its path, the pyc file to look at or
            None when the listing already settled it, the stat of the
            source as (mtime, size) or None to read it where it is
            ensured, and the current time
    """
    folders = {}
    for file in py_files:
        path = to_posix(str(file))
        folder, _, name = path.rpartition('/')
        if not name:
            continue
        folders.setdefault(folder, set()).add(name)

    if now is None:
        now = time.time()
    for folder, names in folders.items():
        yield from plan_folder(folder, names, now)


def plan_folder(folder, names, now):
    """
    List one folder and answer a work item for every source of it.

    One listing of the __pycache__ folder and one of the folder itself,
    and no other call where a directory entry answers the stat, see
    DIRENT_STAT_IS_FREE.

    The pyc file of a source is named after the source alone, so the
    listing of __pycache__ comes first and tells which sources have no
    pyc file at all. Those are stale there and then, without a stat of
    their own: the answer of a stat would only repeat what the listing
    said, that the pyc file does not exist. Their item carries no pyc
    file, and ensure() compiles them as they are.

    What the two listings can not settle is left to ensure(), which runs
    on a worker: the header of a pyc file, and the stat of a source on
    the platform where a directory entry does not answer it.

    Args:
        folder (str): Posix path of the folder
        names (set[str]): File names of the sources to check
        now (float): Current time, time.time()

    Returns:
        list[tuple]: The work items of the folder, see plan()
    """
    # The pyc file of every source, by the name it caches to, and that
    # name is derived from the source name alone: the __pycache__ folder
    # is listed first, so the sources without a pyc file are known before
    # the source folder is looked at. A name that is not a python source,
    # e.g. "demo.pyi", has no cache and is not a file to compile
    pyc_names = {}
    for name in names:
        try:
            pyc_names[name] = pyc_path(name).rpartition('/')[2]
        except ValueError:
            continue
    if not pyc_names:
        return []

    pyc_folder = joinpath(folder, PYCACHE)
    wanted = set(pyc_names.values())
    if DIRENT_STAT_IS_FREE:
        # The mtime of a pyc file answers the gate of a source, and the
        # entry of the source answers its stat, so neither half opens a
        # file and the cold tree costs nothing
        pyc_mtimes = _list_pyc_mtimes(pyc_folder, wanted)
    else:
        # The names alone tell which pyc files exist, and every stat is
        # left to the worker: it opens the pyc file anyway and reads the
        # stat of its descriptor, see ensure()
        pyc_there = _list_pyc_names(pyc_folder, wanted)

    items = []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                name = entry.name
                pyc_name = pyc_names.get(name)
                if pyc_name is None:
                    # Not a source of the scope, e.g. an entry of a
                    # __pycache__ folder that holds thousands of files
                    continue
                file = joinpath(folder, name)
                if DIRENT_STAT_IS_FREE:
                    pyc_mtime = pyc_mtimes.get(pyc_name)
                    if pyc_mtime is None:
                        # The first run after an update: no pyc file at
                        # all, and no stat says more than the listing
                        items.append((file, None, None, now))
                        continue
                    try:
                        st = entry.stat()
                    except OSError:
                        # Removed between the listing and the stat, or an
                        # entry the stat can not answer for
                        continue
                    mtime, size = st.st_mtime, st.st_size
                    if mtime > pyc_mtime and mtime <= now:
                        # Stricter than python, see the module docstring: a
                        # pyc file older than its source can not hold the
                        # bytecode of that source, and the answer needs not
                        # a byte of the file. This is the state of every
                        # source of a release that was just updated
                        items.append((file, None, None, now))
                        continue
                    # A source dated in the future, e.g. written before the
                    # clock was set back, is older than every pyc file
                    # compiled after it: the header decides alone, otherwise
                    # the file would be compiled at every run
                    items.append((file, joinpath(pyc_folder, pyc_name), (mtime, size), now))
                    continue
                if pyc_name not in pyc_there:
                    # The listing of the __pycache__ folder holds no such
                    # pyc file: it does not exist, e.g. the first run after
                    # an update, and the source is stale without any other
                    # call
                    items.append((file, None, None, now))
                    continue
                # The stat of the source is left to the worker, see
                # ensure()
                items.append((file, joinpath(pyc_folder, pyc_name), None, now))
    except OSError:
        # An unreadable folder holds no source to compile, e.g. a
        # folder removed by a package update during the run
        return []
    return items


def ensure(item):
    """
    Answer the source of one work item when it has to be compiled.

    This is the work of a batch: an item is

        (file, pyc, source, now)

    A pyc file of None means the listing already settled the answer, the
    source has no bytecode cache and is compiled as it is. Otherwise the
    pyc file is opened, and its mtime and header decide: the source is
    compiled only when the cache does not hold its bytecode.

    A source of None means the listing took no stat, see
    DIRENT_STAT_IS_FREE, and the stat is read here, on a worker, from the
    descriptor of the pyc file that is opened anyway. The stat of the
    source itself is one call: the open comes first because the answer of
    the descriptor costs less than an fstatat() by path followed by the
    open.

    One task of a pool holds every kind of item, and a task almost always
    does: the sources without a pyc file come from a listing, the pyc
    files that can still be fresh come from the same listing, and
    splitting them into two kinds of task only makes the queue of the
    pool read worse.

    Args:
        item (tuple): The work item of one source, see plan()

    Returns:
        str | None: Path of the source when it has to be compiled, None
            when its pyc file holds its bytecode or the source is gone
    """
    file, pyc, source, now = item
    if pyc is None:
        # The listing settled it: there is no pyc file to look at
        return file
    if source is None:
        # The listing took no stat, so both answers come from here: the
        # stat of the source by path, the mtime of the pyc file from the
        # descriptor of the file that is opened anyway
        try:
            st = os.stat(file)
        except OSError:
            # Removed since the listing, there is nothing to compile
            return None
        mtime, size = st.st_mtime, st.st_size
        try:
            with atomic_open(pyc, 'rb', buffering=0) as f:
                # The clock first: a source dated in the future is compared
                # by its header alone, so the descriptor needs no stat
                if mtime <= now and mtime > os.fstat(f.fileno()).st_mtime:
                    # Stricter than python, see the module docstring: a pyc
                    # file older than its source can not hold the bytecode
                    # of that source, and the answer needs no byte of it
                    return file
                header = f.read(HEADER_SIZE)
        except OSError:
            # Unreadable, e.g. held with an exclusive share mode by a
            # scanner that the retry of atomic_open outlasted
            return file
    else:
        # The listing stat-ed the source, so only the header is read
        mtime, size = source
        try:
            # buffering=0 reads the 16 bytes of the header and nothing else:
            # a buffered file object would read a block of the disk instead
            with atomic_open(pyc, 'rb', buffering=0) as f:
                header = f.read(HEADER_SIZE)
        except OSError:
            # Unreadable, e.g. held with an exclusive share mode by a
            # scanner that the retry of atomic_open outlasted
            return file
    # The header holds the stat of the source truncated to 32 bits, and
    # its mtime truncated to seconds
    if parse_header(header) == (int(mtime) & STAT_MASK, size & STAT_MASK):
        return None
    return file


def _list_pyc_names(pyc_folder, names):
    """
    The asked pyc files present in a __pycache__ folder.

    The listing alone, no stat per pyc file: a caller that lists the
    folder without stat-ing its entries only needs to know whether the
    pyc file of a source is there, and ensure() reads its other answers
    from the file it opens anyway.

    Args:
        pyc_folder (str): Posix path of the __pycache__ folder
        names (set[str]): File names of the pyc files to look for

    Returns:
        set[str]: The names of the asked pyc files that exist. A missing
            folder answers an empty set, e.g. the first run of a tree
            that was never imported
    """
    found = set()
    try:
        with os.scandir(pyc_folder) as entries:
            for entry in entries:
                if entry.name in names:
                    found.add(entry.name)
    except OSError:
        return set()
    return found


def _list_pyc_mtimes(pyc_folder, names):
    """
    The mtime of the asked pyc files of a __pycache__ folder, by name.

    A listing, rather than a stat per pyc file: the directory entry of
    Windows holds the stat of the file, so the mtime of thousands of
    pyc files costs one call. Only the names asked for are stat-ed, a
    __pycache__ folder also holds the pyc files of the other
    interpreters and of the sources the caller did not ask for.

    Args:
        pyc_folder (str): Posix path of the __pycache__ folder
        names (set[str]): File names of the pyc files to stat

    Returns:
        dict[str, float]: File name of the pyc file to its mtime. A
            missing folder answers an empty dict, e.g. the first run of
            a tree that was never imported
    """
    mtimes = {}
    try:
        with os.scandir(pyc_folder) as entries:
            for entry in entries:
                name = entry.name
                if name not in names:
                    continue
                try:
                    mtimes[name] = entry.stat().st_mtime
                except OSError:
                    # Removed between the listing and the stat
                    continue
    except OSError:
        return {}
    return mtimes
