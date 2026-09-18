"""
Directory crawling, the source side of packing.

The walk is deterministic: every directory comes before its content, siblings
are sorted by name (Unicode code point order), so packing the same tree twice
gives the same bytes, and a tree packed on Windows matches the same tree packed
on Linux.
"""
import os

from .errors import AsarError, AsarUnsupportedError
from .model import KIND_DIR, KIND_FILE, has_parent
from .pattern import GlobDirPattern, GlobPattern


def list_dir(local_path, arc_prefix=None, missing_ok=False):
    """
    List the content of a directory, sorted by name.

    Args:
        local_path (str): Directory to list
        arc_prefix (str): Archive path of the directory, None for the root
        missing_ok (bool): Return an empty list when the directory is not there.
            A directory that disappears while a tree is walked is not an entry
            of the archive any more, while the source directory of a pack is
            asked for without it: a source that is not there is a mistake of
            the caller, not an empty archive

    Returns:
        list: ``[(arc_path, local_path, kind)]``, directories and files are
            interleaved in name order

    Raises:
        AsarError: If the directory can not be listed
        AsarUnsupportedError: If an entry is neither a file nor a directory
    """
    try:
        with os.scandir(local_path) as it:
            entries = sorted(it, key=lambda entry: entry.name)
    except OSError as e:
        if missing_ok and isinstance(e, FileNotFoundError):
            # A Windows junction whose target is gone is listed as a directory
            # but has nothing to list: the reference implementation packs it as
            # an empty directory, and the entry is already with its parent
            return []
        raise AsarError(f'Unable to list directory "{local_path}": {e}') from e
    result = []
    for entry in entries:
        arc_path = entry.name if not arc_prefix else f'{arc_prefix}/{entry.name}'
        # is_dir() and is_file() follow symbolic links, the same way the JS
        # packer resolves them (a link to a file is packed as a file)
        if entry.is_dir():
            result.append((arc_path, entry.path, KIND_DIR))
        elif entry.is_file():
            result.append((arc_path, entry.path, KIND_FILE))
        else:
            raise AsarUnsupportedError(
                f'Unsupported file type of "{arc_path}", it is neither a file nor a directory'
            )
    return result


def crawl_tree(root, include=None):
    """
    Walk a directory tree level by level.

    Args:
        root (str): Root directory
        include (GlobPattern): Patterns the archive keeps, None to walk the whole
            tree. A directory that no pattern can reach is not entered, so an
            include list of `dist/**` and `package.json` never reads
            `node_modules`

    Returns:
        list: ``[(arc_path, local_path, kind)]``, a directory always comes
            before its content

    Raises:
        AsarError: If the root is not there, or if a directory that is there can
            not be listed
    """
    root = os.path.abspath(root)
    # The entries of one level are read together and only the directories they
    # hold are the work of the next turn, so no entry travels through the stack
    # again. Within a level siblings keep the sorted order of their directory,
    # and a directory is always read before its content. The root is listed
    # without `missing_ok`, the directories below it are read with it: a
    # directory that is not there any more is empty
    level = list_dir(root)
    order = []
    while level:
        next_level = []
        for arc_path, local_path, kind in level:
            order.append((arc_path, local_path, kind))
            if kind != KIND_DIR:
                continue
            if include is not None and not include.may_match_below(arc_path):
                # The directory stays an entry of the tree, it is only not
                # entered: nothing below it can be selected, so there is
                # nothing to read
                continue
            next_level.extend(list_dir(local_path, arc_prefix=arc_path, missing_ok=True))
        level = next_level
    return order


def crawl_folder(root, include=None, exclude=None, unpack=None, unpack_dir=None):
    """
    Walk a directory tree and decide what goes into the archive.

    Args:
        root (str): Root directory
        include (list): Glob patterns of the entries to add, None to add every
            file of the tree. An empty directory that matches is kept.
        exclude (list): Glob patterns of the entries to drop, dropping a
            directory drops its whole subtree
        unpack (list): Glob patterns of the files to store next to the archive
            instead of inside it, a pattern without '/' matches the file name
        unpack_dir (list): Patterns of the directories to store next to the
            archive, their whole content follows

    Returns:
        list: ``[(arc_path, local_path, kind, unpacked)]`` in archive order
    """
    # The patterns are compiled once here, and released with the crawl
    include = GlobPattern(include) if include else None
    exclude = GlobPattern(exclude) if exclude else None
    unpack = GlobPattern(unpack, match_base=True) if unpack else None
    unpack_dir = GlobDirPattern(unpack_dir) if unpack_dir else None

    order = crawl_tree(root, include=include)
    # A dropped directory drops everything below it
    dropped = set()
    for arc_path, _, kind in order:
        if kind != KIND_DIR:
            continue
        if has_parent(arc_path, dropped):
            dropped.add(arc_path)
        elif exclude and exclude.match(arc_path):
            dropped.add(arc_path)

    # A file is kept when it is not excluded and matches the include patterns
    files = []
    for arc_path, local_path, kind in order:
        if kind != KIND_FILE or has_parent(arc_path, dropped):
            continue
        if exclude and exclude.match(arc_path):
            continue
        if include and not include.match(arc_path):
            continue
        files.append((arc_path, local_path))

    # Directories are kept when their content is kept, or when they match the
    # include patterns themselves (so an empty directory can be added on purpose)
    needed = set()
    if include:
        for arc_path, _, kind in order:
            if kind == KIND_DIR and include.match(arc_path):
                needed.add(arc_path)
    for arc_path, _ in files:
        while True:
            arc_path = arc_path.rpartition('/')[0]
            if not arc_path or arc_path in needed:
                break
            needed.add(arc_path)

    entries = []
    included = set(path for path, _ in files)
    for arc_path, local_path, kind in order:
        if arc_path in dropped or has_parent(arc_path, dropped):
            continue
        if kind == KIND_DIR:
            if include and arc_path not in needed:
                continue
            unpacked = False
            if unpack_dir:
                unpacked = unpack_dir.match(arc_path)
            entries.append((arc_path, local_path, KIND_DIR, unpacked))
            continue
        if arc_path not in included:
            continue
        unpacked = False
        if unpack:
            unpacked = unpack.match(arc_path)
        entries.append((arc_path, local_path, KIND_FILE, unpacked))
    return entries
