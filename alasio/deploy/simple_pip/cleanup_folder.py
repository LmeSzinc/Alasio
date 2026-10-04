from alasio.ext.path.calc import uppath


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
    the drive root ("C:"), e.g. "/env" and "C:/env" are the top level
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
                the caller (the empty string, "/" or "C:") is never
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
        # root "/" and the drive root "C:" of an absolute path
        folders = {
            folder
            for folder in self._folders
            if folder and folder != '/' and not folder.endswith(':')
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
        if folder == '/' or folder.endswith(':'):
            break
        yield folder
        folder = uppath(folder)


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
