"""
Two strategies for cleaning a tree of the files an update does not keep.

CleanupFolder collects the folders a removal leaves empty: the caller removes
the files first, then removes the folders the answer lists. CleanupByPreserved
is the inverse: the caller registers what it keeps and every entry that is not
preserved is collected, without removing anything first. A folder with nothing
preserved at or below it is one entry, its subtree is not walked, so the
cleanup of a tree costs one walk of its preserved branches instead of one walk
of everything.

The paths are the ones of the caller: CleanupFolder takes relative paths, the
style of the paths of a pack, e.g. "alasio/module/core.py"; CleanupByPreserved
takes absolute paths, e.g. "/env/module/core.py". In both the root of the tree
is never returned and the removal can never escape it. The removal itself is
left to the caller in both strategies.
"""

import os

from alasio.ext.path.calc import is_root, uppath


class CleanupFolder:
    """
    Collect the folders left empty by the removal of files.

    The caller registers the paths it knows: the files that exist, the
    folders that exist and the files that were removed, then
    get_cleanup_folders() returns the folders to remove, the deepest
    first:

        cleaner = CleanupFolder()
        cleaner.register_file(new_files)          # files that exist now
        cleaner.register_folder(new_folders)      # folders that exist now
        cleaner.register_deleted(removed_files)   # files that are removed
        for folder in cleaner.get_cleanup_folders():
            folder_rmtree_empty(root.joinpath(folder))

    A registered file keeps its folder alive: the folder of the file and
    every parent folder of it is never returned. A registered folder and
    the folder of a removed file are removal candidates: they are
    returned when no registered file is left at or below them, together
    with the parent folders that the removal of a last entry leaves
    empty. A path registered as removed is dropped from the registered
    files and folders, they describe what is left: the last registration
    wins. The removal itself is left to the caller: os.rmdir() only
    removes an empty folder, so a folder that still holds a file of no
    record, e.g. a file placed by hand, fails the removal and is kept.

    The paths are relative to the root folder of the caller, the style of
    the paths of a pack, e.g. "alasio/module/core.py": the root itself is
    the empty string and is never returned, so the removal can never
    escape the tree of the caller. An absolute path is accepted too, the
    walk of the parent folders stops at the filesystem root ("/") and at
    the drive root ("C:/"), e.g. "/env" and "C:/env" are the top level
    folders returned. The roots themselves are never returned, even when
    they are registered as folders.
    """

    def __init__(self):
        # Paths of the files that exist, they keep their folders alive
        self._files: "set[str]" = set()
        # Paths of the folders that exist, the removal candidates
        self._folders: "set[str]" = set()
        # Paths of the files that are removed, their folders are the
        # removal candidates
        self._deleted: "set[str]" = set()

    def register_file(self, path):
        """
        Register the paths of the files that exist.

        A file keeps its folder alive: the folder of the file and every
        parent folder of it is never returned by get_cleanup_folders(),
        until the file itself is registered as removed.

        Args:
            path (str | set[str] | dict[str, any] | list[str] | tuple[str]):
                Paths of the files, one path, or many paths in a set, a
                dict (its keys are the paths), a list or a tuple
        """
        self._files |= _to_paths(path)

    def register_folder(self, path):
        """
        Register the paths of the folders that exist.

        A folder is a removal candidate: it is returned by
        get_cleanup_folders() when no file at or below it is registered,
        e.g. an empty folder is removed, a folder holding a file at any
        depth is kept.

        Args:
            path (str | set[str] | dict[str, any] | list[str] | tuple[str]):
                Paths of the folders, one path, or many paths in a set, a
                dict (its keys are the paths), a list or a tuple
        """
        self._folders |= _to_paths(path)

    def register_deleted(self, path):
        """
        Register the paths of the files that are removed.

        A removed path is dropped from the registered files and folders:
        they describe what is left. The folder of a removed file is a
        removal candidate: it is returned by get_cleanup_folders() when
        no file at or below it is registered, together with the parent
        folders that the removal of a last entry leaves empty.

        Args:
            path (str | set[str] | dict[str, any] | list[str] | tuple[str]):
                Paths of the removed files, one path, or many paths in a
                set, a dict (its keys are the paths), a list or a tuple
        """
        paths = _to_paths(path)
        self._deleted |= paths
        self._files -= paths
        self._folders -= paths

    def get_cleanup_folders(self):
        """
        Get the folders to remove.

        Returns:
            list[str]: Paths of the folders, the deepest first, so a
                folder is removed before its parent folder. The root of
                the caller (the empty string, "/" or "C:/") is never
                listed, the caller removes the folders with os.rmdir(): a
                folder that is not empty fails the removal and is kept
        """
        # The folders holding a file, with their parent folders: they are
        # never removed. The walk stops at the first folder the set
        # already holds, the parent folders above it were collected by
        # the walk that added the folder
        occupied = set()
        for path in self._files:
            for parent in _iter_parents(path):
                if parent in occupied:
                    break
                occupied.add(parent)
        # The removal candidates: the registered folders, the folders of
        # the removed files and the parent folders a removal leaves empty.
        # Same early stop, the parent folders of a registered folder are
        # collected by the walk of the folder itself. A root is not a
        # folder of the caller and is never a candidate, even when it is
        # registered: the empty string of a relative path, the filesystem
        # root "/" and the drive root "C:/" of an absolute path
        folders = {
            folder
            for folder in self._folders
            if folder and not is_root(folder)
        }
        for path in self._folders:
            for parent in _iter_parents(path):
                if parent in folders:
                    break
                folders.add(parent)
        for path in self._deleted:
            for parent in _iter_parents(path):
                if parent in folders:
                    break
                folders.add(parent)
        # The deepest first, a folder is removed before its parent folder,
        # the path breaks the ties for a deterministic order
        return sorted(folders - occupied, key=lambda path: (-path.count('/'), path))


class CleanupByPreserved:
    """
    Collect the entries of a tree that are not preserved.

    The inverse of CleanupFolder: the caller registers what the tree holds and
    what the run preserves, and get_cleanup_entries() returns what is left. The
    class does no IO, like CleanupFolder: the caller walks the tree and does
    the removal itself.

        cleaner = CleanupByPreserved()
        cleaner.register_file(files)                 # files that exist now
        cleaner.register_folder(folders)             # folders that exist now
        cleaner.register_preserved(kept_files)       # files that stay
        cleaner.register_preserved_folder('log')     # folders that stay whole
        for path, is_dir in cleaner.get_cleanup_entries():
            ...                                      # remove it, a folder
                                                     # entry means its subtree

    A preserved file keeps its folder and every parent folder of it: those
    folders are descended into and their other entries are returned. A folder
    with no preserved file at or below it is returned as one folder entry, its
    registered files are not returned one by one, so the removal of a whole
    version folder is one entry instead of one per file. A preserved folder is
    never returned, and neither is anything below it.

    The registration order is the one of CleanupFolder: the tree is registered
    first (register_file / register_folder), then what the run preserves
    (register_preserved / register_preserved_folder). A registration of a
    preserved path drops the matching registered entries on the spot, so
    get_cleanup_entries() is one pass over the entries that are left.

    The paths are absolute, the paths the caller walks and registers, e.g.
    "/env/module/core.py" or "C:/env/module/core.py": the root of the
    filesystem and the root of a drive are never returned as a folder, and the
    walk of the parent folders stops at them, so a removal can never escape the
    tree of the caller. The paths are normalized to '/' separators, a
    registration and an answer of one file are the same string on every system.
    """

    def __init__(self):
        # Paths of the files that exist
        self._files: "set[str]" = set()
        # Paths of the folders that exist
        self._folders: "set[str]" = set()
        # Paths of the files that are preserved
        self._preserved: "set[str]" = set()
        # Paths of the folders that are preserved with their content
        self._preserved_folders: "set[str]" = set()

    def register_file(self, path):
        """
        Register the paths of the files that exist.

        Args:
            path (str | set[str] | dict[str, any] | list[str] | tuple[str]):
                Absolute paths of the files, one path, or many paths in a set,
                a dict (its keys are the paths), a list or a tuple
        """
        self._files |= _to_entries(path)

    def register_folder(self, path):
        """
        Register the paths of the folders that exist.

        Args:
            path (str | set[str] | dict[str, any] | list[str] | tuple[str]):
                Absolute paths of the folders, one path, or many paths in a
                set, a dict (its keys are the paths), a list or a tuple
        """
        self._folders |= _to_entries(path)

    def register_preserved(self, path):
        """
        Register the paths of the files that are preserved.

        A preserved file keeps its folder alive: the folder and every parent
        folder of it are descended into, the other entries of them are
        returned. The file itself is dropped from the registered entries on the
        spot, so the walk of the answer never looks a preserved path up: the
        tree is registered first, then the preserved paths, like the
        registrations of CleanupFolder (the last registration wins).

        Args:
            path (str | set[str] | dict[str, any] | list[str] | tuple[str]):
                Absolute paths of the files, one path, or many paths in a set,
                a dict (its keys are the paths), a list or a tuple
        """
        paths = _to_entries(path)
        self._preserved |= paths
        self._files -= paths
        self._folders -= paths

    def register_preserved_folder(self, path):
        """
        Register the paths of the folders that are preserved with their content.

        A preserved folder is never returned, nor is anything below it: the
        registered entries at or below it are dropped on the spot, so the walk
        of the answer never compares a path with the preserved folders.

        Args:
            path (str | set[str] | dict[str, any] | list[str] | tuple[str]):
                Absolute paths of the folders, one path, or many paths in a
                set, a dict (its keys are the paths), a list or a tuple
        """
        folders = _to_entries(path)
        self._preserved_folders |= folders
        self._files = {entry for entry in self._files if not _is_below(entry, folders)}
        self._folders = {entry for entry in self._folders if not _is_below(entry, folders)}

    def get_cleanup_entries(self):
        """
        Get the entries to remove, the deepest first.

        The entries are the files that are not preserved, the folders that go
        with their content, and the folders that only become empty once their
        files are removed: a registered file that is not preserved leaves its
        folder empty, the folder goes after the file. The caller removes them
        in the order of the answer, a file before its folder and a folder
        before its parent folder, with file_remove() and folder_rmtree_empty().

        Returns:
            list[tuple[str, bool]]: (Path, True for a folder) of the entries to
                remove, the deepest first, each path once
        """
        # the folders that hold a preserved entry, all the way up: they are
        # kept, and so is everything below a preserved folder
        kept = set()
        for path in self._preserved | self._preserved_folders:
            kept.add(path)
            for parent in _iter_parents(path):
                kept.add(parent)

        # every file that is left goes, the preserved ones were dropped by the
        # registration; the folders the caller registered are removed after
        # them, the deepest first, a folder that still holds a preserved file
        # is in kept and is left alone
        files = set(self._files)
        emptied = {folder for folder in self._folders if folder not in kept}

        entries = [(path, False) for path in files]
        entries.extend((path, True) for path in emptied)
        # the deepest first: a file before its folder, a folder before its
        # parent folder, the path breaks the ties for a deterministic order
        entries.sort(key=lambda entry: (-entry[0].count('/'), entry[0]))
        return entries


def _is_below(path, folders):
    """
    Whether a path is below one of the folders, itself included.

    Args:
        path (str): Normalized path
        folders (set[str]): Normalized folders

    Returns:
        bool: True when the path is a folder of the set or below one
    """
    if not folders:
        return False
    if path in folders:
        return True
    for parent in _iter_parents(path):
        if parent in folders:
            return True
    return False


def _iter_parents(path):
    """
    Iter the parent folders of a path, the deepest first.

    The walk stops before the root of the tree: the parent of a relative
    path is the empty string, the parent folders of an absolute path
    stop at the top level folder, e.g. "/env" of "/env/Lib/x.py" on
    Linux and "C:/env" of "C:/env/Lib/x.py" on Windows.

    Args:
        path (str): Normalized path of a file or a folder

    Yields:
        str: Normalized paths of the parent folders
    """
    folder = uppath(path)
    while folder:
        # the root of the filesystem and the root of a drive are not
        # folders of the tree of the caller
        if is_root(folder):
            break
        yield folder
        folder = uppath(folder)


def _to_entries(path):
    """
    Normalize a registration into a set of absolute paths.

    Args:
        path (str | set[str] | dict[str, any] | list[str] | tuple[str]):
            One path, or many paths in a set, a dict (its keys are the paths),
            a list or a tuple

    Returns:
        set[str]: The paths, '/'-separated

    Raises:
        TypeError: If the argument is not a str, a set, a dict, a list nor a
            tuple
    """
    return {entry.replace(os.sep, '/') for entry in _to_paths(path)}


def _to_paths(path):
    """
    Normalize a path argument into a set of paths.

    Args:
        path (str | set[str] | dict[str, any] | list[str] | tuple[str]):
            One path, or many paths in a set, a dict (its keys are the
            paths), a list or a tuple

    Returns:
        set[str]: The paths

    Raises:
        TypeError: If the argument is not a str, a set, a dict, a list
            nor a tuple
    """
    if isinstance(path, str):
        return {path}
    if isinstance(path, (set, dict, list, tuple)):
        return set(path)
    raise TypeError(
        f'A path must be a str, a set, a dict, a list or a tuple, got {type(path).__name__}'
    )
