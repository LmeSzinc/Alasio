import os


def iter_files(root, ext='', recursive=False, follow_symlinks=False):
    """
    Iter full filepath of files in folder with the good performance

    Don't do os.path.isdir() in for loop, that would be 40 times slower
        for file in os.listdir(folder):
            if os.path.isdir(os.path.join(folder, file)):
                ...

    Args:
        root (str):
        ext (str): If ext is given, iter files with extension only
            If ext is empty, iter all files
        recursive (bool): True to recursively traverse subdirectories
        follow_symlinks (bool): True to follow symlinks

    Yields:
        str: Full path
    """
    if ext:
        # Iter all files with given extension
        # iter_files(folder, suffix='.json')
        if recursive:
            try:
                with os.scandir(root) as entries:
                    for entry in entries:
                        # Iter files
                        if entry.name.endswith(ext):
                            try:
                                if entry.is_file(follow_symlinks=follow_symlinks):
                                    yield entry.path
                                    continue
                            except FileNotFoundError:
                                continue
                        # Iter subdirectories
                        try:
                            is_dir = entry.is_dir(follow_symlinks=follow_symlinks)
                        except FileNotFoundError:
                            continue
                        if is_dir:
                            yield from iter_files(entry.path, ext, recursive, follow_symlinks)
                            continue
            except (FileNotFoundError, NotADirectoryError):
                return
        else:
            try:
                with os.scandir(root) as entries:
                    for entry in entries:
                        # Iter files
                        if entry.name.endswith(ext):
                            try:
                                if entry.is_file(follow_symlinks=follow_symlinks):
                                    yield entry.path
                            except FileNotFoundError:
                                continue
            except (FileNotFoundError, NotADirectoryError):
                return
    else:
        # Iter all files (directory not included)
        # iter_files(folder)
        if recursive:
            try:
                with os.scandir(root) as entries:
                    for entry in entries:
                        # Iter files
                        try:
                            if entry.is_file(follow_symlinks=follow_symlinks):
                                yield entry.path
                                continue
                        except FileNotFoundError:
                            continue
                        # Iter subdirectories
                        try:
                            is_dir = entry.is_dir(follow_symlinks=follow_symlinks)
                        except FileNotFoundError:
                            continue
                        if is_dir:
                            yield from iter_files(entry.path, ext, recursive, follow_symlinks)
                            continue
            except (FileNotFoundError, NotADirectoryError):
                return
        else:
            try:
                with os.scandir(root) as entries:
                    for entry in entries:
                        # Iter files
                        try:
                            if entry.is_file(follow_symlinks=follow_symlinks):
                                yield entry.path
                        except FileNotFoundError:
                            continue
            except (FileNotFoundError, NotADirectoryError):
                return


def iter_filenames(root, ext='', follow_symlinks=False):
    """
    Iter filename of files in folder with the good performance

    Args:
        root (str):
        ext (str): If ext is given, iter files with extension only
            If ext is empty, iter all files
        follow_symlinks (bool): True to follow symlinks

    Yields:
        str: Filename
    """
    if ext:
        # Iter all files with given extension
        # iter_files(folder, suffix='.json')
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    if entry.name.endswith(ext):
                        try:
                            if entry.is_file(follow_symlinks=follow_symlinks):
                                yield entry.name
                        except FileNotFoundError:
                            continue
        except (FileNotFoundError, NotADirectoryError):
            return
    else:
        # Iter all files (directory not included)
        # iter_files(folder)
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    try:
                        if entry.is_file(follow_symlinks=follow_symlinks):
                            yield entry.name
                    except FileNotFoundError:
                        continue
        except (FileNotFoundError, NotADirectoryError):
            return


def iter_folders(root, recursive=False, follow_symlinks=False):
    """
    Iter full filepath of directories in folder with the good performance

    Args:
        root (str):
        recursive (bool): True to recursively traverse subdirectories
        follow_symlinks (bool): True to follow symlinks

    Yields:
        str: Full path
    """
    # Iter all directories
    # iter_folders(folder)
    if recursive:
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    # Iter subdirectories
                    try:
                        is_dir = entry.is_dir(follow_symlinks=follow_symlinks)
                    except FileNotFoundError:
                        continue
                    if is_dir:
                        yield entry.path
                        yield from iter_folders(entry.path, recursive, follow_symlinks)
                        continue
        except (FileNotFoundError, NotADirectoryError):
            return
    else:
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    # Iter folders
                    try:
                        if entry.is_dir(follow_symlinks=follow_symlinks):
                            yield entry.path
                    except FileNotFoundError:
                        continue
        except (FileNotFoundError, NotADirectoryError):
            return


def iter_foldernames(root, recursive=False, follow_symlinks=False):
    """
    Iter name of directories in folder with the good performance

    Args:
        root (str):
        recursive (bool): True to recursively traverse subdirectories
        follow_symlinks (bool): True to follow symlinks

    Yields:
        str: Folder name
    """
    # Iter all directories
    # iter_folders(folder)
    if recursive:
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    # Iter folders
                    try:
                        if entry.is_dir(follow_symlinks=follow_symlinks):
                            yield entry.name
                            continue
                    except FileNotFoundError:
                        continue
                    # Iter subdirectories
                    try:
                        is_dir = entry.is_dir(follow_symlinks=follow_symlinks)
                    except FileNotFoundError:
                        continue
                    if is_dir:
                        yield from iter_folders(entry.path, recursive, follow_symlinks)
                        continue
        except (FileNotFoundError, NotADirectoryError):
            return
    else:
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    # Iter folders
                    try:
                        if entry.is_dir(follow_symlinks=follow_symlinks):
                            yield entry.name
                    except FileNotFoundError:
                        continue
        except (FileNotFoundError, NotADirectoryError):
            return


def iter_entry(root, recursive=False, follow_symlinks=False):
    """
    Iter DirEntry objects in folder with good performance

    This yields the actual DirEntry objects from os.scandir(), which can be
    more efficient than yielding paths since DirEntry caches filesystem info.
    You can then call entry.path, entry.name, entry.is_file(), entry.is_dir(),
    entry.stat(), etc. on the results without additional filesystem calls.

    Args:
        root (str): Root directory to iterate
        recursive (bool): True to recursively traverse subdirectories
        follow_symlinks (bool): True to follow symlinks

    Yields:
        os.DirEntry: Directory entry object with cached filesystem info
    """
    if recursive:
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    # Yield the entry itself
                    yield entry
                    # Recurse into subdirectories
                    try:
                        is_dir = entry.is_dir(follow_symlinks=follow_symlinks)
                    except FileNotFoundError:
                        continue
                    if is_dir:
                        yield from iter_entry(entry.path, recursive, follow_symlinks)
        except (FileNotFoundError, NotADirectoryError):
            return
    else:
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    # Yield the entry itself
                    yield entry
        except (FileNotFoundError, NotADirectoryError):
            return


_NO_CACHE = object()


class CachePathExists:
    def __init__(self):
        self._file_exist: "dict[str, bool]" = {}

    def path_exists(self, path: str):
        """
        Args:
            path (str):

        Returns:
            bool:
        """
        exist = self._file_exist.get(path, _NO_CACHE)
        if exist == _NO_CACHE:
            # Yes, there might be a TOCTOU race condition,
            # but it's just OK to do os.path.exists twice
            exist = os.path.exists(path)
            self._file_exist[path] = exist
            return exist
        else:
            return exist
