"""
Tests for the requirements parser of the client: Requirements.

The parser takes the content of a requirements file and provides the
requirements pinned to an exact version as dict_deps: a dict of the PEP 503
normalized name to the version, see parse_req. A declaration that does not
pin a version (a range, a wildcard, a bare name, a URL, a local path, a pip
option) is skipped, and two different versions of one name raise a ValueError.
"""
import pytest

from alasio.deploy.simple_pip import parse_req
from alasio.deploy.simple_pip.parse_req import Requirements
from alasio.deploy.simple_pip.pip_list import PipList
from alasio.testing.filesystem import fs  # noqa: F401

# Root of the tests, a site-packages look-alike
SITE = '/env/Lib/site-packages'


def parse_requirements(content):
    """
    dict_deps of a requirements content.

    Returns:
        dict[str, str]: PEP 503 normalized name -> exact version
    """
    return Requirements(content).dict_deps


class TestParsePin:
    @pytest.mark.parametrize('line, expected', [
        # a plain pin
        ('httpx==0.28.1', {'httpx': '0.28.1'}),
        ('httpx == 0.28.1', {'httpx': '0.28.1'}),
        # the extras are not part of the name
        ('httpx[cli,http2]==0.28.1', {'httpx': '0.28.1'}),
        # the name is normalized, PEP 503
        ('PyYAML==6.0.1', {'pyyaml': '6.0.1'}),
        ('ruamel.yaml==0.18.6', {'ruamel-yaml': '0.18.6'}),
        ('opencv-python-headless==4.10.0.84', {'opencv-python-headless': '4.10.0.84'}),
        # an environment marker is not evaluated, the pin is read as it is
        ('httpx==0.28.1; python_version < "3.9"', {'httpx': '0.28.1'}),
        ('httpx==0.28.1 ;python_version<"3.9"', {'httpx': '0.28.1'}),
        ('pywin32==306 ; sys_platform == "win32"', {'pywin32': '306'}),
        # the version forms of PEP 440 and the legacy ones
        ('foo==1.0rc1', {'foo': '1.0rc1'}),
        ('foo==1.0.post1', {'foo': '1.0.post1'}),
        ('foo==1.0.dev0', {'foo': '1.0.dev0'}),
        ('foo==1!2.0', {'foo': '1!2.0'}),
        ('foo==1.0+local.1', {'foo': '1.0+local.1'}),
        ('foo==1.0-1', {'foo': '1.0-1'}),
    ])
    def test_pin(self, line, expected):
        """A requirement pinned to an exact version is parsed."""
        assert parse_requirements(line) == expected


class TestNotPinned:
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

    def test_comment_after_whitespace(self):
        """A '#' that does not follow a whitespace is not a comment."""
        assert parse_requirements('httpx==0.28.1#comment') == {}


class TestFileForm:
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

    def test_line_continuation(self):
        """A line that ends with a '\\' continues on the next line, e.g. the --hash lines."""
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

    def test_include(self):
        """A '-r' include is not followed, the caller reads the included file."""
        assert parse_requirements('-r deploy/requirements.txt\nhttpx==0.28.1') == {'httpx': '0.28.1'}

    def test_crlf(self):
        """A file with CRLF line endings is parsed like a LF one."""
        assert parse_requirements('httpx==0.28.1\r\npyyaml==6.0.1\r\n') == {
            'httpx': '0.28.1',
            'pyyaml': '6.0.1',
        }

    def test_cr(self):
        """A file with the CR line endings of an old Mac is parsed."""
        assert parse_requirements('httpx==0.28.1\rpyyaml==6.0.1\r') == {
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


class TestDictDeps:
    def test_normalized_keys(self):
        """The keys of the pins are the dist-key of a pack, PEP 503."""
        content = """\
PyYAML==6.0.1
ruamel.yaml==0.18.6
typing-extensions==4.12.2
"""
        assert parse_requirements(content) == {
            'pyyaml': '6.0.1',
            'ruamel-yaml': '0.18.6',
            'typing-extensions': '4.12.2',
        }

    def test_cached(self):
        """dict_deps is parsed on the first read and cached on the parser."""
        parser = Requirements('httpx==0.28.1')
        assert parser.dict_deps == {'httpx': '0.28.1'}
        assert parser.dict_deps is parser.dict_deps

    def test_duplicated(self):
        """The same pin twice is one entry."""
        assert parse_requirements('httpx==0.28.1\nhttpx==0.28.1') == {'httpx': '0.28.1'}

    def test_duplicated_spelling(self):
        """Two spellings of one name with the same version are one entry."""
        assert parse_requirements('ruamel.yaml==0.18.6\nruamel-yaml==0.18.6') == {
            'ruamel-yaml': '0.18.6',
        }

    def test_conflicting(self):
        """Two different versions of one name raise a ValueError on the read."""
        parser = Requirements('httpx==0.28.1\nhttpx==0.28.2')
        with pytest.raises(ValueError, match='Conflicting versions of "httpx"'):
            _ = parser.dict_deps

    def test_conflicting_normalized(self):
        """The conflict is checked on the normalized name."""
        content = 'PyYAML==6.0.1\npyyaml==6.0.2'
        with pytest.raises(ValueError, match='Conflicting versions of "pyyaml"'):
            _ = parse_requirements(content)


class TestSharedRules:
    """The pin rules the release side reuses (alasio.deploy_dev.pack_server.parse_dep)."""

    def test_parse_requirement_keeps_the_name_as_written(self):
        """The pin holds the name as written, the caller normalizes it."""
        assert parse_req.parse_requirement('PyYAML[cli]==6.0.1; python_version < "3.9"') == (
            'PyYAML',
            '6.0.1',
        )

    def test_parse_requirement_of_a_declaration_without_pin(self):
        """A declaration that pins no exact version has no pin."""
        assert parse_req.parse_requirement('PyYAML>=6.0.1') is None

    def test_build_pins(self):
        """The pins are keyed by the normalized name."""
        assert parse_req.build_pins([('PyYAML', '6.0.1'), ('ruamel.yaml', '0.18.6')]) == {
            'pyyaml': '6.0.1',
            'ruamel-yaml': '0.18.6',
        }


class TestWithPipList:
    def test_the_keys_find_the_installed_distributions(self, fs):
        """The keys of the pins are the dist-key of the installed distributions."""
        fs.create_file(
            f'{SITE}/ruamel_yaml-0.18.6.dist-info/METADATA',
            contents='Metadata-Version: 2.1\nName: ruamel.yaml\nVersion: 0.18.6\n\n',
        )
        pins = parse_requirements('ruamel.yaml==0.18.6')
        installed = {dist.dist_key: dist for dist in PipList(SITE).list()}
        assert pins == {'ruamel-yaml': '0.18.6'}
        assert list(installed) == list(pins)
        assert installed['ruamel-yaml'].version == pins['ruamel-yaml']


class TestDependencies:
    def test_no_pip(self):
        """The file is parsed with string work, no pip of any kind is imported nor run."""
        assert 'pip' not in vars(parse_req)
        assert 'importlib' not in vars(parse_req)
