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
from alasio.deploy.simple_pip.parse_req import DepInfo, Requirements
from alasio.deploy.simple_pip.pip_list import PipList
from alasio.logger import logger
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


def parse_dep_infos(content):
    """
    dict_pins of a requirements content.

    Returns:
        dict[str, DepInfo]: PEP 503 normalized name -> pin and source
    """
    return Requirements(content).dict_pins


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
        """Two different versions keep the first, the later one is a warning."""
        parser = Requirements('httpx==0.28.1\nhttpx==0.28.2')
        with logger.mock_capture_writer() as capture:
            assert parser.dict_deps == {'httpx': '0.28.1'}
        assert capture.concat() == """\
WARNING | Conflicting versions of "httpx" in the requirements: "0.28.1" and "0.28.2", keeping "0.28.1"
"""

    def test_conflicting_normalized(self):
        """The conflict is checked on the normalized name."""
        content = 'PyYAML==6.0.1\npyyaml==6.0.2'
        with logger.mock_capture_writer() as capture:
            assert parse_requirements(content) == {'pyyaml': '6.0.1'}
        assert capture.concat() == """\
WARNING | Conflicting versions of "pyyaml" in the requirements: "6.0.1" and "6.0.2", keeping "6.0.1"
"""


class TestSharedRules:
    """The requirement text rules the release side reuses (alasio.deploy_dev.pack_server.parse_dep)."""

    def test_parse_requirement_keeps_the_name_as_written(self):
        """The pin holds the name as written, the caller normalizes it."""
        assert parse_req.parse_requirement('PyYAML[cli]==6.0.1; python_version < "3.9"') == (
            'PyYAML',
            '6.0.1',
        )

    def test_parse_requirement_of_a_declaration_without_pin(self):
        """A declaration that pins no exact version has no pin."""
        assert parse_req.parse_requirement('PyYAML>=6.0.1') is None


class TestWithPipList:
    def test_the_keys_find_the_installed_distributions(self, fs):
        """The keys of the pins are the dist-key of the installed distributions."""
        fs.create_file(
            f'{SITE}/ruamel_yaml-0.18.6.dist-info/METADATA',
            contents='Metadata-Version: 2.1\nName: ruamel.yaml\nVersion: 0.18.6\n\n',
        )
        pins = parse_requirements('ruamel.yaml==0.18.6')
        installed = PipList(SITE).list()
        assert pins == {'ruamel-yaml': '0.18.6'}
        assert list(installed) == list(pins)
        assert installed['ruamel-yaml'].version == pins['ruamel-yaml']


class TestDependencies:
    def test_no_pip(self):
        """The file is parsed with string work, no pip of any kind is imported nor run."""
        assert 'pip' not in vars(parse_req)
        assert 'importlib' not in vars(parse_req)


class TestDepInfo:
    def test_default_source(self):
        """A DepInfo without a source is a pin of the official PyPI."""
        assert DepInfo('httpx', '0.28.1') == DepInfo('httpx', '0.28.1', '')

    def test_plain_requirements(self):
        """A requirements file without annotations has no source."""
        assert parse_dep_infos('httpx==0.28.1\npyyaml==6.0.1') == {
            'httpx': DepInfo('httpx', '0.28.1', ''),
            'pyyaml': DepInfo('pyyaml', '6.0.1', ''),
        }

    def test_name_normalized(self):
        """The name of a DepInfo is the normalized name, the key of the dict."""
        assert parse_dep_infos('PyYAML==6.0.1') == {
            'pyyaml': DepInfo('pyyaml', '6.0.1', ''),
        }

    def test_views(self):
        """dict_deps and dict_sources are the views of the same pins."""
        content = """\
httpx==0.28.1
    # from https://py38deps.github.io/py38deps/simple/
pyyaml==6.0.1
    # from https://example.com/simple
"""
        parser = Requirements(content)
        assert parser.dict_pins == {
            'httpx': DepInfo(
                'httpx', '0.28.1', 'https://py38deps.github.io/py38deps/simple'),
            'pyyaml': DepInfo('pyyaml', '6.0.1', 'https://example.com/simple'),
        }
        assert parser.dict_deps == {'httpx': '0.28.1', 'pyyaml': '6.0.1'}
        assert parser.dict_sources == {
            'httpx': 'https://py38deps.github.io/py38deps/simple',
            'pyyaml': 'https://example.com/simple',
        }

    def test_cached(self):
        """The pins and their views are parsed on the first read and cached."""
        parser = Requirements('httpx==0.28.1')
        assert parser.dict_deps is parser.dict_deps
        assert parser.dict_pins is parser.dict_pins
        assert parser.dict_sources is parser.dict_sources

    def test_generator_yields_dep_info(self):
        """The pin iterator yields DepInfo objects in the file order."""
        parser = Requirements('httpx==0.28.1\npyyaml==6.0.1\n')
        assert list(parser._iter_dep_info()) == [
            DepInfo('httpx', '0.28.1', ''),
            DepInfo('pyyaml', '6.0.1', ''),
        ]


class TestExtraIndex:
    @pytest.mark.parametrize('content, expected', [
        ('--extra-index-url https://example.com/simple\nhttpx==0.28.1\n',
         'https://example.com/simple'),
        ('--extra-index-url=https://example.com/simple\nhttpx==0.28.1\n',
         'https://example.com/simple'),
        ('--extra-index-url https://example.com/simple\n# a comment\nhttpx==0.28.1\n',
         'https://example.com/simple'),
        ('--extra-index-url https://example.com/simple  # a comment\nhttpx==0.28.1\n',
         'https://example.com/simple'),
        ('\n  \n--extra-index-url https://example.com/simple\n',
         'https://example.com/simple'),
        ('\ufeff--extra-index-url https://example.com/simple\n',
         'https://example.com/simple'),
        ('--extra-index-url https://example.com/simple/\n',
         'https://example.com/simple/'),
    ])
    def test_url(self, content, expected):
        """The URL of the first --extra-index-url is returned."""
        assert Requirements(content).extra_index == expected

    @pytest.mark.parametrize('content', [
        '',
        '\n  \n',
        'httpx==0.28.1\n',
        '--index-url https://example.com/simple\nhttpx==0.28.1\n',
        'httpx==0.28.1\n# --extra-index-url https://example.com/simple\n',
        '--extra-index-url\n',
    ])
    def test_none(self, content):
        """An index-url, a comment or a plain file declares no extra index."""
        assert Requirements(content).extra_index == ''

    def test_cached(self):
        """The property is read on the first access and cached."""
        parser = Requirements('--extra-index-url https://example.com/simple\n')
        assert parser.extra_index is parser.extra_index


class TestAnnotationStyles:
    @pytest.mark.parametrize('content, expected', [
        # uv --annotation-style=split, the real Alas2026 output
        ("""\
anyio==4.15.1
    # via
    #   httpx2
    #   starlette
    # from https://py38deps.github.io/py38deps/simple/
""", 'https://py38deps.github.io/py38deps/simple'),
        # uv --annotation-style=line
        ("""\
anyio==4.15.1
    # via httpx2, starlette
    # from https://py38deps.github.io/py38deps/simple
""", 'https://py38deps.github.io/py38deps/simple'),
        # pip-compile split style, a source annotation
        ("""\
anyio==4.15.1
    # via
    #   httpx2
    #   starlette
    # from https://example.com/simple
""", 'https://example.com/simple'),
        # pip-compile line style, a source annotation
        ("""\
anyio==4.15.1
    # via httpx2, starlette
    # from https://example.com/simple
""", 'https://example.com/simple'),
        # the source annotation before the via annotations
        ("""\
anyio==4.15.1
    # from https://example.com/simple
    # via httpx2, starlette
""", 'https://example.com/simple'),
        # a comment that is not an annotation does not break the association
        ("""\
anyio==4.15.1
    # via
    #   httpx2
    # a comment
    # from https://example.com/simple
    #   starlette
""", 'https://example.com/simple'),
        # an annotation without indentation
        ('anyio==4.15.1\n# from https://example.com/simple\n', 'https://example.com/simple'),
        # an inline from annotation
        ('anyio==4.15.1  # from https://example.com/simple', 'https://example.com/simple'),
    ])
    def test_source(self, content, expected):
        """Every annotation style of uv and pip-compile is read."""
        assert parse_dep_infos(content) == {
            'anyio': DepInfo('anyio', '4.15.1', expected),
        }

    @pytest.mark.parametrize('content', [
        # pip-compile split style without a source
        """\
click==8.1.7
    # via
    #   -r requirements.in
    #   flask
""",
        # pip-compile line style without a source
        """\
click==8.1.7
    # via -r requirements.in, flask
""",
        # the setup.py and pyproject.toml origins of pip-compile
        """\
click==8.1.7
    # via flask (setup.py)
    # via waitress (pyproject.toml)
""",
        # an inline via comment
        'click==8.1.7  # via flask',
        # the header of pip-compile
        """\
#
# This file is autogenerated by pip-compile with Python 3.8
# by the following command:
#
#    pip-compile requirements.in
#
click==8.1.7
""",
    ])
    def test_without_source(self, content):
        """The via annotations and the comments are not a source."""
        assert parse_dep_infos(content) == {
            'click': DepInfo('click', '8.1.7', ''),
        }

    def test_comment_line_is_not_split_again(self):
        """A comment line is one comment, a later '#' is not a new annotation."""
        content = """\
httpx==0.28.1
    # via flask # from https://example.com/simple
"""
        assert parse_dep_infos(content) == {
            'httpx': DepInfo('httpx', '0.28.1', ''),
        }

    def test_inline_comment_is_not_split_again(self):
        """Only the first inline comment is taken, a later '#' is not a new one."""
        content = 'httpx==0.28.1  # via flask  # from https://example.com/simple\n'
        assert parse_dep_infos(content) == {
            'httpx': DepInfo('httpx', '0.28.1', ''),
        }

    def test_blank_line_does_not_end_the_pin(self):
        """A blank line keeps the pin, the annotation can follow it."""
        content = 'httpx==0.28.1\n\n    # from https://example.com/simple\n'
        assert parse_dep_infos(content) == {
            'httpx': DepInfo('httpx', '0.28.1', 'https://example.com/simple'),
        }

    def test_options_do_not_end_the_pin(self):
        """An option line belongs to the file, not to the pin before it."""
        content = """\
httpx==0.28.1
--extra-index-url https://example.com/simple
    # from https://example.com/simple
"""
        assert parse_dep_infos(content) == {
            'httpx': DepInfo('httpx', '0.28.1', 'https://example.com/simple'),
        }

    def test_hash_continuation(self):
        """The --hash lines of a pip-compile output are not part of the pin."""
        content = """\
--extra-index-url https://example.com/simple

certifi==2024.2.2 \\
    --hash=sha256:aaaa \\
    --hash=sha256:bbbb
    # via requests
    # from https://example.com/simple
"""
        assert parse_dep_infos(content) == {
            'certifi': DepInfo('certifi', '2024.2.2', 'https://example.com/simple'),
        }

    def test_real_alas2026_fragment(self):
        """The annotations of the real requirements file are read."""
        content = """\
# This file is generated by `python -m dev_tools.requirements_compile`, do not edit it by hand.
#
# `--index-url https://pypi.org/simple` is not in the file on purpose.

--extra-index-url https://py38deps.github.io/py38deps/simple/

adbutils==0.11.0
    # via
    #   azurlaneautoscript (pyproject.toml)
    #   uiautomator2
    # from https://pypi.org/simple
anyio==4.15.1
    # via
    #   httpx2
    #   starlette
    # from https://py38deps.github.io/py38deps/simple/
av==18.1.0
    # via azurlaneautoscript (pyproject.toml)
    # from https://py38deps.github.io/py38deps/simple/
"""
        assert parse_dep_infos(content) == {
            'adbutils': DepInfo('adbutils', '0.11.0', ''),
            'anyio': DepInfo(
                'anyio', '4.15.1', 'https://py38deps.github.io/py38deps/simple'),
            'av': DepInfo('av', '18.1.0', 'https://py38deps.github.io/py38deps/simple'),
        }


class TestSourceNormalization:
    @pytest.mark.parametrize('annotation, expected', [
        ('https://example.com/simple', 'https://example.com/simple'),
        ('https://example.com/simple/', 'https://example.com/simple'),
        ('https://example.com/simple//', 'https://example.com/simple'),
        ('HTTPS://Example.COM/simple/', 'https://example.com/simple'),
        ('https://example.com:443/simple', 'https://example.com/simple'),
        ('http://example.com:80/simple', 'http://example.com/simple'),
        ('https://example.com:8443/simple/', 'https://example.com:8443/simple'),
        ('https://user:pass@Example.COM/simple/', 'https://user:pass@example.com/simple'),
        ('https://example.com', 'https://example.com'),
        ('https://example.com/', 'https://example.com'),
        ('https://example.com/simple?token=abc', 'https://example.com/simple?token=abc'),
        # the encoding of the path and the query is kept
        ('https://example.com/simple%2Frepo/?token=a%20b',
         'https://example.com/simple%2Frepo?token=a%20b'),
        # a fragment is not part of an index base url
        ('https://example.com/simple/#frag', 'https://example.com/simple'),
        ('http://[::1]:8080/simple/', 'http://[::1]:8080/simple'),
    ])
    def test_normalized(self, annotation, expected):
        """The scheme, host, default port and trailing slashes are normalized."""
        content = f'httpx==0.28.1\n    # from {annotation}\n'
        assert parse_dep_infos(content) == {
            'httpx': DepInfo('httpx', '0.28.1', expected),
        }


class TestOfficialSource:
    @pytest.mark.parametrize('annotation', [
        'https://pypi.org/simple',
        'https://pypi.org/simple/',
        'HTTPS://PyPI.ORG/simple',
        'https://pypi.org:443/simple/',
    ])
    def test_undefined(self, annotation):
        """The official PyPI is no source, the download side probes the mirrors."""
        content = f'httpx==0.28.1\n    # from {annotation}\n'
        assert parse_dep_infos(content) == {
            'httpx': DepInfo('httpx', '0.28.1', ''),
        }

    def test_dict_sources(self):
        """The official annotation is not in dict_sources."""
        content = 'httpx==0.28.1\n    # from https://pypi.org/simple\n'
        assert Requirements(content).dict_sources == {'httpx': ''}

    def test_official_does_not_conflict(self):
        """An official annotation is no source, it conflicts with nothing."""
        content = """\
httpx==0.28.1
    # from https://pypi.org/simple
    # from https://example.com/simple
"""
        assert parse_dep_infos(content) == {
            'httpx': DepInfo('httpx', '0.28.1', 'https://example.com/simple'),
        }


class TestUndefinedSource:
    @pytest.mark.parametrize('source', [
        # a distribution file, not an index
        'https://example.com/httpx-0.28.1-py3-none-any.whl',
        'https://example.com/httpx-0.28.1.tar.gz',
        'https://example.com/httpx-0.28.1.zip',
        # a source that is not http(s)
        'file:///wheels/simple',
        'git+https://example.com/repo',
        'ftp://example.com/simple',
        # a malformed source
        'pypi.org/simple',
        'https://',
        'https:///simple',
        'https://example.com:bad/simple',
    ])
    def test_undefined(self, source):
        """A source that is not an index is a warning, the source is undefined."""
        content = f'httpx==0.28.1\n    # from {source}\n'
        with logger.mock_capture_writer() as capture:
            pins = parse_dep_infos(content)
        assert pins == {'httpx': DepInfo('httpx', '0.28.1', '')}
        assert capture.concat() == f"""\
WARNING | The "# from" annotation of "httpx" is not an index url: {source!r}, the source is undefined
"""


class TestUnparseableSource:
    def test_not_a_url(self):
        """A '# from' text that is not a URL is a warning, the pin is official."""
        content = 'httpx==0.28.1\n    # from <unknown>\n'
        with logger.mock_capture_writer() as capture:
            pins = parse_dep_infos(content)
        assert pins == {'httpx': DepInfo('httpx', '0.28.1', '')}
        assert capture.concat() == """\
WARNING | The "# from" annotation of "httpx" is not an index url: '<unknown>', the source is undefined
"""

    def test_from_before_the_first_pin(self):
        """A '# from' without a pin before it is ignored."""
        content = '# from https://pypi.org/simple\nhttpx==0.28.1\n'
        with logger.mock_capture_writer() as capture:
            pins = parse_dep_infos(content)
        assert pins == {'httpx': DepInfo('httpx', '0.28.1', '')}
        assert capture.concat() == ''

    def test_from_after_a_declaration_without_pin(self):
        """A '# from' after a declaration that is not a pin is ignored."""
        content = 'httpx>=0.28.1\n    # from https://pypi.org/simple\npyyaml==6.0.1\n'
        with logger.mock_capture_writer() as capture:
            pins = parse_dep_infos(content)
        assert pins == {'pyyaml': DepInfo('pyyaml', '6.0.1', '')}
        assert capture.concat() == ''

    def test_extra_index_without_provided_source(self):
        """An extra index without a provided source is one summary warning."""
        content = """\
--extra-index-url https://py38deps.github.io/py38deps/simple/
httpx==0.28.1
pyyaml==6.0.1
"""
        with logger.mock_capture_writer() as capture:
            pins = parse_dep_infos(content)
        assert pins == {
            'httpx': DepInfo('httpx', '0.28.1', ''),
            'pyyaml': DepInfo('pyyaml', '6.0.1', ''),
        }
        assert capture.concat() == """\
WARNING | 2 pins have no provided source, treated as official: the requirements file declares the extra index "https://py38deps.github.io/py38deps/simple/"
"""

    def test_extra_index_with_a_provided_source(self):
        """A pin from a provided source suppresses the summary warning."""
        content = """\
--extra-index-url https://py38deps.github.io/py38deps/simple/
httpx==0.28.1
    # from https://example.com/simple
"""
        with logger.mock_capture_writer() as capture:
            pins = parse_dep_infos(content)
        assert pins == {
            'httpx': DepInfo('httpx', '0.28.1', 'https://example.com/simple'),
        }
        assert capture.concat() == ''

    def test_extra_index_with_an_official_source(self):
        """The official PyPI is no provided source, the summary warning stays."""
        content = """\
--extra-index-url https://py38deps.github.io/py38deps/simple/
httpx==0.28.1
    # from https://pypi.org/simple
"""
        with logger.mock_capture_writer() as capture:
            pins = parse_dep_infos(content)
        assert pins == {
            'httpx': DepInfo('httpx', '0.28.1', ''),
        }
        assert capture.concat() == """\
WARNING | 1 pins have no provided source, treated as official: the requirements file declares the extra index "https://py38deps.github.io/py38deps/simple/"
"""


class TestSourceConflicts:
    def test_one_pin_two_sources(self):
        """Two different '# from' annotations keep the first, the later one is a warning."""
        content = """\
httpx==0.28.1
    # from https://example.com/simple
    # from https://py38deps.github.io/py38deps/simple
"""
        with logger.mock_capture_writer() as capture:
            pins = parse_dep_infos(content)
        assert pins == {'httpx': DepInfo('httpx', '0.28.1', 'https://example.com/simple')}
        assert capture.concat() == """\
WARNING | Conflicting sources of "httpx" in the requirements: "https://example.com/simple" and "https://py38deps.github.io/py38deps/simple", keeping "https://example.com/simple"
"""

    def test_one_pin_two_spellings_of_the_same_source(self):
        """Two spellings of one source are one source."""
        content = """\
httpx==0.28.1
    # from https://example.com/simple
    # from https://Example.COM/simple/
"""
        with logger.mock_capture_writer() as capture:
            pins = parse_dep_infos(content)
        assert pins == {
            'httpx': DepInfo('httpx', '0.28.1', 'https://example.com/simple'),
        }
        assert capture.concat() == ''

    def test_duplicated_pin_different_sources(self):
        """Two lines of one pin with different sources keep the first source."""
        content = """\
httpx==0.28.1
    # from https://example.com/simple
httpx==0.28.1
    # from https://py38deps.github.io/py38deps/simple
"""
        with logger.mock_capture_writer() as capture:
            pins = parse_dep_infos(content)
        assert pins == {'httpx': DepInfo('httpx', '0.28.1', 'https://example.com/simple')}
        assert capture.concat() == """\
WARNING | Conflicting sources of "httpx" in the requirements: "https://example.com/simple" and "https://py38deps.github.io/py38deps/simple", keeping "https://example.com/simple"
"""

    def test_duplicated_pin_with_one_source(self):
        """An empty source does not overwrite the source of the other line."""
        content = """\
httpx==0.28.1
httpx==0.28.1
    # from https://example.com/simple
"""
        assert parse_dep_infos(content) == {
            'httpx': DepInfo('httpx', '0.28.1', 'https://example.com/simple'),
        }

    def test_dict_deps_of_a_source_conflict(self):
        """The version view parses the same pins, the source conflict is only a warning."""
        content = """\
httpx==0.28.1
    # from https://example.com/simple
    # from https://py38deps.github.io/py38deps/simple
"""
        parser = Requirements(content)
        with logger.mock_capture_writer() as capture:
            assert parser.dict_deps == {'httpx': '0.28.1'}
            assert parser.dict_sources == {'httpx': 'https://example.com/simple'}
        assert capture.concat() == """\
WARNING | Conflicting sources of "httpx" in the requirements: "https://example.com/simple" and "https://py38deps.github.io/py38deps/simple", keeping "https://example.com/simple"
"""

    def test_duplicated_pin_different_versions(self):
        """Two different versions keep the first, the later one is a warning."""
        content = """\
httpx==0.28.1
    # from https://example.com/simple
httpx==0.28.2
    # from https://py38deps.github.io/py38deps/simple
"""
        with logger.mock_capture_writer() as capture:
            pins = parse_dep_infos(content)
        assert pins == {'httpx': DepInfo('httpx', '0.28.1', 'https://example.com/simple')}
        assert capture.concat() == """\
WARNING | Conflicting versions of "httpx" in the requirements: "0.28.1" and "0.28.2", keeping "0.28.1"
"""
