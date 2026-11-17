"""
Tests for the requirements a compile is built from
(alasio/deploy_dev/compile/requirement.py).

The texts themselves are parsed by the parsers of the project, and their tests
are where those parsers live (tests/deploy/simple_pip/test_parse_req.py and
tests/deploy_dev/pack_server/test_parse_dep.py): this file covers what a
compile adds on top, the origin of a requirement, the PEP 503 name, and the
evaluation of the markers against the environment of the compile.
"""
import pytest
from packaging.markers import default_environment

from alasio.deploy.pip_download._target_python import TargetPython
from alasio.deploy_dev.compile import (
    CompileError, RequirementSpec, compile_environment, dependencies_of, requirements_of_pyproject
)
from alasio.deploy_dev.compile.requirement import PROJECT_ORIGIN
from alasio.testing.filesystem import fs  # noqa: F401

pytestmark = [
    pytest.mark.parallel,
]

# The interpreter the tests compile for: a fixed python 3.8, whatever runs the
# suite (the platform is the one of the machine, like uv)
TARGET = TargetPython(py_version_info=(3, 8, 20))

ENVIRONMENT = compile_environment(TARGET)

# The platform of the machine that runs the suite, a marker of the compile
# environment has to match it
RUNNING = default_environment()['sys_platform']


class TestCompileEnvironment:
    """The PEP 508 environment of a compile."""

    def test_the_platform_is_the_running_one(self):
        assert ENVIRONMENT['sys_platform'] == RUNNING
        assert ENVIRONMENT['platform_machine'] == default_environment()['platform_machine']

    def test_the_python_version_is_the_target(self):
        assert ENVIRONMENT['python_version'] == '3.8'
        assert ENVIRONMENT['python_full_version'] == '3.8.20'
        assert ENVIRONMENT['implementation_name'] == 'cpython'


class TestRequirementSpec:
    """The requirement model, the one value of the parser of the project."""

    def test_it_is_the_client_requirement(self):
        """The compile does not carry a shape of a requirement of its own."""
        from alasio.deploy.simple_pip.parse_req import RequirementSpec as ClientRequirement

        assert RequirementSpec is ClientRequirement

    def test_the_specifiers_are_cached_on_the_requirement(self):
        """The parse is cached on the instance, not shared between instances."""
        spec = RequirementSpec(name='demo', specifier='>=1.0,<2.0')
        first = spec.specifiers

        assert spec.specifiers is first
        assert str(first) == '<2.0,>=1.0'
        # another requirement of the same text is another constraint of another lock
        assert RequirementSpec(name='other', specifier='>=1.0,<2.0').specifiers is not first

    def test_the_specifiers_of_a_requirement_that_pins_nothing(self):
        spec = RequirementSpec(name='demo')

        assert len(spec.specifiers) == 0
        assert spec.specifiers is spec.specifiers

    def test_an_invalid_specifier_is_reported(self):
        with pytest.raises(ValueError, match='Invalid specifier'):
            _ = RequirementSpec(name='demo', specifier='>=1.0,lolwat').specifiers

    def test_an_invalid_specifier_keeps_raising(self):
        """Nothing is cached on a failure, the read is not silently empty."""
        spec = RequirementSpec(name='demo', specifier='>=1.0,lolwat')

        for _ in range(2):
            with pytest.raises(ValueError):
                _ = spec.specifiers


class TestDependenciesOf:
    """The dependencies a version declares, the Requires-Dist of a METADATA."""

    def test_the_names_and_the_ranges(self):
        deps = dependencies_of(['h11>=0.16', 'sniffio'], [''], ENVIRONMENT, 'httpcore2')

        assert deps == [
            RequirementSpec(name='h11', specifier='>=0.16', origin='httpcore2'),
            RequirementSpec(name='sniffio', origin='httpcore2'),
        ]

    def test_the_extras_of_a_requirement_are_kept(self):
        deps = dependencies_of(['httpx[http2]>=1'], [''], ENVIRONMENT, 'demo')

        assert deps[0].extras == ('http2',)

    def test_a_marker_of_another_platform(self):
        deps = dependencies_of(
            ['pywin32; sys_platform == "cygwin"'], [''], ENVIRONMENT, 'demo')

        assert deps == []

    def test_a_marker_of_the_running_platform(self):
        deps = dependencies_of(
            [f'colorama; sys_platform == "{RUNNING}"'], [''], ENVIRONMENT, 'demo')

        assert [dep.name for dep in deps] == ['colorama']

    def test_a_marker_of_an_extra(self):
        requires = ['h2; extra == "http2"']

        assert dependencies_of(requires, [''], ENVIRONMENT, 'demo') == []
        assert [
            dep.name for dep in dependencies_of(requires, ['', 'http2'], ENVIRONMENT, 'demo')
        ] == ['h2']

    def test_a_marker_is_evaluated_once(self):
        deps = dependencies_of(
            [f'colorama; sys_platform == "{RUNNING}"'], [''], ENVIRONMENT, 'demo')

        assert all(dep.marker == '' for dep in deps)

    def test_an_invalid_requirement_is_reported(self):
        with pytest.raises(CompileError, match='Invalid requirement .* of "demo"'):
            dependencies_of(['not a requirement !'], [''], ENVIRONMENT, 'demo')

    def test_a_dependency_that_is_not_a_version(self):
        with pytest.raises(CompileError, match='demo.* requires .*url'):
            dependencies_of(
                ['other @ https://example.com/other-1.0-py3-none-any.whl'],
                [''], ENVIRONMENT, 'demo')


class TestRequirementsOfPyproject:
    """The roots of a compile, the tables of a pyproject.toml."""

    def test_the_project_dependencies(self):
        data = {
            'project': {
                'name': 'demo',
                'dependencies': ['numpy<2', 'alasio[backend]==2.0.3'],
            },
        }

        roots = requirements_of_pyproject(data)

        assert [spec.name for spec in roots] == ['numpy', 'alasio']
        assert roots[0].specifier == '<2'
        assert roots[1].extras == ('backend',)
        assert all(spec.origin == PROJECT_ORIGIN for spec in roots)

    def test_an_optional_dependency_group(self):
        data = {
            'project': {
                'name': 'demo',
                'dependencies': ['numpy'],
                'optional-dependencies': {'dev': ['pytest>=8'], 'docs': ['sphinx']},
            },
        }

        roots = requirements_of_pyproject(data, extras=['dev'])

        assert [spec.name for spec in roots] == ['numpy', 'pytest']

    def test_every_optional_dependency_group(self):
        data = {
            'project': {
                'name': 'demo',
                'optional-dependencies': {'dev': ['pytest'], 'docs': ['sphinx']},
            },
        }

        roots = requirements_of_pyproject(data, all_extras=True)

        assert [spec.name for spec in roots] == ['pytest', 'sphinx']

    def test_an_unknown_extra(self):
        data = {'project': {'name': 'demo', 'optional-dependencies': {'dev': ['pytest']}}}

        with pytest.raises(CompileError, match='no optional dependency group "docs"'):
            requirements_of_pyproject(data, extras=['docs'])

    def test_a_dependency_group(self):
        data = {
            'project': {'name': 'demo'},
            'dependency-groups': {'lint': ['ruff>=0.16']},
        }

        roots = requirements_of_pyproject(data, groups=['lint'])

        assert [spec.name for spec in roots] == ['ruff']

    def test_an_entry_that_is_not_a_requirement(self):
        """The include-group entry of PEP 735 is not a requirement."""
        data = {
            'project': {'name': 'demo'},
            'dependency-groups': {'lint': ['ruff', {'include-group': 'test'}]},
        }

        roots = requirements_of_pyproject(data, groups=['lint'])

        assert [spec.name for spec in roots] == ['ruff']

    def test_a_poetry_project(self):
        data = {
            'tool': {
                'poetry': {
                    'name': 'demo',
                    'dependencies': {'python': '^3.8', 'numpy': '^1.24', 'httpx': '0.28.1'},
                    'group': {'dev': {'dependencies': {'pytest': '>=8'}}},
                },
            },
        }

        roots = requirements_of_pyproject(data)

        assert [(spec.name, spec.specifier) for spec in roots] == [
            ('numpy', '<2.0,>=1.24'),
            ('httpx', '==0.28.1'),
            ('pytest', '>=8'),
        ]

    def test_a_poetry_extra(self):
        data = {
            'tool': {
                'poetry': {
                    'name': 'demo',
                    'extras': {'dev': ['pytest>=8']},
                    'dependencies': {'numpy': '1.24.4'},
                },
            },
        }

        roots = requirements_of_pyproject(data, extras=['dev'])

        assert [spec.name for spec in roots] == ['numpy', 'pytest']

    def test_a_poetry_git_dependency(self):
        data = {
            'tool': {
                'poetry': {
                    'name': 'demo',
                    'dependencies': {'demo-other': {'git': 'https://example.com/x.git'}},
                },
            },
        }

        with pytest.raises(CompileError, match='names a git'):
            requirements_of_pyproject(data)

    def test_a_poetry_table_with_extras_and_markers(self):
        data = {
            'tool': {
                'poetry': {
                    'name': 'demo',
                    'dependencies': {
                        'httpx': {'version': '^2.0', 'extras': ['http2'],
                                  'markers': 'python_version < "3.9"'},
                    },
                },
            },
        }

        roots = requirements_of_pyproject(data)

        assert roots[0].extras == ('http2',)
        assert roots[0].marker == 'python_version < "3.9"'
        assert roots[0].specifier == '<3.0,>=2.0'

    def test_a_root_marker_of_another_interpreter(self):
        """A root requirement of a python the lock is not compiled for is dropped."""
        data = {
            'project': {
                'name': 'demo',
                'dependencies': ['tomli; python_version < "3.11"', 'numpy'],
            },
        }

        roots = requirements_of_pyproject(data, environment=ENVIRONMENT)

        assert [spec.name for spec in roots] == ['tomli', 'numpy']

        data['project']['dependencies'] = ['numpy', 'tomli; python_version >= "3.11"']
        roots = requirements_of_pyproject(data, environment=ENVIRONMENT)

        assert [spec.name for spec in roots] == ['numpy']
