"""
Parse a client requirements file into the distributions pinned to an exact
version.

The environment of a client has to satisfy the requirements file of the
deployed tree, the file the deploy config names (Deploy.Python.RequirementsFile,
e.g. 'requirements.txt'): it lists the distributions the project runs with,
every one of them pinned to the exact version of the environment. This module
parses one such file into its pins, so the client can read the file without
pip: the pins are what a dependency check compares with the installed
distributions (PipList), the versions the environment has to satisfy.

The parser takes the content of the file as a string -- the caller reads the
file, so the parser touches no disk -- and provides the pins as dict_deps: a
dict of the PEP 503 normalized distribution name to the version, e.g.
{'httpx': '0.28.1'}. The pins are parsed from the content on the first read of
dict_deps and cached on the parser.

A declaration that does not pin a version is skipped: a range (>=, <=, ~=,
!=), a wildcard (==1.2.*), a bare name, a URL, a local path and a pip option
have no version to install. The extras and the environment markers of a pin
are read as they are, no marker is evaluated: the parser does not resolve
dependencies, it reads the versions a file declares.

An exact pin is one version: two different versions of one name in one file
cannot be told apart, they raise a ValueError when the pins are read.

The format rules are implemented once, in this module: the client is the side
that ships, the release tooling imports it, never the other way around. The
release side reuses Requirements for a requirements file and parse_requirement
/ build_pins for the PEP 508 texts and the dependency tables of a
pyproject.toml, see alasio.deploy_dev.pack_server.parse_dep.

Usage:
    Requirements(content).dict_deps
"""

import re

from alasio.deploy.simple_pip.pip_list import normalize_name
from alasio.ext.cache import cached_property


def parse_requirement(text):
    """
    Parse one requirement text into a pin.

    The text is a subset of PEP 508: the extras are ignored, an environment
    marker after a ';' is ignored, the version is a text of version
    characters with a digit, a wildcard is not a version. The release side
    parses the PEP 508 texts of a pyproject.toml with it, see
    alasio.deploy_dev.pack_server.parse_dep.

    Args:
        text (str): Requirement text, e.g. 'httpx[cli]==0.28.1; python_version < "3.9"'

    Returns:
        tuple[str, str] | None: (name, version) of the pin, name as written
            in the file, None if the text is not a requirement pinned to an
            exact version
    """
    # the environment marker is not evaluated, the pin is read as it is
    text = text.partition(';')[0].strip()
    match = re.fullmatch(
        # the distribution name, PEP 508, e.g. 'httpx', 'ruamel.yaml'
        r'(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)'
        # the extras, e.g. '[cli]', they are ignored
        r'(?:\s*\[[^\]]*\])?'
        # the exact version operator, e.g. '=='. A range operator of PEP 440
        # ('>=', '~=') and the arbitrary equality ('===') do not match
        r'\s*==\s*'
        # the version: the characters a version is written with, PEP 440 and
        # the legacy forms, e.g. 0.28.1, 1.0rc1, 1!2.0, 1.0+local.1, 1.0-1
        r'(?P<version>[A-Za-z0-9._+!-]+)',
        text,
    )
    if not match:
        return None
    version = match.group('version')
    if not re.search(r'[0-9]', version):
        # a version carries a digit, e.g. 1.0.dev0 does and a text like 'dev'
        # does not: a text without a digit is not a version, no wheel can
        # carry it
        return None
    return match.group('name'), version


def build_pins(deps):
    """
    Build the pins of a dependency file from its parsed (name, version) pairs.

    The release side builds the pins of a pyproject.toml with it, see
    alasio.deploy_dev.pack_server.parse_dep.

    Args:
        deps (Iterable[tuple[str, str]]): (name, version) pairs, name as
            written in the file

    Returns:
        dict[str, str]: PEP 503 normalized name -> version

    Raises:
        ValueError: If two requirements pin two different versions of the
            same name: the file is ambiguous, the caller cannot tell which
            version the environment has to satisfy
    """
    pins = {}
    for name, version in deps:
        name = normalize_name(name)
        other = pins.get(name)
        if other is None:
            pins[name] = version
        elif other != version:
            raise ValueError(
                f'Conflicting versions of "{name}" in the requirements: "{other}" and "{version}"')
    return pins


def _iter_requirement(content):
    """
    Iter the requirement texts of a requirements file content.

    The lines pip understands but this parser does not pin a version with are
    handled here: a comment (a '#' at the start of a line or after
    whitespace), an option line (--index-url, -r, -e) and the options after a
    requirement (--hash). A '-r' include is not followed: the caller reads
    the included file and passes its content as its own Requirements.

    Args:
        content (str): Content of a requirements file

    Yields:
        str: Text of one requirement, e.g. 'httpx==0.28.1'
    """
    # the UTF-8 BOM and the line endings
    content = content.lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n')
    # a line that ends with a '\' continues on the next one
    content = re.sub(r'\\[ \t]*\n', '', content)
    for line in content.split('\n'):
        # a '#' starts a comment at the start of a line or after whitespace
        line = re.sub(r'(^|\s)#.*', '', line).strip()
        if not line:
            continue
        # an option line, e.g. --index-url or -r
        if line.startswith('-'):
            continue
        # the options after a requirement, e.g. --hash, are not part of it
        line = re.split(r'\s+(?=-)', line, maxsplit=1)[0]
        if line:
            yield line


class Requirements:
    """
    Requirements file of a client, the pip requirement file format.

    dict_deps of the parser is the mapping of the exact pins of the file,
    every other declaration is skipped, see the module docstring. The forms
    the parser handles:

    - ``name==version``, with extras (``name[extra]==version``) and an
      environment marker (``name==version; python_version < "3.9"``), a
      marker is not evaluated
    - a line continuation with a trailing ``\\``, e.g. the --hash lines of a
      pip-compile output
    - a comment: a '#' at the start of a line or after whitespace

    The name of a pin is normalized with PEP 503 in dict_deps, the same
    dist-key a pack and the installed distributions of PipList use.

    Usage:
        Requirements(content).dict_deps
    """

    def __init__(self, content):
        """
        Args:
            content (str): Content of a requirements file, e.g.
                requirements.txt
        """
        self.content = content

    @cached_property
    def dict_deps(self):
        """
        Pins of the file, parsed on the first read and cached.

        Returns:
            dict[str, str]: PEP 503 normalized name -> exact version of every
                requirement pinned with '==', e.g. {'httpx': '0.28.1'}

        Raises:
            ValueError: If two requirements pin two different versions of
                the same name
        """
        deps = []
        for text in _iter_requirement(self.content):
            dep = parse_requirement(text)
            if dep is not None:
                deps.append(dep)
        return build_pins(deps)
