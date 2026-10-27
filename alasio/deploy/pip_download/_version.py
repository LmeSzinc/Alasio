"""
PEP 440 version parsing and ordering.

Ported from `packaging/version.py` (packaging, Apache-2.0 OR BSD-2-Clause):
the `Version` class, the parsing of the version string and the comparison key
are the source implementation, the parts we do not use (pickle support,
`Version.from_parts`, `__replace__`, the deprecated aliases) are not copied.

The version of the source file was packaging 26.3; the version pattern is the
non-possessive variant, because this project still runs on python 3.8 where
possessive quantifiers are not supported by `re`.

Usage:
    from alasio.deploy.pip_download._version import Version

    Version('1.0') == Version('1.0.0')   # True
    Version('1.0a1') < Version('1.0')    # True
"""

import re

__all__ = ['VERSION_PATTERN', 'InvalidVersion', 'Version', 'parse']

# Letters that are alternate spellings of a pre/post release marker, the
# preferred spelling is the value
_LETTER_NORMALIZATION = {
    'alpha': 'a',
    'beta': 'b',
    'c': 'rc',
    'pre': 'rc',
    'preview': 'rc',
    'rev': 'post',
    'r': 'post',
}

# The pattern of a PEP 440 version string, from packaging/version.py. The
# whitespace around the version is allowed, the pattern is compiled with
# re.VERBOSE and re.IGNORECASE.
#
# The possessive quantifiers (`?+`, `*+`) of the newest pattern are replaced
# by the plain ones: python 3.8 does not support them, and the behaviour is
# the same, only slower on a pathological input.
VERSION_PATTERN = r"""
    v?
    (?:
        (?:(?P<epoch>[0-9]+)!)?                           # epoch
        (?P<release>[0-9]+(?:\.[0-9]+)*)                  # release segment
        (?P<pre>                                          # pre-release
            [._-]?
            (?P<pre_l>alpha|a|beta|b|preview|pre|c|rc)
            [._-]?
            (?P<pre_n>[0-9]+)?
        )?
        (?P<post>                                         # post release
            (?:-(?P<post_n1>[0-9]+))
            |
            (?:
                [._-]?
                (?P<post_l>post|rev|r)
                [._-]?
                (?P<post_n2>[0-9]+)?
            )
        )?
        (?P<dev>                                          # dev release
            [._-]?
            (?P<dev_l>dev)
            [._-]?
            (?P<dev_n>[0-9]+)?
        )?
    )
    (?:\+
        (?P<local>                                        # local version
            [a-z0-9]+
            (?:[._-][a-z0-9]+)*
        )
    )?
"""


class InvalidVersion(ValueError):
    """
    Raised when a version string is not a valid PEP 440 version.

    Ported from packaging/version.py.
    """


class Version:
    """
    A PEP 440 version.

    Ported from packaging/version.py: the parsing and the comparison key are
    the source implementation. Instances are immutable and hashable, the six
    comparisons are supported, and two spellings of the same version
    (`1.0` and `1.0.0`, `1.0-1` and `1.0.post1`) compare equal.

    Attributes:
        epoch (int): Epoch of the version, 0 when the string carries none
        release (tuple[int, ...]): Release segment, trailing zeros kept
        pre (tuple[str, int]): Pre-release segment ('a', 'b' or 'rc') and its
            number, None for a version without one
        post (int): Post-release number, None for a version without one
        dev (int): Development release number, None for a version without one
        local (str): Local version segment, None for a version without one
    """

    __slots__ = ('_dev', '_epoch', '_local', '_post', '_pre', '_release', '_key')

    _regex = re.compile(r'\s*' + VERSION_PATTERN + r'\s*', re.VERBOSE | re.IGNORECASE)

    def __init__(self, version):
        """
        Parse a PEP 440 version string.

        Ported from packaging/version.py.

        Args:
            version (str): Version string, e.g. '0.21.1', '1.0rc1',
                '1!2.0.post1+local'

        Raises:
            InvalidVersion: If the string is not a PEP 440 version
        """
        try:
            match = self._regex.fullmatch(version)
        except TypeError:
            raise InvalidVersion(f'Invalid version: {version!r}') from None
        if not match:
            raise InvalidVersion(f'Invalid version: {version!r}')

        self._epoch = int(match.group('epoch')) if match.group('epoch') else 0
        self._release = tuple(map(int, match.group('release').split('.')))
        self._pre = _parse_letter_version(match.group('pre_l'), match.group('pre_n'))
        self._post = _parse_letter_version(
            match.group('post_l'), match.group('post_n1') or match.group('post_n2'))
        self._dev = _parse_letter_version(match.group('dev_l'), match.group('dev_n'))
        self._local = _parse_local_version(match.group('local'))
        # the comparison key is built on the first compare, most versions of
        # an index page are never compared when a pin is selected
        self._key = None

    @property
    def _cmpkey(self):
        """
        The comparison key of the version, built once per instance.

        Returns:
            tuple: (epoch, trimmed release, suffix) and the local segment
                when the version carries one
        """
        if self._key is None:
            self._key = _cmpkey(
                self._epoch, self._release, self._pre, self._post, self._dev, self._local)
        return self._key

    def __hash__(self):
        return hash(self._cmpkey)

    def __eq__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self._cmpkey == other._cmpkey

    def __ne__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self._cmpkey != other._cmpkey

    def __lt__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self._cmpkey < other._cmpkey

    def __le__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self._cmpkey <= other._cmpkey

    def __gt__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self._cmpkey > other._cmpkey

    def __ge__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self._cmpkey >= other._cmpkey

    def __str__(self):
        """
        The normalized string of the version, e.g. '1.0rc1', '1.0.post1'.

        Returns:
            str: The version that round-trips through Version()
        """
        version = '.'.join(map(str, self.release))
        if self._epoch:
            version = f'{self._epoch}!{version}'
        if self._pre is not None:
            version += ''.join(map(str, self._pre))
        if self._post is not None:
            version += f'.post{self._post[1]}'
        if self._dev is not None:
            version += f'.dev{self._dev[1]}'
        if self._local is not None:
            version += f'+{".".join(str(part) for part in self._local)}'
        return version

    def __repr__(self):
        return f'<Version({str(self)!r})>'

    @property
    def epoch(self):
        """
        Returns:
            int: Epoch of the version, 0 when the string carries none
        """
        return self._epoch

    @property
    def release(self):
        """
        Returns:
            tuple[int, ...]: Release segments, trailing zeros kept
        """
        return self._release

    @property
    def pre(self):
        """
        Returns:
            tuple[str, int] | None: ('a' | 'b' | 'rc', number), None for a
                version without a pre-release
        """
        return self._pre

    @property
    def post(self):
        """
        Returns:
            int | None: Post-release number, None for a version without one
        """
        return self._post[1] if self._post else None

    @property
    def dev(self):
        """
        Returns:
            int | None: Development release number, None for a version
                without one
        """
        return self._dev[1] if self._dev else None

    @property
    def local(self):
        """
        Returns:
            str | None: Local version segment, None for a version without one
        """
        if self._local:
            return '.'.join(str(part) for part in self._local)
        return None

    @property
    def public(self):
        """
        Returns:
            str: The version without its local segment
        """
        return str(self).split('+', 1)[0]

    @property
    def base_version(self):
        """
        Returns:
            str: The epoch and the release segment, without pre, post, dev
                and local
        """
        release_segment = '.'.join(map(str, self._release))
        return f'{self._epoch}!{release_segment}' if self._epoch else release_segment

    @property
    def is_prerelease(self):
        """
        Returns:
            bool: True for a pre-release or a development release
        """
        return self._dev is not None or self._pre is not None

    @property
    def is_postrelease(self):
        """
        Returns:
            bool: True for a post-release
        """
        return self._post is not None


def parse(version):
    """
    Parse a version string, the same as the Version constructor.

    Ported from packaging/version.py.

    Args:
        version (str): Version string

    Returns:
        Version: The parsed version

    Raises:
        InvalidVersion: If the string is not a PEP 440 version
    """
    return Version(version)


class _TrimmedRelease(Version):
    """
    A version whose release segment lost its trailing zeros.

    Ported from packaging/version.py: the parts of the source version are
    copied as they are, only the `release` property is overridden, so the
    other segments of the version are printed unchanged.
    """

    __slots__ = ()

    def __init__(self, version):
        """
        Args:
            version (Version): Version to trim, a str is parsed like Version
        """
        if isinstance(version, Version):
            self._epoch = version._epoch
            self._release = version._release
            self._dev = version._dev
            self._pre = version._pre
            self._post = version._post
            self._local = version._local
            # the key of the source is still valid: it trims the release by
            # itself, and the other parts are the same
            self._key = version._key
            return
        super().__init__(version)

    @property
    def release(self):
        """
        Returns:
            tuple[int, ...]: Release segment without trailing zeros, one
                zero is kept ('0.0' -> (0,))
        """
        release = self._release
        i = len(release)
        while i > 1 and release[i - 1] == 0:
            i -= 1
        return release if i == len(release) else release[:i]


def _parse_letter_version(letter, number):
    """
    Parse the letter and the number of a pre/post/dev segment.

    Ported from packaging/version.py.

    Args:
        letter (str | None): Letter of the segment, e.g. 'alpha'
        number (str | None): Number of the segment, e.g. '1'

    Returns:
        tuple[str, int] | None: (normalized letter, number), None when the
            segment is not present
    """
    if letter:
        # the letters are normalized to their lower case form
        letter = letter.lower()
        # some words are alternate spellings of other words
        letter = _LETTER_NORMALIZATION.get(letter, letter)
        # an implicit 0 when there is no numeral associated with the letter
        return letter, int(number or 0)

    if number:
        # a number without a letter is the implicit post release syntax,
        # e.g. '1.0-1'
        return 'post', int(number)

    return None


_local_version_separators = re.compile(r'[\._-]')


def _parse_local_version(local):
    """
    Parse a local version segment into its parts.

    Ported from packaging/version.py.

    Args:
        local (str | None): Local segment, e.g. 'abc.1.twelve'

    Returns:
        tuple[int | str, ...] | None: The parts, numbers as int, None when
            the version carries no local segment
    """
    if local is not None:
        return tuple(
            part.lower() if not part.isdigit() else int(part)
            for part in _local_version_separators.split(local)
        )
    return None


# Sort ranks for pre-release: dev-only < a < b < rc < stable (no pre-release)
_PRE_RANK = {'a': 0, 'b': 1, 'rc': 2}
_PRE_RANK_DEV_ONLY = -1
_PRE_RANK_STABLE = 3
# In local version segments, strings sort before ints per PEP 440
_LOCAL_STR_RANK = -1
# The suffix of a stable release, see _cmpkey() for the layout
_STABLE_SUFFIX = (_PRE_RANK_STABLE, 0, 0, 0, 1, 0)


def _cmpkey(epoch, release, pre, post, dev, local):
    """
    Build a comparison key for PEP 440 ordering.

    Ported from packaging/version.py. Trailing zeros are stripped from the
    release so that `1.0.0 == 1`; the suffix is a flat 6-int tuple encoding
    pre/post/dev, and the local segment is compared as tuples of
    (rank, text): a string sorts before a number and strings compare
    lexicographically.

    Args:
        epoch (int): Epoch of the version
        release (tuple[int, ...]): Release segment
        pre (tuple[str, int] | None): Pre-release segment
        post (tuple[str, int] | None): Post-release segment
        dev (tuple[str, int] | None): Development release segment
        local (tuple[int | str, ...] | None): Local version segment

    Returns:
        tuple: The key of the version
    """
    # strip trailing zeros: 1.0.0 compares equal to 1
    i = len(release)
    while i and release[i - 1] == 0:
        i -= 1
    trimmed = release if i == len(release) else release[:i]

    # fast path: a stable release without a local segment
    if pre is None and post is None and dev is None and local is None:
        return epoch, trimmed, _STABLE_SUFFIX

    if pre is None and post is None and dev is not None:
        # dev-only (e.g. 1.0.dev1) sorts before all pre-releases
        pre_rank, pre_n = _PRE_RANK_DEV_ONLY, 0
    elif pre is None:
        pre_rank, pre_n = _PRE_RANK_STABLE, 0
    else:
        pre_rank, pre_n = _PRE_RANK[pre[0]], pre[1]

    post_rank = 0 if post is None else 1
    post_n = 0 if post is None else post[1]

    dev_rank = 1 if dev is None else 0
    dev_n = 0 if dev is None else dev[1]

    suffix = (pre_rank, pre_n, post_rank, post_n, dev_rank, dev_n)

    if local is None:
        return epoch, trimmed, suffix

    cmp_local = tuple(
        (part, '') if isinstance(part, int) else (_LOCAL_STR_RANK, part)
        for part in local
    )
    return epoch, trimmed, suffix, cmp_local
