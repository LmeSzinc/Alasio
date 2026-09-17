"""
Glob matching of archive paths, used by ``include`` / ``exclude`` / ``unpack`` /
``unpack_dir``.

Patterns are matched against the path inside the archive, with POSIX separators:

- ``*`` matches any character except '/'
- ``?`` matches one character except '/'
- ``**`` matches any character including '/', and ``**/`` also matches nothing
  (so ``dist/**`` matches ``dist/main.js`` but not ``dist`` itself)
- the match is case sensitive on every platform, and covers the whole path
- a pattern without '/' can also be matched against the basename, see
  ``match_base`` (the asar CLI matches --unpack patterns this way)

Character classes (``[a-z]``) and brace expansion (``{js,css}``) are not
supported, they are matched as literal text.
"""
import re
from functools import lru_cache

# Compiled patterns are cached, a crawl matches every entry against the same
# handful of patterns
CACHE_SIZE = 512


def translate(pattern):
    """
    Translate a glob pattern into a regular expression source.

    Args:
        pattern (str): Glob pattern

    Returns:
        str: Regular expression source, anchored on both ends
    """
    parts = ['^']
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == '*':
            if pattern.startswith('**/', index):
                # `a/**/b` also matches `a/b`
                parts.append('(?:.*/)?')
                index += 3
            elif pattern.startswith('**', index):
                parts.append('.*')
                index += 2
            else:
                parts.append('[^/]*')
                index += 1
        elif char == '?':
            parts.append('[^/]')
            index += 1
        else:
            parts.append(re.escape(char))
            index += 1
    parts.append('$')
    return ''.join(parts)


@lru_cache(maxsize=CACHE_SIZE)
def compile_pattern(pattern):
    """
    Compile a glob pattern.

    Args:
        pattern (str): Glob pattern

    Returns:
        re.Pattern: Compiled pattern
    """
    return re.compile(translate(pattern), re.DOTALL)


def match_path(path, pattern, match_base=False):
    """
    Check whether an archive path matches a glob pattern.

    Args:
        path (str): Archive path, POSIX separators
        pattern (str): Glob pattern
        match_base (bool): Match the basename of the path when the pattern
            contains no '/'

    Returns:
        bool: True if the path matches
    """
    if match_base and '/' not in pattern:
        path = path.rpartition('/')[2]
    return compile_pattern(pattern).match(path) is not None


def match_any(path, patterns, match_base=False):
    """
    Check whether an archive path matches any pattern of a list.

    Args:
        path (str): Archive path, POSIX separators
        patterns (list): Glob patterns
        match_base (bool): Match the basename of the path when a pattern
            contains no '/'

    Returns:
        bool: True if the path matches at least one pattern
    """
    return any(match_path(path, pattern, match_base=match_base) for pattern in patterns)


def match_dir(path, pattern):
    """
    Check whether a directory is selected by an ``unpack_dir`` pattern.

    The asar CLI accepts a literal prefix for backward compatibility, plus a
    glob pattern, and everything below a matched directory is unpacked as well.

    Args:
        path (str): Archive path of the directory, POSIX separators
        pattern (str): Pattern of the unpack directory

    Returns:
        bool: True if the directory matches
    """
    return path.startswith(pattern) or match_path(path, pattern)
