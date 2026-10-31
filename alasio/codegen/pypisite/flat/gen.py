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
"""

from ..gen import PypiSite
from .obj_flat import FlatIndex, FlatProject, FlatRegistry, FlatTableCompiled, FlatTablePure, FlatVersion

__all__ = [
    'PypiSiteFlat',
]


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
