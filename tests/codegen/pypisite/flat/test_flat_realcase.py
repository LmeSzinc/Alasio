"""
The flat page of a folder of wheels, built from the page py38deps published.

The data is the wheel list of https://py38deps.github.io/py38deps/flat/ on
2026-11-01, kept in `py38deps-flat.txt`: 29 projects, 46 versions, 1476 wheels,
the digest of every one of them and the release each version came from. The
page the module writes is kept in `py38deps-flat.html` and compared byte by
byte.

The data is the reason the test exists: a page of a real folder is where the
platforms of a matrix, the compressed platform sets, the stable abi cells and
the fallback wheels of pure python all show up at once, in the names a package
index really publishes.

The golden page does not change on its own. When a change of the page is
intended, delete the file, run the test to write it again, read the diff and
commit it.
"""
from alasio.codegen.pypisite.flat import flat_page
from tests.codegen.pypisite import fixture

# the folder the fixture and the golden page of this test live in
HERE = fixture.HERE.joinpath('flat')

# the wheels of the folder, one record a line
DATA = HERE.joinpath('py38deps-flat.txt')

# the page of the folder, as the generator writes it
GOLDEN = HERE.joinpath('py38deps-flat.html')

# the title of the page, the one the site gives it
TITLE = 'py38deps wheels'

# the command a reader of the page copies
COMMAND = 'pip install --no-index --find-links https://py38deps.github.io/py38deps/flat/ <package>'

# the projects, the versions and the wheels of the fixture
PROJECTS = 29
VERSIONS = 46
WHEELS = 1476

# the projects whose every version is a wheel of pure python, and the ones that
# ship a compiled extension
PURE_PROJECTS = 19
COMPILED_PROJECTS = 10

# a version of a project of pure python wheels is a row of its table, a version
# of a compiled project is the section of a matrix
PURE_VERSIONS = 32
COMPILED_VERSIONS = 14


def group_by_project(entries):
    """
    Group the versions of the fixture by the project they belong to.

    Args:
        entries (list[fixture.Entry]): The versions of the fixture

    Returns:
        dict[str, list[fixture.Entry]]: The versions of each project, the
            projects sorted by name, the versions in the order of the fixture
    """
    projects = {}
    for entry in entries:
        projects.setdefault(entry.project, []).append(entry)
    return {name: projects[name] for name in sorted(projects)}


def build_page():
    """
    Build the page of the folder from the wheels of the fixture.

    Every project is the wheels of it, any versions: flat_page groups them, a
    section a compiled version and the table of the versions of pure python, so
    the page of the site is the page of the module and nothing of the layout of
    it lives in this test.

    Returns:
        str: The page, the shape a reader of the folder opens
    """
    projects = {}
    origins = {}
    for name, entries in group_by_project(fixture.read_entries(DATA)).items():
        projects[name] = [link for entry in entries for link in entry.links]
        origins[name] = {entry.version: entry.origin for entry in entries}
    return flat_page(projects, TITLE, command=COMMAND, origins=origins)


def read_golden():
    """
    Read the page committed with the test.

    Returns:
        str: The page, LF line endings
    """
    with open(GOLDEN, encoding='utf-8', newline='') as f:
        return f.read()


class TestFixture:
    def test_projects(self):
        assert len(group_by_project(fixture.read_entries(DATA))) == PROJECTS

    def test_versions(self):
        assert len(fixture.read_entries(DATA)) == VERSIONS

    def test_wheels(self):
        assert sum(len(entry.files) for entry in fixture.read_entries(DATA)) == WHEELS

    def test_every_version_has_an_origin(self):
        """The site publishes every version from a release of its own."""
        for entry in fixture.read_entries(DATA):
            text, url = entry.origin
            assert text
            assert url.startswith('https://github.com/py38deps/py38deps/releases/tag/')

    def test_pure_and_compiled_projects(self):
        projects = group_by_project(fixture.read_entries(DATA))
        pure = [name for name, versions in projects.items() if all(entry.pure for entry in versions)]
        assert len(pure) == PURE_PROJECTS
        assert len(projects) - len(pure) == COMPILED_PROJECTS

    def test_pure_and_compiled_versions(self):
        entries = fixture.read_entries(DATA)
        assert sum(1 for entry in entries if entry.pure) == PURE_VERSIONS
        assert sum(1 for entry in entries if not entry.pure) == COMPILED_VERSIONS


class TestPage:
    def test_page_is_the_golden(self):
        """The page of the real folder, byte by byte."""
        fixture.assert_page(build_page(), read_golden())

    def test_page_is_deterministic(self):
        assert build_page() == build_page()

    def test_golden_holds_lf(self):
        """The page is compared byte by byte, so the committed file stays LF."""
        assert '\r' not in read_golden()

    def test_projects_and_versions(self):
        page = build_page()
        assert page.count('<section class="project"') == PROJECTS
        # the versions of a project of pure python wheels are rows of its table
        assert page.count('<section class="version"') == COMPILED_VERSIONS

    def test_every_wheel_is_linked_once(self):
        """A wheel of the folder is a link of the page, with the digest pip verifies."""
        page = build_page()
        assert page.count('#sha256=') == WHEELS
        assert page.count(' · sha256: ') == WHEELS

    def test_anchors(self):
        """Every version of the page carries an anchor, whichever table names it."""
        page = build_page()
        assert page.count('class="anchor"') == PROJECTS + VERSIONS

    def test_release_tags(self):
        """Every version names the release it was published in, pure or compiled."""
        page = build_page()
        assert page.count('class="release-tag"') == VERSIONS
        entry = next(one for one in fixture.read_entries(DATA) if one.project == 'h2')
        text, url = entry.origin
        assert (
            f'<th scope="row" id="h2-{entry.version}">{entry.version} '
            f'<a class="release-tag" href="{url}" title="release {text}">{text}</a> '
            f'<a class="anchor" href="#h2-{entry.version}"') in page

    def test_badges(self):
        """A badge sits in the heading of a section and in the line of the index."""
        page = build_page()
        assert page.count('<span class="badge pure">') == PURE_PROJECTS * 2
        assert page.count('<span class="badge compiled">') == COMPILED_PROJECTS * 2 + COMPILED_VERSIONS

    def test_matrix_of_a_version(self):
        """av ships 46 wheels of 10 platforms and 5 python tags."""
        page = build_page()
        assert '<p class="meta">46 wheels · 10 platforms · 5 Python tags</p>' in page

    def test_stable_abi_note(self):
        """A matrix that holds a stable abi wheel says what the mark means."""
        page = build_page()
        assert 'class="abi3"' in page
        assert (
            'Cells marked <span class="abi3">abi3</span> are stable-ABI wheels: they install on '
            'the stated Python version and every newer one.') in page

    def test_pure_fallback_of_a_compiled_version(self):
        """A version that ships a wheel of pure python shows it as the row of any."""
        page = build_page()
        assert '<span class="plat">any</span>' in page
        assert 'yarl-1.25.1-py3-none-any.whl' in page

    def test_anchor_of_a_project_and_a_version(self):
        page = build_page()
        assert '<section class="project" id="markupsafe">' in page
        assert '<section class="version" id="markupsafe-3.0.4">' in page
        assert '<a href="#markupsafe-3.0.4">3.0.4</a>' in page

    def test_index_lists_every_project(self):
        page = build_page()
        toc = page[page.index('<nav id="contents"'):page.index('</nav>')]
        for name in group_by_project(fixture.read_entries(DATA)):
            assert f'<a href="#{name}">{name}</a>' in toc

    def test_a_pure_project_links_its_versions(self):
        """The row of a version of pure python is the anchor the index links to."""
        page = build_page()
        assert (
            '<th scope="row" id="starlette-1.10.0">1.10.0 <a class="release-tag" '
            'href="https://github.com/py38deps/py38deps/releases/tag/20261026-starlette%3D%3D1.10.0" '
            'title="release 20261026-starlette==1.10.0">20261026-starlette==1.10.0</a> '
            '<a class="anchor" href="#starlette-1.10.0"') in page
        assert (
            '<span class="versions"><a href="#starlette-1.10.0">1.10.0</a> · '
            '<a href="#starlette-1.9.0">1.9.0</a> · <a href="#starlette-1.8.0">1.8.0</a> · '
            '<a href="#starlette-1.7.0">1.7.0</a> · <a href="#starlette-1.6.0">1.6.0</a></span>') in page

    def test_header_of_the_page(self):
        """The title, the line under it, and the command in a box of one line."""
        page = build_page()
        assert (
            '      <pre><code>pip install --no-index --find-links '
            f'https://py38deps.github.io/py38deps/flat/ &lt;package&gt;</code></pre>\n') in page
        assert (
            '      <p class="lede">Every wheel this index provides, one section per distribution. '
            'Anchors are stable: #name, #name-version.</p>\n') in page

    def test_footer_of_the_page(self):
        page = build_page()
        assert (
            '      <p>Every link carries the sha256 of the file it points at.</p>\n') in page

    def test_style_of_the_page(self):
        page = build_page()
        assert '<style>' in page
        assert page.count('</style>') == 1
