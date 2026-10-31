"""
The html generator: one method per tag, plus the document and the leaves.

The methods of the generator all do the same three things: make the object,
take it into the current container and return it. So a call is a line of the
page, and a with block is a container:

    gen = HtmlGen()
    with gen.Doc('Wheels') as page:
        with page.body():
            gen.H1('Wheels')
            with gen.Ul(class_='list'):
                for name in names:
                    gen.Li(gen.A(name, href=f'{name}/index.html'))

The tag methods are made from TAG_TABLE, so a tag is added in one place and the
generator gains its method with it. A custom element is written with El().

Usage:
    from alasio.codegen.html import HtmlGen

    gen = HtmlGen()
    gen.A('httpx2', href='httpx2/index.html').wrap('inline')
"""

from .gen_base import HtmlGenBase
from .obj_base import HtmlError
from .obj_document import Document
from .obj_element import TAG_TABLE, Element, Line
from .obj_simple import Comment, Raw, Text

__all__ = [
    'HtmlGen',
]


class HtmlGen(HtmlGenBase):
    """
    A generator of one html page.

    Args:
        indent_size (int): Spaces per indent level. Defaults to 2
    """

    # -- the document -----------------------------------------------------

    def Doc(self, title, head=(), body=(), lang='en', charset='utf-8'):
        """
        Define a whole html page.

        Examples:
            with gen.Doc('Links for httpx2') as page:
                with page.head():
                    gen.Meta(name='viewport', content='width=device-width, initial-scale=1')
                with page.body():
                    gen.H1('Links for httpx2')

        Args:
            title (str): Text of the title, escaped
            head (Iterable): Items of the head, between the charset and the title
            body (Iterable): Items of the body
            lang (str): Language of the page
            charset (str): Encoding declared by the page

        Returns:
            Document: The page, a context manager
        """
        item = Document(self, title, head=head, body=body, lang=lang, charset=charset)
        self._add_item(item)
        return item

    # -- the leaves -------------------------------------------------------

    def Text(self, text):
        """
        Define a text node, escaped.

        Args:
            text (str | int | float): Value to show

        Returns:
            Text: The text node
        """
        item = Text(self, text)
        self._add_item(item)
        return item

    def Raw(self, text, indent=True):
        """
        Define an already escaped fragment, emitted as it is.

        Args:
            text (str): The fragment
            indent (bool): True to strip the common indentation of the fragment
                and add the indentation of the level it lands at

        Returns:
            Raw: The fragment
        """
        item = Raw(self, text, indent=indent)
        self._add_item(item)
        return item

    def Comment(self, text):
        """
        Define a comment.

        Args:
            text (str): Text of the comment

        Returns:
            Comment: The comment
        """
        item = Comment(self, text)
        self._add_item(item)
        return item

    def Line(self, *children):
        """
        Define the children of one line, with no tag around them.

        Examples:
            with gen.Line():
                gen.A('x', href='y')
                gen.Br()
            # <a href="y">x</a><br>

        Args:
            *children (HtmlObject | str | int | float | list | tuple | None):
                Content of the line

        Returns:
            Line: The line
        """
        item = Line(self, *children)
        self._add_item(item)
        return item

    # -- the elements -----------------------------------------------------

    def El(self, tag, *children, **attrs):
        """
        Define an element of any tag name, the custom elements included.

        Examples:
            gen.El('my-widget', class_='card')
            # <my-widget class="card"></my-widget>

        Args:
            tag (str): Tag name, any case
            *children (HtmlObject | str | int | float | list | tuple | None):
                Content of the element
            **attrs (str | bool | list | tuple | None): Attributes

        Returns:
            Element: The element
        """
        item = Element(self, tag, *children, **attrs)
        self._add_item(item)
        return item


def _make_tag_method(tag):
    """
    Make the generator method of one tag.

    Args:
        tag (str): Tag name, e.g. 'a' or 'h1'

    Returns:
        function: The method, named after the tag in CamelCase (A, H1)
    """
    void = TAG_TABLE[tag]

    def method(self, *children, **attrs):
        item = Element(self, tag, *children, **attrs)
        self._add_item(item)
        return item

    method.__name__ = tag.capitalize()
    method.__qualname__ = f'HtmlGen.{method.__name__}'
    method.__doc__ = (
        f'Define a <{tag}> element.\n\n'
        f'Args:\n'
        f'    *children: Content of the element{" (a <" + tag + "> takes none)" if void else ""}\n'
        f'    **attrs: Attributes of the element\n\n'
        f'Returns:\n'
        f'    Element: The element\n'
    )
    return method


for _tag in TAG_TABLE:
    _name = _tag.capitalize()
    if hasattr(HtmlGen, _name):
        raise HtmlError(f'The tag <{_tag}> would override the method HtmlGen.{_name}')
    setattr(HtmlGen, _name, _make_tag_method(_tag))
del _tag, _name
