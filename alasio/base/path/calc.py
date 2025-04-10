import os

WINDOWS_SEP = os.sep == '\\'


def normpath(path: str) -> str:
    """
    Equivalent to os.path.normpath(self)
    """
    if WINDOWS_SEP:
        # In most cases just normpath('xxx.png') check '/' first to be faster
        if '/' in path:
            return path.rstrip('\\/').replace('/', '\\')
        else:
            return path.rstrip('\\')
    else:
        if '\\' in path:
            return path.rstrip('\\/').replace('\\', '/')
        else:
            return path.rstrip('/')


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


def joinnormpath(root: str, path: str) -> str:
    """
    Equivalent to joinpath(root, normpath(path))
    Reduce python function call to be about 0.1us faster

    Args:
        root: Base path, needs to be normalized first
        path: Relative path

    Returns:
        str:
    """
    if WINDOWS_SEP:
        if '/' in path:
            path = path.rstrip('\\/').replace('/', '\\')
        else:
            path = path.rstrip('\\')
    else:
        if '\\' in path:
            path = path.rstrip('\\/').replace('\\', '/')
        else:
            path = path.rstrip('/')

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
    if WINDOWS_SEP:
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
    if WINDOWS_SEP:
        if len(path) >= 2:
            return path[1] == ':'
        return False
    else:
        return path.startswith('/')


def abspath(path: str) -> str:
    """
    A simplified os.path.abspath()
    Note that, to improve performance, this method should be used less
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
