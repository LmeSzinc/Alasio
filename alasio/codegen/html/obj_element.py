"""
The element of a page: a tag, its attributes and its children.

Two objects live here:

- Element: a tag with content. The tag table decides which tags are void (an
  empty element, `<br>`, `<meta>`, `<img>`): a void tag renders no closing tag
  and takes no child, so `<br>x</br>` cannot be written by mistake;
- Line: no tag at all, the children joined on one line. It is what makes an
  anchor and the line break after it one line instead of two.

The void tags of the table are the ones of HTML: any other name is an ordinary
element, so a custom element (`my-widget`) needs no entry.

Usage:
    from alasio.codegen.html import HtmlGen

    gen = HtmlGen()
    gen.A('httpx2', href='httpx2/index.html')     # <a href="httpx2/index.html">httpx2</a>
    gen.Br()                                      # <br>
    with gen.Line():
        gen.A('x', href='y')
        gen.Br()                                  # <a href="y">x</a><br>
"""

from .obj_base import HtmlContainer, HtmlError, NotInline, check_tag_name, normalize_attrs, render_attrs
from .obj_simple import as_text

__all__ = [
    'ELEMENTS',
    'TAG_TABLE',
    'VOID_ELEMENTS',
    'Element',
    'Line',
]

# The html elements that hold content, in the order of the html reference
ELEMENTS = (
    'a', 'abbr', 'address', 'article', 'aside', 'audio',
    'b', 'bdi', 'bdo', 'blockquote', 'body', 'button',
    'canvas', 'caption', 'cite', 'code', 'colgroup',
    'data', 'datalist', 'dd', 'del', 'details', 'dfn', 'dialog', 'div', 'dl', 'dt',
    'em',
    'fieldset', 'figcaption', 'figure', 'footer', 'form',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'head', 'header', 'hgroup', 'html',
    'i', 'iframe', 'ins',
    'kbd',
    'label', 'legend', 'li',
    'main', 'map', 'mark', 'menu', 'meter',
    'nav', 'noscript',
    'object', 'ol', 'optgroup', 'option', 'output',
    'p', 'picture', 'pre', 'progress',
    'q',
    'rp', 'rt', 'ruby',
    's', 'samp', 'script', 'section', 'select', 'slot', 'small', 'span', 'strong', 'style',
    'sub', 'summary', 'sup',
    'table', 'tbody', 'td', 'template', 'textarea', 'tfoot', 'th', 'thead', 'time', 'title', 'tr',
    'u', 'ul',
    'var', 'video',
)

# The html elements with no content and no closing tag
VOID_ELEMENTS = (
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source',
    'track', 'wbr',
)

# {tag: True when the tag is void}, the one table the generator and the
# Element class read, so a tag is added in one place
TAG_TABLE = {tag: False for tag in ELEMENTS}
TAG_TABLE.update({tag: True for tag in VOID_ELEMENTS})


def _has_content(children):
    """
    Whether a sequence of children holds anything once the None are skipped.

    Args:
        children (Iterable): Children as a caller passed them

    Returns:
        bool: True when at least one child is not None
    """
    for child in children:
        if child is None:
            continue
        if isinstance(child, (list, tuple)):
            if _has_content(child):
                return True
            continue
        return True
    return False


class Element(HtmlContainer):
    """
    One element: a tag, its attributes and its children.

    Args:
        gen (HtmlGenBase): Generator of the page
        tag (str): Tag name, any case, rendered lowercase
        *children (HtmlObject | str | int | float | list | tuple | None):
            Content of the element, in the order it is rendered. A list or a
            tuple is flattened, None is skipped
        **attrs (str | bool | list | tuple | None): Attributes, the order of
            the arguments is the order of the rendered attributes, see
            normalize_attrs() for the name and value rules

    Raises:
        ValueError: If the tag or an attribute name is not an html name, if two
            attributes reach the same name, or if a void tag gets content
        TypeError: If a child is not an html object, a text or a number
    """

    def __init__(self, gen, tag, *children, **attrs):
        super().__init__(gen)
        self.tag = check_tag_name(tag)
        self.is_void = TAG_TABLE.get(self.tag, False)
        if self.is_void and _has_content(children):
            raise ValueError(f'The html void element <{self.tag}> has no content')
        self.attrs = normalize_attrs(attrs)
        attrs_str = render_attrs(self.attrs)
        self.opening = f'<{self.tag}{attrs_str}>'
        self.closing = f'</{self.tag}>'
        self.add_all(children)

    def _coerce(self, item):
        return as_text(self.gen, item)

    def __enter__(self):
        if self.is_void:
            raise HtmlError(f'The html void element <{self.tag}> has no content')
        return super().__enter__()

    def inline_str(self):
        if self.is_void:
            return self.opening
        if self._is_newline() and self.children:
            raise NotInline(f'<{self.tag}> is asked to break its children into lines')
        content = ''.join(child.inline_str() for child in self.children)
        return f'{self.opening}{content}{self.closing}'

    def _generate_block(self):
        indent = self.indent_str
        yield f'{indent}{self.opening}'
        for child in self.children:
            yield from child.generate()
        yield f'{indent}{self.closing}'

    def generate(self):
        if self.is_void:
            yield f'{self.indent_str}{self.opening}'
            return
        if self._wrap != 'newline':
            try:
                inline = self.inline_str()
            except NotInline:
                inline = None
            if inline is not None and self._fits(inline):
                yield f'{self.indent_str}{inline}'
                return
        yield from self._generate_block()

    def __repr__(self):
        return f'<Element <{self.tag}>>'


class Line(HtmlContainer):
    """
    The children of a line, with no tag around them.

    A container that puts one child per line (a body, a div in newline mode)
    would put an anchor and the line break after it on two lines; a Line is what
    keeps them together, which is the shape the wheels of a pip index page take.

    Args:
        gen (HtmlGenBase): Generator of the page
        *children (HtmlObject | str | int | float | list | tuple | None):
            Content of the line

    Raises:
        TypeError: If a child is not an html object, a text or a number
    """

    def __init__(self, gen, *children):
        super().__init__(gen)
        # a line is one line: entering it does not switch it to the block form
        self._wrap_explicit = True
        self.add_all(children)

    def _coerce(self, item):
        return as_text(self.gen, item)

    def inline_str(self):
        return ''.join(child.inline_str() for child in self.children)

    def generate(self):
        try:
            content = self.inline_str()
        except NotInline as e:
            raise HtmlError(f'A line holds a fragment that is not rendered on one line: {e}') from e
        yield f'{self.indent_str}{content}'

    def __repr__(self):
        return f'<Line {len(self.children)} child(ren)>'
