"""
PEP 440 version specifiers.

Ported from `packaging/specifiers.py` (packaging, Apache-2.0 OR BSD-2-Clause):
`Specifier`, `SpecifierSet` and the `_compare_*` implementations, the
`_version_split`/`_pad_version` helpers of the wildcard form included. The
specifier string is split on ',' and each item is parsed with a small pattern
instead of the large pattern of the source: the version part is validated by
`Version` itself, so the pattern only reads the operator. The `filter()` of
the source is not ported, the fetcher never filters a list of versions.

Usage:
    from alasio.deploy.pip_download._specifiers import SpecifierSet

    '3.8.10' in SpecifierSet('>=3.8,<3.11')     # True
"""

import itertools
import re

from alasio.deploy.pip_download._utils import canonicalize_version
from alasio.deploy.pip_download._version import InvalidVersion, Version

__all__ = ['InvalidSpecifier', 'Specifier', 'SpecifierSet']

# The operators of PEP 440. The three-character operator comes first, so the
# pattern takes the longest one
_OPERATOR_PATTERN = re.compile(r'^\s*(===|~=|==|!=|<=|>=|<|>)\s*(\S+)\s*$')
# The longest operator is tried first by the alternation of the pattern
_OPERATORS = {
    '~=': 'compatible',
    '==': 'equal',
    '!=': 'not_equal',
    '<=': 'less_than_equal',
    '>=': 'greater_than_equal',
    '<': 'less_than',
    '>': 'greater_than',
    '===': 'arbitrary',
}

# The prefix of a version component that is not a release number, used by the
# wildcard compare to find where the release segment ends
_prefix_regex = re.compile(r'^([0-9]+)((?:a|b|c|rc)[0-9]+)$')


def _coerce_version(version):
    """
    Convert an unparsed version into a Version.

    Ported from packaging/specifiers.py.

    Args:
        version (Version | str): Version to convert

    Returns:
        Version: The version
    """
    if not isinstance(version, Version):
        version = Version(version)
    return version


class InvalidSpecifier(ValueError):
    """
    Raised when a specifier string is not valid PEP 440.

    Ported from packaging/specifiers.py.
    """


def _version_split(version):
    """
    Split a version into its comparison components.

    Ported from packaging/specifiers.py: the epoch is the first component,
    then the release numbers, and a component like '1rc1' is split into
    '1' and 'rc1'.

    Args:
        version (str): Version string

    Returns:
        list[str]: The components
    """
    result = []

    epoch, _, rest = version.rpartition('!')
    result.append(epoch or '0')

    for item in rest.split('.'):
        match = _prefix_regex.search(item)
        if match:
            result.extend(match.groups())
        else:
            result.append(item)
    return result


def _version_join(components):
    """
    Join components produced by `_version_split`.

    Ported from packaging/specifiers.py.

    Args:
        components (list[str]): Split components

    Returns:
        str: The version string
    """
    epoch, *rest = components
    return f'{epoch}!{".".join(rest)}'


def _is_not_suffix(segment):
    """
    Whether a version component is a release number, not a suffix.

    Ported from packaging/specifiers.py.

    Args:
        segment (str): One component of a split version

    Returns:
        bool: True when the component is not a pre/post/dev suffix
    """
    return not any(
        segment.startswith(prefix) for prefix in ('dev', 'a', 'b', 'rc', 'post'))


def _pad_version(left, right):
    """
    Pad the release segments of two split versions to the same length.

    Ported from packaging/specifiers.py: the release segment is 0-padded, so
    that a prefix match (`==3.8.*`) compares the same number of components.

    Args:
        left (list[str]): Split version
        right (list[str]): Split version

    Returns:
        tuple[list[str], list[str]]: The padded components of both versions
    """
    left_split, right_split = [], []

    # the release segment of both versions
    left_split.append(list(itertools.takewhile(lambda x: x.isdigit(), left)))
    right_split.append(list(itertools.takewhile(lambda x: x.isdigit(), right)))

    # the rest of both versions
    left_split.append(left[len(left_split[0]):])
    right_split.append(right[len(right_split[0]):])

    # insert the padding
    left_split.insert(1, ['0'] * max(0, len(right_split[0]) - len(left_split[0])))
    right_split.insert(1, ['0'] * max(0, len(left_split[0]) - len(right_split[0])))

    return (
        list(itertools.chain.from_iterable(left_split)),
        list(itertools.chain.from_iterable(right_split)),
    )


class Specifier:
    """
    One PEP 440 version specifier, e.g. '>=3.8' or '==3.8.*'.

    Ported from packaging/specifiers.py: the compare methods are the source
    implementation, the `prereleases` auto-detection is simplified to the
    question the fetcher asks ("does this specifier name a pre-release
    itself?").

    Attributes:
        operator (str): Operator of the specifier, e.g. '>='
        version (str): Version part of the specifier, e.g. '3.8'
    """

    def __init__(self, spec='', prereleases=None):
        """
        Args:
            spec (str): The specifier, e.g. '>=3.8'
            prereleases (bool | None): Whether the specifier accepts
                pre-releases. Defaults to None, decided by the specifier

        Raises:
            InvalidSpecifier: If the specifier is not valid
        """
        match = _OPERATOR_PATTERN.match(spec)
        if not match:
            raise InvalidSpecifier(f"Invalid specifier: '{spec}'")

        operator, version = match.group(1), match.group(2)
        # the version part is validated here, so a malformed specifier fails
        # when it is read, like the source pattern fails it: the wildcard form
        # names a prefix, the version part is what precedes the '.*'
        if operator != '===':
            check = version[:-2] if operator in ('==', '!=') and version.endswith('.*') else version
            try:
                parsed = Version(check)
            except InvalidVersion as e:
                raise InvalidSpecifier(f"Invalid specifier: '{spec}'") from e
            if operator == '~=' and len(parsed.release) < 2:
                # the compatible release operator needs a minor version
                raise InvalidSpecifier(f"Invalid specifier: '{spec}'")
            if parsed.local is not None and operator not in ('==', '!='):
                # a local version is only allowed by the equality operators
                raise InvalidSpecifier(f"Invalid specifier: '{spec}'")

        self._spec = (operator, version)
        self._prereleases = prereleases

    @property
    def prereleases(self):
        """
        Whether the specifier accepts pre-release versions.

        Ported from packaging/specifiers.py: an explicit value wins, otherwise
        a specifier that itself names a pre-release accepts pre-releases.

        Returns:
            bool: True when pre-releases are accepted
        """
        if self._prereleases is not None:
            return self._prereleases

        operator, version = self._spec
        if operator in ['==', '>=', '<=', '~=', '===']:
            if operator == '==' and version.endswith('.*'):
                # the wildcard is not part of the version
                version = version[:-2]
            try:
                if Version(version).is_prerelease:
                    return True
            except ValueError:
                # the identity operator accepts an arbitrary string
                return False
        return False

    @prereleases.setter
    def prereleases(self, value):
        """
        Args:
            value (bool): Whether the specifier accepts pre-releases
        """
        self._prereleases = value

    @property
    def operator(self):
        """
        Returns:
            str: The operator of the specifier, e.g. '>='
        """
        return self._spec[0]

    @property
    def version(self):
        """
        Returns:
            str: The version part of the specifier, e.g. '3.8'
        """
        return self._spec[1]

    def __str__(self):
        """
        Returns:
            str: The specifier, e.g. '>=3.8'
        """
        return '{}{}'.format(*self._spec)

    def __repr__(self):
        return f'<Specifier({str(self)!r})>'

    @property
    def _canonical_spec(self):
        """
        Returns:
            tuple[str, str]: The operator and the canonical version, the
                identity of the specifier
        """
        canonical_version = canonicalize_version(
            self._spec[1], strip_trailing_zero=(self._spec[0] != '~='))
        return self._spec[0], canonical_version

    def __hash__(self):
        return hash(self._canonical_spec)

    def __eq__(self, other):
        if isinstance(other, str):
            try:
                other = self.__class__(other)
            except InvalidSpecifier:
                return NotImplemented
        elif not isinstance(other, self.__class__):
            return NotImplemented
        return self._canonical_spec == other._canonical_spec

    def _compare_compatible(self, prospective, spec):
        """
        Compare with `~=`, the compatible release operator.

        Ported from packaging/specifiers.py: `~=2.2` is `>=2.2, ==2.*`.

        Args:
            prospective (Version): Version to check
            spec (str): Version part of the specifier

        Returns:
            bool: True if the version is compatible
        """
        # everything but the last release item, suffix segments ignored
        prefix = _version_join(
            list(itertools.takewhile(_is_not_suffix, _version_split(spec)))[:-1])
        # the prefix notation of the compare
        prefix += '.*'
        return (
            self._get_operator('>=')(prospective, spec)
            and self._get_operator('==')(prospective, prefix)
        )

    def _compare_equal(self, prospective, spec):
        """
        Compare with `==`, the wildcard form included.

        Ported from packaging/specifiers.py.

        Args:
            prospective (Version): Version to check
            spec (str): Version part of the specifier

        Returns:
            bool: True if the version matches
        """
        # prefix matching, e.g. '3.8.*'
        if spec.endswith('.*'):
            # the local segment is ignored by a prefix match
            normalized_prospective = canonicalize_version(
                prospective.public, strip_trailing_zero=False)
            # the normalized version string without the trailing '.*'
            normalized_spec = canonicalize_version(spec[:-2], strip_trailing_zero=False)
            # split the spec by bangs and dots, with an implicit dot between a
            # release segment and a pre-release segment
            split_spec = _version_split(normalized_spec)
            # the same for the prospective version
            split_prospective = _version_split(normalized_prospective)

            # 0-pad the prospective version, then shorten it to the length of
            # the spec, so the spec is a prefix of the prospective version or
            # it is not
            padded_prospective, _ = _pad_version(split_prospective, split_spec)
            shortened_prospective = padded_prospective[:len(split_spec)]
            return shortened_prospective == split_spec

        # convert the spec into a Version
        spec_version = Version(spec)
        # a specifier without a local segment ignores the local segment of the
        # prospective version
        if not spec_version.local:
            prospective = Version(prospective.public)
        return prospective == spec_version

    def _compare_not_equal(self, prospective, spec):
        """
        Compare with `!=`.

        Ported from packaging/specifiers.py.

        Args:
            prospective (Version): Version to check
            spec (str): Version part of the specifier

        Returns:
            bool: True if the version does not match
        """
        return not self._compare_equal(prospective, spec)

    def _compare_less_than_equal(self, prospective, spec):
        """
        Compare with `<=`.

        Ported from packaging/specifiers.py.

        Args:
            prospective (Version): Version to check
            spec (str): Version part of the specifier

        Returns:
            bool: True if the version is less than or equal to the spec
        """
        # local version identifiers are not permitted in the specifier, so the
        # local segment of the prospective version is removed
        return Version(prospective.public) <= Version(spec)

    def _compare_greater_than_equal(self, prospective, spec):
        """
        Compare with `>=`.

        Ported from packaging/specifiers.py.

        Args:
            prospective (Version): Version to check
            spec (str): Version part of the specifier

        Returns:
            bool: True if the version is greater than or equal to the spec
        """
        return Version(prospective.public) >= Version(spec)

    def _compare_less_than(self, prospective, spec_str):
        """
        Compare with `<`.

        Ported from packaging/specifiers.py: a pre-release of the version of
        the specifier does not match, `1.0rc1 < 1.0` is not "less than 1.0".

        Args:
            prospective (Version): Version to check
            spec_str (str): Version part of the specifier

        Returns:
            bool: True if the version is less than the spec
        """
        spec = Version(spec_str)

        # the prospective version is not less than the spec
        if not prospective < spec:
            return False

        # unless the specifier names a pre-release itself, a pre-release of the
        # same version does not match: '<3.1' does not match '3.1.dev0', but
        # matches '3.0.dev0'
        if not spec.is_prerelease and prospective.is_prerelease:
            if Version(prospective.base_version) == Version(spec.base_version):
                return False

        return True

    def _compare_greater_than(self, prospective, spec_str):
        """
        Compare with `>`.

        Ported from packaging/specifiers.py: a post-release of the version of
        the specifier does not match, and neither does a local version of it.

        Args:
            prospective (Version): Version to check
            spec_str (str): Version part of the specifier

        Returns:
            bool: True if the version is greater than the spec
        """
        spec = Version(spec_str)

        # the prospective version is not greater than the spec
        if not prospective > spec:
            return False

        # unless the specifier names a post-release itself, a post-release of
        # the same version does not match: '>3.1' does not match '3.1.post0'
        if not spec.is_postrelease and prospective.is_postrelease:
            if Version(prospective.base_version) == Version(spec.base_version):
                return False

        # a local version of the version of the specifier does not match
        if prospective.local is not None:
            if Version(prospective.base_version) == Version(spec.base_version):
                return False

        return True

    def _compare_arbitrary(self, prospective, spec):
        """
        Compare with `===`, the arbitrary identity operator.

        Ported from packaging/specifiers.py.

        Args:
            prospective (Version): Version to check
            spec (str): Version part of the specifier

        Returns:
            bool: True if the strings are equal, ignoring case
        """
        return str(prospective).lower() == str(spec).lower()

    def _get_operator(self, op):
        """
        The compare method of an operator.

        Ported from packaging/specifiers.py.

        Args:
            op (str): Operator, e.g. '>='

        Returns:
            Callable: The compare method
        """
        return getattr(self, f'_compare_{_OPERATORS[op]}')

    def __contains__(self, item):
        """
        Args:
            item (Version | str): Version to check

        Returns:
            bool: True if the version is contained in the specifier
        """
        return self.contains(item)

    def contains(self, item, prereleases=None):
        """
        Whether a version is contained in the specifier.

        Ported from packaging/specifiers.py, the prerelease filter included:
        a pre-release is rejected unless the specifier accepts pre-releases.

        Args:
            item (Version | str): Version to check
            prereleases (bool | None): Whether pre-releases are accepted.
                Defaults to None, decided by the specifier

        Returns:
            bool: True if the version is contained in the specifier
        """
        if prereleases is None:
            prereleases = self.prereleases

        normalized_item = _coerce_version(item)

        if normalized_item.is_prerelease and not prereleases:
            return False

        return self._get_operator(self.operator)(normalized_item, self.version)


class SpecifierSet:
    """
    A set of PEP 440 specifiers, e.g. '>=3.8,!=3.9.*'.

    Ported from packaging/specifiers.py: the set is the comma separated list
    of the specifier string, and a version is contained in it when it is
    contained in every specifier of the set.
    """

    def __init__(self, specifiers='', prereleases=None):
        """
        Args:
            specifiers (str | SpecifierSet): The specifier string, e.g.
                '>=3.8,<3.11', or another SpecifierSet to copy
            prereleases (bool | None): Whether pre-releases are accepted.
                Defaults to None, decided by the specifiers

        Raises:
            InvalidSpecifier: If a specifier of the string is not valid
        """
        if isinstance(specifiers, SpecifierSet):
            self._specs = specifiers._specs
            self._prereleases = prereleases
            return

        self._prereleases = prereleases
        self._specs = {}
        # the empty specifier set accepts every version
        for spec in specifiers.split(','):
            if not spec.strip():
                continue
            parsed = Specifier(spec)
            self._specs[parsed] = None

    @property
    def prereleases(self):
        """
        Whether the set accepts pre-release versions.

        Ported from packaging/specifiers.py: an explicit value wins, otherwise
        the set accepts pre-releases when one of its specifiers does.

        Returns:
            bool: True when pre-releases are accepted
        """
        if self._prereleases is not None:
            return self._prereleases
        if not self._specs:
            return False
        return any(specifier.prereleases for specifier in self._specs)

    @prereleases.setter
    def prereleases(self, value):
        """
        Args:
            value (bool): Whether the set accepts pre-releases
        """
        self._prereleases = value

    def __iter__(self):
        """
        Returns:
            Iterator[Specifier]: The specifiers of the set
        """
        return iter(self._specs)

    def __len__(self):
        """
        Returns:
            int: Number of specifiers of the set
        """
        return len(self._specs)

    def __str__(self):
        """
        Returns:
            str: The specifiers, comma separated
        """
        return ','.join(sorted(str(specifier) for specifier in self._specs))

    def __repr__(self):
        return f'<SpecifierSet({str(self)!r})>'

    def __hash__(self):
        return hash(str(self))

    def __eq__(self, other):
        if isinstance(other, str):
            try:
                other = self.__class__(str(other))
            except InvalidSpecifier:
                return NotImplemented
        elif not isinstance(other, self.__class__):
            return NotImplemented
        return self._specs == other._specs

    def __contains__(self, item):
        """
        Args:
            item (Version | str): Version to check

        Returns:
            bool: True if the version is contained in every specifier
        """
        return self.contains(item)

    def contains(self, item, prereleases=None):
        """
        Whether a version is contained in every specifier of the set.

        Ported from packaging/specifiers.py.

        Args:
            item (Version | str): Version to check
            prereleases (bool | None): Whether pre-releases are accepted.
                Defaults to None, decided by the set

        Returns:
            bool: True if the version is contained in the set
        """
        if prereleases is None:
            prereleases = self.prereleases

        # the early exit of the source: a pre-release is rejected by the whole
        # set unless the set accepts pre-releases
        if not prereleases and _coerce_version(item).is_prerelease:
            return False

        return all(
            specifier.contains(item, prereleases=prereleases)
            for specifier in self._specs
        )
