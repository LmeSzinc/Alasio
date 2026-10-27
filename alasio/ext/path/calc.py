import os

WINDOWS_SEP = os.sep == '\\'


def normpath(path: str) -> str:
    """
    Normalize whatever to separator "/"

    Roots keep their trailing separator so they stay absolute: "/" on
    POSIX and "C:/" on Windows. A bare drive is treated as the drive
    root, "C:" -> "C:/", because "C:" is drive-relative in the OS.
    """
    if WINDOWS_SEP:
        # In most cases just normpath('xxx.png') check '/' first to be faster
        if '\\' in path:
            normalized = path.rstrip('\\/').replace('\\', '/')
        else:
            normalized = path.rstrip('\\/')
        if len(normalized) == 2 and normalized[1] == ':' and normalized[0].isalpha():
            return normalized + '/'
    else:
        normalized = path.rstrip('/')
    # A bare root ("/" / "\\") is kept as "/" instead of becoming an empty
    # string, an empty input stays empty
    return normalized or ('/' if path else '')


def joinpath(root: str, path: str) -> str:
    """
    Equivalent to os.path.join(self, path)
    Cannot handle "./" and "../", if you need "../" use uppath() instead

    Args:
        root: Base path, needs to be normalized first
        path: Relative path, needs to be normalized first

    Returns:
        str:
    """
    if root:
        if path:
            if root[-1] == '/':
                # Root already ends with a separator, don't create '//path'
                return root + path
            else:
                return f'{root}/{path}'
        else:
            return root
    else:
        return path


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
        # In most cases just normpath('xxx.png') check '/' first to be faster
        if '\\' in path:
            path = path.rstrip('\\/').replace('\\', '/')
        else:
            path = path.rstrip('\\/')
        if len(path) == 2 and path[1] == ':' and path[0].isalpha():
            path += '/'
    else:
        path = path.rstrip('/')

    if root:
        if path:
            if root[-1] == '/':
                # Root already ends with a separator, don't create '//path'
                return root + path
            else:
                return f'{root}/{path}'
        else:
            return root
    else:
        return path


def uppath(root: str, up: int = 1) -> str:
    """
    Equivalent to os.path.join(self, '../')

    The walk stops where the path model of this module ends: at the empty
    string for a relative path, at "/" for a rooted path, and at the drive
    root "C:/" for a path of a drive. A bare drive "C:" is the drive root,
    the same way normpath() reads it.

    Args:
        root: Base path, needs to be normalized first
        up: Directory upward level

    Returns:
        str:
    """
    if WINDOWS_SEP:
        if len(root) == 2 and root[0].isalpha() and root[1] == ':':
            # A bare drive is the drive root, the same way normpath() reads
            # it: "C:" -> "C:/", and the root is its own parent
            return root + '/' if up else root
        # A rooted path stops at "/", a drive path at its drive root, and a
        # relative path at the empty string. Input is normalized, so the
        # rooted paths start with "/"
        rooted = root.startswith('/')
        for _ in range(up):
            root, _, _ = root.rpartition('/')
            if not root:
                # Absolute path can only up to "/"
                return '/' if rooted else ''
            # Absolute path can only up to the drive root "C:/"
            elif root[-1] == ':':
                return root + '/'
        return root
    else:
        is_absolute = root.startswith('/')
        for _ in range(up):
            root, _, _ = root.rpartition('/')
            if not root:
                if is_absolute:
                    # Absolute path can only up to "/"
                    return '/'
                else:
                    # Relative path can only up to empty string
                    return ''
        return root


def is_abspath(path: str) -> bool:
    """
    Check whether a path is absolute, with an answer that does not depend on
    the python version.

    os.path.isabs() is not used: on Windows a rooted path without a drive
    ('/assets') counted as absolute up to python 3.12 and became relative in
    python 3.13, so the same path was accepted or rejected depending on the
    interpreter that runs it. The rule here is the one normpath() and uppath()
    work with:

    - POSIX: the path starts at the root, '/'
    - Windows: a rooted path, which is the root of the current drive ('/a')
      or a UNC share ('\\\\server\\share'), or a drive with a root after it
      ('C:/', 'C:/a'). A bare drive ('C:') is the drive root, the same way
      normpath() reads it, because 'C:' is drive relative in the OS. A drive
      relative path ('C:a') is not absolute, and a colon that is not a drive
      letter ('a:b') does not make one.

    Args:
        path (str): Path to check, normalized or not

    Returns:
        bool: True when the path is absolute
    """
    if not WINDOWS_SEP:
        return path.startswith('/')
    # A drive is tested first, 'C:/folder/file' is the shape of most of the
    # paths the project checks: 'C:' is the drive root, 'C:/' and 'C:/a' are
    # absolute, and 'C:a' is drive relative and stays relative.
    if path[1:2] == ':' and path[0].isalpha():
        return path[2:3] in ('/', '\\', '')
    # a rooted path: '/a' of the current drive, or '\\server\share'
    return path.startswith(('/', '\\'))


def abspath(path: str) -> str:
    """
    A simplified os.path.abspath()
    Note that, to improve performance, this method should be used less
    """
    if is_abspath(path):
        return path
    root = normpath(os.getcwd())
    return joinpath(root, path)


def to_posix(path):
    """
    Convert to posix path

    Args:
        path (str):

    Returns:
        str:
    """
    return path.replace('\\', '/')


def to_python_import(path):
    """
    Convert path to python dot-like import
    path/to/python.py -> path.to.python

    Args:
        path (str):

    Returns:
        str:
    """
    if path.endswith('.py'):
        path = path[:-3]
    if WINDOWS_SEP:
        path = path.strip('\\/')
        if '\\' in path:
            path = path.replace('\\', '.')
    else:
        path = path.strip('/')
    if '/' in path:
        path = path.replace('/', '.')
    return path


def subpath_to(path, root):
    """
    Calculate sub-path to `root`
    If `path` is not a sub-path to `root`, return None

    Args:
        path (str): Normalized path
        root (str): Normalized path

    Returns:
        str | None: Sub-path to `root`, None if `path` is not a sub-path
    """
    if not path.startswith(root):
        return None
    rest = path[len(root):]
    # path == root, the empty path is the sub-path of an empty root
    if not rest:
        return rest
    # An empty root is the prefix of no non-empty path
    if not root:
        return None
    # Both paths are normalized (separator "/"), a plain string prefix is not
    # enough, the rest of the path must continue at a separator boundary,
    # otherwise "/a/bc" would be treated as a sub-path to "/a/b"
    if rest[0] == '/' or root.endswith('/'):
        return rest.lstrip('/')
    return None


def subpath(path, root):
    """
    Calculate sub-path to `root`, `path` must be a sub-path of `root`

    Args:
        path (str): Normalized path
        root (str): Normalized path

    Raises:
        ValueError: If `path` is not a sub-path of `root`

    Returns:
        str: Sub-path to `root`
    """
    result = subpath_to(path, root)
    if result is None:
        raise ValueError(f'Not a sub-path: path="{path}", root="{root}"')
    return result


def get_name(path: str) -> str:
    """
    /abc/def.png -> def.png
    /abc/def     -> def
    /abc/.git    -> .git
    """
    if '/' in path:
        _, _, path = path.rpartition('/')
    return path


def get_stem(path: str) -> str:
    """
    /abc/def.png -> def
    /abc/def     -> def
    /abc/.git    -> ""
    """
    if '/' in path:
        _, _, path = path.rpartition('/')
    stem, dot, _ = path.rpartition('.')
    if dot:
        return stem
    else:
        return path


def get_rootstem(path: str) -> str:
    """
    /abc/def.part1.png -> def
    /abc/def.png -> def
    /abc/def     -> def
    /abc/.git    -> ""
    """
    if '/' in path:
        _, _, path = path.rpartition('/')
    stem, dot, _ = path.partition('.')
    if dot:
        return stem
    else:
        return path


def get_suffix(path: str) -> str:
    """
    /abc/def.png -> .png
    /abc/def     -> ""
    /abc/.git    -> .git
    """
    if '/' in path:
        _, _, path = path.rpartition('/')
    path, dot, suffix = path.rpartition('.')
    if dot:
        return dot + suffix
    return path


def get_multisuffix(path: str) -> str:
    """
    /abc/def.part1.png -> .part1.png
    /abc/def.png -> .png
    /abc/def     -> ""
    /abc/.git    -> .git
    """
    if '/' in path:
        _, _, path = path.rpartition('/')
    _, dot, suffix = path.partition('.')
    if dot:
        return dot + suffix
    else:
        return ''


def with_name(path: str, name: str) -> str:
    """
    /abc/def.png -> /abc/xxx
    /abc/def     -> /abc/xxx
    /abc/.git    -> /abc/xxx
    """
    root, sep, _ = path.rpartition('/')
    if sep:
        return f'{root}{sep}{name}'
    else:
        return name


def with_stem(path: str, stem: str) -> str:
    """
    /abc/def.png -> /abc/xxx.png
    /abc/def     -> /abc/xxx
    /abc/.git    -> /abc/xxx.git
    """
    root, sep, name = path.rpartition('/')
    _, dot, suffix = name.rpartition('.')
    if sep:
        if dot:
            return f'{root}{sep}{stem}.{suffix}'
        else:
            return f'{root}{sep}{stem}'
    else:
        if dot:
            return f'{stem}.{suffix}'
        else:
            return stem


def with_rootstem(path: str, stem: str) -> str:
    """
    /abc/def.part1.png -> /abc/xxx.part1.png
    /abc/def.png -> /abc/xxx.png
    /abc/def     -> /abc/xxx
    /abc/.git    -> /abc/xxx.git
    """
    root, sep, name = path.rpartition('/')
    _, dot, suffix = name.partition('.')
    if sep:
        if dot:
            return f'{root}{sep}{stem}.{suffix}'
        else:
            return f'{root}{sep}{stem}'
    else:
        if dot:
            return f'{stem}.{suffix}'
        else:
            return stem


def with_suffix(path: str, suffix: str) -> str:
    """
    /abc/def.png -> /abc/def.xxx
    /abc/def     -> /abc/def.xxx
    /abc/.git    -> /abc/.xxx
    """
    root, sep, name = path.rpartition('/')
    stem, dot, _ = name.rpartition('.')
    if sep:
        if dot:
            return f'{root}{sep}{stem}{suffix}'
        else:
            return f'{root}{sep}{name}{suffix}'
    else:
        if dot:
            return f'{stem}{suffix}'
        else:
            return f'{name}{suffix}'


def with_multisuffix(path: str, suffix: str) -> str:
    """
    /abc/def.part1.png -> /abc/def.xxx.xxx
    /abc/def     -> /abc/def.xxx
    /abc/.git    -> /abc/.xxx
    """
    root, sep, name = path.rpartition('/')
    stem, dot, _ = name.partition('.')
    if sep:
        if dot:
            return f'{root}{sep}{stem}{suffix}'
        else:
            return f'{root}{sep}{name}{suffix}'
    else:
        if dot:
            return f'{stem}{suffix}'
        else:
            return f'{name}{suffix}'
