"""
The dependency declarations a compile starts from, and the ones a version declares.

A compile reads the requirements of a project (a pyproject.toml) and walks
them: every resolved version declares its own dependencies in the
``Requires-Dist`` of its METADATA, which are the same declarations in PEP 508
form (see :func:`dependencies_of`). Both sides are the model of the project,
``alasio.deploy.simple_pip.parse_req.RequirementSpec``, so the resolver never
has to know where a requirement came from, and the client that installs a lock
and the compiler that resolves one carry the same value:

- ``name`` is the PEP 503 normalized distribution name, the key of the pins
  (the parser reads the name as written, and a compile normalizes it);
- ``extras`` are the extras the requirement asks for, expanded when the
  version is resolved (``httpx[http2]`` pulls the dependencies of ``http2``);
- ``specifier`` is the PEP 440 constraint text, '' when the requirement pins
  nothing (``numpy``, ``av>=12.3``, ``httpx2==2.16.0``), and ``specifiers`` is
  its parsed form, read once per requirement;
- ``marker`` is the PEP 508 marker text, '' when the requirement applies
  everywhere. A marker is evaluated when the requirement is added, against the
  interpreter the lock is compiled for, so a marker never reaches the resolver;
- ``origin`` is who declared the requirement: the project itself
  (``pyproject.toml``) or the distribution that requires the name, which is
  what the ``# via`` annotation of a lock is made of;
- ``source`` is the index the requirement names, '' for any index. A mod that
  reads a private index for one distribution declares it in
  ``[tool.uv.sources]``.

The texts are parsed by the parsers of the project, never here: the
requirement text rules are the ones of the client
(alasio.deploy.simple_pip.parse_req, see RequirementSpec.from_line), and the
tables of a pyproject.toml, poetry constraints included, are read by
alasio.deploy_dev.pack_server.parse_dep (see pyproject_specs). This module
adds what a compile needs on top of them: the origin of a requirement (and the
PEP 503 name of a dependency of a distribution), and the evaluation of a marker
against the environment of the compile.

A URL, git or local path dependency has no version to resolve and no index to
read a METADATA from: it is refused rather than guessed, see :class:`CompileError`.

Usage:
    from alasio.deploy_dev.compile.requirement import RequirementSpec, dependencies_of

    roots = requirements_of_pyproject(data, extras=['backend'], environment=env)
    children = dependencies_of(meta.requires_dist, ['', 'http2'], environment, 'httpx2')
"""


from msgspec.structs import replace
from packaging.markers import InvalidMarker, Marker, default_environment
from packaging.utils import canonicalize_name

from alasio.deploy.simple_pip.parse_req import RequirementSpec
from alasio.deploy_dev.compile.errors import CompileError
from alasio.deploy_dev.pack_server.parse_dep import pyproject_specs

__all__ = [
    'RequirementSpec',
    'compile_environment',
    'dependencies_of',
    'requirements_of_pyproject',
]

# The origin of a requirement the project declares itself, the annotation a
# lock writes as '<project> (pyproject.toml)'
PROJECT_ORIGIN = 'pyproject.toml'


def compile_environment(target):
    """
    The PEP 508 environment a lock is compiled for.

    The platforms are the ones of the running machine, exactly like
    ``uv pip compile`` without ``--universal``: the lock is compiled on the
    machine that will install it, so a dependency that exists for one platform
    only is in the lock of that platform only. The python version is the one
    of the target, which may differ from the interpreter running the tool.

    Args:
        target (TargetPython): The interpreter the lock is compiled for

    Returns:
        dict[str, str]: The environment, the keys of PEP 508
    """
    environment = default_environment()
    major, minor, micro = target.py_version_info[:3]
    environment['implementation_name'] = 'cpython'
    environment['implementation_version'] = f'{major}.{minor}.{micro}'
    environment['platform_python_implementation'] = 'CPython'
    environment['python_full_version'] = f'{major}.{minor}.{micro}'
    environment['python_version'] = f'{major}.{minor}'
    return environment


def _evaluate(marker, environment, extras):
    """
    Whether a marker matches one of the extras being expanded.

    Args:
        marker (str): PEP 508 marker text, '' for none
        environment (dict[str, str]): The environment of the compile
        extras (Iterable[str]): Extras being expanded, the base dependencies
            are the empty extra

    Returns:
        bool: True when the requirement applies

    Raises:
        CompileError: If the marker does not parse
    """
    if not marker:
        return True
    try:
        parsed = Marker(marker)
    except InvalidMarker as e:
        raise CompileError(f'Invalid environment marker "{marker}": {e}') from None
    for extra in extras:
        if parsed.evaluate({**environment, 'extra': extra}):
            return True
    return False


def dependencies_of(requires, extras, environment, origin):
    """
    The dependencies a distribution declares, for the extras being expanded.

    The markers are evaluated here, once, against the environment the lock is
    compiled for: what a resolver sees is a list of requirements that all
    apply, which is why a marker never reaches the resolver. The name of each
    requirement is normalized, because the METADATA writes it as it was
    published (``PyYAML``, ``zope.interface``).

    Args:
        requires (Iterable[str]): The ``Requires-Dist`` values of a METADATA
        extras (Iterable[str]): Extras being expanded, the base dependencies
            are the empty extra
        environment (dict[str, str]): The environment of the compile
        origin (str): The distribution that declares them, the parent of the
            requirements

    Returns:
        list[RequirementSpec]: The dependencies that apply, in the order the
            metadata declares them

    Raises:
        CompileError: If a requirement or a marker does not parse, or a
            requirement names a url
    """
    extras = list(dict.fromkeys(extras)) or ['']
    out = []
    for text in requires:
        parsed = RequirementSpec.from_line(text)
        if parsed is None:
            raise CompileError(
                f'Invalid requirement "{text}" of "{origin}"')
        if parsed.url:
            raise CompileError(
                f'"{origin}" requires "{text}", a url, a git repository or a path, which a '
                f'compile cannot resolve')
        if not _evaluate(parsed.marker, environment, extras):
            continue
        # the marker is evaluated here, the resolver never sees one
        out.append(replace(
            parsed,
            name=canonicalize_name(parsed.name),
            marker='',
            origin=origin,
        ))
    return out


def requirements_of_pyproject(data, extras=(), all_extras=False, groups=(), environment=None):
    """
    The requirements a pyproject.toml declares, the roots of a compile.

    The tables are read by the parser of the project, see
    alasio.deploy_dev.pack_server.parse_dep.pyproject_specs: [project] of
    PEP 621, [dependency-groups] of PEP 735, and the poetry tables when the
    document has no [project] table. The PEP 508 texts, the poetry constraints
    and the ranges are read there, once, for every consumer of a
    pyproject.toml.

    Args:
        data (dict): Parsed pyproject.toml
        extras (Iterable[str]): Optional dependency groups to include.
            Defaults to (), the base dependencies only
        all_extras (bool): True to include every optional dependency group.
            Defaults to False
        groups (Iterable[str]): Dependency groups to include. Defaults to (),
            none of them
        environment (dict[str, str], optional): The environment the markers are
            evaluated against, see compile_environment. Defaults to None, every
            marker is kept for the caller to evaluate

    Returns:
        list[RequirementSpec]: The requirements, in the declared order

    Raises:
        CompileError: If a requirement, a marker or a poetry constraint cannot
            be read, or if a named extra or group does not exist
    """
    try:
        parsed = pyproject_specs(
            data, extras=extras, all_extras=all_extras, groups=groups)
    except ValueError as e:
        raise CompileError(str(e)) from None
    out = []
    for spec in parsed:
        if environment is not None and not _evaluate(spec.marker, environment, ['']):
            continue
        # the name is the key of the pins, the parser keeps it as written
        out.append(replace(spec, name=canonicalize_name(spec.name), origin=PROJECT_ORIGIN))
    return out
