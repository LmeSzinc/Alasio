"""
Tests for the generator of pip index pages and the two page builders.
"""
import pytest

from alasio.codegen.html import HtmlGen
from alasio.codegen.pypisite import PypiHeader, PypiSite, Wheel, WheelLink, find_links_page, simple_page
from alasio.testing.filesystem import fs  # noqa: F401

# the digest of a wheel, the shape the fetcher of the pack server records
DIGEST_A = 'a' * 64
DIGEST_B = 'b' * 64

# the wheels of the tests, one pure python and one per platform
FILE_PURE = 'httpx2-2.13.1-py3-none-any.whl'
FILE_WIN = 'httpx2-2.13.1-cp38-cp38-win_amd64.whl'
FILE_MSGS = 'msgspec-0.22.0-cp38-cp38-win_amd64.whl'

# the page of one distribution, the shape pip reads as an index
SIMPLE_PAGE = f"""\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="pypi:repository-version" content="1.0">
    <title>Links for httpx2</title>
  </head>
  <body>
    <h1>Links for httpx2</h1>
    <a href="2.13.1/{FILE_WIN}#sha256={DIGEST_B}">{FILE_WIN}</a><br>
    <a href="2.13.1/{FILE_PURE}#sha256={DIGEST_A}">{FILE_PURE}</a><br>
  </body>
</html>
"""

# the page of the whole folder, the shape pip reads with --find-links
FIND_LINKS_PAGE = f"""\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>Wheels of Author_Repo_master</title>
  </head>
  <body>
    <h1>Wheels of Author_Repo_master</h1>
    <a href="httpx2/2.13.1/{FILE_PURE}#sha256={DIGEST_A}">{FILE_PURE}</a><br>
    <a href="msgspec/0.22.0/{FILE_MSGS}#sha256={DIGEST_B}">{FILE_MSGS}</a><br>
  </body>
</html>
"""


class TestPypiSiteIsAnHtmlGen:
    def test_subclass(self):
        assert issubclass(PypiSite, HtmlGen)

    def test_instance(self):
        assert isinstance(PypiSite(), HtmlGen)

    def test_generic_methods_inherited(self):
        """The domain generator writes the generic tags with no extra code."""
        gen = PypiSite()
        with gen.Div(class_='card'):
            gen.P('x')
        assert gen.generate_str() == """\
<div class="card">
  <p>x</p>
</div>
"""

    def test_write_inherited(self, fs):
        gen = PypiSite()
        gen.Wheel(WheelLink('httpx2', '2.13.1', FILE_PURE))
        assert gen.write('/wheel/httpx2/index.html') is True
        with open('/wheel/httpx2/index.html', encoding='utf-8') as f:
            assert f.read() == f"""\
<a href="2.13.1/{FILE_PURE}">{FILE_PURE}</a><br>
"""

    def test_indent_size_inherited(self):
        gen = PypiSite(indent_size=4)
        with gen.PypiHeader('httpx2'):
            gen.Wheels([WheelLink('httpx2', '2.13.1', FILE_PURE)])
        assert gen.generate_str() == f"""\
<!DOCTYPE html>
<html lang="en">
    <head>
        <meta charset="utf-8">
        <meta name="pypi:repository-version" content="1.0">
        <title>Links for httpx2</title>
    </head>
    <body>
        <h1>Links for httpx2</h1>
        <a href="2.13.1/{FILE_PURE}">{FILE_PURE}</a><br>
    </body>
</html>
"""


class TestWheelMethods:
    def test_wheel_attaches(self):
        gen = PypiSite()
        item = gen.Wheel(WheelLink('httpx2', '2.13.1', FILE_PURE))
        assert isinstance(item, Wheel)
        assert gen.items == [item]

    def test_wheels_returns_the_lines(self):
        gen = PypiSite()
        items = gen.Wheels([
            WheelLink('httpx2', '2.13.1', FILE_PURE, DIGEST_A),
            WheelLink('httpx2', '2.13.1', FILE_WIN, DIGEST_B),
        ])
        assert all(isinstance(item, Wheel) for item in items)
        assert gen.items == items

    def test_wheels_sorted(self):
        """The order of the page does not depend on the order of the input."""
        gen = PypiSite()
        items = gen.Wheels([
            WheelLink('httpx2', '2.13.1', FILE_PURE, DIGEST_A),
            WheelLink('httpx2', '2.13.1', FILE_WIN, DIGEST_B),
        ])
        assert [item.link.filename for item in items] == [FILE_WIN, FILE_PURE]

    def test_wheels_sorted_by_pep440(self):
        """The versions order the way pip resolves them, a pre-release before its release."""
        gen = PypiSite()
        items = gen.Wheels([
            WheelLink('httpx2', '2.13.1', 'httpx2-2.13.1-py3-none-any.whl'),
            WheelLink('httpx2', '2.14.0rc1', 'httpx2-2.14.0rc1-py3-none-any.whl'),
            WheelLink('httpx2', '2.9.0', 'httpx2-2.9.0-py3-none-any.whl'),
        ])
        assert [item.link.version for item in items] == ['2.9.0', '2.13.1', '2.14.0rc1']

    def test_wheel_keeps_the_caller_order(self):
        """The single wheel is written where the caller puts it, no order of its own."""
        gen = PypiSite()
        gen.Wheel(WheelLink('httpx2', '2.13.1', 'httpx2-2.13.1-py3-none-any.whl'))
        gen.Wheel(WheelLink('httpx2', '2.9.0', 'httpx2-2.9.0-py3-none-any.whl'))
        assert gen.generate_str() == """\
<a href="2.13.1/httpx2-2.13.1-py3-none-any.whl">httpx2-2.13.1-py3-none-any.whl</a><br>
<a href="2.9.0/httpx2-2.9.0-py3-none-any.whl">httpx2-2.9.0-py3-none-any.whl</a><br>
"""

    def test_wheels_of_the_same_links_sort(self):
        """The same calls through Wheels come out ordered."""
        gen = PypiSite()
        gen.Wheels([
            WheelLink('httpx2', '2.13.1', 'httpx2-2.13.1-py3-none-any.whl'),
            WheelLink('httpx2', '2.9.0', 'httpx2-2.9.0-py3-none-any.whl'),
        ])
        assert gen.generate_str() == """\
<a href="2.9.0/httpx2-2.9.0-py3-none-any.whl">httpx2-2.9.0-py3-none-any.whl</a><br>
<a href="2.13.1/httpx2-2.13.1-py3-none-any.whl">httpx2-2.13.1-py3-none-any.whl</a><br>
"""

    def test_wheels_in_a_container(self):
        gen = PypiSite()
        with gen.Div():
            items = gen.Wheels([WheelLink('httpx2', '2.13.1', FILE_PURE)])
            assert gen.context.children == items

    def test_wheels_empty(self):
        gen = PypiSite()
        assert gen.Wheels([]) == []
        assert gen.generate_str() == ''

    def test_wheels_of_two_names(self):
        """Outside the page of a distribution, the links only have to agree."""
        gen = PypiSite()
        with pytest.raises(ValueError, match='another distribution'):
            gen.Wheels([WheelLink('httpx2', '2.13.1', FILE_PURE), WheelLink('msgspec', '0.22.0', FILE_MSGS)])

    def test_header_attaches(self):
        gen = PypiSite()
        item = gen.PypiHeader('httpx2')
        assert isinstance(item, PypiHeader)
        assert gen.items == [item]


class TestSimplePage:
    def test_page(self):
        """The page of one distribution, every wheel of it, sorted."""
        assert simple_page('httpx2', [
            WheelLink('httpx2', '2.13.1', FILE_PURE, DIGEST_A),
            WheelLink('httpx2', '2.13.1', FILE_WIN, DIGEST_B),
        ]) == SIMPLE_PAGE

    def test_name_normalized(self):
        assert simple_page('HTTPX2', [WheelLink('HTTPX2', '2.13.1', FILE_PURE)]).startswith("""\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="pypi:repository-version" content="1.0">
    <title>Links for httpx2</title>
""")

    def test_lang(self):
        assert simple_page('httpx2', [], lang='zh-CN').startswith("""\
<!DOCTYPE html>
<html lang="zh-CN">
""")

    def test_empty(self):
        assert simple_page('httpx2', []) == """\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="pypi:repository-version" content="1.0">
    <title>Links for httpx2</title>
  </head>
  <body>
    <h1>Links for httpx2</h1>
  </body>
</html>
"""

    def test_versions_in_natural_order(self):
        """2.9.0 sorts before 2.13.1, the text order would put it after."""
        page = simple_page('httpx2', [
            WheelLink('httpx2', '2.13.1', FILE_PURE),
            WheelLink('httpx2', '2.9.0', 'httpx2-2.9.0-py3-none-any.whl'),
        ])
        assert page.index('httpx2-2.9.0') < page.index('httpx2-2.13.1')

    def test_without_digest(self):
        assert simple_page('httpx2', [WheelLink('httpx2', '2.13.1', FILE_PURE)]) == f"""\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="pypi:repository-version" content="1.0">
    <title>Links for httpx2</title>
  </head>
  <body>
    <h1>Links for httpx2</h1>
    <a href="2.13.1/{FILE_PURE}">{FILE_PURE}</a><br>
  </body>
</html>
"""

    def test_other_distribution(self):
        with pytest.raises(ValueError, match='cannot link the wheel of another distribution'):
            simple_page('httpx2', [WheelLink('msgspec', '0.22.0', FILE_MSGS)])

    def test_invalid_project(self):
        with pytest.raises(ValueError, match='not a distribution name'):
            simple_page('../httpx2', [])


class TestFindLinksPage:
    def test_page(self):
        """Every wheel of the folder, relative to the page, sorted by name."""
        assert find_links_page([
            WheelLink('msgspec', '0.22.0', FILE_MSGS, DIGEST_B),
            WheelLink('httpx2', '2.13.1', FILE_PURE, DIGEST_A),
        ], title='Wheels of Author_Repo_master') == FIND_LINKS_PAGE

    def test_default_title(self):
        assert find_links_page([]) == """\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>Wheels</title>
  </head>
  <body>
    <h1>Wheels</h1>
  </body>
</html>
"""

    def test_title_escaped(self):
        page = find_links_page([], title='a & <b>')
        assert '<title>a &amp; &lt;b&gt;</title>' in page
        assert '<h1>a &amp; &lt;b&gt;</h1>' in page

    def test_no_repository_version(self):
        """The find-links page of a folder is not a PEP 503 project page."""
        assert 'pypi:repository-version' not in find_links_page([])

    def test_name_normalized(self):
        page = find_links_page([WheelLink('HTTPX2', '2.13.1', FILE_PURE, DIGEST_A)])
        assert f'href="httpx2/2.13.1/{FILE_PURE}#sha256={DIGEST_A}"' in page

    def test_sort_across_names(self):
        page = find_links_page([
            WheelLink('msgspec', '0.22.0', FILE_MSGS),
            WheelLink('httpx2', '2.13.1', FILE_PURE),
            WheelLink('av', '18.1.0', 'av-18.1.0-cp38-cp38-win_amd64.whl'),
        ])
        assert page.index('av/') < page.index('httpx2/') < page.index('msgspec/')

    def test_duplicate_dropped(self):
        link = WheelLink('httpx2', '2.13.1', FILE_PURE, DIGEST_A)
        assert find_links_page([link, link]).count('<a ') == 1

    def test_duplicate_of_another_digest(self):
        with pytest.raises(ValueError, match='two digests'):
            find_links_page([
                WheelLink('httpx2', '2.13.1', FILE_PURE, DIGEST_A),
                WheelLink('httpx2', '2.13.1', FILE_PURE, DIGEST_B),
            ])


class TestPagesAreStable:
    def test_simple_page_repeatable(self):
        """The same links render the same bytes, whatever their order."""
        one = [WheelLink('httpx2', '2.13.1', FILE_PURE, DIGEST_A), WheelLink('httpx2', '2.13.1', FILE_WIN, DIGEST_B)]
        other = list(reversed(one))
        assert simple_page('httpx2', one) == simple_page('httpx2', other)

    def test_find_links_page_repeatable(self):
        one = [WheelLink('httpx2', '2.13.1', FILE_PURE, DIGEST_A), WheelLink('msgspec', '0.22.0', FILE_MSGS, DIGEST_B)]
        other = list(reversed(one))
        assert find_links_page(one) == find_links_page(other)

    def test_simple_page_of_two_paths(self):
        """The domain page and the generic document render the same bytes."""
        links = [WheelLink('httpx2', '2.13.1', FILE_PURE, DIGEST_A)]
        gen = PypiSite()
        with gen.Doc('Links for httpx2') as page:
            with page.head():
                gen.Meta(name='pypi:repository-version', content='1.0')
            with page.body():
                gen.H1('Links for httpx2')
                gen.Wheels(links)
        assert gen.generate_str() == simple_page('httpx2', links)
