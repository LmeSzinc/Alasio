"""
Tests for the objects of a flat page: the sections of a project and a version,
the two tables and the index.
"""
import pytest

from alasio.codegen.pypisite import WheelLink
from alasio.codegen.pypisite.flat import FlatProject, FlatVersion, PypiSiteFlat

from .data import DIGEST_A, FILE_MAC, FILE_PURE, FILE_WIN
from .expected import COMPILED_SECTION, PURE_SECTION


class TestWheelName:
    def test_section(self):
        gen = PypiSiteFlat()
        with gen.WheelName('httpx2'):
            pass
        assert gen.generate_str() == """\
<section class="project" id="httpx2">
  <h2>httpx2 <a class="anchor" href="#httpx2" aria-label="link to httpx2">#</a></h2>
</section>
"""

    def test_badge_of_a_compiled_project(self):
        gen = PypiSiteFlat()
        with gen.WheelName('httpx2'):
            with gen.WheelVersion('2.13.1'):
                gen.WheelTableCompiled([WheelLink('httpx2', '2.13.1', FILE_WIN)])
        assert gen.generate_str() == COMPILED_SECTION

    def test_badge_of_a_pure_project(self):
        gen = PypiSiteFlat()
        with gen.WheelName('anyio'):
            gen.WheelTablePurePython([
                WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl'),
                WheelLink('anyio', '4.14.2', 'anyio-4.14.2-py3-none-any.whl'),
            ])
        assert gen.generate_str() == PURE_SECTION

    def test_badge_text_is_not_the_class(self):
        """The class of a badge is the kind, the words of it are what a reader reads."""
        gen = PypiSiteFlat()
        with gen.WheelName('anyio'):
            gen.WheelTablePurePython([WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl')])
        page = gen.generate_str()
        assert '<span class="badge pure">pure python</span>' in page
        assert 'badge pure python' not in page

    def test_badge_of_a_mixed_project(self):
        """A project is compiled when any of its versions is, whatever comes last."""
        gen = PypiSiteFlat()
        with gen.WheelName('anyio'):
            gen.WheelTablePurePython([WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl')])
            with gen.WheelVersion('4.14.2'):
                gen.WheelTableCompiled([WheelLink('anyio', '4.14.2', 'anyio-4.14.2-cp38-cp38-win_amd64.whl')])
        page = gen.generate_str()
        assert '<h2>anyio <span class="badge compiled">compiled</span>' in page

    def test_name_normalized(self):
        gen = PypiSiteFlat()
        with gen.WheelName('HTTPX2'):
            pass
        page = gen.generate_str()
        assert '<section class="project" id="httpx2">' in page
        assert '<a class="anchor" href="#httpx2" aria-label="link to httpx2">#</a>' in page

    @pytest.mark.parametrize('name', ['', '../httpx2', 'httpx2/2', 'a b', '-httpx2'])
    def test_invalid_name(self, name):
        with pytest.raises(ValueError, match='not a distribution name'):
            PypiSiteFlat().WheelName(name)

    def test_context_restored(self):
        gen = PypiSiteFlat()
        with gen.WheelName('httpx2'):
            assert gen.flat_project is not None
        assert gen.flat_project is None

    def test_context_restored_on_error(self):
        gen = PypiSiteFlat()
        with pytest.raises(RuntimeError):
            with gen.WheelName('httpx2'):
                raise RuntimeError('half a page')
        assert gen.flat_project is None
        assert gen.context is None
        assert gen.indent == 0

    def test_same_project_twice(self):
        """Two sections of one project are one line of the index, one badge."""
        gen = PypiSiteFlat()
        gen.inject_index()
        with gen.WheelName('httpx2'):
            with gen.WheelVersion('2.13.1'):
                gen.WheelTableCompiled([WheelLink('httpx2', '2.13.1', FILE_WIN)])
        with gen.WheelName('httpx2'):
            with gen.WheelVersion('2.9.0'):
                gen.WheelTableCompiled([WheelLink('httpx2', '2.9.0', 'httpx2-2.9.0-cp38-cp38-win_amd64.whl')])
        assert len(gen.flat) == 1
        page = gen.generate_str()
        assert page.count('<a href="#httpx2">httpx2</a>') == 1
        assert page.count('<section class="project" id="httpx2">') == 2

    def test_is_a_container(self):
        gen = PypiSiteFlat()
        project = gen.WheelName('httpx2')
        assert isinstance(project, FlatProject)
        assert gen.items == [project]


class TestWheelVersion:
    def test_origin(self):
        gen = PypiSiteFlat()
        with gen.WheelName('av'):
            with gen.WheelVersion('18.1.0', origin=('20261019-PyAV==18.1.0', 'https://example.com/tag')):
                gen.WheelTableCompiled([WheelLink('av', '18.1.0', 'av-18.1.0-cp38-cp38-win_amd64.whl')])
        assert (
            '<h3>18.1.0 <span class="badge compiled">compiled</span> <a class="release-tag" '
            'href="https://example.com/tag" title="release 20261019-PyAV==18.1.0">'
            '20261019-PyAV==18.1.0</a> ') in gen.generate_str()

    def test_no_origin(self):
        gen = PypiSiteFlat()
        with gen.WheelName('httpx2'):
            with gen.WheelVersion('2.13.1'):
                gen.WheelTableCompiled([WheelLink('httpx2', '2.13.1', FILE_WIN)])
        assert 'release-tag' not in gen.generate_str()

    def test_section_id(self):
        gen = PypiSiteFlat()
        with gen.WheelName('httpx2'):
            with gen.WheelVersion('2.13.1'):
                gen.WheelTableCompiled([WheelLink('httpx2', '2.13.1', FILE_WIN)])
        page = gen.generate_str()
        assert '<section class="version" id="httpx2-2.13.1">' in page
        assert '<a class="anchor" href="#httpx2-2.13.1" aria-label="link to httpx2-2.13.1">#</a>' in page

    def test_outside_a_project(self):
        with pytest.raises(ValueError, match='outside the section of its project'):
            PypiSiteFlat().WheelVersion('2.13.1')

    def test_empty_version(self):
        gen = PypiSiteFlat()
        with gen.WheelName('httpx2'):
            with pytest.raises(ValueError, match='not a text'):
                gen.WheelVersion('')

    def test_bad_origin(self):
        gen = PypiSiteFlat()
        with gen.WheelName('httpx2'):
            with pytest.raises(ValueError, match=r'\(text, url\) pair'):
                gen.WheelVersion('2.13.1', origin='a-tag')

    def test_without_table(self):
        """A version section is the heading of a table, a section of nothing is a typo."""
        gen = PypiSiteFlat()
        with pytest.raises(ValueError, match='has no table'):
            with gen.WheelName('httpx2'):
                with gen.WheelVersion('2.13.1'):
                    pass

    def test_without_table_on_error(self):
        """An error in the block keeps its own message, the table check does not mask it."""
        gen = PypiSiteFlat()
        with pytest.raises(RuntimeError, match='half a page'):
            with gen.WheelName('httpx2'):
                with gen.WheelVersion('2.13.1'):
                    raise RuntimeError('half a page')

    def test_two_tables(self):
        gen = PypiSiteFlat()
        with gen.WheelName('httpx2'):
            with gen.WheelVersion('2.13.1'):
                gen.WheelTableCompiled([WheelLink('httpx2', '2.13.1', FILE_WIN)])
                with pytest.raises(ValueError, match='already has a table'):
                    gen.WheelTableCompiled([WheelLink('httpx2', '2.13.1', FILE_WIN)])

    def test_same_version_twice(self):
        gen = PypiSiteFlat()
        with gen.WheelName('httpx2'):
            with gen.WheelVersion('2.13.1'):
                gen.WheelTableCompiled([WheelLink('httpx2', '2.13.1', FILE_WIN)])
            with pytest.raises(ValueError, match='already has its table'):
                gen.WheelVersion('2.13.1')

    def test_is_a_container(self):
        gen = PypiSiteFlat()
        with gen.WheelName('httpx2'):
            with gen.WheelVersion('2.13.1') as version:
                gen.WheelTableCompiled([WheelLink('httpx2', '2.13.1', FILE_WIN)])
        assert isinstance(version, FlatVersion)
        assert version.anchor == 'httpx2-2.13.1'


class TestWheelTableCompiled:
    def test_table(self):
        gen = PypiSiteFlat()
        with gen.WheelName('httpx2'):
            with gen.WheelVersion('2.13.1'):
                gen.WheelTableCompiled([WheelLink('httpx2', '2.13.1', FILE_WIN)])
        assert gen.generate_str() == COMPILED_SECTION

    def test_meta_counts_the_input(self):
        gen = PypiSiteFlat()
        with gen.WheelName('httpx2'):
            with gen.WheelVersion('2.13.1'):
                gen.WheelTableCompiled([
                    WheelLink('httpx2', '2.13.1', FILE_WIN),
                    WheelLink('httpx2', '2.13.1', FILE_MAC),
                    WheelLink('httpx2', '2.13.1', FILE_PURE),
                ])
        assert '<p class="meta">3 wheels · 2 platforms · 2 Python tags</p>' in gen.generate_str()

    def test_pure_row(self):
        """The wheels of pure python are the last row, merged across the columns."""
        gen = PypiSiteFlat()
        with gen.WheelName('httpx2'):
            with gen.WheelVersion('2.13.1'):
                gen.WheelTableCompiled([
                    WheelLink('httpx2', '2.13.1', FILE_WIN),
                    WheelLink('httpx2', '2.13.1', FILE_MAC),
                    WheelLink('httpx2', '2.13.1', FILE_PURE),
                ])
        assert (
            '          <tr><th scope="row"><span class="plat">any</span></th>'
            '<td class="pure" colspan="2">'
            f'<a href="httpx2/2.13.1/{FILE_PURE}" title="{FILE_PURE}">py3-none-any</a>'
            '</td></tr>') in list(gen.generate())

    def test_pure_row_last(self):
        gen = PypiSiteFlat()
        with gen.WheelName('httpx2'):
            with gen.WheelVersion('2.13.1'):
                gen.WheelTableCompiled([
                    WheelLink('httpx2', '2.13.1', FILE_PURE),
                    WheelLink('httpx2', '2.13.1', FILE_WIN),
                ])
        page = gen.generate_str()
        assert page.index('win_amd64') < page.index('<span class="plat">any</span>')

    def test_two_pure_files_one_cell(self):
        gen = PypiSiteFlat()
        with gen.WheelName('anyio'):
            with gen.WheelVersion('4.15.1'):
                gen.WheelTableCompiled([
                    WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py2.py3-none-any.whl'),
                    WheelLink('anyio', '4.15.1', 'anyio-4.15.1-cp38-cp38-win_amd64.whl'),
                ])
        assert (
            '<td class="pure" colspan="1">'
            '<a href="anyio/4.15.1/anyio-4.15.1-py2.py3-none-any.whl" '
            'title="anyio-4.15.1-py2.py3-none-any.whl">py2-none-any, py3-none-any</a>'
            '</td>') in gen.generate_str()

    def test_abi3_mark_and_note(self):
        gen = PypiSiteFlat()
        with gen.WheelName('av'):
            with gen.WheelVersion('18.1.0'):
                gen.WheelTableCompiled([WheelLink('av', '18.1.0', 'av-18.1.0-cp38-abi3-win_amd64.whl')])
        page = gen.generate_str()
        assert '>cp38<span class="abi3">abi3</span></a>' in page
        # the note names the mark with the same span the cell carries, so it has
        # the style of it and not the style of the text around it
        assert (
            '<p class="note">Cells marked <span class="abi3">abi3</span> are stable-ABI wheels: '
            'they install on the stated Python version and every newer one.</p>') in page

    def test_no_abi3_note(self):
        gen = PypiSiteFlat()
        with gen.WheelName('httpx2'):
            with gen.WheelVersion('2.13.1'):
                gen.WheelTableCompiled([WheelLink('httpx2', '2.13.1', FILE_WIN)])
        assert 'class="note"' not in gen.generate_str()

    def test_digest_in_the_title_and_the_link(self):
        gen = PypiSiteFlat()
        with gen.WheelName('httpx2'):
            with gen.WheelVersion('2.13.1'):
                gen.WheelTableCompiled([WheelLink('httpx2', '2.13.1', FILE_WIN, DIGEST_A)])
        page = gen.generate_str()
        assert f'href="httpx2/2.13.1/{FILE_WIN}#sha256={DIGEST_A}"' in page
        assert f'title="{FILE_WIN} · sha256: {DIGEST_A}"' in page

    def test_outside_a_version(self):
        gen = PypiSiteFlat()
        with gen.WheelName('httpx2'):
            with pytest.raises(ValueError, match='belongs to the section of a version'):
                gen.WheelTableCompiled([WheelLink('httpx2', '2.13.1', FILE_WIN)])

    def test_wheel_of_another_version(self):
        gen = PypiSiteFlat()
        with gen.WheelName('httpx2'):
            with pytest.raises(ValueError, match='another version'):
                with gen.WheelVersion('2.13.1'):
                    gen.WheelTableCompiled([
                        WheelLink('httpx2', '2.9.0', 'httpx2-2.9.0-cp38-cp38-win_amd64.whl')])

    def test_wheel_of_another_distribution(self):
        gen = PypiSiteFlat()
        with gen.WheelName('httpx2'):
            with pytest.raises(ValueError, match='another distribution'):
                with gen.WheelVersion('2.13.1'):
                    gen.WheelTableCompiled([
                        WheelLink('msgspec', '2.13.1', 'msgspec-2.13.1-cp38-cp38-win_amd64.whl')])

    def test_all_pure(self):
        gen = PypiSiteFlat()
        with gen.WheelName('anyio'):
            with pytest.raises(ValueError, match='WheelTablePurePython'):
                with gen.WheelVersion('4.15.1'):
                    gen.WheelTableCompiled([WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl')])


class TestWheelTablePurePython:
    def test_table(self):
        gen = PypiSiteFlat()
        with gen.WheelName('anyio'):
            gen.WheelTablePurePython([
                WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl'),
                WheelLink('anyio', '4.14.2', 'anyio-4.14.2-py3-none-any.whl'),
            ])
        assert gen.generate_str() == PURE_SECTION

    def test_rows(self):
        gen = PypiSiteFlat()
        with gen.WheelName('anyio'):
            item = gen.WheelTablePurePython([
                WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl'),
                WheelLink('anyio', '4.14.2', 'anyio-4.14.2-py3-none-any.whl'),
            ])
        assert [version for version, _group in item.rows] == ['4.15.1', '4.14.2']

    def test_files_of_one_version(self):
        gen = PypiSiteFlat()
        with gen.WheelName('anyio'):
            gen.WheelTablePurePython([
                WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py2.py3-none-any.whl'),
                WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl'),
            ])
        page = gen.generate_str()
        assert '<p class="meta">1 version · 2 wheels</p>' in page
        assert (
            '<td><a href="anyio/4.15.1/anyio-4.15.1-py2.py3-none-any.whl" '
            'title="anyio-4.15.1-py2.py3-none-any.whl">anyio-4.15.1-py2.py3-none-any.whl</a><br>'
            '<a href="anyio/4.15.1/anyio-4.15.1-py3-none-any.whl" '
            'title="anyio-4.15.1-py3-none-any.whl">anyio-4.15.1-py3-none-any.whl</a></td>') in page

    def test_version_cell(self):
        """The cell of a version is the anchor of the row: its number and a hash."""
        gen = PypiSiteFlat()
        with gen.WheelName('anyio'):
            gen.WheelTablePurePython([WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl')])
        assert (
            '<tr><th scope="row" id="anyio-4.15.1">4.15.1 <a class="anchor" href="#anyio-4.15.1" '
            'aria-label="link to anyio-4.15.1">#</a></th>') in gen.generate_str()

    def test_origin_of_a_version(self):
        """A row names the release of its version, the way a version section does."""
        gen = PypiSiteFlat()
        with gen.WheelName('h2'):
            gen.WheelTablePurePython(
                [WheelLink('h2', '4.4.1', 'h2-4.4.1-py3-none-any.whl')],
                origins={'4.4.1': ('2026xxxx-h2==4.4.1', 'https://example.com/4.4.1')},
            )
        assert (
            '<th scope="row" id="h2-4.4.1">4.4.1 <a class="release-tag" href="https://example.com/4.4.1" '
            'title="release 2026xxxx-h2==4.4.1">2026xxxx-h2==4.4.1</a> '
            '<a class="anchor" href="#h2-4.4.1"') in gen.generate_str()

    def test_no_origin(self):
        """A version the caller has no source for is the version and the hash alone."""
        gen = PypiSiteFlat()
        with gen.WheelName('anyio'):
            gen.WheelTablePurePython(
                [WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl')],
                origins={},
            )
        assert 'release-tag' not in gen.generate_str()

    def test_origin_of_one_version_of_two(self):
        """A source the caller knows for one version does not invent one for the other."""
        gen = PypiSiteFlat()
        with gen.WheelName('h2'):
            gen.WheelTablePurePython(
                [
                    WheelLink('h2', '4.4.1', 'h2-4.4.1-py3-none-any.whl'),
                    WheelLink('h2', '4.3.0', 'h2-4.3.0-py3-none-any.whl'),
                ],
                origins={'4.4.1': ('tag-4.4.1', 'https://example.com/4.4.1')},
            )
        page = gen.generate_str()
        assert page.count('class="release-tag"') == 1
        assert '<th scope="row" id="h2-4.3.0">4.3.0 <a class="anchor"' in page

    def test_origin_of_an_unknown_version(self):
        """A misspelled version would drop a source and look like a site without one."""
        gen = PypiSiteFlat()
        with gen.WheelName('anyio'), pytest.raises(ValueError, match='of no version of it'):
            gen.WheelTablePurePython(
                [WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl')],
                origins={'4.15': ('tag', 'https://example.com')},
            )

    def test_origin_not_a_pair(self):
        gen = PypiSiteFlat()
        with gen.WheelName('anyio'), pytest.raises(ValueError, match=r'\(text, url\) pair'):
            gen.WheelTablePurePython(
                [WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl')],
                origins={'4.15.1': '2026xxxx-anyio==4.15.1'},
            )

    def test_origin_without_a_text_or_url(self):
        gen = PypiSiteFlat()
        with gen.WheelName('anyio'), pytest.raises(ValueError, match='is not a text'):
            gen.WheelTablePurePython(
                [WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl')],
                origins={'4.15.1': ('tag', '')},
            )

    def test_origins_not_a_mapping(self):
        gen = PypiSiteFlat()
        with gen.WheelName('anyio'), pytest.raises(TypeError, match='mapping'):
            gen.WheelTablePurePython(
                [WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl')],
                origins=[('4.15.1', ('tag', 'https://example.com'))],
            )

    def test_empty(self):
        gen = PypiSiteFlat()
        with gen.WheelName('anyio'):
            gen.WheelTablePurePython([])
        page = gen.generate_str()
        assert '<p class="meta">0 versions · 0 wheels</p>' in page
        assert '<tbody>' in page

    def test_platform_wheel(self):
        gen = PypiSiteFlat()
        with gen.WheelName('httpx2'):
            with pytest.raises(ValueError, match='wheel of a platform'):
                gen.WheelTablePurePython([WheelLink('httpx2', '2.13.1', FILE_WIN)])

    def test_outside_a_project(self):
        gen = PypiSiteFlat()
        with pytest.raises(ValueError, match='belongs to the section of a project'):
            gen.WheelTablePurePython([WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl')])

    def test_inside_a_version(self):
        gen = PypiSiteFlat()
        with gen.WheelName('anyio'):
            with pytest.raises(ValueError, match='outside the section of a version'):
                with gen.WheelVersion('4.15.1'):
                    gen.WheelTablePurePython([
                        WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl')])

    def test_version_with_a_section(self):
        """A version with its own section cannot also be a row of the pure table."""
        gen = PypiSiteFlat()
        with gen.WheelName('anyio'):
            with gen.WheelVersion('4.14.2'):
                gen.WheelTableCompiled([
                    WheelLink('anyio', '4.14.2', 'anyio-4.14.2-cp38-cp38-win_amd64.whl')])
            with pytest.raises(ValueError, match='already has its table'):
                gen.WheelTablePurePython([WheelLink('anyio', '4.14.2', 'anyio-4.14.2-py3-none-any.whl')])

    def test_wheel_of_another_distribution(self):
        gen = PypiSiteFlat()
        with gen.WheelName('anyio'):
            with pytest.raises(ValueError, match='another distribution'):
                gen.WheelTablePurePython([WheelLink('httpx2', '2.13.1', FILE_PURE)])


class TestIndex:
    def test_index_before_the_sections(self):
        """The index is written first and filled when the page renders."""
        gen = PypiSiteFlat()
        gen.inject_index()
        with gen.WheelName('httpx2'):
            with gen.WheelVersion('2.13.1'):
                gen.WheelTableCompiled([WheelLink('httpx2', '2.13.1', FILE_WIN)])
        assert gen.generate_str() == """\
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
"""

    def test_projects_in_the_order_of_the_page(self):
        gen = PypiSiteFlat()
        gen.inject_index()
        with gen.WheelName('msgspec'):
            with gen.WheelVersion('0.22.0'):
                gen.WheelTableCompiled([
                    WheelLink('msgspec', '0.22.0', 'msgspec-0.22.0-cp38-cp38-win_amd64.whl')])
        with gen.WheelName('anyio'):
            gen.WheelTablePurePython([WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl')])
        page = gen.generate_str()
        assert page.index('<a href="#msgspec">') < page.index('<a href="#anyio">')

    def test_pure_project_links_its_versions(self):
        """Every row of a pure table carries the anchor the index links to."""
        gen = PypiSiteFlat()
        gen.inject_index()
        with gen.WheelName('anyio'):
            gen.WheelTablePurePython([
                WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl'),
                WheelLink('anyio', '4.14.2', 'anyio-4.14.2-py3-none-any.whl'),
            ])
        page = gen.generate_str()
        assert (
            '<li><a href="#anyio">anyio</a> <span class="badge pure">pure python</span> '
            '<span class="versions"><a href="#anyio-4.15.1">4.15.1</a> · '
            '<a href="#anyio-4.14.2">4.14.2</a></span></li>') in page
        assert '<th scope="row" id="anyio-4.15.1">4.15.1 ' in page

    def test_one_version(self):
        gen = PypiSiteFlat()
        gen.inject_index()
        with gen.WheelName('anyio'):
            gen.WheelTablePurePython([WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl')])
        assert '<span class="versions"><a href="#anyio-4.15.1">4.15.1</a></span>' in gen.generate_str()

    def test_mixed_project(self):
        """A project of both kinds links the section and the row the same way."""
        gen = PypiSiteFlat()
        gen.inject_index()
        with gen.WheelName('anyio'):
            with gen.WheelVersion('4.14.2'):
                gen.WheelTableCompiled([
                    WheelLink('anyio', '4.14.2', 'anyio-4.14.2-cp38-cp38-win_amd64.whl')])
            gen.WheelTablePurePython([WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl')])
        page = gen.generate_str()
        assert (
            '<span class="versions"><a href="#anyio-4.14.2">4.14.2</a> · '
            '<a href="#anyio-4.15.1">4.15.1</a></span>') in page

    def test_title(self):
        gen = PypiSiteFlat()
        gen.inject_index('Projects')
        page = gen.generate_str()
        assert '<nav id="contents" aria-label="Projects">' in page
        assert '<h2>Projects</h2>' in page

    def test_title_escaped(self):
        gen = PypiSiteFlat()
        gen.inject_index('a & <b>')
        page = gen.generate_str()
        assert 'aria-label="a &amp; &lt;b&gt;"' in page
        assert '<h2>a &amp; &lt;b&gt;</h2>' in page

    def test_into_a_body(self):
        gen = PypiSiteFlat()
        with gen.Doc('Wheels') as page:
            with page.body():
                gen.inject_index()
                with gen.WheelName('anyio'):
                    gen.WheelTablePurePython([
                        WheelLink('anyio', '4.15.1', 'anyio-4.15.1-py3-none-any.whl')])
        assert '    <nav id="contents" aria-label="Contents">\n      <h2>Contents</h2>' in gen.generate_str()
