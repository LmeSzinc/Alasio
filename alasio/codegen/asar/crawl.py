"""
Directory crawling, the source side of packing.

The walk is deterministic: every directory comes before its content, siblings
are sorted by name (Unicode code point order), so packing the same tree twice
gives the same bytes, and a tree packed on Windows matches the same tree packed
on Linux.
"""
import os
import stat

from .errors import AsarError, AsarUnsupportedError
from .model import KIND_DIR, KIND_FILE

# Attribute of a Windows directory whose content lives somewhere else: a
# junction and the other reparse points alias a directory without being a
# symbolic link. The constant and the attribute it is read from are not there
# on POSIX, where every alias of a directory is a symbolic link.
FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)


def is_a_way_back(entry):
    """
    Tell whether a link of a directory points back at a directory that holds it.

    Following such a link walks the directory that holds the link again, and
    the link with it: the walk would never end. Only a link is looked at, and
    what it points at is resolved (a junction of Windows is not a symbolic
    link, but it aliases a directory the same way) before it is compared with
    the path that reached the entry.

    Args:
        entry (os.DirEntry): Directory entry, of a directory

    Returns:
        bool: True when following the entry would walk the tree forever
    """
    if not entry.is_symlink():
        # The attribute of the entry is not there on every platform, and it is
        # None when a stat result was built without it
        attributes = getattr(entry.stat(follow_symlinks=False), 'st_file_attributes', 0) or 0
        if not attributes & FILE_ATTRIBUTE_REPARSE_POINT:
            return False
    path = os.path.normcase(os.path.normpath(entry.path))
    target = os.path.normcase(os.path.normpath(os.path.realpath(entry.path)))
    # The entry is inside the directory the link points at, so that directory
    # holds the link and walking it walks the link again. The target is given a
    # trailing separator to only match whole names, and the root of a file
    # system (where every path is inside) is kept as it is
    return path.startswith(os.path.join(target, ''))


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
        AsarUnsupportedError: If an entry is neither a file nor a directory, or
            if a link points back at a directory that holds it
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
            if is_a_way_back(entry):
                raise AsarUnsupportedError(
                    f'Directory "{arc_path}" is a link back to a directory above it, '
                    f'it would be walked forever'
                )
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
    Walk a directory tree level by level.

    Args:
        root (str): Root directory

    Returns:
        list: ``[(arc_path, local_path, kind)]``, a directory always comes
            before its content

    Raises:
        AsarError: If the root is not there, or if a directory that is there can
            not be listed
        AsarUnsupportedError: If a link of the tree points back at a directory
            that holds it, see ``is_a_way_back()``
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
            next_level.extend(list_dir(local_path, arc_prefix=arc_path, missing_ok=True))
        level = next_level
    return order
