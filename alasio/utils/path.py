import os


def normpath(path: str) -> str:
    """
    Normalize path
    """
    if os.name == 'nt':
        return path.replace('/', '\\').rstrip('\\')
    else:
        return path.replace('\\', '/').rstrip('/')


def joinpath(
        root: str,
        path: str = '',
        up: int = 0
) -> str:
    """
    Args:
        root: Base path, needs to be normalized first
        path: Relative path
        up: Directory upward level

    Returns:
        str:
    """
    sep = os.sep
    path = normpath(path)

    if os.name == 'nt':
        if up > 0:
            for _ in range(up):
                root, _, _ = root.rpartition(sep)
                # Relative path can only up to empty string
                if not root:
                    return path
                # Absolute path can only up to "C:"
                elif root.endswith(':'):
                    break
        if path:
            return f'{root}{sep}{path}'
        else:
            return root
    else:
        if up > 0:
            is_absolute = root.startswith(sep)
            for _ in range(up):
                root, _, _ = root.rpartition(sep)
                if not root:
                    if is_absolute:
                        # Absolute path can only up to "/"
                        if path:
                            return f'{sep}{path}'
                        else:
                            return sep
                    else:
                        # Relative path can only up to empty string
                        return path
        if path:
            return f'{root}{sep}{path}'
        else:
            return root


def iter_filepath(
        folder: str,
        is_dir: bool = False,
        ext: str = '',
        dir_check=True,
):
    """
    Iter full filepath of files or directory in folder with the best performance possible

    Don't do os.path.isdir() in for loop, that would be 40 times slower
        for file in os.listdir(folder):
            if os.path.isdir(os.path.join(folder, file)):
                ...

    Args:
        folder:
        is_dir:
        ext:
        dir_check:

    Yields:
        str: Full path
    """
    if is_dir:
        # Iter all directories
        # iter_folder(folder, is_dir=True)
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.is_dir():
                    yield entry.path
    elif ext:
        if dir_check:
            # Iter all files with given extension
            # iter_folder(folder, ext='.json)
            with os.scandir(folder) as entries:
                for entry in entries:
                    if entry.name.endswith(ext) and entry.is_file():
                        yield entry.path
        else:
            # Iter all files with given extension without checking is_file()
            # Disable dir_check can reduce 33% time cost if you don't care or already know they are files or not
            # iter_folder(folder, ext='.json', dir_check=False)
            for file in os.listdir(folder):
                if file.endswith(ext):
                    yield f'{folder}{os.sep}{file}'
    else:
        # Iter all files (directory not included)
        # iter_folder(folder)
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.is_file():
                    yield entry.path


def iter_filename(
        folder: str,
        is_dir: bool = False,
        ext: str = '',
        dir_check=True,
):
    """
    Iter name of files or directory in folder with the best performance possible

    Don't do os.path.isdir() in for loop, that would be 40 times slower
        for file in os.listdir(folder):
            if os.path.isdir(os.path.join(folder, file)):
                ...

    Args:
        folder:
        is_dir:
        ext:
        dir_check:

    Yields:
        str: File name
    """
    if is_dir:
        # Iter all directories
        # iter_folder(folder, is_dir=True)
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.is_dir():
                    yield entry.name
    elif ext:
        if dir_check:
            # Iter all files with given extension
            # iter_folder(folder, ext='.json)
            with os.scandir(folder) as entries:
                for entry in entries:
                    name = entry.name
                    if name.endswith(ext) and entry.is_file():
                        yield name
        else:
            # Iter all files with given extension without checking is_file()
            # Disable dir_check can reduce 33% time cost if you don't care or already know they are files or not
            # iter_folder(folder, ext='.json', dir_check=False)
            for file in os.listdir(folder):
                if file.endswith(ext):
                    yield file
    else:
        # Iter all files (directory not included)
        # iter_folder(folder)
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.is_file():
                    yield entry.name
