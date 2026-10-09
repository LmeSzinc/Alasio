"""
Parse a dependency file into the requirements pinned to an exact version.

A pack config with PythonDeps builds the packs of the dependencies of a repo,
the versions to build are read from the dependency files of the repo. The
requirements file format is parsed on the client side, by the module that
ships, see alasio.deploy.simple_pip.parse_req: this module reads the TOML
forms with the client rules and re-exports the client parser for the release
modules:

- RequirementsParser parses a requirements file, the pip requirement file
  format; the rules are the client parser's, Requirements
- PyprojectParser parses a pyproject.toml: the [project] tables of PEP 621,
  the [dependency-groups] table of PEP 735 and the dependency tables of
  poetry; its PEP 508 texts and its pins are parsed by the client rules too,
  parse_requirement and build_pins

A parser takes the content of its file as a string -- the caller reads the
files, e.g. the file of every commit of the lookback window, so no parser
touches the disk -- and provides the pins as dict_deps: a dict of the PEP 503
normalized distribution name to the version, e.g. {'httpx': '0.28.1'}. The
pins are parsed from the content on the first read of dict_deps and cached on
the parser.

A declaration that does not pin a version is skipped: a range (>=, <=, ~=,
!=), a wildcard (==1.2.*), a bare name, a URL, a local path and a pip option
have no version to build a pack of. The extras and the environment markers of
a pin are read as they are, no marker is evaluated: the parsers do not
resolve dependencies, they read the versions a file declares.

An exact pin is one version: two different versions of one name in one file
cannot be told apart, they raise a ValueError when the pins are read.

Usage:
    RequirementsParser(content).dict_deps
    PyprojectParser(content).dict_deps
"""

import re

from alasio.deploy.simple_pip.parse_req import Requirements, build_pins, parse_requirement
# re-exported for the release modules that normalize a distribution name,
# e.g. fetch_wheel and dep_gen
from alasio.deploy.simple_pip.pip_list import normalize_name  # noqa: F401
from alasio.ext.cache import cached_property


def _load_toml(content):
    """
    Load a TOML document from its content.

    tomllib is the standard library module of Python 3.11+, tomli is the
    backport with the same API for the older versions, like the import of
    build_wheel.

    Args:
        content (str): Content of the document

    Returns:
        dict: Parsed document

    Raises:
        ImportError: If tomllib is not available and tomli is not installed
        ValueError: If the content is not a TOML document, TOMLDecodeError is
            a ValueError
    """
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            raise ImportError('Dependency `tomli` is required on Python < 3.11') from None
    return tomllib.loads(content)


def _parse_requirement_list(items):
    """
    Parse a list of PEP 508 requirement strings, the entries of a pyproject table.

    Args:
        items (list | None): Entries of the table, e.g. [project].dependencies.
            An entry of another type (e.g. a {include-group} entry of PEP 735)
            is not a requirement and is skipped.

    Returns:
        list[tuple[str, str]]: (name, version) pairs of the exact pins, name
            as written in the file
    """
    if not isinstance(items, list):
        return []
    deps = []
    for text in items:
        if not isinstance(text, str):
            continue
        dep = parse_requirement(text)
        if dep is not None:
            deps.append(dep)
    return deps


def _parse_poetry_constraint(constraint):
    """
    Parse the exact version of a poetry version constraint, if it pins one.

    A poetry constraint is exact when it is a plain version string: a plain
    '0.28.1', '=0.28.1' and '==0.28.1' are one exact version, poetry reads a
    plain version string as one version, see the parse_constraint of
    poetry-core (checked with 1.9.1 and 2.5.0). An operator of a range ('^',
    '~', '~=', '<', '<=', '>', '>=', '!='), a wildcard ('1.2.*') and an entry
    without a version are not exact pins either.

    Args:
        constraint (str | dict): Value of a poetry dependency entry: a
            constraint string or a table with a 'version' key, e.g.
            {version = "==0.28.1", markers = "python_version < '3.9'"}.
            An entry without a version (a git, path or url dependency) pins
            nothing and yields None.

    Returns:
        str | None: Exact version of the constraint, None if the constraint
            does not pin one
    """
    if isinstance(constraint, dict):
        constraint = constraint.get('version')
    if not isinstance(constraint, str):
        return None
    text = constraint.strip()
    if text.startswith('=='):
        text = text[2:].strip()
    elif text.startswith('='):
        text = text[1:].strip()
    elif not text or not text[0].isdigit():
        # a plain version is exact in poetry, a text that starts with
        # anything else is an operator ('^1.0', '>=1.0', '*') or a text like
        # 'dev', none of them is a plain version
        return None
    # a version carries only version characters, the characters of PEP 440
    # and the legacy forms (e.g. 0.28.1, 1.0rc1, 1!2.0, 1.0+local.1, 1.0-1):
    # '1.0.*', '1.0, >=1.1' and '1.0 || 1.1' do not slip through
    if not re.fullmatch(r'[A-Za-z0-9._+!-]+', text):
        return None
    # a version carries a digit, e.g. 1.0.dev0 does and a text like 'dev'
    # does not
    if not re.search(r'[0-9]', text):
        return None
    return text


def _parse_poetry_table(table):
    """
    Parse a poetry dependency table, e.g. [tool.poetry.dependencies].

    Args:
        table (dict | None): Table of the document, name -> constraint. The
            'python' entry is the interpreter constraint of the project, not
            a distribution of PyPI, it is skipped.

    Returns:
        list[tuple[str, str]]: (name, version) pairs of the exact pins, name
            as written in the file
    """
    if not isinstance(table, dict):
        return []
    deps = []
    for name, constraint in table.items():
        if name == 'python':
            continue
        version = _parse_poetry_constraint(constraint)
        if version is not None:
            deps.append((name, version))
    return deps


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
    dist-key of the pack files.

    Usage:
        RequirementsParser(content).dict_deps
    """


class PyprojectParser:
    """
    Parser of a pyproject.toml, the dependency declarations of the document.

    The tables a pyproject.toml declares its dependencies in are read:

    - ``[project].dependencies`` and ``[project.optional-dependencies].*``
      (PEP 621) and ``[dependency-groups].*`` (PEP 735), each entry is a
      PEP 508 requirement, only the ones pinned with '==' are returned
    - ``[tool.poetry.dependencies]``, ``[tool.poetry.dev-dependencies]`` and
      ``[tool.poetry.group.*.dependencies]``, each entry is a poetry version
      constraint, a plain version string ('0.28.1'), '=0.28.1' and '==0.28.1'
      pin one version, a range ('^', '~', '>=' ...) does not

    The other tables of the document (e.g. ``[build-system]``, a tool of a
    build backend) do not declare the dependencies the project runs with and
    are not read. The name of an entry is normalized with PEP 503 in
    dict_deps, like the dist-key of the pack files.

    Usage:
        PyprojectParser(content).dict_deps
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
        return _load_toml(self.content)

    @cached_property
    def dict_deps(self):
        """
        Pins of the document, parsed on the first read and cached.

        Returns:
            dict[str, str]: PEP 503 normalized name -> exact version of every
                dependency pinned to one version, e.g. {'httpx': '0.28.1'}

        Raises:
            ValueError: If the content is not a TOML document, or the file
                pins two different versions of the same name
            ImportError: If tomllib is not available (Python < 3.11) and
                tomli is not installed
        """
        deps = self._parse_pep508()
        deps.extend(self._parse_poetry())
        return build_pins(deps)

    def _parse_pep508(self):
        """
        Parse the PEP 508 requirements of the document, [project] and [dependency-groups].

        Returns:
            list[tuple[str, str]]: (name, version) pairs of the exact pins,
                name as written in the file
        """
        deps = []
        project = self.data.get('project')
        if isinstance(project, dict):
            deps.extend(_parse_requirement_list(project.get('dependencies')))
            optional = project.get('optional-dependencies')
            if isinstance(optional, dict):
                for items in optional.values():
                    deps.extend(_parse_requirement_list(items))

        groups = self.data.get('dependency-groups')
        if isinstance(groups, dict):
            for items in groups.values():
                deps.extend(_parse_requirement_list(items))

        return deps

    def _parse_poetry(self):
        """
        Parse the dependency tables of [tool.poetry].

        Returns:
            list[tuple[str, str]]: (name, version) pairs of the exact pins,
                name as written in the file
        """
        tool = self.data.get('tool')
        if not isinstance(tool, dict):
            return []
        poetry = tool.get('poetry')
        if not isinstance(poetry, dict):
            return []

        deps = _parse_poetry_table(poetry.get('dependencies'))
        deps.extend(_parse_poetry_table(poetry.get('dev-dependencies')))
        groups = poetry.get('group')
        if isinstance(groups, dict):
            for group in groups.values():
                if isinstance(group, dict):
                    deps.extend(_parse_poetry_table(group.get('dependencies')))
        return deps
