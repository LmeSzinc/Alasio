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
from .pattern import match_any, match_dir


def list_dir(local_path, arc_prefix=None):
    """
    List the content of a directory, sorted by name.

    Args:
        local_path (str): Directory to list
        arc_prefix (str): Archive path of the directory, None for the root

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
        raise AsarError(f'Unable to list directory "{local_path}": {e}')
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


def crawl_tree(root):
    """
    Walk a directory tree depth first.

    Args:
        root (str): Root directory

    Returns:
        list: ``[(arc_path, local_path, kind)]``, a directory always comes
            before its content

    Raises:
        AsarError: If the directory can not be listed
    """
    root = os.path.abspath(root)
    # The stack pops the last item first, siblings are pushed reversed to keep
    # the sorted order, the walk is iterative so that a deep tree is fine
    stack = list(reversed(list_dir(root)))
    order = []
    while stack:
        arc_path, local_path, kind = stack.pop()
        order.append((arc_path, local_path, kind))
        if kind == KIND_DIR:
            stack.extend(reversed(list_dir(local_path, arc_prefix=arc_path)))
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
    include = list(include) if include else None
    exclude = list(exclude) if exclude else None
    unpack = list(unpack) if unpack else None
    unpack_dir = list(unpack_dir) if unpack_dir else None

    order = crawl_tree(root)
    # A dropped directory drops everything below it
    dropped = set()
    for arc_path, _, kind in order:
        if kind != KIND_DIR:
            continue
        if has_parent(arc_path, dropped):
            dropped.add(arc_path)
        elif exclude and match_any(arc_path, exclude):
            dropped.add(arc_path)

    # A file is kept when it is not excluded and matches the include patterns
    files = []
    for arc_path, local_path, kind in order:
        if kind != KIND_FILE or has_parent(arc_path, dropped):
            continue
        if exclude and match_any(arc_path, exclude):
            continue
        if include and not match_any(arc_path, include):
            continue
        files.append((arc_path, local_path))

    # Directories are kept when their content is kept, or when they match the
    # include patterns themselves (so an empty directory can be added on purpose)
    needed = set()
    if include:
        for arc_path, _, kind in order:
            if kind == KIND_DIR and match_any(arc_path, include):
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
                unpacked = any(match_dir(arc_path, pattern) for pattern in unpack_dir)
            entries.append((arc_path, local_path, KIND_DIR, unpacked))
            continue
        if arc_path not in included:
            continue
        unpacked = False
        if unpack:
            unpacked = match_any(arc_path, unpack, match_base=True)
        entries.append((arc_path, local_path, KIND_FILE, unpacked))
    return entries
