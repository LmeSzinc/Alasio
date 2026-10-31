"""
The page objects of a pip index page.

Two objects live here, both subclasses of the generic ones:

- PypiHeader: the document of a PEP 503 page of one distribution. The head is
  fixed (the charset, the simple api version, the title) and the body starts with
  the `Links for <name>` heading, so a caller writes only the wheels:

      with gen.PypiHeader('httpx2'):
          gen.Wheels(links)

  The with block of the page goes to its body: a `<meta>` straight into a
  document is refused (see Document), and a page builder should not have to
  spell out the body of the document it just asked for.

- Wheel: one wheel of a page: the anchor with the file name and the line break
  after it, in one Line so a container of one item per line keeps them together.

Usage:
    from alasio.codegen.pypisite import PypiSite, WheelLink

    gen = PypiSite()
    with gen.PypiHeader('httpx2'):
        gen.Wheel(WheelLink('httpx2', '2.13.1', 'httpx2-2.13.1-py3-none-any.whl'))
"""

from alasio.codegen.html import Document, Element, Line

from .wheel import WheelLink, is_name, normalize_name

__all__ = [
    'PypiHeader',
    'Wheel',
]

# The version of the simple api the page speaks, PEP 629
REPOSITORY_VERSION = '1.0'


class PypiHeader(Document):
    """
    The page of one distribution of a pip simple index.

    The page is the file `{name}/index.html` of the folder a client reads with
    `--find-links` or as an index, so its wheels link to the version folders
    below it: `WheelLink.href()` without the name.

    Args:
        gen (PypiSite): Generator of the page
        project (str): Distribution name, any case and separator style, it is
            normalized with PEP 503 for the title and the heading
        head (Iterable): Extra items of the head, after the charset and the
            simple api version and before the title. The items must be detached
            (Element(gen, ...)), see Document
        lang (str): Language of the page. Defaults to 'en'

    Raises:
        ValueError: If the project is not a distribution name
        HtmlError: If an item of head is already in the page
    """

    def __init__(self, gen, project, head=(), lang='en'):
        name = normalize_name(project)
        if not is_name(name):
            raise ValueError(f'The project of a pip page is not a distribution name: {project!r}')
        title = f'Links for {name}'
        super().__init__(
            gen,
            title,
            head=[
                Element(gen, 'meta', name='pypi:repository-version', content=REPOSITORY_VERSION),
                *head,
            ],
            body=[Element(gen, 'h1', title)],
            lang=lang,
        )
        self.project = name
        # the project the wheels of the page belong to, restored when the block
        # ends: Wheels reads it to refuse a wheel of another distribution
        self._project_prev = ''

    def __enter__(self):
        # the with block of the page is its body: a caller writes the wheels, not
        # the skeleton of the document it asked for
        self._project_prev = self.gen.project
        self.gen.project = self.project
        self.body_obj.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.body_obj.__exit__(exc_type, exc_val, exc_tb)
        self.gen.project = self._project_prev
        # the exception, when there is one, keeps going
        return False

    def __repr__(self):
        return f'<PypiHeader {self.project}>'


class Wheel(Line):
    """
    One wheel of a pip index page: the anchor and the line break after it.

    The anchor text is the file name and the link is the one WheelLink builds,
    so the page and the folder of the wheels cannot disagree.

    Args:
        gen (PypiSite): Generator of the page
        link (WheelLink): The wheel to link
        with_name (bool): True for the find-links page of a whole folder, the
            link then starts at the name of the distribution. Defaults to False

    Raises:
        TypeError: If the link is not a WheelLink
    """

    def __init__(self, gen, link, with_name=False):
        if not isinstance(link, WheelLink):
            raise TypeError(f'A wheel of a pip page is not a WheelLink: {type(link).__name__}')
        self.link = link
        self.with_name = with_name
        super().__init__(
            gen,
            Element(gen, 'a', link.filename, href=link.href(with_name=with_name)),
            Element(gen, 'br'),
        )

    def __repr__(self):
        return f'<Wheel {self.link.filename}>'
