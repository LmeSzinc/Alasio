"""
Tests for the dependency file parsers: RequirementsParser and PyprojectParser.

The parsers take the content of one dependency file each (a requirements
file, a pyproject.toml) and provide the requirements pinned to an exact
version as dict_deps: a dict of the PEP 503 normalized name to the version,
see parse_dep. A declaration that does not pin a version (a range, a
wildcard, a bare name, a URL, a local path, a pip option) is skipped, and two
different versions of one name in one file raise a ValueError.
"""
import pytest

from alasio.deploy.simple_pip import parse_req
from alasio.deploy.simple_pip.parse_req import Requirements
from alasio.deploy.simple_pip.pip_list import normalize_name
from alasio.deploy_dev.pack_server import parse_dep
from alasio.deploy_dev.pack_server.parse_dep import PyprojectParser, RequirementsParser


def parse_requirements(content):
    """
    dict_deps of a requirements content.

    Returns:
        dict[str, str]: PEP 503 normalized name -> exact version
    """
    return RequirementsParser(content).dict_deps


def parse_pyproject(content):
    """
    dict_deps of a pyproject content.

    Returns:
        dict[str, str]: PEP 503 normalized name -> exact version
    """
    return PyprojectParser(content).dict_deps


class TestRequirementsParser:
    """Parse a requirements file, the pip requirement file format."""

    @pytest.mark.parametrize('line, expected', [
        # a plain pin
        ('httpx==0.28.1', {'httpx': '0.28.1'}),
        ('httpx == 0.28.1', {'httpx': '0.28.1'}),
        # extras are not part of the name
        ('httpx[cli,http2]==0.28.1', {'httpx': '0.28.1'}),
        # the name is normalized, PEP 503
        ('PyYAML==6.0.1', {'pyyaml': '6.0.1'}),
        ('ruamel.yaml==0.18.6', {'ruamel-yaml': '0.18.6'}),
        ('opencv-python-headless==4.10.0.84', {'opencv-python-headless': '4.10.0.84'}),
        # an environment marker is not evaluated, the pin is read as it is
        ('httpx==0.28.1; python_version < "3.9"', {'httpx': '0.28.1'}),
        ('httpx==0.28.1 ;python_version<"3.9"', {'httpx': '0.28.1'}),
        # the version forms of PEP 440 and the legacy ones
        ('foo==1.0rc1', {'foo': '1.0rc1'}),
        ('foo==1.0.post1', {'foo': '1.0.post1'}),
        ('foo==1!2.0', {'foo': '1!2.0'}),
        ('foo==1.0+local.1', {'foo': '1.0+local.1'}),
        ('foo==1.0-1', {'foo': '1.0-1'}),
    ])
    def test_pin(self, line, expected):
        """A requirement pinned to an exact version is parsed."""
        assert parse_requirements(line) == expected

    @pytest.mark.parametrize('line', [
        # a range, no version is pinned
        'httpx',
        'httpx>=0.28.1',
        'httpx<=0.28.1',
        'httpx>0.28.1',
        'httpx<0.29',
        'httpx~=0.28.1',
        'httpx!=0.28.1',
        'httpx>=0.27,<0.29',
        # a wildcard is not one version
        'httpx==0.28.*',
        # arbitrary equality is not a version pin
        'httpx===0.28.1',
        # a URL and a local path, no version is pinned
        'httpx @ https://example.com/httpx-0.28.1-py3-none-any.whl',
        'https://example.com/httpx-0.28.1-py3-none-any.whl',
        './wheels/httpx-0.28.1-py3-none-any.whl',
        # pip options
        '--index-url https://mirrors.aliyun.com/pypi/simple',
        '--extra-index-url=https://example.com/simple',
        '--find-links ./wheels',
        '--trusted-host example.com',
        '-r base.txt',
        '-rbase.txt',
        '-c constraints.txt',
        '-e .',
        # a text without a digit is not a version
        'httpx==dev',
    ])
    def test_not_pinned(self, line):
        """A declaration that does not pin an exact version is skipped."""
        assert parse_requirements(line) == {}

    def test_file(self):
        """The lines of a file are parsed one by one, into one result."""
        content = """\
# requirements.txt
httpx==0.28.1
pyyaml==6.0.1  # a comment, the pin is kept
# httpx==0.27.0

--index-url https://mirrors.aliyun.com/pypi/simple
msgspec>=0.18.6,<=0.22.0
"""
        assert parse_requirements(content) == {'httpx': '0.28.1', 'pyyaml': '6.0.1'}

    def test_comment_after_whitespace(self):
        """A '#' that does not follow a whitespace is not a comment."""
        assert parse_requirements('httpx==0.28.1#comment') == {}

    def test_line_continuation(self):
        """A line that ends with a '\\' continues on the next line."""
        content = """\
certifi==2024.2.2 \\
    --hash=sha256:aaaa \\
    --hash=sha256:bbbb
charset-normalizer==3.3.2 \\
    --hash=sha256:cccc

    # via requests
"""
        assert parse_requirements(content) == {
            'certifi': '2024.2.2',
            'charset-normalizer': '3.3.2',
        }

    def test_options(self):
        """The options after a requirement are not part of it."""
        assert parse_requirements('httpx==0.28.1 --hash=sha256:dddd') == {'httpx': '0.28.1'}
        assert parse_requirements(
            'httpx==0.28.1 ; python_version < "3.9" --hash=sha256:dddd') == {'httpx': '0.28.1'}

    def test_crlf(self):
        """A file with CRLF line endings is parsed like a LF one."""
        assert parse_requirements('httpx==0.28.1\r\npyyaml==6.0.1\r\n') == {
            'httpx': '0.28.1',
            'pyyaml': '6.0.1',
        }

    def test_bom(self):
        """A UTF-8 BOM is not part of the first requirement."""
        assert parse_requirements('\ufeffhttpx==0.28.1') == {'httpx': '0.28.1'}

    def test_empty(self):
        """A file without a requirement has no pin."""
        assert parse_requirements('') == {}
        assert parse_requirements('\n  \n# comment\n') == {}

    def test_cached(self):
        """dict_deps is parsed on the first read and cached on the parser."""
        parser = RequirementsParser('httpx==0.28.1')
        assert parser.dict_deps == {'httpx': '0.28.1'}
        assert parser.dict_deps is parser.dict_deps

    def test_duplicated(self):
        """The same pin twice is one entry."""
        assert parse_requirements('httpx==0.28.1\nhttpx==0.28.1') == {'httpx': '0.28.1'}

    def test_conflicting(self):
        """Two different versions of one name raise a ValueError on the read."""
        parser = RequirementsParser('httpx==0.28.1\nhttpx==0.28.2')
        with pytest.raises(ValueError, match='Conflicting versions of "httpx"'):
            _ = parser.dict_deps

    def test_conflicting_normalized(self):
        """The conflict is checked on the normalized name."""
        content = 'PyYAML==6.0.1\npyyaml==6.0.2'
        with pytest.raises(ValueError, match='Conflicting versions of "pyyaml"'):
            parse_requirements(content)


class TestPyprojectParser:
    """Parse a pyproject.toml, the dependency declarations of the document."""

    def test_project_dependencies(self):
        """[project].dependencies is read (PEP 621)."""
        content = """\
[project]
name = "alasio"
dependencies = [
    "httpx==0.28.1",
    "msgspec>=0.18.6,<=0.22.0",
    "typing-extensions",
    "uvicorn~=0.30.0",
    "PyYAML==6.0.1 ; python_version < '3.12'",
]
"""
        assert parse_pyproject(content) == {'httpx': '0.28.1', 'pyyaml': '6.0.1'}

    def test_optional_dependencies(self):
        """[project.optional-dependencies] groups are read (PEP 621)."""
        content = """\
[project.optional-dependencies]
backend = ["hypercorn==0.17.3", "trio>=0.27.0"]
test = [
    "pytest==8.3.4",
]
"""
        assert parse_pyproject(content) == {'hypercorn': '0.17.3', 'pytest': '8.3.4'}

    def test_dependency_groups(self):
        """[dependency-groups] is read (PEP 735), an include entry is skipped."""
        content = """\
[dependency-groups]
test = [
    "pytest==8.3.4",
    {include-group = "lint"},
]
lint = ["ruff==0.16.0"]
"""
        assert parse_pyproject(content) == {'pytest': '8.3.4', 'ruff': '0.16.0'}

    def test_poetry_dependencies(self):
        """[tool.poetry.dependencies] is read, with the poetry constraint rules."""
        content = """\
[tool.poetry.dependencies]
python = "==3.8.20"
httpx = "==0.28.1"
trio = "0.27.0"
anyio = "^4.0"
idna = "~3.4"
urllib3 = { version = "==2.2.2", markers = "python_version < '3.12'" }
colorama = { version = "0.4.6", optional = true, extras = ["cli"] }
requests = { git = "https://github.com/psf/requests.git" }
local = { path = "../local" }
web = { url = "https://example.com/web-1.0-py3-none-any.whl" }
"""
        assert parse_pyproject(content) == {
            'httpx': '0.28.1',
            'trio': '0.27.0',
            'urllib3': '2.2.2',
            'colorama': '0.4.6',
        }

    def test_poetry_groups(self):
        """[tool.poetry.group.*.dependencies] and the legacy dev-dependencies are read."""
        content = """\
[tool.poetry.group.dev.dependencies]
pytest = "==8.3.4"

[tool.poetry.group.lint.dependencies]
ruff = "0.16.0"

[tool.poetry.dev-dependencies]
black = "==24.1.0"
"""
        assert parse_pyproject(content) == {
            'pytest': '8.3.4',
            'ruff': '0.16.0',
            'black': '24.1.0',
        }

    def test_poetry_single_equals(self):
        """A single '=' pins a version in poetry."""
        content = '[tool.poetry.dependencies]\nhttpx = "=0.28.1"\n'
        assert parse_pyproject(content) == {'httpx': '0.28.1'}

    def test_poetry_name_normalized(self):
        """The name of a poetry entry is normalized like a requirement name."""
        content = '[tool.poetry.dependencies]\nPyYAML = "==6.0.1"\n'
        assert parse_pyproject(content) == {'pyyaml': '6.0.1'}

    def test_not_read(self):
        """The tables that do not declare the dependencies of the project are not read."""
        content = """\
[build-system]
requires = ["setuptools>=61.0", "msgspec"]

[tool.poetry.extras]
speedup = ["numpy"]

[tool.poetry.dependencies]
httpx = "^0.28.0"

[tool.other]
dependencies = ["httpx==0.28.1"]
"""
        assert parse_pyproject(content) == {}

    def test_wrong_types(self):
        """A table or an entry of an unexpected type is skipped."""
        content = """\
[project]
name = "alasio"
dependencies = "httpx==0.28.1"

[project.optional-dependencies]
backend = "hypercorn==0.17.3"

[tool.poetry]
dependencies = 1

[tool.poetry.group.dev.dependencies]
pytest = "==8.3.4"
"""
        assert parse_pyproject(content) == {'pytest': '8.3.4'}

    def test_data(self):
        """The parsed TOML document is exposed and cached."""
        parser = PyprojectParser('[project]\nname = "alasio"\n')
        assert parser.data == {'project': {'name': 'alasio'}}
        assert parser.data is parser.data

    def test_cached(self):
        """dict_deps is parsed on the first read and cached on the parser."""
        parser = PyprojectParser('[project]\ndependencies = ["httpx==0.28.1"]\n')
        assert parser.dict_deps == {'httpx': '0.28.1'}
        assert parser.dict_deps is parser.dict_deps

    def test_conflicting(self):
        """Two different versions of one name raise a ValueError on the read."""
        content = """\
[project]
dependencies = ["httpx==0.28.1"]

[tool.poetry.dependencies]
httpx = "==0.28.2"
"""
        parser = PyprojectParser(content)
        with pytest.raises(ValueError, match='Conflicting versions of "httpx"'):
            _ = parser.dict_deps

    def test_invalid_toml(self):
        """A content that is not a TOML document is refused on the read."""
        parser = PyprojectParser('[project')
        with pytest.raises(ValueError):
            _ = parser.dict_deps

    def test_empty(self):
        """A document without a dependency has no pin."""
        assert parse_pyproject('') == {}
        assert parse_pyproject('[project]\nname = "alasio"\n') == {}


class TestClientRules:
    """The rules of the client modules, reused by the release side."""

    def test_requirements_parser_is_the_client_parser(self):
        """A requirements file is parsed by the client class, the release name is it."""
        assert issubclass(RequirementsParser, Requirements)
        assert RequirementsParser('httpx==0.28.1').dict_deps == {'httpx': '0.28.1'}

    def test_the_rules_are_the_client_ones(self):
        """The pin rules and the dist-key of the release side are the client functions."""
        assert parse_dep.parse_requirement is parse_req.parse_requirement
        assert parse_dep.build_pins is parse_req.build_pins
        assert parse_dep.normalize_name is normalize_name

    def test_no_copy_of_the_requirements_file_rules(self):
        """parse_dep holds no copy of the requirements file format rules."""
        assert 'REGEX_PIN' not in vars(parse_dep)
        assert 'REGEX_NAME_SEPARATOR' not in vars(parse_dep)
        assert '_parse_requirement' not in vars(parse_dep)
        assert '_build_deps' not in vars(parse_dep)
        assert '_iter_requirement' not in vars(parse_dep)
