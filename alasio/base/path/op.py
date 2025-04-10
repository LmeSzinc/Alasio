import os


def iter_files(root: str, suffix: str = ''):
    """
    Iter full filepath of files in folder with the good performance

    Don't do os.path.isdir() in for loop, that would be 40 times slower
        for file in os.listdir(folder):
            if os.path.isdir(os.path.join(folder, file)):
                ...

    Args:
        root:
        suffix:

    Yields:
        str: Full path
    """
    if suffix:
        # Iter all files with given extension
        # iter_files(folder, suffix='.json')
        with os.scandir(root) as entries:
            for entry in entries:
                if entry.name.endswith(suffix) and entry.is_file(follow_symlinks=False):
                    yield entry.path
    else:
        # Iter all files (directory not included)
        # iter_files(folder)
        with os.scandir(root) as entries:
            for entry in entries:
                if entry.is_file(follow_symlinks=False):
                    yield entry.path


def iter_filenames(root: str, suffix: str = ''):
    """
    Iter filename of files in folder with the good performance

    Args:
        root:
        suffix: If suffix is given, iter files with suffix only
            If suffix is empty, iter all files

    Yields:
        str: Filename
    """
    if suffix:
        # Iter all files with given extension
        # iter_files(folder, suffix='.json')
        with os.scandir(root) as entries:
            for entry in entries:
                if entry.name.endswith(suffix) and entry.is_file(follow_symlinks=False):
                    yield entry.name
    else:
        # Iter all files (directory not included)
        # iter_files(folder)
        with os.scandir(root) as entries:
            for entry in entries:
                if entry.is_file(follow_symlinks=False):
                    yield entry.name


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
    with os.scandir(root) as entries:
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                yield entry.path


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
    with os.scandir(root) as entries:
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                yield entry.name
