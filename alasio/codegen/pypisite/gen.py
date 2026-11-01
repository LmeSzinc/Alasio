"""
The generator of pip index pages: the generic html generator plus the wheels.

PypiSite is HtmlGen with three domain methods, so a page of a pip index is
written with the generic tags too when it needs them:

    gen = PypiSite()
    with gen.PypiHeader('httpx2') as page:
        with gen.P(class_='note'):
            gen.Text('Downloaded from the index of the project.')
        gen.Wheels(links)
    gen.write('wheel/httpx2/index.html')

The three methods are the whole domain knowledge of the module:

- Wheel: one wheel of a page, see obj_pypi;
- Wheels: the wheels of a page, checked, deduplicated and sorted once, so the
  order of the page does not depend on the order the caller read them in;
- PypiHeader: the page of one distribution, see obj_pypi.

The find-links page of a whole folder is the generic document with one heading
and the wheels named by their distribution:

    with gen.Doc(title) as page:
        with page.body():
            gen.H1(title)
            gen.Wheels(links, with_name=True)

Usage:
    from alasio.codegen.pypisite import PypiSite, WheelLink

    gen = PypiSite()
    with gen.PypiHeader('httpx2'):
        gen.Wheels([WheelLink('httpx2', '2.13.1', 'httpx2-2.13.1-py3-none-any.whl')])
    gen.generate_str()
"""

from alasio.codegen.html import HtmlGen

from .obj_pypi import PypiHeader, Wheel
from .wheel import prepare_links

__all__ = [
    'PypiSite',
    'find_links_page',
    'find_links_site',
    'simple_page',
    'simple_site',
]


class PypiSite(HtmlGen):
    """
    A generator of one pip index page.

    Args:
        indent_size (int): Spaces per indent level. Defaults to 2
    """

    def __init__(self, indent_size=2):
        super().__init__(indent_size=indent_size)
        # the distribution the page belongs to, '' outside a PypiHeader block:
        # Wheels refuses a wheel of another distribution when it is set
        self.project = ''

    def Wheel(self, link, with_name=False):
        """
        Define one wheel of the page, where the caller puts it.

        The single wheel is written in the order the caller writes the calls:
        it is the escape hatch for a line the module should not reorder (a
        hand-written page, a wheel the run wants first). Use Wheels() for a set
        of wheels: it checks, deduplicates and orders them.

        Args:
            link (WheelLink): The wheel to link
            with_name (bool): True to start the link at the name of the
                distribution, for the find-links page of a whole folder

        Returns:
            Wheel: The line of the wheel

        Raises:
            TypeError: If the link is not a WheelLink
        """
        item = Wheel(self, link, with_name=with_name)
        self._add_item(item)
        return item

    def Wheels(self, links, with_name=False):
        """
        Define the wheels of the page: checked, deduplicated and sorted.

        The order is the one pip resolves a version in (PEP 440): version, then
        file name, and the name first when with_name is True. The caller's
        order does not matter, so two runs of the same links render the same
        page and a page that did not change is not written again.

        Args:
            links (Iterable[WheelLink]): The wheels of the page
            with_name (bool): True for the find-links page of a whole folder

        Returns:
            list[Wheel]: The lines of the wheels, in the order of the page

        Raises:
            TypeError: If a link is not a WheelLink
            ValueError: If two links carry one file name with different
                digests, or the page is the page of another distribution
        """
        return [
            self._add_item(Wheel(self, link, with_name=with_name))
            for link in prepare_links(links, with_name=with_name, project=self.project)
        ]

    def PypiHeader(self, project, head=(), lang='en'):
        """
        Define the page of one distribution of a pip simple index.

        Examples:
            with gen.PypiHeader('httpx2'):
                gen.Wheels(links)

        Args:
            project (str): Distribution name, any case and separator style
            head (Iterable): Extra items of the head, detached objects only
            lang (str): Language of the page

        Returns:
            PypiHeader: The page, a context manager of its body

        Raises:
            ValueError: If the project is not a distribution name
        """
        item = PypiHeader(self, project, head=head, lang=lang)
        self._add_item(item)
        return item


def simple_site(project, links, lang='en'):
    """
    Build the page of one distribution: the generator of it.

    The page sits in the folder of the distribution (wheel/{name}/index.html):
    its wheels link to the version folders below it. The generator is the page,
    so a caller that writes it to a file writes only when the page changed:

        simple_site('httpx2', links).write('wheel/httpx2/index.html')

    Args:
        project (str): Distribution name, any case and separator style
        links (Iterable[WheelLink]): The wheels of the distribution
        lang (str): Language of the page

    Returns:
        PypiSite: The generator of the page

    Raises:
        ValueError: If the project is not a distribution name, or a link is a
            wheel of another distribution
    """
    gen = PypiSite()
    with gen.PypiHeader(project, lang=lang):
        gen.Wheels(links)
    return gen


def simple_page(project, links, lang='en'):
    """
    Render the page of one distribution as a text.

    Args:
        project (str): Distribution name, any case and separator style
        links (Iterable[WheelLink]): The wheels of the distribution
        lang (str): Language of the page

    Returns:
        str: The page

    Raises:
        ValueError: If the project is not a distribution name, or a link is a
            wheel of another distribution
    """
    return simple_site(project, links, lang=lang).generate_str()


def find_links_site(links, title='Wheels'):
    """
    Build the find-links page of a folder of wheels: the generator of it.

    The page is the index.html of the folder (wheel/index.html): its wheels link
    to the version folders below the folders of the distributions, the path
    `name/version/file` pip reads with --find-links.

    Args:
        links (Iterable[WheelLink]): The wheels of the folder
        title (str): Title and heading of the page, escaped

    Returns:
        PypiSite: The generator of the page

    Raises:
        ValueError: If two links carry one file name with different digests
    """
    gen = PypiSite()
    with gen.Doc(title) as page:
        with page.body():
            gen.H1(title)
            gen.Wheels(links, with_name=True)
    return gen


def find_links_page(links, title='Wheels'):
    """
    Render the find-links page of a folder of wheels as a text.

    Args:
        links (Iterable[WheelLink]): The wheels of the folder
        title (str): Title and heading of the page, escaped

    Returns:
        str: The page

    Raises:
        ValueError: If two links carry one file name with different digests
    """
    return find_links_site(links, title=title).generate_str()
