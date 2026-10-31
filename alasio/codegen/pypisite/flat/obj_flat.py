"""
The objects of a flat page: the sections, the tables of them and the contents.

The page is built in one pass but the parts of it are not independent of each
other:

- the badge of a project is `compiled` when any of its versions is, so the badge
  of a section cannot be known when the section is opened;
- the index is written before the sections it lists, so it cannot be filled when
  it is created;
- a pure python table covers the versions of a project and has no section of its
  own, so the index must not link to them.

So the sections and the tables register themselves in a FlatRegistry as they are
made, and the badge and the index read it when the page renders. Rendering is
lazy already (an object renders when the page is written), so registration is
complete by then; the one rule a caller keeps is that no section is made after
the page is rendered.

Usage:
    from alasio.codegen.pypisite.flat import PypiSiteFlat

    gen = PypiSiteFlat()
    with gen.Doc('Wheels') as page:
        with page.body():
            gen.inject_index()
            with gen.WheelName('av'):
                with gen.WheelVersion('18.1.0'):
                    gen.WheelTableCompiled(links)
    gen.generate_str()
"""

from alasio.codegen.html import Element, HtmlContainer, escape

from ..wheel import is_name, normalize_name
from .table import build_pure_table, build_version

__all__ = [
    'FlatIndex',
    'FlatProject',
    'FlatRegistry',
    'FlatTableCompiled',
    'FlatTablePure',
    'FlatVersion',
]

# The badge of a version, and the word of a project that holds a compiled version
COMPILED = 'compiled'

# The badge of a project whose every version is of pure python wheels
PURE = 'pure'

# The words a badge of each kind shows. The class of a badge is the kind itself,
# which is the name the style sheet matches; the text is what a reader reads, and
# one of the two is a name of more than one word.
BADGE_TEXT = {
    COMPILED: 'compiled',
    PURE: 'pure python',
}

# The line under a matrix that holds a stable abi cell, around the mark of one:
# the mark is the same span the cell carries, so the two read as one thing
ABI3_NOTE_BEFORE = 'Cells marked '
ABI3_NOTE_AFTER = (
    ' are stable-ABI wheels: they install on the stated Python version and every newer one.')


def _units(count, unit):
    """
    Count a thing the way the page reads it: '1 wheel', '2 wheels'.

    Args:
        count (int): Number of the thing
        unit (str): Name of the thing, singular

    Returns:
        str: The count and the name
    """
    return f'{count} {unit}' if count == 1 else f'{count} {unit}s'


def _check_origin(origin, where):
    """
    Check the pair that says where the wheels of a version come from.

    Args:
        origin (tuple | list | None): (text, url) of the release, None for a
            version the caller has no source for
        where (str): The version the pair belongs to, for the message

    Returns:
        tuple: (text, url), None when there is no origin

    Raises:
        ValueError: If the value is not a pair of two texts
    """
    if origin is None:
        return None
    if not isinstance(origin, (tuple, list)) or len(origin) != 2:
        raise ValueError(f'The origin of "{where}" is a (text, url) pair: {origin!r}')
    text, url = origin
    for field, value in (('text', text), ('url', url)):
        if not isinstance(value, str) or not value:
            raise ValueError(f'The {field} of the origin of "{where}" is not a text: {value!r}')
    return text, url


def _origin(gen, origin):
    """
    The link that says where the wheels of a version come from.

    Args:
        gen (PypiSiteFlat): Generator of the page
        origin (tuple): (text, url) of the release, from _check_origin

    Returns:
        Element: The <a> of the source, in the style of a release tag
    """
    text, url = origin
    return Element(gen, 'a', text, class_='release-tag', href=url, title=f'release {text}')


def _badge(gen, kind):
    """
    The badge of a project or a version of a kind.

    Args:
        gen (PypiSiteFlat): Generator of the page
        kind (str): 'compiled' or 'pure'

    Returns:
        Element: The <span> of the badge, with the class of the kind and the
            words of it
    """
    return Element(gen, 'span', BADGE_TEXT[kind], class_=['badge', kind])


def _title(link):
    """
    The tooltip of a link: the file and the digest pip verifies.

    The two facts stay on one line: a row of a matrix is one line of html, and a
    newline in an attribute would split that row in the source of the page.

    Args:
        link (WheelLink): The wheel of the link

    Returns:
        str: The file name, and the digest when the link carries one
    """
    if not link.sha256:
        return link.filename
    return f'{link.filename} · sha256: {link.sha256}'


def _anchor(gen, cell):
    """
    The link of one wheel in a cell of a table.

    Args:
        gen (PypiSiteFlat): Generator of the page
        cell (FlatCell): The wheel and the text the cell shows

    Returns:
        Element: The anchor, with the mark of a stable abi wheel when it is one
    """
    anchor = Element(gen, 'a', cell.label, href=cell.link.href(with_name=True), title=_title(cell.link))
    if cell.abi3:
        anchor.add(Element(gen, 'span', 'abi3', class_='abi3'))
    return anchor


class FlatVersionRecord:
    """
    One version of a project, as the index and the badges read it.

    Args:
        version (str): Exact version of the distribution

    Attributes:
        version (str): Exact version
        kind (str): 'compiled' or 'pure', None until the table of it is made
        anchored (bool): True when the version has an anchor the index can link
            to: the section of it, or the row of a table that names it
    """

    def __init__(self, version):
        self.version = version
        self.kind = None
        self.anchored = False

    def __repr__(self):
        return f'<FlatVersionRecord {self.version} {self.kind}>'


class FlatProjectRecord:
    """
    One project of a flat page: the versions of it, in the order of the page.

    Args:
        name (str): Normalized distribution name
    """

    def __init__(self, name):
        self.name = name
        self.versions = {}

    def version(self, version):
        """
        The record of one version, made on the first call.

        Args:
            version (str): Exact version of the distribution

        Returns:
            FlatVersionRecord: The record of the version
        """
        record = self.versions.get(version)
        if record is None:
            record = self.versions[version] = FlatVersionRecord(version)
        return record

    @property
    def kind(self):
        """
        The badge of the project, read from its versions.

        Returns:
            str: 'compiled' when a version of it is compiled, 'pure' when every
                version is, None when the section holds no table yet
        """
        kinds = {record.kind for record in self.versions.values()}
        if COMPILED in kinds:
            return COMPILED
        if kinds == {PURE}:
            return PURE
        return None

    def anchored_versions(self):
        """
        The versions the index can link to.

        Returns:
            list[str]: The versions that have an anchor on the page
        """
        return [record.version for record in self.versions.values() if record.anchored]

    def __repr__(self):
        return f'<FlatProjectRecord {self.name} {len(self.versions)} version(s)>'


class FlatRegistry:
    """
    The projects and versions written into a flat page, in the order of it.

    The registry is the memory of one page: the sections fill it as they are
    made, the index and the badges read it when the page renders. A project is
    registered once however many sections it has.
    """

    def __init__(self):
        self.projects = {}

    def project(self, name):
        """
        The record of one project, made on the first call.

        Args:
            name (str): Normalized distribution name

        Returns:
            FlatProjectRecord: The record of the project
        """
        record = self.projects.get(name)
        if record is None:
            record = self.projects[name] = FlatProjectRecord(name)
        return record

    def __len__(self):
        return len(self.projects)

    def __repr__(self):
        return f'<FlatRegistry {len(self.projects)} project(s)>'


class _Parts(HtmlContainer):
    """
    A container that renders its children with no tag of its own.

    The table of a version is three parts of a section (the meta line, the
    matrix, the note), not one element. A section takes it as one object, so a
    caller writes the table once and the parts land where they belong.

    The parts are at the level of the container, not below it: the container has
    no tag, so there is no element for them to be inside of.
    """

    @property
    def child_indent(self):
        return self._indent

    def generate(self):
        for child in self.children:
            yield from child.generate()


def _child(parent, tag, **attrs):
    """
    Make an element inside a container: made, taken by the container, returned.

    The with block of an element puts the objects made inside it into the
    element, but it does not put the element itself into its own parent, so the
    skeleton of a table spells the two steps out.

    Args:
        parent (HtmlContainer): Container the element belongs to
        tag (str): Tag name
        **attrs (str | bool | list | tuple | None): Attributes of the element

    Returns:
        Element: The element, a context manager of its content
    """
    item = Element(parent.gen, tag, **attrs)
    parent.add(item)
    return item


class FlatProject(HtmlContainer):
    """
    The section of one project of a flat page.

    The section is opened by the call and filled by the with block of it, so the
    versions of a project stay together:

        with gen.WheelName('av'):
            with gen.WheelVersion('18.1.0'):
                gen.WheelTableCompiled(links)

    The badge of the section is not known when the section is made, a project is
    compiled when any of its versions is, so it is read from the registry when
    the page renders.

    Args:
        gen (PypiSiteFlat): Generator of the page
        name (str): Distribution name, any case and separator style

    Raises:
        ValueError: If the name is not a distribution name
    """

    def __init__(self, gen, name):
        super().__init__(gen)
        key = normalize_name(name)
        if not is_name(key):
            raise ValueError(f'The project of a flat page is not a distribution name: {name!r}')
        self.name = key
        self.record = gen.flat.project(key)
        # the project each with block of this object is in, newest last
        self._projects = []

    def __enter__(self):
        self._projects.append(self.gen.flat_project)
        self.gen.flat_project = self
        return super().__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        super().__exit__(exc_type, exc_val, exc_tb)
        self.gen.flat_project = self._projects.pop()
        return False

    def _heading(self):
        """
        The heading of the section, made when the page renders.

        Returns:
            Element: The <h2> of the project
        """
        gen = self.gen
        children = [self.name]
        kind = self.record.kind
        if kind:
            children.append(' ')
            children.append(_badge(gen, kind))
        children.append(' ')
        children.append(Element(
            gen, 'a', '#', class_='anchor', href=f'#{self.name}',
            aria_label=f'link to {self.name}'))
        return Element(gen, 'h2', *children)._reindent(self.child_indent)

    def generate(self):
        indent = self.indent_str
        yield f'{indent}<section class="project" id="{escape(self.name)}">'
        yield from self._heading().generate()
        for child in self.children:
            yield from child.generate()
        yield f'{indent}</section>'

    def __repr__(self):
        return f'<FlatProject {self.name}>'


class FlatVersion(HtmlContainer):
    """
    The section of one version of a project.

    The section holds one table; the badge of the section and the kind the index
    reads come from that table, and the table refuses to be made outside the
    with block of a version.

    Args:
        gen (PypiSiteFlat): Generator of the page
        version (str): Exact version of the distribution
        origin (tuple, optional): (text, url) of where the wheels come from, a
            release tag of the host that published them. Defaults to None, the
            page then writes no source for the version

    Raises:
        ValueError: If the section is outside the section of a project, if the
            version is empty, if the version already has a table, or if the
            origin is not a pair of two texts
    """

    def __init__(self, gen, version, origin=None):
        super().__init__(gen)
        project = gen.flat_project
        if project is None:
            raise ValueError(
                f'The version "{version}" of a flat page is outside the section of its project: '
                f'write it in the with block of gen.WheelName(name)')
        if not isinstance(version, str) or not version:
            raise ValueError(f'The version of a flat page is not a text: {version!r}')
        self.version = version
        self.project = project
        self.origin = _check_origin(origin, version)
        self.anchor = f'{project.name}-{version}'
        self.record = project.record.version(version)
        if self.record.kind is not None:
            raise ValueError(
                f'The version "{version}" of "{project.name}" already has its table: a version has '
                f'one section and one table')
        self.record.anchored = True
        self.table = None
        # the version each with block of this object is in, newest last
        self._versions = []

    def __enter__(self):
        self._versions.append(self.gen.flat_version)
        self.gen.flat_version = self
        return super().__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        super().__exit__(exc_type, exc_val, exc_tb)
        self.gen.flat_version = self._versions.pop()
        if exc_type is None and self.table is None:
            raise ValueError(
                f'The version "{self.version}" of "{self.project.name}" has no table: a version '
                f'section holds one WheelTableCompiled')
        return False

    def _heading(self):
        """
        The heading of the section, made when the page renders.

        Returns:
            Element: The <h3> of the version
        """
        gen = self.gen
        children = [
            self.version,
            ' ',
            _badge(gen, COMPILED),
        ]
        if self.origin is not None:
            children.append(' ')
            children.append(_origin(gen, self.origin))
        children.append(' ')
        children.append(Element(
            gen, 'a', '#', class_='anchor', href=f'#{self.anchor}',
            aria_label=f'link to {self.anchor}'))
        return Element(gen, 'h3', *children)._reindent(self.child_indent)

    def generate(self):
        indent = self.indent_str
        yield f'{indent}<section class="version" id="{escape(self.anchor)}">'
        yield from self._heading().generate()
        for child in self.children:
            yield from child.generate()
        yield f'{indent}</section>'

    def __repr__(self):
        return f'<FlatVersion {self.project.name}-{self.version}>'


class FlatTableCompiled(_Parts):
    """
    The table of one version: the meta line, the matrix and the abi3 note.

    The rows of the matrix are the platforms, the columns the python/abi pairs.
    The wheels of pure python of the same version are the last row (`any`),
    merged across the columns: they install where a wheel of a platform does not
    exist, so they are the fallback of every column at once.

    Args:
        gen (PypiSiteFlat): Generator of the page
        links (Iterable[WheelLink]): Every wheel of the version, the wheels of
            pure python included

    Raises:
        TypeError: If a link is not a WheelLink
        ValueError: If the table is outside the section of a version, if the
            version has a table already, if the links are not the wheels of that
            version of that project, or if no wheel of the version belongs to a
            platform
    """

    def __init__(self, gen, links):
        super().__init__(gen)
        version = gen.flat_version
        if version is None:
            raise ValueError(
                'The table of a flat page belongs to the section of a version: write it in the '
                'with block of gen.WheelVersion(version)')
        if version.table is not None:
            raise ValueError(
                f'The version "{version.anchor}" already has a table: a version section holds one')
        table = build_version(links)
        for link in table.wheels:
            if link.key != version.project.name:
                raise ValueError(
                    f'The table of "{version.anchor}" holds the wheel of another distribution: '
                    f'"{link.filename}"')
            if link.version != version.version:
                raise ValueError(
                    f'The table of "{version.anchor}" holds the wheel of another version: '
                    f'"{link.filename}"')
        self.table = table
        self.add(Element(gen, 'p', self._meta(), class_='meta'))
        self.add(self._matrix())
        if table.has_abi3:
            self.add(Element(
                gen, 'p', ABI3_NOTE_BEFORE, Element(gen, 'span', 'abi3', class_='abi3'), ABI3_NOTE_AFTER,
                class_='note'))
        version.table = self
        version.record.kind = COMPILED

    def _meta(self):
        """
        The line the page counts the version with.

        Returns:
            str: e.g. '46 wheels · 10 platforms · 5 Python tags'
        """
        table = self.table
        return (
            f'{_units(len(table.wheels), "wheel")} · '
            f'{_units(len(table.rows), "platform")} · '
            f'{_units(len(table.columns), "Python tag")}')

    def _row_head(self, label):
        """
        The first cell of a row: its platform.

        Args:
            label (str): Name of the row

        Returns:
            Element: The <th> of the row
        """
        gen = self.gen
        return Element(gen, 'th', Element(gen, 'span', label, class_='plat'), scope='row')

    def _cell(self, key, column):
        """
        One cell of the matrix.

        Args:
            key (tuple): Row key of the cell
            column (str): Column of the cell

        Returns:
            Element: The <td>, empty and marked when the version has no such wheel
        """
        gen = self.gen
        entries = self.table.cells.get((key, column))
        if not entries:
            return Element(gen, 'td', class_='na')
        parts = []
        for index, cell in enumerate(entries):
            if index:
                parts.append(Element(gen, 'br'))
            parts.append(_anchor(gen, cell))
        return Element(gen, 'td', *parts, class_='ok')

    def _pure_row(self):
        """
        The last row: the wheels of pure python, merged across the columns.

        Returns:
            list[Element]: The cells of the row
        """
        gen = self.gen
        parts = []
        for index, cell in enumerate(self.table.pure):
            if index:
                parts.append(Element(gen, 'br'))
            parts.append(_anchor(gen, cell))
        cell = Element(gen, 'td', *parts, class_='pure', colspan=len(self.table.columns))
        return [self._row_head('any'), cell]

    def _matrix(self):
        """
        The matrix of the version, as a box that scrolls on a narrow screen.

        Returns:
            Element: The <div> of the table
        """
        gen = self.gen
        head = [Element(gen, 'th', Element(gen, 'span', 'platform', class_='platform-head'), scope='col')]
        head.extend(Element(gen, 'th', column, scope='col') for column in self.table.columns)
        wrap = Element(gen, 'div', class_='table-wrap')
        table = _child(wrap, 'table', class_='matrix')
        thead = _child(table, 'thead')
        tbody = _child(table, 'tbody')
        with thead:
            gen.Tr(*head)
        with tbody:
            for key, display in self.table.rows:
                cells = [self._row_head(display)]
                cells.extend(self._cell(key, column) for column in self.table.columns)
                gen.Tr(*cells)
            if self.table.pure:
                gen.Tr(*self._pure_row())
        return wrap

    def __repr__(self):
        return f'<FlatTableCompiled {len(self.table.wheels)} wheel(s)>'


class FlatTablePure(_Parts):
    """
    The table of a project of pure python wheels: every version, one file a line.

    A version of pure python wheels has one file that installs everywhere, so a
    matrix of it would be one cell; the table of the project puts the versions
    in the rows instead, the dimension such a project is really spread on. A
    version has no section of its own, so the row of it carries the anchor the
    index links to: the cell of the version is the place a reader looks for it.

    The row of a version holds the same source a version section holds, when the
    caller knows one: a site that publishes every version from a release writes
    the release next to the version, and a site that reads the wheels of another
    host (the pack server) writes nothing.

    Args:
        gen (PypiSiteFlat): Generator of the page
        links (Iterable[WheelLink]): The wheels of the project, any number of
            versions of it, in the order the page shows them
        origins (dict, optional): {version: (text, url)} of where the wheels of
            a version come from. A version missing from it is shown without a
            source, and a caller that has none passes nothing. Defaults to None

    Raises:
        TypeError: If a link is not a WheelLink, or origins is not a mapping
        ValueError: If the table is outside the section of a project, if it is
            inside the section of a version, if the links belong to another
            distribution, if a version of it already has a section, if a wheel
            of a platform is in the list, if an origin is not a pair of two
            texts, or if origins names a version the table does not hold
    """

    def __init__(self, gen, links, origins=None):
        super().__init__(gen)
        project = gen.flat_project
        if project is None:
            raise ValueError(
                'The pure table of a flat page belongs to the section of a project: write it in '
                'the with block of gen.WheelName(name)')
        if gen.flat_version is not None:
            raise ValueError(
                'The pure table of a flat page covers the versions of a project: it belongs '
                'outside the section of a version')
        rows = build_pure_table(links)
        self.origins = self._check_origins(origins, rows)
        for _version, group in rows:
            for link in group:
                if link.key != project.name:
                    raise ValueError(
                        f'The pure table of "{project.name}" holds the wheel of another '
                        f'distribution: "{link.filename}"')
        for version, _group in rows:
            record = project.record.version(version)
            if record.kind is not None:
                raise ValueError(
                    f'The version "{version}" of "{project.name}" already has its table: the pure '
                    f'table covers the versions that have no section')
            record.kind = PURE
            # the row of the version is its anchor: the index links to it, and
            # the reader lands on the line of the file
            record.anchored = True
        self.rows = rows
        self.add(Element(gen, 'p', self._meta(), class_='meta'))
        self.add(self._matrix())

    @staticmethod
    def _check_origins(origins, rows):
        """
        Read the source of every version of the table.

        A version the caller has no source for is not in the answer, and a
        version in it that the table does not hold is refused: a misspelled
        version would otherwise drop the source of a row and look like a site
        that publishes nothing.

        Args:
            origins (dict | None): {version: (text, url)}
            rows (list): [(version, [WheelLink])] of the table

        Returns:
            dict: {version: (text, url)}, empty when the caller has no source

        Raises:
            TypeError: If origins is not a mapping
            ValueError: If an origin is not a pair of two texts, or names a
                version the table does not hold
        """
        if origins is None:
            return {}
        if not hasattr(origins, 'items'):
            raise TypeError(
                f'The origins of a pure table are a {{version: (text, url)}} mapping: '
                f'{type(origins).__name__}')
        versions = {version for version, _group in rows}
        unknown = sorted(str(version) for version in origins if version not in versions)
        if unknown:
            raise ValueError(
                f'The origins of the pure table hold {", ".join(repr(version) for version in unknown)} '
                f'of no version of it: the table shows {sorted(versions)}')
        return {version: _check_origin(origin, version) for version, origin in origins.items()}

    def _meta(self):
        """
        The line the page counts the project with.

        Returns:
            str: e.g. '27 versions · 27 wheels'
        """
        wheels = sum(len(group) for _version, group in self.rows)
        return f'{_units(len(self.rows), "version")} · {_units(wheels, "wheel")}'

    def _matrix(self):
        """
        The two column table of the versions.

        Returns:
            Element: The <div> of the table
        """
        gen = self.gen
        wrap = Element(gen, 'div', class_='table-wrap')
        table = _child(wrap, 'table', class_=['matrix', 'pure'])
        thead = _child(table, 'thead')
        tbody = _child(table, 'tbody')
        with thead:
            gen.Tr(
                Element(gen, 'th', 'version', scope='col'),
                Element(gen, 'th', 'file', scope='col'))
        with tbody:
            for version, group in self.rows:
                parts = []
                for link in group:
                    if parts:
                        parts.append(Element(gen, 'br'))
                    parts.append(Element(
                        gen, 'a', link.filename, href=link.href(with_name=True), title=_title(link)))
                gen.Tr(
                    self._version_head(version),
                    Element(gen, 'td', *parts))
        return wrap

    def _version_head(self, version):
        """
        The cell of one version: its number, the hash anchor of the row.

        The row has no heading of its own, so the cell that names the version is
        what a link from the index points at, and the mark of an anchor makes it
        look like one.

        Args:
            version (str): Exact version of the row

        Returns:
            Element: The <th> of the row, with the id of the anchor
        """
        gen = self.gen
        anchor = f'{self.gen.flat_project.name}-{version}'
        children = [version, ' ']
        origin = self.origins.get(version)
        if origin is not None:
            children.append(_origin(gen, origin))
            children.append(' ')
        children.append(Element(
            gen, 'a', '#', class_='anchor', href=f'#{anchor}', aria_label=f'link to {anchor}'))
        return Element(gen, 'th', *children, scope='row', id=anchor)

    def __repr__(self):
        return f'<FlatTablePure {len(self.rows)} version(s)>'


class FlatIndex(HtmlContainer):
    """
    The table of contents of a flat page.

    The index is placed where it belongs in the page and filled when the page
    renders: a page writes its index above the sections it lists, and the
    projects are known only after the sections are made. Filling it at render
    time keeps the two in one pass, with no insertion into the page.

    Args:
        gen (PypiSiteFlat): Generator of the page
        title (str): Heading of the index, escaped. Defaults to 'Contents'
    """

    def __init__(self, gen, title='Contents'):
        super().__init__(gen)
        self.title = title
        self._built = False

    def _build(self):
        """
        Fill the index from the registry, once per page.
        """
        if self._built:
            return
        self._built = True
        gen = self.gen
        self.add(Element(gen, 'h2', self.title))
        toc = Element(gen, 'ul', class_='toc').wrap('newline')
        for record in gen.flat.projects.values():
            toc.add(self._item(record))
        self.add(toc)

    def _versions(self, record):
        """
        The versions of one project, as the index shows them.

        Every table gives its versions an anchor: the section of a version, or
        the row of the pure table that names it. So the index reads the same
        whatever the project is made of, `4.15.1 · 4.14.2`, and a project whose
        versions have no anchor yet (a section that holds no table) is counted
        instead of linked.

        Args:
            record (FlatProjectRecord): The project

        Returns:
            Element: The <span> of the versions, one link a version
        """
        gen = self.gen
        anchored = record.anchored_versions()
        if not anchored:
            count = _units(len(record.versions), 'version')
            return Element(gen, 'span', count, class_='versions')
        parts = []
        for index, version in enumerate(anchored):
            if index:
                parts.append(' · ')
            parts.append(Element(gen, 'a', version, href=f'#{record.name}-{version}'))
        return Element(gen, 'span', *parts, class_='versions')

    def _item(self, record):
        """
        One line of the index.

        Args:
            record (FlatProjectRecord): The project of the line

        Returns:
            Element: The <li> of the project
        """
        gen = self.gen
        children = [Element(gen, 'a', record.name, href=f'#{record.name}')]
        kind = record.kind
        if kind:
            children.append(' ')
            children.append(_badge(gen, kind))
        children.append(' ')
        children.append(self._versions(record))
        return Element(gen, 'li', *children)

    def generate(self):
        self._build()
        indent = self.indent_str
        yield f'{indent}<nav id="contents" aria-label="{escape(self.title)}">'
        for child in self.children:
            yield from child.generate()
        yield f'{indent}</nav>'

    def __repr__(self):
        return f'<FlatIndex {self.title!r}>'
