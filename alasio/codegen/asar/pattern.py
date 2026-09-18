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
  ``GlobPattern.match_base`` (the asar CLI matches --unpack patterns this way)

Character classes (``[a-z]``) and brace expansion (``{js,css}``) are not
supported, they are matched as literal text.

This is the dialect of the asar CLI, not the one of git's path rules (see
``alasio.git.attr.pathregex``). The two differ on a slash-less pattern (git
matches it at any depth), on a matched directory (git also matches everything
below it) and on character classes (git supports them), so the translators are
kept apart on purpose.
"""
import re


def split_literal(pattern):
    """
    Split a glob pattern at its first metacharacter.

    Args:
        pattern (str): Glob pattern

    Returns:
        tuple[str, bool]: The literal start of the pattern and whether the
            pattern holds no metacharacter at all
    """
    for index, char in enumerate(pattern):
        if char in '*?':
            return pattern[:index], False
    return pattern, True


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


class GlobPattern:
    """
    A set of glob patterns, compiled once and matched against archive paths.

    The compiled matchers belong to the object instead of a module level cache,
    so they are released together with the patterns: a crawl compiles the
    patterns it is given once, and the memory is back once the crawl is done.
    """
    __slots__ = (
        'patterns', 'match_base', 'regexes', 'base_regexes',
        '_prefixes', '_exacts', '_anywhere',
    )

    def __init__(self, patterns, match_base=False):
        """
        Args:
            patterns (str | Iterable[str]): Glob patterns, a single pattern is
                accepted as well
            match_base (bool): Match the basename of the path for the patterns
                that contain no '/'
        """
        if isinstance(patterns, str):
            # A single pattern is not iterated, the characters of a pattern are
            # not patterns of their own
            patterns = (patterns,)
        patterns = tuple(patterns)
        self.patterns = patterns
        self.match_base = match_base
        # A pattern that holds a separator is only matched on the whole path,
        # and a basename holds no separator, so the two groups are kept apart
        # instead of testing every pattern against both
        regexes = []
        base_regexes = []
        # Where the matches of a pattern can be, see may_match_below(): the
        # subtree it starts in, as a prefix that carries its separator so that
        # the walk only has to compare strings, or the one path it selects
        prefixes = []
        exacts = []
        anywhere = False
        for pattern in patterns:
            regex = re.compile(translate(pattern), re.DOTALL)
            if match_base and '/' not in pattern:
                base_regexes.append(regex)
            else:
                regexes.append(regex)
            literal, exact = split_literal(pattern)
            if exact:
                # A pattern without a metacharacter selects one path, the walk
                # only goes toward it
                exacts.append(pattern)
                continue
            anchor = literal.rpartition('/')[0]
            if anchor:
                # A match starts with the literal part of the pattern, so it is
                # inside the directory that part points at
                prefixes.append(anchor + '/')
            else:
                # The literal part is inside the first segment, a match can be
                # anywhere
                anywhere = True
        self.regexes = tuple(regexes)
        self.base_regexes = tuple(base_regexes)
        self._prefixes = tuple(prefixes)
        self._exacts = tuple(exacts)
        self._anywhere = anywhere

    def __repr__(self):
        return f'{type(self).__name__}({", ".join(repr(pattern) for pattern in self.patterns)})'

    def match(self, path):
        """
        Check whether an archive path is selected by any of the patterns.

        Args:
            path (str): Archive path, POSIX separators

        Returns:
            bool: True if the path is selected
        """
        if any(regex.match(path) is not None for regex in self.regexes):
            return True
        if not self.base_regexes:
            return False
        path = path.rpartition('/')[2]
        return any(regex.match(path) is not None for regex in self.base_regexes)

    def may_match_below(self, path):
        """
        Check whether a directory may hold an entry that a pattern selects.

        The walk uses it to skip the directories no pattern can reach: the
        literal start of a pattern says where its matches can be, so an include
        list of ``dist/**`` and ``package.json`` never reads ``node_modules``.
        Only a set of patterns that is matched on the whole path can be
        estimated this way, a pattern of ``match_base`` selects a basename and
        can therefore match anywhere.

        Args:
            path (str): Archive path of a directory, POSIX separators

        Returns:
            bool: True if the directory or something below it may be selected
        """
        if self._anywhere or not path:
            # A pattern whose literal part is inside its first segment can match
            # anywhere, and the root holds every entry
            return True
        if path.startswith(self._prefixes):
            # Inside the subtree a pattern points at
            return True
        # Not inside one yet: only the directories on the way to a match are
        # worth entering. The prefixes carry their separator already, and the
        # concatenation is only built when it is needed
        under = path + '/'
        for prefix in self._prefixes:
            if prefix.startswith(under):
                return True
        for exact in self._exacts:
            if exact.startswith(under):
                return True
        return False


class GlobDirPattern(GlobPattern):
    """
    A glob pattern that selects a directory and everything below it.

    The asar CLI accepts a literal prefix next to a glob pattern, so a directory
    is selected when its path starts with a pattern, or when a pattern matches
    its path.
    """

    def match(self, path):
        """
        Check whether a directory is selected by this pattern.

        Args:
            path (str): Archive path of the directory, POSIX separators

        Returns:
            bool: True if the directory is selected
        """
        if any(path.startswith(pattern) for pattern in self.patterns):
            return True
        return super().match(path)
