"""
The generator of a flat page: PypiSite plus the sections a reader of the page sees.

The pages pip reads are written by PypiSite (the wheel link pages of a folder);
this module writes the page a person reads: the same wheels, one section per
project, a matrix per compiled version, and an index of the projects at the top.

The five methods are the whole domain knowledge of the module:

- WheelName: the section of one project, `with` block of the versions of it;
- WheelVersion: the section of one version, `with` block of its table;
- WheelTableCompiled: the matrix of one version, the wheels of pure python of
  the version as its last row;
- WheelTablePurePython: the two column table of a project of pure python wheels;
- inject_index: the table of contents, filled when the page renders.

Two of them count on the other: an index is written before the sections it lists
but read after them, and the badge of a project comes from the versions below it,
so the sections register themselves in gen.flat and the index reads it at render
time (see obj_flat).

Usage:
    from alasio.codegen.pypisite.flat import PypiSiteFlat

    gen = PypiSiteFlat()
    with gen.Doc('Wheels of Author_Repo_master') as page:
        with page.body():
            gen.H1('Wheels of Author_Repo_master')
            gen.inject_index()
            with gen.WheelName('av'):
                for version, links in av_versions:
                    with gen.WheelVersion(version):
                        gen.WheelTableCompiled(links)
            with gen.WheelName('anyio'):
                gen.WheelTablePurePython(anyio_links)
    gen.write('wheel/overview.html')

The whole page of a folder of wheels is one call, flat_page: it writes the
skeleton, the index and the sections of every project from the wheels alone.

Usage:
    from alasio.codegen.pypisite.flat import flat_page

    flat_page({'httpx2': links}, 'Wheels of Author_Repo_master')
"""

from alasio.codegen.html import Element, Raw

from ..gen import PypiSite
from ..wheel import normalize_name, split_by_version
from .obj_flat import FlatIndex, FlatProject, FlatRegistry, FlatTableCompiled, FlatTablePure, FlatVersion
from .style import FLAT_STYLE
from .tags import is_pure, parse_tags

__all__ = [
    'PypiSiteFlat',
    'flat_page',
    'flat_site',
]

# The line under the heading of a flat page, written by flat_page
LEDE = ('Every wheel this index provides, one section per distribution. '
        'Anchors are stable: #name, #name-version.')

# The line of the footer of a flat page, written by flat_page
FOOTER = 'Every link carries the sha256 of the file it points at.'


class PypiSiteFlat(PypiSite):
    """
    A generator of one flat page of a folder of wheels.

    Args:
        indent_size (int): Spaces per indent level. Defaults to 2
    """

    def __init__(self, indent_size=2):
        super().__init__(indent_size=indent_size)
        # the projects and versions of the page, read by the index and the
        # badges when the page renders
        self.flat = FlatRegistry()
        # the section the objects made now belong to, None outside a section
        self.flat_project = None
        self.flat_version = None

    def WheelName(self, name):
        """
        Define the section of one project of the page.

        Examples:
            with gen.WheelName('av'):
                with gen.WheelVersion('18.1.0'):
                    gen.WheelTableCompiled(links)

        Args:
            name (str): Distribution name, any case and separator style, it is
                normalized with PEP 503 for the anchor of the section

        Returns:
            FlatProject: The section, a context manager of its content

        Raises:
            ValueError: If the name is not a distribution name
        """
        item = FlatProject(self, name)
        self._add_item(item)
        return item

    def WheelVersion(self, version, origin=None):
        """
        Define the section of one version inside the section of its project.

        Args:
            version (str): Exact version of the distribution
            origin (tuple, optional): (text, url) of where the wheels come from.
                Defaults to None, the page then writes no source

        Returns:
            FlatVersion: The section, a context manager of its table

        Raises:
            ValueError: If the section is outside the section of a project, or
                the version already has a table
        """
        item = FlatVersion(self, version, origin=origin)
        self._add_item(item)
        return item

    def WheelTableCompiled(self, links):
        """
        Define the matrix of the version of the open section.

        The input is every wheel of the version, the wheels of pure python
        included: a compiled version can ship a fallback wheel that installs
        anywhere, and that wheel is the last row of the matrix instead of a
        table of its own.

        Args:
            links (Iterable[WheelLink]): The wheels of the version

        Returns:
            FlatTableCompiled: The meta line, the matrix and the note

        Raises:
            ValueError: If the table is outside the section of a version, if the
                links are not the wheels of that version of that project, or if
                no wheel of them belongs to a platform
        """
        item = FlatTableCompiled(self, links)
        self._add_item(item)
        return item

    def WheelTablePurePython(self, links, origins=None):
        """
        Define the table of the project of the open section, one line a version.

        A project of pure python wheels has no platform to spread, so its table
        covers every version of it and its versions have no section: a caller
        does not write WheelVersion for them.

        Args:
            links (Iterable[WheelLink]): The wheels of the project, in the order
                the page shows the versions, usually the newest first
            origins (dict, optional): {version: (text, url)} of where the wheels
                of a version come from, the same pair a version section takes.
                A version missing from it is shown without a source, and a
                caller that has none passes nothing. Defaults to None

        Returns:
            FlatTablePure: The meta line and the two column table

        Raises:
            TypeError: If a link is not a WheelLink, or origins is not a mapping
            ValueError: If the table is outside the section of a project, if it
                is inside the section of a version, if a wheel of a platform is
                in the list, if a version of it already has a section, or if
                origins names a version the table does not hold
        """
        item = FlatTablePure(self, links, origins=origins)
        self._add_item(item)
        return item

    def inject_index(self, title='Contents'):
        """
        Define the table of contents at the place of the call.

        The index is filled when the page renders, not when it is called, so it
        is written above the sections it lists: the projects are known by then.

        Examples:
            with page.body():
                gen.H1(title)
                gen.inject_index()
                with gen.WheelName('av'):
                    ...

        Args:
            title (str): Heading of the index, escaped. Defaults to 'Contents'

        Returns:
            FlatIndex: The index
        """
        item = FlatIndex(self, title=title)
        self._add_item(item)
        return item


def _project(gen, name, links, origins):
    """
    Write the section of one project: a section a compiled version, a table of
    the versions of pure python.

    A version whose every wheel is of pure python has no platform to spread, so
    it is a row of the table of the project rather than a section of its own; a
    version that ships both kinds of wheel is a section, and the wheels of pure
    python of it are the last row of its matrix.

    Args:
        gen (PypiSiteFlat): Generator of the page
        name (str): Distribution name, any case and separator style
        links (Iterable[WheelLink]): The wheels of the project, any versions
        origins (dict): {version: (text, url)} of where the wheels come from

    Raises:
        ValueError: If a link is not a WheelLink, or an origin names a version
            the project does not have
    """
    by_version = split_by_version(links)
    unknown = sorted(str(version) for version in origins if version not in by_version)
    if unknown:
        raise ValueError(
            f'The origins of "{normalize_name(name)}" hold {", ".join(repr(version) for version in unknown)} '
            f'of no version of it: the project shows {sorted(by_version)}')
    with gen.WheelName(name):
        pure = []
        pure_origins = {}
        for version, group in by_version.items():
            if all(is_pure(parse_tags(link.filename)) for link in group):
                pure.extend(group)
                if version in origins:
                    pure_origins[version] = origins[version]
                continue
            with gen.WheelVersion(version, origin=origins.get(version)):
                gen.WheelTableCompiled(group)
        if pure:
            gen.WheelTablePurePython(pure, origins=pure_origins)


def flat_site(projects, title, command=None, lang='en', origins=None):
    """
    Build a whole flat page of a folder of wheels: the generator of it.

    The page is the one a reader opens: a heading, the index of the projects, a
    section a project, and the footer. The tables of the page are the ones the
    methods above write, so the page of this function and the page a caller
    composes with them are the same page. The generator is the page, so a caller
    that writes it to a file writes only when the page changed:

        flat_site(projects, title).write('wheel/flat.html')

    Args:
        projects (dict[str, Iterable[WheelLink]]): {name: the wheels of it}. The
            wheels of a project may be of several versions; the mapping and the
            versions in it are in the order the page shows them
        title (str): Title and heading of the page, escaped
        command (str, optional): Command a reader copies, e.g. the pip install
            line of the folder. None writes no box. Defaults to None
        lang (str): Language of the page. Defaults to 'en'
        origins (dict, optional): {name: {version: (text, url)}} of where the
            wheels of a version come from, see WheelVersion. Defaults to None,
            no version names a source

    Returns:
        PypiSiteFlat: The generator of the page

    Raises:
        ValueError: If a link is not a WheelLink, if a name is not a
            distribution name, or if an origin names a version a project does
            not have
    """
    origins = {normalize_name(name): value for name, value in (origins or {}).items()}
    gen = PypiSiteFlat()
    head = [Element(gen, 'style', Raw(gen, FLAT_STYLE, indent=False))]
    with gen.Doc(title, head=head, lang=lang) as page:
        with page.body():
            with gen.Header():
                gen.H1(title)
                gen.P(LEDE, class_='lede').wrap('inline')
                if command is not None:
                    # one line: a <pre> keeps the whitespace of the source
                    gen.Pre(Element(gen, 'code', command)).wrap('inline')
            gen.inject_index()
            with gen.Main():
                for name, links in projects.items():
                    _project(gen, name, links, origins.get(normalize_name(name)) or {})
            with gen.Footer():
                gen.P(FOOTER).wrap('inline')
    return gen


def flat_page(projects, title, command=None, lang='en', origins=None):
    """
    Render a whole flat page of a folder of wheels as a text.

    Args:
        projects (dict[str, Iterable[WheelLink]]): {name: the wheels of it}, see
            flat_site
        title (str): Title and heading of the page, escaped
        command (str, optional): Command a reader copies. Defaults to None
        lang (str): Language of the page. Defaults to 'en'
        origins (dict, optional): {name: {version: (text, url)}}. Defaults to
            None, no version names a source

    Returns:
        str: The page

    Raises:
        ValueError: If a link is not a WheelLink, if a name is not a
            distribution name, or if an origin names a version a project does
            not have
    """
    return flat_site(projects, title, command=command, lang=lang, origins=origins).generate_str()
