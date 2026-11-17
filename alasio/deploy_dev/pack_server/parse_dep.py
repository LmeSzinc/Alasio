"""
Parse a dependency file into the requirements it declares.

A pack config builds the packs of the dependencies of a repo, the versions to
build are read from the dependency file the mod entry of the repo names, see
lookback_wheel. The requirement text rules are parsed on the client side, by
the module that ships, see alasio.deploy.simple_pip.parse_req: this module
reads the dependency files with those rules and re-exports the client parser
for the release modules:

- RequirementsParser parses a requirements file, the pip requirement file
  format; the rules are the client parser's, Requirements
- PyprojectParser parses a pyproject.toml: the [project] tables of PEP 621,
  the [dependency-groups] table of PEP 735 and the dependency tables of
  poetry

A parser takes the content of its file as a string -- the caller reads the
files, e.g. the file of every commit of the lookback window, so no parser
touches the disk -- and provides two views of the same parse:

- ``specs`` is every requirement the file declares, as a RequirementSpec: the
  name, the extras, the PEP 440 specifier and the environment marker, ranges
  included (require '>=1.2', '~=1.0', '!=1.3'). A poetry constraint is
  translated to a PEP 440 specifier first (see poetry_specifier);
- ``dict_deps`` is the pins of the file, the PEP 503 normalized name to the
  version of the requirements that pin exactly one, e.g. {'httpx': '0.28.1'}.
  dict_pins is the same pins with the index they came from, a DepInfo per
  name: a requirements file carries the source in its '# from' annotations, a
  pyproject.toml carries no annotation and every pin of it has an empty source
  (the download side probes the mirrors of the mod entry).

Both views are parsed from the content on the first read and cached on the
parser. Which view a caller wants depends on what it does with the answer: a
tool that downloads the versions of a lock reads dict_deps, a tool that
resolves the versions of a declaration reads specs.

A declaration that does not pin a version has no entry in dict_deps: a range
(>=, <=, ~=, !=), a wildcard (==1.2.*), a bare name, a URL, a local path and a
pip option have no version to build a pack of. The extras and the environment
markers of a pin are read as they are, no marker is evaluated: the parsers do
not resolve dependencies, they read the declarations a file has.

An exact pin is one version: two different versions of one name in one file
cannot be told apart, they raise a ValueError when the pins are read.

Usage:
    RequirementsParser(content).dict_deps
    RequirementsParser(content).specs
    PyprojectParser(content).dict_deps
    PyprojectParser(content).specs
"""

import re

from packaging.utils import canonicalize_name as normalize_name  # noqa: F401

from alasio.deploy.pip_download._specifiers import InvalidSpecifier, SpecifierSet
# The requirement text rules are shared with the client on purpose: the
# release side parses the same files the client installs from, and a specifier
# is read once, by the parser of the wheel selector.
from alasio.deploy.simple_pip.parse_req import DepInfo, Requirements, RequirementSpec, parse_requirement  # noqa: F401
# Re-exported for the release modules that normalize a distribution name,
# e.g. fetch_wheel and dep_gen. The release side uses packaging; the client
# keeps its own normalize_name in alasio.deploy.simple_pip.pip_list.
from alasio.ext.cache import cached_property
from alasio.ext.file.tomlfile import toml_loads
from alasio.logger import logger


def build_pins(deps):
    """
    Build the pins of a dependency file from its parsed (name, version) pairs.

    The requirement text rules are shared with the client, see
    alasio.deploy.simple_pip.parse_req; the pins of a pyproject.toml are built
    here, from the pairs the client rule parse_requirement returns. The first
    version of a name wins, a later different one is a warning.

    Args:
        deps (Iterable[tuple[str, str]]): (name, version) pairs, name as
            written in the file

    Returns:
        dict[str, str]: PEP 503 normalized name -> version
    """
    pins = {}
    for name, version in deps:
        name = normalize_name(name)
        other = pins.get(name)
        if other is None:
            pins[name] = version
        elif other != version:
            logger.warning(
                f'Conflicting versions of "{name}" in the requirements: '
                f'"{other}" and "{version}", keeping "{other}"')
    return pins


def _strings(items):
    """
    The string entries of a declared list, the entries of another type dropped.

    Args:
        items (list | None): Entries of a table, e.g. [project].dependencies.
            An entry of another type (e.g. a {include-group} entry of PEP 735)
            is not a requirement

    Returns:
        list[str]: The entries that are requirement texts
    """
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, str)]


def _selected(table, extras, all_extras, label):
    """
    The names of a table of optional groups the caller asks for.

    Args:
        table (dict): The table, e.g. [project].optional-dependencies
        extras (Iterable[str]): The names the caller asks for
        all_extras (bool): True to take every name of the table
        label (str): What one name is called, for the error message

    Returns:
        list[str]: The names, in the declared order

    Raises:
        ValueError: If the caller names a group the document does not declare
    """
    if all_extras:
        return list(table)
    out = []
    for name in extras:
        if name not in table:
            raise ValueError(
                f'The project declares no {label} "{name}", it declares: '
                f'{", ".join(sorted(table)) or "none"}')
        out.append(name)
    return out


def _read_requirement(text, where, strict):
    """
    Read one PEP 508 requirement of a pyproject.toml.

    Args:
        text (str): Requirement text
        where (str): Table the text was read from, for the message
        strict (bool): True to raise on a text that cannot be used, False to
            drop it

    Returns:
        RequirementSpec | None: The requirement, None when it is dropped

    Raises:
        ValueError: If the text is not a requirement, or it names a url, a git
            repository or a local path, and strict is True
    """
    spec = RequirementSpec.from_line(text)
    if spec is None:
        if '||' in text:
            # a poetry union of ranges, which a PEP 440 specifier cannot
            # express: the author reads why, rather than a parse failure
            message = (
                f'The requirement "{text}" of {where} is a union of ranges ("||"), '
                f'which a PEP 440 specifier cannot express')
        else:
            message = f'Invalid requirement "{text}" in {where}'
        if not strict:
            logger.debug(f'Skipped a requirement of {where}: "{text}"')
            return None
        raise ValueError(message)
    if spec.url:
        if not strict:
            logger.debug(f'Skipped a direct reference of {where}: "{text}"')
            return None
        raise ValueError(
            f'The requirement "{text}" in {where} names a url, a git repository or a '
            f'path, which a version of an index cannot express')
    return spec


def pyproject_specs(data, extras=(), all_extras=False, groups=(), all_groups=False, strict=True,
                    poetry_tables='fallback'):
    """
    The requirements a pyproject.toml declares, ranges included.

    The tables read are the ones a pyproject.toml declares its dependencies in:

    - ``[project].dependencies`` (PEP 621), always;
    - ``[project].optional-dependencies.<extra>``, for the extras the caller
      names, or every one of them with `all_extras`;
    - ``[dependency-groups].<group>`` (PEP 735), for the groups the caller
      names. An entry of another type than a string (the ``include-group`` of
      the specification) is not a requirement.

    The poetry tables are read when the document has no ``[project]`` table, or
    always when the caller asks for it: a poetry project declares its
    dependencies in ``[tool.poetry]``, and a document that has both declares
    the dependencies of both (the reader that only lists what a file declares
    reads them all). The extras of a poetry project are the
    ``[tool.poetry.extras]`` table.

    Args:
        data (dict): Parsed pyproject.toml
        extras (Iterable[str]): Optional dependency groups to include.
            Defaults to (), the base dependencies only
        all_extras (bool): True to include every optional dependency group.
            Defaults to False
        groups (Iterable[str]): Dependency groups to include. Defaults to (),
            none of them
        all_groups (bool): True to include every dependency group. Defaults to
            False
        strict (bool): True to raise on a requirement this reader cannot use,
            False to drop it. Defaults to True
        poetry_tables (str): 'fallback' to read [tool.poetry] only when the
            document has no [project] table, 'always' to read it either way.
            Defaults to 'fallback'

    Returns:
        list[RequirementSpec]: The requirements, in the declared order

    Raises:
        ValueError: If a requirement, a marker or a poetry constraint cannot
            be read (with strict), or a named extra or group does not exist
    """
    if poetry_tables not in ('fallback', 'always'):
        raise ValueError(
            f'poetry_tables must be "fallback" or "always", got {poetry_tables!r}')

    project = data.get('project')
    out = []
    if isinstance(project, dict):
        out.extend(_iter_pep508(
            project, data, extras, all_extras, groups, all_groups, strict))
    elif isinstance(data.get('dependency-groups'), dict):
        # a document of dependency groups without a [project] table
        out.extend(_iter_pep508({}, data, extras, all_extras, groups, all_groups, strict))
    if poetry_tables == 'always' or not isinstance(project, dict):
        out.extend(_iter_poetry(data, extras, all_extras, strict))
    return out


def _iter_pep508(project, data, extras, all_extras, groups, all_groups, strict):
    """
    The requirements of the PEP 621 and PEP 735 tables.

    Args:
        project (dict): The [project] table of the document
        data (dict): The parsed document, for [dependency-groups]
        extras (Iterable[str]): Optional dependency groups to include
        all_extras (bool): True to include every optional dependency group
        groups (Iterable[str]): Dependency groups to include
        all_groups (bool): True to include every dependency group
        strict (bool): True to raise on a requirement this reader cannot use

    Yields:
        RequirementSpec: The requirements, in the declared order
    """
    for text in _strings(project.get('dependencies')):
        spec = _read_requirement(text, '[project].dependencies', strict)
        if spec is not None:
            yield spec

    optional = project.get('optional-dependencies')
    if isinstance(optional, dict):
        for name in _selected(optional, extras, all_extras, 'optional dependency group'):
            for text in _strings(optional[name]):
                spec = _read_requirement(
                    text, f'[project.optional-dependencies].{name}', strict)
                if spec is not None:
                    yield spec

    table = data.get('dependency-groups')
    if isinstance(table, dict):
        for name in _selected(table, groups, all_groups, 'dependency group'):
            for text in _strings(table[name]):
                spec = _read_requirement(text, f'[dependency-groups].{name}', strict)
                if spec is not None:
                    yield spec


def _iter_poetry(data, extras, all_extras, strict):
    """
    The requirements of the tables of [tool.poetry].

    Args:
        data (dict): Parsed pyproject.toml
        extras (Iterable[str]): Poetry extras to include
        all_extras (bool): True to include every poetry extra
        strict (bool): True to raise on an entry this reader cannot use

    Yields:
        RequirementSpec: The requirements, in the declared order
    """
    tool = data.get('tool')
    poetry = tool.get('poetry') if isinstance(tool, dict) else None
    if not isinstance(poetry, dict):
        return

    yield from _iter_poetry_table(poetry.get('dependencies'), strict)
    yield from _iter_poetry_table(poetry.get('dev-dependencies'), strict)

    groups = poetry.get('group')
    if isinstance(groups, dict):
        for group in groups.values():
            if isinstance(group, dict):
                yield from _iter_poetry_table(group.get('dependencies'), strict)

    declared = poetry.get('extras')
    if isinstance(declared, dict):
        for name in _selected(declared, extras, all_extras, 'extra'):
            for text in _strings(declared[name]):
                spec = _read_requirement(text, f'[tool.poetry.extras].{name}', strict)
                if spec is not None:
                    yield spec


def _iter_poetry_table(table, strict):
    """
    The requirements of one poetry dependency table.

    Args:
        table (dict | None): Table of the document, name -> constraint. The
            'python' entry is the interpreter constraint of the project, not a
            distribution of PyPI, so it is skipped
        strict (bool): True to raise on an entry this reader cannot use

    Yields:
        RequirementSpec: The requirements, in the declared order
    """
    if not isinstance(table, dict):
        return
    for name, constraint in table.items():
        if name == 'python':
            continue
        spec = _poetry_requirement(name, constraint, strict)
        if spec is not None:
            yield spec


def _poetry_requirement(name, constraint, strict):
    """
    One entry of a poetry dependency table as a requirement.

    A poetry entry is a constraint string, or a table with a 'version', an
    'extras' and a 'markers' key. The other keys name a source of the
    distribution rather than a version of an index: a git, a path or a url is
    refused, like the same declaration of a PEP 508 requirement.

    Args:
        name (str): Name of the entry, as written in the file
        constraint (str | dict): The entry
        strict (bool): True to raise on an entry this reader cannot use

    Returns:
        RequirementSpec | None: The requirement, None when it is dropped

    Raises:
        ValueError: If the entry names a source, or its constraint cannot be
            read, and strict is True
    """
    try:
        extras = ()
        markers = ''
        if isinstance(constraint, dict):
            for key in ('git', 'path', 'url', 'file'):
                if key in constraint:
                    raise ValueError(
                        f'The poetry dependency "{name}" names a {key}, which a version '
                        f'of an index cannot express')
            extras = tuple(sorted(_strings(constraint.get('extras'))))
            markers = constraint.get('markers', '') or ''
            constraint = constraint.get('version', '')
        if not isinstance(constraint, str):
            raise ValueError(
                f'The poetry dependency "{name}" declares no version constraint this '
                f'reader knows')
        specifier = poetry_specifier(name, constraint)
    except ValueError as e:
        if strict:
            raise
        logger.debug(f'Skipped a poetry dependency: {e}')
        return None
    return RequirementSpec(
        name=normalize_name(name),
        extras=extras,
        specifier=specifier,
        marker=str(markers),
    )


# The operators of a poetry constraint, the longest ones first so '^' and '~'
# are read before the PEP 440 operators that start with the same characters
_POETRY_OPERATORS = ('^', '~', '>=', '<=', '==', '!=', '>', '<')


def poetry_specifier(name, constraint):
    """
    Translate a poetry version constraint into a PEP 440 specifier.

    Poetry reads a bare version as an exact pin, ``^`` as a compatible release
    (the leftmost non-zero part is fixed), ``~`` as a minor compatible release
    and ``*`` as anything; the operators of PEP 440 (``>=``, ``<``, ``!=``,
    ``~=``, ``===``) are the operators of PEP 440. A constraint is a
    conjunction, written with commas or spaces.

    The ``||`` of poetry is a union, which a PEP 440 specifier cannot express,
    and is refused. So is ``^``/``~`` of a version that is not a plain release
    number, because the upper bound is computed from the release parts.

    Args:
        name (str): Name of the dependency, for the error message
        constraint (str): The poetry constraint, e.g. '^1.2', '>=1.0,<2.0',
            '1.2.*', '==1.2.3'

    Returns:
        str: The PEP 440 specifier text, sorted and comma separated, '' when
            the constraint pins nothing

    Raises:
        ValueError: If the constraint is a form this reader does not know
    """
    text = constraint.strip()
    if not text or text == '*':
        return ''
    if '||' in text:
        raise ValueError(
            f'The poetry constraint "{constraint}" of "{name}" is a union of ranges, '
            f'which a PEP 440 specifier cannot express')

    specifiers = []
    for part in text.replace(' ', ',').split(','):
        part = part.strip()
        if not part or part == '*':
            continue
        if part.startswith('^'):
            specifiers.extend(_poetry_caret(name, constraint, part[1:].strip()))
            continue
        if part.startswith('~') and not part.startswith('~='):
            specifiers.extend(_poetry_tilde(name, constraint, part[1:].strip()))
            continue
        operator = next(
            (candidate for candidate in _POETRY_OPERATORS if part.startswith(candidate)), '')
        version = part[len(operator):].strip()
        if not operator and version.startswith('='):
            # the explicit form of a bare version in poetry, '=1.2.3'
            version = version[1:].strip()
        if not version:
            raise ValueError(
                f'The poetry constraint "{constraint}" of "{name}" carries no version')
        if operator:
            specifiers.append(f'{operator}{version}')
        else:
            # a bare version is an exact pin in poetry, and one leading '=' is
            # its explicit form; a wildcard keeps its wildcard for PEP 440
            specifiers.append(f'=={version}')

    translated = ','.join(sorted(specifiers))
    try:
        # a consumer of the parser never checks the specifier again: the wheel
        # selector rejects a text this reader would hand it
        SpecifierSet(translated)
    except InvalidSpecifier as e:
        raise ValueError(
            f'The poetry constraint "{constraint}" of "{name}" is not a PEP 440 '
            f'specifier: {e}') from None
    return translated


def _poetry_caret(name, constraint, version):
    """
    The PEP 440 specifiers of a poetry caret constraint.

    The leftmost non-zero part of the version is the one that is fixed:
    ``^1.2.3`` is ``>=1.2.3,<2.0.0`` and ``^0.2.3`` is ``>=0.2.3,<0.3.0``.

    Args:
        name (str): Name of the dependency, for the error message
        constraint (str): The whole constraint, for the error message
        version (str): Version part of the constraint

    Returns:
        list[str]: e.g. ['>=1.2.3', '<2.0.0']

    Raises:
        ValueError: If the version is not a plain release number
    """
    parts = _poetry_parts(name, constraint, version)
    index = next((i for i, part in enumerate(parts) if part), len(parts) - 1)
    upper = parts[:index] + [parts[index] + 1]
    upper += [0] * (len(parts) - len(upper))
    return [f'>={version}', f'<{".".join(str(part) for part in upper)}']


def _poetry_tilde(name, constraint, version):
    """
    The PEP 440 specifiers of a poetry tilde constraint.

    ``~1.2.3`` is ``>=1.2.3,<1.3.0`` and ``~1.2`` is ``>=1.2,<1.3``: the minor
    part is fixed, the parts after it are free.

    Args:
        name (str): Name of the dependency, for the error message
        constraint (str): The whole constraint, for the error message
        version (str): Version part of the constraint

    Returns:
        list[str]: e.g. ['>=1.2.3', '<1.3.0']

    Raises:
        ValueError: If the version is not a plain release number
    """
    parts = _poetry_parts(name, constraint, version)
    if len(parts) < 2:
        upper = [parts[0] + 1]
    else:
        upper = parts[:1] + [parts[1] + 1] + [0] * (len(parts) - 2)
    return [f'>={version}', f'<{".".join(str(part) for part in upper)}']


def _poetry_parts(name, constraint, version):
    """
    The release numbers of a poetry constraint version.

    Args:
        name (str): Name of the dependency, for the error message
        constraint (str): The whole constraint, for the error message
        version (str): Version part of the constraint

    Returns:
        list[int]: The release parts, e.g. [1, 2, 3] of '1.2.3'

    Raises:
        ValueError: If the version is not a plain release number
    """
    if not re.fullmatch(r'\d+(?:\.\d+)*', version):
        raise ValueError(
            f'The poetry constraint "{constraint}" of "{name}" does not name a plain '
            f'release number, which "^" and "~" are computed from')
    return [int(part) for part in version.split('.')]


class RequirementsParser(Requirements):
    """
    Parser of a requirements file, the pip requirement file format.

    The rules are the client parser's (alasio.deploy.simple_pip.parse_req):
    the client is the side that ships, the release tooling imports it, never
    the other way around. The forms the parser handles:

    - ``name==version``, with extras (``name[extra]==version``) and an
      environment marker (``name==version; python_version < "3.9"``), a
      marker is not evaluated
    - a line continuation with a trailing ``\\``, e.g. the --hash lines of a
      pip-compile output
    - a comment: a '#' at the start of a line or after whitespace

    The name of a pin is normalized with PEP 503 in dict_deps, like the
    dist-key of the pack files. specs is the same file read as the requirement
    texts it declares, the ranges included.

    Usage:
        RequirementsParser(content).dict_deps
        RequirementsParser(content).specs
    """


class PyprojectParser:
    """
    Parser of a pyproject.toml, the dependency declarations of the document.

    The tables a pyproject.toml declares its dependencies in are read:

    - ``[project].dependencies`` and ``[project.optional-dependencies].*``
      (PEP 621) and ``[dependency-groups].*`` (PEP 735), each entry is a
      PEP 508 requirement
    - ``[tool.poetry.dependencies]``, ``[tool.poetry.dev-dependencies]`` and
      ``[tool.poetry.group.*.dependencies]``, each entry is a poetry version
      constraint, translated to a PEP 440 specifier by poetry_specifier

    The other tables of the document (e.g. ``[build-system]``, a tool of a
    build backend) do not declare the dependencies the project runs with and
    are not read. The name of an entry is normalized with PEP 503 in
    dict_deps, like the dist-key of the pack files.

    dict_deps is the exact pins of the document (only '==' entries), specs is
    every declaration the document has, the ranges included.

    Usage:
        PyprojectParser(content).dict_deps
        PyprojectParser(content).specs
    """

    def __init__(self, content):
        """
        Args:
            content (str): Content of a pyproject.toml
        """
        self.content = content

    @cached_property
    def data(self):
        """
        Parsed TOML document of the content, parsed on the first read and cached.

        Returns:
            dict: Parsed document

        Raises:
            ImportError: If tomllib is not available (Python < 3.11) and
                tomli is not installed
            ValueError: If the content is not a TOML document
        """
        return toml_loads(self.content)

    @cached_property
    def specs(self):
        """
        The requirements of the document, ranges included.

        A requirement this reader cannot use is an error: a document the
        compiler would resolve incorrectly is worse than a compile that stops.

        Returns:
            list[RequirementSpec]: The requirements, in the declared order

        Raises:
            ValueError: If a requirement or a poetry constraint cannot be read
            ImportError: If tomllib is not available (Python < 3.11) and
                tomli is not installed
        """
        return pyproject_specs(self.data)

    @cached_property
    def dict_deps(self):
        """
        Pins of the document, parsed on the first read and cached.

        Returns:
            dict[str, str]: PEP 503 normalized name -> exact version of every
                dependency pinned to one version, e.g. {'httpx': '0.28.1'}

        Raises:
            ValueError: If the content is not a TOML document
            ImportError: If tomllib is not available (Python < 3.11) and
                tomli is not installed

        A declaration that cannot be used (an invalid requirement, a poetry
        table this reader does not translate) is skipped: this view only lists
        the pins, and a pack server never has to resolve a range.
        """
        deps = []
        for spec in pyproject_specs(
            self.data, all_extras=True, all_groups=True, strict=False, poetry_tables='always'
        ):
            version = spec.pin_version
            if version is not None:
                deps.append((spec.name, version))
        return build_pins(deps)

    @cached_property
    def dict_pins(self):
        """
        Pins of the document and their sources, parsed on the first read and cached.

        A pyproject.toml carries no '# from' annotation, so every pin of the
        document has an empty source: the download side probes the mirrors of
        the mod entry, see DepInfo.

        Returns:
            dict[str, DepInfo]: PEP 503 normalized name -> pin, the source
                of every pin ''
        """
        return {
            name: DepInfo(name, version, '')
            for name, version in self.dict_deps.items()
        }
