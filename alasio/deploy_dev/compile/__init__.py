"""
Compile the dependencies of a project into the lock a client installs.

The compiler is the developer side of the pack channel: it reads the
``pyproject.toml`` of a project, resolves the versions of its dependencies
against the indexes the project declares, and writes the pins into its
requirements file. That file is the lock ``pack_deps`` decides the pack channel
over, and the file a client installs the dependency step from, so one run of
this package replaces a run of ``uv pip compile`` and needs no external binary.

The metadata of a version is read by alasio.deploy_dev.metadata, shared with
the pack_deps tool: a compile followed by a ``PackDepsManager`` run in the same
process reads every index page and every METADATA once.

The rules are the ones of this project rather than the ones of uv: a wheel of
the target platform (never an sdist, never a build), no URL or git dependency,
one platform per lock, and the versions a previous lock pinned are preferred
while they still satisfy the declarations.

Usage:
    from alasio.deploy_dev.compile import RequirementCompiler

    compiler = RequirementCompiler(header='# generated, do not edit')
    result = compiler.compile()
    result.pins      # {'httpx2': '2.16.0', ...}
"""

from alasio.deploy_dev.compile.candidates import Candidate, Index, versions_of
from alasio.deploy_dev.compile.compiler import DEFAULT_INDEX, REQUIREMENTS_FILE, CompileResult, RequirementCompiler
from alasio.deploy_dev.compile.errors import CompileError
from alasio.deploy_dev.compile.requirement import (
    RequirementSpec, compile_environment, dependencies_of, requirements_of_pyproject
)
from alasio.deploy_dev.compile.resolver import PROJECT_ORIGIN, Resolution, Resolver
from alasio.deploy_dev.compile.writer import requirements_text

__all__ = [
    'DEFAULT_INDEX',
    'PROJECT_ORIGIN',
    'REQUIREMENTS_FILE',
    'Candidate',
    'CompileError',
    'CompileResult',
    'Index',
    'RequirementCompiler',
    'RequirementSpec',
    'Resolution',
    'Resolver',
    'compile_environment',
    'dependencies_of',
    'requirements_of_pyproject',
    'requirements_text',
    'versions_of',
]
