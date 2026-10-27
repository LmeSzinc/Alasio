"""
Name and filename helpers of the PyPI index.

Ported from `packaging/utils.py` (packaging, Apache-2.0 OR BSD-2-Clause):
`canonicalize_name`, `canonicalize_version`, `parse_wheel_filename` and the
name/build/tag patterns. `check_requires_python` is ported from
`pip/_internal/utils/packaging.py` (pip, MIT).

Usage:
    from alasio.deploy.pip_download._utils import canonicalize_name, parse_wheel_filename

    canonicalize_name('ruamel.yaml')                 # 'ruamel-yaml'
    parse_wheel_filename('httpx-0.28.1-py3-none-any.whl')
"""

import re

from alasio.deploy.pip_download._tags import parse_tag
from alasio.deploy.pip_download._version import InvalidVersion, Version, _TrimmedRelease

__all__ = [
    'InvalidName',
    'InvalidWheelFilename',
    'canonicalize_name',
    'canonicalize_version',
    'check_requires_python',
    'parse_wheel_filename',
]

# Core metadata spec for `Name`
_validate_regex = re.compile(r'[a-z0-9]|[a-z0-9][a-z0-9._-]*[a-z0-9]', re.IGNORECASE | re.ASCII)
_normalized_regex = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*', re.ASCII)
# PEP 427: the build number must start with a digit
_build_tag_regex = re.compile(r'(\d+)(.*)', re.ASCII)
# PEP 427: valid characters for an escaped project name in a wheel filename.
# Requires at least one character, so an empty project name is rejected.
_wheel_name_regex = re.compile(r'^[\w._]+\Z', re.UNICODE)


class InvalidName(ValueError):
    """
    An invalid distribution name.

    Ported from packaging/utils.py.
    """


class InvalidWheelFilename(ValueError):
    """
    An invalid wheel filename was found, users should refer to PEP 427.

    Ported from packaging/utils.py.
    """


def canonicalize_name(name, validate=False):
    """
    PEP 503 normalized name of a distribution.

    Ported from packaging/utils.py: the run of `-`, `_` and `.` is a single
    `-` and the name is lowercased. The normalized name is the key of the
    index url and of the links of a page.

    Args:
        name (str): Name of a distribution, e.g. 'ruamel.yaml'
        validate (bool): Check that the name is a valid distribution name.
            Defaults to False

    Returns:
        str: The normalized name, e.g. 'ruamel-yaml'

    Raises:
        InvalidName: If validate is True and the name is not valid
    """
    if validate and not _validate_regex.fullmatch(name):
        raise InvalidName(f'name is invalid: {name!r}')
    # ensure all '.' and '_' are '-', the PEP 503 normalization
    value = name.lower().replace('_', '-').replace('.', '-')
    # condense repeats
    while '--' in value:
        value = value.replace('--', '-')
    return value


def canonicalize_version(version, strip_trailing_zero=True):
    """
    The canonical form of a version string.

    Ported from packaging/utils.py: the release segment loses its trailing
    zeros (one zero is kept), the other segments are the normalized form of
    the version. An invalid version is returned unaltered.

    Args:
        version (Version | str): Version to canonicalize
        strip_trailing_zero (bool): Strip the trailing zeros of the release
            segment. Defaults to True, the form PEP 625 writes

    Returns:
        str: The canonical version, e.g. '1.4' for '1.4.0.0.0'
    """
    if isinstance(version, str):
        try:
            version = Version(version)
        except InvalidVersion:
            return version
    return str(_TrimmedRelease(version) if strip_trailing_zero else version)


def parse_wheel_filename(filename, validate_order=False):
    """
    Parse a wheel filename into its name, version, build tag and tags.

    Ported from packaging/utils.py. The wheel filename format (PEP 427):
    {name}-{version}(-{build tag})?-{python tag}-{abi tag}-{platform tag}.whl

    Args:
        filename (str): Filename of a wheel, e.g. 'httpx-0.28.1-py3-none-any.whl'
        validate_order (bool): Check that the components of a compressed tag
            set are sorted, per PEP 425. Defaults to False

    Returns:
        tuple[str, Version, tuple, frozenset[Tag]]: The normalized name, the
            version, the build tag (() when absent, else (number, text)) and
            the tags of the file

    Raises:
        InvalidWheelFilename: If the filename is not a wheel filename
    """
    if not filename.endswith('.whl'):
        raise InvalidWheelFilename(
            f"Invalid wheel filename (extension must be '.whl'): {filename!r}")

    filename = filename[:-len('.whl')]
    dashes = filename.count('-')
    if dashes not in (4, 5):
        raise InvalidWheelFilename(
            f'Invalid wheel filename (wrong number of parts): {filename!r}')

    parts = filename.split('-', dashes - 2)
    name_part = parts[0]
    # see PEP 427 for the rules on escaping the project name
    if '__' in name_part or _wheel_name_regex.match(name_part) is None:
        raise InvalidWheelFilename(f'Invalid project name: {filename!r}')
    name = canonicalize_name(name_part)

    try:
        version = Version(parts[1])
    except InvalidVersion as e:
        raise InvalidWheelFilename(
            f'Invalid wheel filename (invalid version): {filename!r}') from e

    if dashes == 5:
        build_part = parts[2]
        build_match = _build_tag_regex.match(build_part)
        if build_match is None:
            raise InvalidWheelFilename(
                f'Invalid build number: {build_part} in {filename!r}')
        build = (int(build_match.group(1)), build_match.group(2))
    else:
        build = ()

    tag_str = parts[-1]
    try:
        tags = parse_tag(tag_str, validate_order=validate_order)
    except ValueError as e:
        raise InvalidWheelFilename(
            f'Invalid wheel filename (invalid tag component): {filename!r}') from e
    return name, version, build, tags


def check_requires_python(requires_python, version_info):
    """
    Check whether a Python version matches a `Requires-Python` specifier.

    Ported from pip/_internal/utils/packaging.py. The specifier is the value
    of the `data-requires-python` attribute of an index link; an empty value
    means the distribution declares nothing and every interpreter is allowed.

    Args:
        requires_python (str | None): The specifier, e.g. '>=3.8'
        version_info (tuple[int, ...]): Python version to check, e.g.
            sys.version_info[:3]

    Returns:
        bool: True if the version satisfies the specifier

    Raises:
        InvalidSpecifier: If the specifier is not valid
    """
    # imported here: the specifier module builds a SpecifierSet per call and
    # is only needed by the callers that evaluate requires-python
    from alasio.deploy.pip_download._specifiers import SpecifierSet

    if requires_python is None:
        # the package provides no information
        return True
    requires_python_specifier = SpecifierSet(requires_python)

    python_version = Version('.'.join(map(str, version_info)))
    return python_version in requires_python_specifier
