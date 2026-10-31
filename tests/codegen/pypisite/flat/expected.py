"""
The pages the unit tests of a flat page expect, written out in full.

A page is asserted line by line and not by fragments: an indentation, a class
or the place of a blank row shows up in the expected text, and a failure reads
as the page itself. The two constants here are shared by the tests of the
objects and by the tests of the generator, which build the same section.
"""
from alasio.codegen.pypisite.flat import PypiSiteFlat

from .data import FILE_MAC, FILE_PURE, FILE_WIN

__all__ = [
    'COMPILED_SECTION',
    'PURE_SECTION',
    'WHEELS_PAGE',
    'page_of',
]

# the section of one compiled version, the shape a reader of the page sees
COMPILED_SECTION = f"""\
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
          <tr><th scope="row"><span class="plat">win_amd64</span></th><td class="ok"><a href="httpx2/2.13.1/{FILE_WIN}" title="{FILE_WIN}">cp38</a></td></tr>
        </tbody>
      </table>
    </div>
  </section>
</section>
"""

# the section of one project of pure python wheels: the versions are the rows,
# and the cell of a version carries the anchor of the row
PURE_SECTION = """\
<section class="project" id="anyio">
  <h2>anyio <span class="badge pure">pure python</span> <a class="anchor" href="#anyio" aria-label="link to anyio">#</a></h2>
  <p class="meta">2 versions · 2 wheels</p>
  <div class="table-wrap">
    <table class="matrix pure">
      <thead>
        <tr><th scope="col">version</th><th scope="col">file</th></tr>
      </thead>
      <tbody>
        <tr><th scope="row" id="anyio-4.15.1">4.15.1 <a class="anchor" href="#anyio-4.15.1" aria-label="link to anyio-4.15.1">#</a></th><td><a href="anyio/4.15.1/anyio-4.15.1-py3-none-any.whl" title="anyio-4.15.1-py3-none-any.whl">anyio-4.15.1-py3-none-any.whl</a></td></tr>
        <tr><th scope="row" id="anyio-4.14.2">4.14.2 <a class="anchor" href="#anyio-4.14.2" aria-label="link to anyio-4.14.2">#</a></th><td><a href="anyio/4.14.2/anyio-4.14.2-py3-none-any.whl" title="anyio-4.14.2-py3-none-any.whl">anyio-4.14.2-py3-none-any.whl</a></td></tr>
      </tbody>
    </table>
  </div>
</section>
"""

# the whole page of one compiled project, the shape the caller writes
WHEELS_PAGE = f"""\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>Wheels of Author_Repo_master</title>
  </head>
  <body>
    <h1>Wheels of Author_Repo_master</h1>
    <nav id="contents" aria-label="Contents">
      <h2>Contents</h2>
      <ul class="toc">
        <li><a href="#httpx2">httpx2</a> <span class="badge compiled">compiled</span> <span class="versions"><a href="#httpx2-2.13.1">2.13.1</a></span></li>
      </ul>
    </nav>
    <section class="project" id="httpx2">
      <h2>httpx2 <span class="badge compiled">compiled</span> <a class="anchor" href="#httpx2" aria-label="link to httpx2">#</a></h2>
      <section class="version" id="httpx2-2.13.1">
        <h3>2.13.1 <span class="badge compiled">compiled</span> <a class="anchor" href="#httpx2-2.13.1" aria-label="link to httpx2-2.13.1">#</a></h3>
        <p class="meta">3 wheels · 2 platforms · 2 Python tags</p>
        <div class="table-wrap">
          <table class="matrix">
            <thead>
              <tr><th scope="col"><span class="platform-head">platform</span></th><th scope="col">cp38</th><th scope="col">cp39</th></tr>
            </thead>
            <tbody>
              <tr><th scope="row"><span class="plat">macosx_11_0_arm64</span></th><td class="na"></td><td class="ok"><a href="httpx2/2.13.1/{FILE_MAC}" title="{FILE_MAC}">cp39</a></td></tr>
              <tr><th scope="row"><span class="plat">win_amd64</span></th><td class="ok"><a href="httpx2/2.13.1/{FILE_WIN}" title="{FILE_WIN}">cp38</a></td><td class="na"></td></tr>
              <tr><th scope="row"><span class="plat">any</span></th><td class="pure" colspan="2"><a href="httpx2/2.13.1/{FILE_PURE}" title="{FILE_PURE}">py3-none-any</a></td></tr>
            </tbody>
          </table>
        </div>
      </section>
    </section>
  </body>
</html>
"""


def page_of(links, title='Wheels of Author_Repo_master'):
    """
    Write the page of one compiled project, the way a caller composes it.

    Args:
        links (list[WheelLink]): The wheels of the version
        title (str): Title of the page

    Returns:
        str: The page
    """
    gen = PypiSiteFlat()
    with gen.Doc(title) as page:
        with page.body():
            gen.H1(title)
            gen.inject_index()
            with gen.WheelName('httpx2'):
                with gen.WheelVersion('2.13.1'):
                    gen.WheelTableCompiled(links)
    return gen.generate_str()
