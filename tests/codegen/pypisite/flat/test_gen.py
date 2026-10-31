"""
Tests for the generator of a flat page: the page a caller composes, the generic
methods it inherits, and the promises a page keeps between two runs.
"""
from alasio.codegen.html import Element, HtmlGen, Raw
from alasio.codegen.pypisite import PypiSite, WheelLink
from alasio.codegen.pypisite.flat import FLAT_STYLE, PypiSiteFlat
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
