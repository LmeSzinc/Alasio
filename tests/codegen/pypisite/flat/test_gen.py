"""
Tests for the generator of a flat page: the page a caller composes, the generic
methods it inherits, and the promises a page keeps between two runs.
"""
import pytest

from alasio.codegen.html import Element, HtmlGen, Raw
from alasio.codegen.pypisite import PypiSite, WheelLink
from alasio.codegen.pypisite.flat import FLAT_STYLE, PypiSiteFlat, flat_page
from alasio.testing.filesystem import fs  # noqa: F401

from .data import DIGEST_A, DIGEST_B, FILE_MAC, FILE_PURE, FILE_WIN
from .expected import PURE_SECTION, WHEELS_PAGE, page_of


class TestStyle:
    def test_sheet_is_inline(self):
        """The page is one file: the sheet is a text of the module, not a second file."""
        assert FLAT_STYLE.startswith(':root {')
        assert FLAT_STYLE.endswith('}\n')

    def test_pure_table_column(self):
        """The version of a pure table is a label: it centres in the width of the text.

        The matrix shares its sheet with the pure table, and the first column of a
        matrix is the wide column of the platforms. A version is not a platform,
        so the table of the pure wheels takes the two rules back.
        """
        assert 'table.matrix.pure th:first-child { width:auto;' in FLAT_STYLE
        assert 'table.matrix.pure tbody th { text-align:center;' in FLAT_STYLE

    def test_abi3_mark(self):
        """The mark of a stable abi wheel has a style of its own, in a cell and in the note."""
        assert '.abi3 {' in FLAT_STYLE


class TestFlatPage:
    def test_page(self):
        """The page of one compiled project, composed the way a caller writes it."""
        gen = PypiSiteFlat()
        with gen.Doc('Wheels of Author_Repo_master') as page:
            with page.body():
                gen.H1('Wheels of Author_Repo_master')
                gen.inject_index()
                with gen.WheelName('httpx2'):
                    with gen.WheelVersion('2.13.1'):
                        gen.WheelTableCompiled([
                            WheelLink('httpx2', '2.13.1', FILE_WIN),
                            WheelLink('httpx2', '2.13.1', FILE_MAC),
                            WheelLink('httpx2', '2.13.1', FILE_PURE),
                        ])
        assert gen.generate_str() == WHEELS_PAGE

    def test_page_of_a_pure_project(self):
        gen = PypiSiteFlat()
        with gen.Doc('Wheels') as page:
            with page.body():
                gen.H1('Wheels')
                gen.inject_index()
                with gen.WheelName('anyio'):
                    gen.WheelTablePurePython([
                        WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl')])
        assert gen.generate_str() == """\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>Wheels</title>
  </head>
  <body>
    <h1>Wheels</h1>
    <nav id="contents" aria-label="Contents">
      <h2>Contents</h2>
      <ul class="toc">
        <li><a href="#anyio">anyio</a> <span class="badge pure">pure python</span> <span class="versions"><a href="#anyio-4.15.1">4.15.1</a></span></li>
      </ul>
    </nav>
    <section class="project" id="anyio">
      <h2>anyio <span class="badge pure">pure python</span> <a class="anchor" href="#anyio" aria-label="link to anyio">#</a></h2>
      <p class="meta">1 version · 1 wheel</p>
      <div class="table-wrap">
        <table class="matrix pure">
          <thead>
            <tr><th scope="col">version</th><th scope="col">file</th></tr>
          </thead>
          <tbody>
            <tr><th scope="row" id="anyio-4.15.1">4.15.1 <a class="anchor" href="#anyio-4.15.1" aria-label="link to anyio-4.15.1">#</a></th><td><a href="anyio/4.15.1/anyio-4.15.1-py3-none-any.whl" title="anyio-4.15.1-py3-none-any.whl">anyio-4.15.1-py3-none-any.whl</a></td></tr>
          </tbody>
        </table>
      </div>
    </section>
  </body>
</html>
"""

    def test_page_without_a_project(self):
        gen = PypiSiteFlat()
        gen.inject_index()
        assert gen.generate_str() == """\
<nav id="contents" aria-label="Contents">
  <h2>Contents</h2>
  <ul class="toc">
  </ul>
</nav>
"""

    def test_style_in_the_head(self):
        """The page is one file: the sheet goes into the head of the document."""
        gen = PypiSiteFlat()
        head = [Element(gen, 'style', Raw(gen, FLAT_STYLE, indent=False))]
        with gen.Doc('Wheels', head=head) as page:
            with page.body():
                gen.H1('Wheels')
        text = gen.generate_str()
        assert text.startswith("""\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <style>
:root {""")
        assert text.endswith("""\
</style>
    <title>Wheels</title>
  </head>
  <body>
    <h1>Wheels</h1>
  </body>
</html>
""")


class TestFlatPageFunction:
    """The whole page of a folder of wheels, from the wheels alone."""

    def test_page(self):
        page = flat_page({'httpx2': [WheelLink('httpx2', '2.13.1', FILE_WIN)]}, 'Wheels')
        assert page.startswith("""\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <style>
:root {""")
        assert page.endswith("""\
    </style>
    <title>Wheels</title>
  </head>
  <body>
    <header>
      <h1>Wheels</h1>
      <p class="lede">Every wheel this index provides, one section per distribution. Anchors are stable: #name, #name-version.</p>
    </header>
    <nav id="contents" aria-label="Contents">
      <h2>Contents</h2>
      <ul class="toc">
        <li><a href="#httpx2">httpx2</a> <span class="badge compiled">compiled</span> <span class="versions"><a href="#httpx2-2.13.1">2.13.1</a></span></li>
      </ul>
    </nav>
    <main>
      <section class="project" id="httpx2">
        <h2>httpx2 <span class="badge compiled">compiled</span> <a class="anchor" href="#httpx2" aria-label="link to httpx2">#</a></h2>
        <section class="version" id="httpx2-2.13.1">
          <h3>2.13.1 <span class="badge compiled">compiled</span> <a class="anchor" href="#httpx2-2.13.1" aria-label="link to httpx2-2.13.1">#</a></h3>
          <p class="meta">1 wheel · 1 platform · 1 Python tag</p>
          <div class="table-wrap">
            <table class="matrix">
              <thead>
                <tr><th scope="col"><span class="platform-head">platform</span></th><th scope="col">cp38</th></tr>
              </thead>
              <tbody>
                <tr><th scope="row"><span class="plat">win_amd64</span></th><td class="ok"><a href="httpx2/2.13.1/httpx2-2.13.1-cp38-cp38-win_amd64.whl" title="httpx2-2.13.1-cp38-cp38-win_amd64.whl">cp38</a></td></tr>
              </tbody>
            </table>
          </div>
        </section>
      </section>
    </main>
    <footer>
      <p>Every link carries the sha256 of the file it points at.</p>
    </footer>
  </body>
</html>
""")

    def test_the_same_page_as_the_methods(self):
        """The page of the function is the page a caller composes with the methods."""
        gen = PypiSiteFlat()
        head = [Element(gen, 'style', Raw(gen, FLAT_STYLE, indent=False))]
        with gen.Doc('Wheels', head=head) as page:
            with page.body():
                with gen.Header():
                    gen.H1('Wheels')
                    gen.P(
                        'Every wheel this index provides, one section per distribution. '
                        'Anchors are stable: #name, #name-version.',
                        class_='lede',
                    ).wrap('inline')
                gen.inject_index()
                with gen.Main():
                    with gen.WheelName('httpx2'):
                        with gen.WheelVersion('2.13.1'):
                            gen.WheelTableCompiled([WheelLink('httpx2', '2.13.1', FILE_WIN)])
                with gen.Footer():
                    gen.P('Every link carries the sha256 of the file it points at.').wrap('inline')
        assert flat_page({'httpx2': [WheelLink('httpx2', '2.13.1', FILE_WIN)]}, 'Wheels') == (
            gen.generate_str())

    def test_command(self):
        page = flat_page(
            {'anyio': [WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl')]},
            'Wheels', command='pip install --find-links http://x <package>')
        assert (
            '      <pre><code>pip install --find-links http://x &lt;package&gt;</code></pre>\n') in page

    def test_no_command(self):
        """A page of a caller that does not know a url writes no box, not an empty one."""
        page = flat_page({'anyio': [WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl')]}, 'Wheels')
        assert '<pre>' not in page
        assert '<header>\n      <h1>Wheels</h1>\n      <p class="lede">' in page

    def test_a_version_a_table(self):
        """Every version of a project is a section, the wheels of it are one table."""
        page = flat_page({
            'httpx2': [
                WheelLink('httpx2', '2.9.0', 'httpx2-2.9.0-cp38-cp38-win_amd64.whl'),
                WheelLink('httpx2', '2.13.1', FILE_WIN),
            ],
        }, 'Wheels')
        assert page.count('<section class="version"') == 2
        assert '<section class="version" id="httpx2-2.9.0">' in page
        assert '<section class="version" id="httpx2-2.13.1">' in page
        # the order of the caller is the order of the page
        assert page.index('id="httpx2-2.9.0"') < page.index('id="httpx2-2.13.1"')

    def test_pure_versions_are_rows(self):
        """A version of pure python wheels is a row of the table, not a section."""
        page = flat_page({'anyio': [
            WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl'),
            WheelLink('anyio', '4.14.2', 'anyio-4.14.2-py3-none-any.whl'),
        ]}, 'Wheels')
        assert '<section class="version"' not in page
        assert '<table class="matrix pure">' in page
        assert '<p class="meta">2 versions · 2 wheels</p>' in page

    def test_mixed_project(self):
        """The compiled versions are sections, the pure versions one table after them."""
        page = flat_page({'anyio': [
            WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl'),
            WheelLink('anyio', '4.14.2', 'anyio-4.14.2-cp38-cp38-win_amd64.whl'),
        ]}, 'Wheels')
        assert '<section class="version" id="anyio-4.14.2">' in page
        assert '<table class="matrix pure">' in page
        assert page.index('id="anyio-4.14.2"') < page.index('<table class="matrix pure">')

    def test_origins(self):
        page = flat_page(
            {'anyio': [WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl')]},
            'Wheels',
            origins={'anyio': {'4.15.1': ('tag-4.15.1', 'https://example.com/4.15.1')}},
        )
        assert (
            '<a class="release-tag" href="https://example.com/4.15.1" title="release tag-4.15.1">'
            'tag-4.15.1</a>') in page

    def test_origin_key_normalized(self):
        """The name of the origins map is the name of the wheel, any spelling."""
        page = flat_page(
            {'ruamel.yaml': [WheelLink('ruamel.yaml', '0.18.6', 'ruamel.yaml-0.18.6-py3-none-any.whl')]},
            'Wheels',
            origins={'ruamel_yaml': {'0.18.6': ('tag', 'https://example.com/0.18.6')}},
        )
        assert 'class="release-tag"' in page

    def test_origin_of_an_unknown_version(self):
        """A misspelled version would drop a source and look like a page without one."""
        with pytest.raises(ValueError, match='of no version of it'):
            flat_page(
                {'anyio': [WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl')]},
                'Wheels',
                origins={'anyio': {'4.15': ('tag', 'https://example.com')}},
            )

    def test_empty(self):
        page = flat_page({}, 'Wheels')
        assert '<main>' in page
        assert '<ul class="toc">\n      </ul>' in page

    def test_name_normalized(self):
        page = flat_page({'HTTPX2': [WheelLink('HTTPX2', '2.13.1', FILE_PURE)]}, 'Wheels')
        assert '<section class="project" id="httpx2">' in page
        assert 'href="httpx2/2.13.1/httpx2-2.13.1-py3-none-any.whl"' in page


class TestGeneratorIntegration:
    def test_is_a_pypi_site(self):
        assert issubclass(PypiSiteFlat, PypiSite)
        assert isinstance(PypiSiteFlat(), HtmlGen)

    def test_generic_methods(self):
        gen = PypiSiteFlat()
        with gen.Div(class_='card'):
            gen.P('x')
        assert gen.generate_str() == """\
<div class="card">
  <p>x</p>
</div>
"""

    def test_wheels_of_the_pip_page(self):
        """The flat generator still writes the pages pip reads."""
        gen = PypiSiteFlat()
        with gen.PypiHeader('httpx2'):
            gen.Wheels([WheelLink('httpx2', '2.13.1', FILE_PURE)])
        assert f'<a href="2.13.1/{FILE_PURE}">{FILE_PURE}</a><br>' in gen.generate_str()

    def test_indent_size(self):
        gen = PypiSiteFlat(indent_size=4)
        with gen.WheelName('anyio'):
            gen.WheelTablePurePython([WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl')])
        assert gen.generate_str().startswith("""\
<section class="project" id="anyio">
    <h2>anyio <span class="badge pure">pure python</span>""")


class TestPageIsStable:
    def test_render_twice(self):
        """Rendering does not change the page."""
        gen = PypiSiteFlat()
        with gen.WheelName('anyio'):
            gen.WheelTablePurePython([WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl')])
        assert gen.generate_str() == gen.generate_str()

    def test_index_render_twice(self):
        """The index is filled once, a second render does not repeat the lines."""
        gen = PypiSiteFlat()
        gen.inject_index()
        with gen.WheelName('anyio'):
            gen.WheelTablePurePython([WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl')])
        assert gen.generate_str() == gen.generate_str()

    def test_links_in_any_order(self):
        """The matrix does not depend on the order the run read the wheels in."""
        one = [
            WheelLink('httpx2', '2.13.1', FILE_WIN, DIGEST_A),
            WheelLink('httpx2', '2.13.1', FILE_PURE, DIGEST_B),
        ]
        assert page_of(one) == page_of(list(reversed(one)))

    def test_write(self, fs):
        gen = PypiSiteFlat()
        with gen.WheelName('anyio'):
            gen.WheelTablePurePython([
                WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl'),
                WheelLink('anyio', '4.14.2', 'anyio-4.14.2-py3-none-any.whl'),
            ])
        assert gen.write('/wheel/overview.html') is True
        with open('/wheel/overview.html', encoding='utf-8') as f:
            assert f.read() == PURE_SECTION

    def test_write_of_an_unchanged_page(self, fs):
        """A rerun of the page writes nothing, the record of the publish stays as it was."""
        gen = PypiSiteFlat()
        with gen.WheelName('anyio'):
            gen.WheelTablePurePython([WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl')])
        assert gen.write('/wheel/overview.html') is True
        assert gen.write('/wheel/overview.html') is False
