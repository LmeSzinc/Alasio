"""
A whole html page: the doctype, the html element, the head and the body.

The document owns the skeleton every page shares, so a caller writes only the
content:

    gen = HtmlGen()
    with gen.Doc('Links for httpx2') as page:
        with page.head():
            gen.Meta(name='viewport', content='width=device-width, initial-scale=1')
        with page.body():
            gen.H1('Links for httpx2')

    # <!DOCTYPE html>
    # <html lang="en">
    #   <head>
    #     <meta charset="utf-8">
    #     <meta name="viewport" content="width=device-width, initial-scale=1">
    #     <title>Links for httpx2</title>
    #   </head>
    #   <body>
    #     <h1>Links for httpx2</h1>
    #   </body>
    # </html>

The charset and the title are written by the document itself: an item added
through head() goes between them, so the title stays the last meta of the head
whatever a caller adds. A child added straight to the document is an error: a
<meta> that silently lands in the body is a layout bug that reads as a typo.

Usage:
    from alasio.codegen.html import HtmlGen

    gen = HtmlGen()
    with gen.Doc('Title') as page:
        with page.body():
            gen.P('hello')
"""

from .obj_base import HtmlContainer, HtmlError, NotInline, escape

__all__ = [
    'Body',
    'Document',
    'Head',
]

# the charset of every page, the characters of an escaped text are ascii and
# the page declares the encoding anyway
CHARSET = 'utf-8'


class Head(HtmlContainer):
    """
    The head of a document: the charset, the items of the caller, the title.

    Args:
        gen (HtmlGenBase): Generator of the page
        title (str): Text of the title, escaped
        charset (str): Encoding declared by the page. Defaults to 'utf-8'
    """

    def __init__(self, gen, title, charset=CHARSET):
        super().__init__(gen)
        self.title = title
        self.charset = charset

    def generate(self):
        indent = self.indent_str
        inner = ' ' * (self.gen.indent_size * self.child_indent)
        yield f'{indent}<head>'
        yield f'{inner}<meta charset="{escape(self.charset)}">'
        for child in self.children:
            yield from child.generate()
        yield f'{inner}<title>{escape(self.title)}</title>'
        yield f'{indent}</head>'

    def __repr__(self):
        return f'<Head {self.title!r}>'


class Body(HtmlContainer):
    """
    The body of a document.

    Args:
        gen (HtmlGenBase): Generator of the page
    """

    def generate(self):
        indent = self.indent_str
        yield f'{indent}<body>'
        for child in self.children:
            yield from child.generate()
        yield f'{indent}</body>'

    def __repr__(self):
        return f'<Body {len(self.children)} child(ren)>'


class Document(HtmlContainer):
    """
    A whole html page.

    Args:
        gen (HtmlGenBase): Generator of the page
        title (str): Text of the title, escaped
        head (Iterable): Items of the head, placed after the charset and before
            the title
        body (Iterable): Items of the body
        lang (str): Language of the document. Defaults to 'en'
        charset (str): Encoding declared by the page. Defaults to 'utf-8'

    Raises:
        TypeError: If an item of head or body is not an html object, a text or
            a number
        ValueError: If an item belongs to another generator
    """

    def __init__(self, gen, title, head=(), body=(), lang='en', charset=CHARSET):
        super().__init__(gen)
        self.title = title
        self.lang = lang
        self.charset = charset
        self.head_obj = Head(gen, title, charset=charset)
        self.body_obj = Body(gen)
        # the two boxes are one level below the document, whether they were
        # made before or inside the with block of it
        self.head_obj._reindent(self.child_indent)
        self.body_obj._reindent(self.child_indent)
        self.head_obj.add_all(head)
        self.body_obj.add_all(body)

    def head(self):
        """
        The head of the page, to be used as a context manager.

        Returns:
            Head: The head box
        """
        return self.head_obj

    def body(self):
        """
        The body of the page, to be used as a context manager.

        Returns:
            Body: The body box
        """
        return self.body_obj

    def _iter_children(self):
        return (self.head_obj, self.body_obj)

    def add(self, item):
        """
        Refuse an item added straight to the document.

        Args:
            item (Any): Item the caller tried to add

        Raises:
            HtmlError: Always: a document takes content through head() or body()
        """
        raise HtmlError(
            f'A document takes no item of its own: add it through head() or body(), got '
            f'{type(item).__name__}')

    def _coerce(self, item):
        raise HtmlError(
            f'A document takes no item of its own: add it through head() or body(), got '
            f'{type(item).__name__}')

    def inline_str(self):
        raise NotInline('A document is never rendered on one line')

    def generate(self):
        indent = self.indent_str
        yield f'{indent}<!DOCTYPE html>'
        yield f'{indent}<html lang="{escape(self.lang)}">'
        yield from self.head_obj.generate()
        yield from self.body_obj.generate()
        yield f'{indent}</html>'

    def __repr__(self):
        return f'<Document {self.title!r}>'
