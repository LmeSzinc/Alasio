import os

IS_WINDOWS = os.name == 'nt'


def normpath(path: str) -> str:
    """
    Equivalent to os.path.normpath(self)
    """
    if IS_WINDOWS:
        return path.replace('/', '\\').rstrip('\\')
    else:
        return path.replace('\\', '/').rstrip('/')


def joinpath(root: str, path: str) -> str:
    """
    Equivalent to os.path.join(self, path)
    but "./" and "../" is not available, to do "../" use uppath() instead

    Args:
        root: Base path, needs to be normalized first
        path: Relative path

    Returns:
        str:
    """
    if path:
        return f'{root}{os.sep}{path}'
    else:
        return root


def uppath(root: str, up: int = 1) -> str:
    """
    Equivalent to os.path.join(self, '../')

    Args:
        root: Base path, needs to be normalized first
        up: Directory upward level

    Returns:
        str:
    """
    if IS_WINDOWS:
        for _ in range(up):
            root, _, _ = root.rpartition(os.sep)
            # Relative path can only up to empty string
            if not root:
                return ''
            # Absolute path can only up to "C:"
            elif root.endswith(':'):
                break
        return root
    else:
        is_absolute = root.startswith(os.sep)
        for _ in range(up):
            root, _, _ = root.rpartition(os.sep)
            if not root:
                if is_absolute:
                    # Absolute path can only up to "/"
                    return os.sep
                else:
                    # Relative path can only up to empty string
                    return ''
        return root


def is_abspath(path: str) -> bool:
    """
    A simplified os.path.isabs()
    """
    if IS_WINDOWS:
        if len(path) >= 2:
            return path[1] == ':'
        return False
    else:
        return path.startswith('/')


def abspath(path: str) -> str:
    """
    A simplified os.path.abspath()
    """
    if is_abspath(path):
        return path
    root = os.getcwd()
    return joinpath(root, path)


def get_name(path: str) -> str:
    """
    /abc/def.png -> def.png
    /abc/def     -> def
    /abc/.git    -> .git
    """
    _, _, name = path.rpartition(os.sep)
    return name


def get_stem(path: str) -> str:
    """
    /abc/def.png -> def
    /abc/def     -> def
    /abc/.git    -> ""
    """
    _, _, name = path.rpartition(os.sep)
    stem, dot, _ = name.rpartition('.')
    if dot:
        return stem
    else:
        return name


def get_suffix(path: str) -> str:
    """
    /abc/def.png -> .png
    /abc/def     -> ""
    /abc/.git    -> .git
    """
    _, _, name = path.rpartition(os.sep)
    _, dot, suffix = name.rpartition('.')
    if dot:
        return suffix
    else:
        return ''


def with_name(path: str, name: str) -> str:
    """
    /abc/def.png -> /abc/xxx
    /abc/def     -> /abc/xxx
    /abc/.git    -> /abc/xxx
    """
    root, _, _ = path.rpartition(os.sep)
    return f'{root}{os.sep}{name}'


def with_stem(path: str, stem: str) -> str:
    """
    /abc/def.png -> /abc/xxx.png
    /abc/def     -> /abc/xxx
    /abc/.git    -> /abc/xxx.git
    """
    root, _, name = path.rpartition(os.sep)
    _, dot, suffix = name.rpartition('.')
    if dot:
        return f'{root}{os.sep}{stem}.{suffix}'
    else:
        return f'{root}{os.sep}{stem}'


def with_suffix(path: str, suffix: str) -> str:
    """
    /abc/def.png -> /abc/def.xxx
    /abc/def     -> /abc/def.xxx
    /abc/.git    -> /abc/.xxx
    """
    root, _, name = path.rpartition(os.sep)
    stem, dot, _ = name.rpartition('.')
    if dot:
        return f'{root}{os.sep}{stem}.{suffix}'
    else:
        return f'{root}{os.sep}{name}.{suffix}'


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
