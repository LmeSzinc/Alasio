import os


def iter_files(root: str, ext: str = ''):
    """
    Iter full filepath of files in folder with the good performance

    Don't do os.path.isdir() in for loop, that would be 40 times slower
        for file in os.listdir(folder):
            if os.path.isdir(os.path.join(folder, file)):
                ...

    Args:
        root:
        ext: If ext is given, iter files with extension only
            If ext is empty, iter all files

    Yields:
        str: Full path
    """
    if ext:
        # Iter all files with given extension
        # iter_files(folder, suffix='.json')
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    if entry.name.endswith(ext) and entry.is_file(follow_symlinks=False):
                        yield entry.path
        except (FileNotFoundError, NotADirectoryError):
            return
    else:
        # Iter all files (directory not included)
        # iter_files(folder)
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    if entry.is_file(follow_symlinks=False):
                        yield entry.path
        except (FileNotFoundError, NotADirectoryError):
            return


def iter_filenames(root: str, ext: str = ''):
    """
    Iter filename of files in folder with the good performance

    Args:
        root:
        ext: If ext is given, iter files with extension only
            If ext is empty, iter all files

    Yields:
        str: Filename
    """
    if ext:
        # Iter all files with given extension
        # iter_files(folder, suffix='.json')
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    if entry.name.endswith(ext) and entry.is_file(follow_symlinks=False):
                        yield entry.name
        except (FileNotFoundError, NotADirectoryError):
            return
    else:
        # Iter all files (directory not included)
        # iter_files(folder)
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    if entry.is_file(follow_symlinks=False):
                        yield entry.name
        except (FileNotFoundError, NotADirectoryError):
            return


def iter_folders(root: str):
    """
    Iter full filepath of directories in folder with the good performance

    Args:
        root:

    Yields:
        str: Full path
    """
    # Iter all directories
    # iter_folders(folder)
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    yield entry.path
    except (FileNotFoundError, NotADirectoryError):
        return


def iter_foldernames(root: str):
    """
    Iter name of directories in folder with the good performance

    Args:
        root:

    Yields:
        str: Folder name
    """
    # Iter all directories
    # iter_folders(folder)
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    yield entry.name
    except (FileNotFoundError, NotADirectoryError):
        return


_NO_CACHE = object()


class CachePathExists:
    def __init__(self):
        self._file_exist: "dict[str, bool]" = {}

    def path_exists(self, path: str) -> bool:
        exist = self._file_exist.get(path, _NO_CACHE)
        if exist == _NO_CACHE:
            # Yes, there might be a TOCTOU race condition,
            # but it's just OK to do os.path.exists twice
            exist = os.path.exists(path)
            self._file_exist[path] = exist
            return exist
        else:
            return exist
