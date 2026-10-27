"""
Tests for the index page links (pip/_internal/models/link.py and
pip/_internal/index/collector.py).
"""
import pytest

from alasio.deploy.pip_download._link import HTMLLinkParser, Link, parse_links

PAGE_URL = 'https://pypi.example/simple/httpx/'


def page(*links, base=''):
    """
    Build an index page of the tests.

    Args:
        *links (str): The `<a>` elements of the page
        base (str): The `<base href>` of the page, '' for none

    Returns:
        bytes: The page
    """
    head = f'<base href="{base}">' if base else ''
    return f'<html><head>{head}</head><body>{"".join(links)}</body></html>'.encode('utf-8')


class TestHtmlLinkParser:
    """The parser keeps the first base href and every anchor."""

    def test_anchors(self):
        parser = HTMLLinkParser(PAGE_URL)
        parser.feed(page('<a href="a.whl">a</a>', '<a name="b"></a>').decode())
        assert parser.anchors == [{'href': 'a.whl'}, {'name': 'b'}]

    def test_first_base_only(self):
        parser = HTMLLinkParser(PAGE_URL)
        parser.feed('<html><head><base href="https://one/"><base href="https://two/"></head></html>')
        assert parser.base_url == 'https://one/'

    def test_base_without_href(self):
        parser = HTMLLinkParser(PAGE_URL)
        parser.feed('<html><head><base target="_blank"></head></html>')
        assert parser.base_url is None


class TestParseLinks:
    """The links of a page, their url, hash and attributes."""

    def test_relative_link(self):
        links = parse_links(page('<a href="httpx-0.28.1-py3-none-any.whl">x</a>'), PAGE_URL)
        assert len(links) == 1
        link = links[0]
        assert link.url == 'https://pypi.example/simple/httpx/httpx-0.28.1-py3-none-any.whl'
        assert link.filename == 'httpx-0.28.1-py3-none-any.whl'
        assert link.is_wheel
        assert link.hashes == {}
        assert link.requires_python is None
        assert not link.is_yanked

    def test_relative_parent_link(self):
        links = parse_links(page('<a href="../../packages/foo-1.0-py3-none-any.whl">x</a>'), PAGE_URL)
        assert links[0].url == 'https://pypi.example/packages/foo-1.0-py3-none-any.whl'

    def test_base_href(self):
        links = parse_links(
            page('<a href="foo-1.0-py3-none-any.whl">x</a>', base='https://cdn.example/files/'),
            PAGE_URL)
        assert links[0].url == 'https://cdn.example/files/foo-1.0-py3-none-any.whl'

    def test_absolute_link(self):
        links = parse_links(
            page('<a href="https://files.pythonhosted.org/packages/x/foo-1.0-py3-none-any.whl">x</a>'),
            PAGE_URL)
        assert links[0].url == 'https://files.pythonhosted.org/packages/x/foo-1.0-py3-none-any.whl'

    def test_hash_fragment(self):
        links = parse_links(
            page('<a href="foo-1.0-py3-none-any.whl#sha256=abc123">x</a>'), PAGE_URL)
        link = links[0]
        assert link.hashes == {'sha256': 'abc123'}
        assert link.hash == 'abc123'
        assert link.hash_name == 'sha256'
        assert link.url.endswith('#sha256=abc123')
        assert link.url_without_fragment.endswith('foo-1.0-py3-none-any.whl')

    def test_hash_precedence(self):
        # the first hash of the fragment wins, like pip: an index page carries
        # one hash per link, the multi hash fragment is not a real case
        links = parse_links(
            page('<a href="foo-1.0-py3-none-any.whl#sha1=old&amp;sha256=new">x</a>'), PAGE_URL)
        assert links[0].hashes == {'sha1': 'old'}
        links = parse_links(
            page('<a href="foo-1.0-py3-none-any.whl#sha256=new&amp;sha1=old">x</a>'), PAGE_URL)
        assert links[0].hashes == {'sha256': 'new'}

    def test_requires_python(self):
        links = parse_links(
            page('<a href="foo-1.0-py3-none-any.whl" data-requires-python="&gt;=3.8">x</a>'),
            PAGE_URL)
        assert links[0].requires_python == '>=3.8'

    def test_yanked(self):
        links = parse_links(
            page('<a href="foo-1.0-py3-none-any.whl" data-yanked="broken build">x</a>'),
            PAGE_URL)
        assert links[0].is_yanked
        assert links[0].yanked_reason == 'broken build'

    def test_anchor_without_href(self):
        assert parse_links(page('<a name="x">x</a>'), PAGE_URL) == []

    def test_entities_in_the_url(self):
        links = parse_links(
            page('<a href="foo-1.0-py3-none-any.whl?a=1&amp;b=2">x</a>'), PAGE_URL)
        assert links[0].url.endswith('foo-1.0-py3-none-any.whl?a=1&b=2')

    def test_space_in_the_url(self):
        links = parse_links(page('<a href="foo 1.0-py3-none-any.whl">x</a>'), PAGE_URL)
        assert links[0].url.endswith('foo%201.0-py3-none-any.whl')

    def test_json_page(self):
        content = b"""{"files": [
            {"url": "foo-1.0-py3-none-any.whl", "hashes": {"sha256": "abc"},
             "requires-python": ">=3.8", "yanked": false},
            {"url": "bar-1.0-py3-none-any.whl", "hashes": {}, "yanked": "broken"},
            {"hashes": {}}
        ]}"""
        links = parse_links(
            content, PAGE_URL, 'application/vnd.pypi.simple.v1+json; charset=utf-8')
        assert [link.filename for link in links] == [
            'foo-1.0-py3-none-any.whl',
            'bar-1.0-py3-none-any.whl',
        ]
        assert links[0].hashes == {'sha256': 'abc'}
        assert links[0].requires_python == '>=3.8'
        assert not links[0].is_yanked
        assert links[1].is_yanked
        assert links[1].yanked_reason == 'broken'


class TestLinkProperties:
    """The properties of a link that the fetcher reads."""

    def test_filename_is_a_single_component(self):
        link = Link('https://host/packages/../../foo-1.0-py3-none-any.whl')
        assert link.filename == 'foo-1.0-py3-none-any.whl'

    def test_filename_of_a_directory_link(self):
        # no path component: the host is the filename, never an empty one
        link = Link('https://host.example/')
        assert link.filename == 'host.example'

    def test_filename_of_a_traversal(self):
        link = Link('https://host/packages/..')
        assert link.filename == 'host'

    def test_ext(self):
        assert Link('https://host/foo-1.0-py3-none-any.whl').ext == '.whl'
        assert not Link('https://host/foo-1.0.tar.gz').is_wheel

    def test_ordering(self):
        first = Link('https://host/a-1.0-py3-none-any.whl')
        second = Link('https://host/b-1.0-py3-none-any.whl')
        assert first < second
        assert first != second
        assert Link(first.url) == first

    def test_str(self):
        link = Link('https://host/a.whl', comes_from=PAGE_URL, requires_python='>=3.8')
        assert str(link) == 'https://host/a.whl (from https://pypi.example/simple/httpx/) ' \
                            '(requires-python:>=3.8)'

    def test_from_element_without_href(self):
        assert Link.from_element({}, PAGE_URL, PAGE_URL) is None

    @pytest.mark.parametrize('url, expected', [
        ('https://host/a.whl?x=1#sha256=abc', 'https://host/a.whl?x=1'),
        ('https://host/a.whl#sha256=abc', 'https://host/a.whl'),
    ])
    def test_url_without_fragment(self, url, expected):
        assert Link(url).url_without_fragment == expected
