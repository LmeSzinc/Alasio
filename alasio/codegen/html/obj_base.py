"""
Base pieces of the html page objects: escaping, attribute names, indent.

A page is a tree of objects. Every object remembers the generator that made it
and the indent level it was made at, so it can render itself without a parent
pointer:

    gen = HtmlGen()
    with gen.Div(class_='card'):
        gen.P('hello')

    # <div class="card">
    #   <p>hello</p>
    # </div>

An object renders itself twice:

- generate() yields the lines of its block form, each line already carrying the
  indentation, the form a container uses when it puts one item per line;
- inline_str() answers the single line the object takes when its parent renders
  on one line.

An object that cannot be one line raises NotInline. The parent that asked then
falls back to the block form: a layout difference must never fail a page.

Usage:
    from alasio.codegen.html import HtmlGen, escape

    escape('<a>')      # '&lt;a&gt;'

    gen = HtmlGen()
    gen.P('hello')
    gen.generate_str()  # '<p>hello</p>\n'
"""

import re
from html import escape as _escape

__all__ = [
    'DEFAULT_WIDTH',
    'HtmlError',
    'HtmlObject',
    'NotInline',
    'check_tag_name',
    'describe',
    'escape',
    'normalize_attrs',
    'render_attrs',
]

# The width a page is rendered to before an element is broken into lines
DEFAULT_WIDTH = 120

# A tag name: a letter, then letters, digits and '-', so a custom element
# ('my-widget') is accepted and a name that cannot be a tag is refused
TAG_NAME = re.compile(r'[a-z][a-z0-9-]*\Z')

# An attribute name: the HTML name characters, the ':' of a namespaced
# attribute ('xml:lang') and the '-' of the data attributes included
ATTR_NAME = re.compile(r'[a-z_:][-a-z0-9_:.]*\Z')


class HtmlError(Exception):
    """
    Raised when a page cannot be generated as it was asked.

    Every check of the module raises an error instead of dropping the offending
    value: a page is written to a server, a silently missing attribute or link
    is a defect a reader of the page cannot see.
    """


class NotInline(HtmlError):
    """
    Raised by inline_str() when the fragment cannot be rendered as one line.

    It is internal to the renderer: a parent catches it and renders its children
    in block form instead, the caller never sees it.
    """


def escape(text):
    """
    Escape a value for a text node or a quoted attribute value.

    One rule serves both contexts: escaping the quote characters of a text node
    is harmless, and a single function cannot be applied to the wrong context.

    Args:
        text (str): Text to escape, any object is rendered with str() first

    Returns:
        str: The text with '&', '<', '>', '"' and "'" as HTML entities
    """
    return _escape(str(text), quote=True)


def normalize_attrs(attrs):
    """
    Normalize the keyword attributes of an element.

    The rules:

    - a trailing '_' lets a name that is a python keyword through, so 'class_'
      reaches the 'class' attribute and 'for_' reaches 'for';
    - the other '_' become '-', so 'data_id' reaches 'data-id';
    - the name is lowercase, HTML attribute names are case insensitive;
    - a value of None or False drops the attribute, True renders it bare (a
      boolean attribute), a list or a tuple is joined with one space (the class
      attribute) and any other value is a text;
    - a set is refused: it has no order, so the same page would render
      differently between two runs.

    Args:
        attrs (dict): Attributes as the caller wrote them

    Returns:
        dict: {name: value} in the order of the declaration, the dropped
            values are not in it

    Raises:
        TypeError: If a name is not a text
        ValueError: If a name is not an HTML attribute name, if two names
            normalize to the same attribute, or if a value is a set
    """
    out = {}
    seen = set()
    for key, value in attrs.items():
        if not isinstance(key, str):
            raise TypeError(f'The name of an html attribute is not a text: {key!r}')
        name = key[:-1] if key.endswith('_') else key
        name = name.replace('_', '-').lower()
        if not ATTR_NAME.match(name):
            raise ValueError(f'Invalid html attribute name: {key!r}')
        if name in seen:
            raise ValueError(
                f'Duplicate html attribute "{name}": {key!r} reaches an attribute another name did')
        seen.add(name)
        if value is None or value is False:
            continue
        if isinstance(value, (set, frozenset)):
            raise ValueError(
                f'The html attribute "{name}" is a set, a set has no order: use a list or a tuple')
        out[name] = value
    return out


def render_attrs(attrs):
    """
    Render normalized attributes.

    Args:
        attrs (dict): Attributes from normalize_attrs()

    Returns:
        str: The attributes with a leading space each, '' when there is none
    """
    parts = []
    for name, value in attrs.items():
        if value is True:
            parts.append(f' {name}')
        elif isinstance(value, (list, tuple)):
            parts.append(f' {name}="{escape(" ".join(str(item) for item in value))}"')
        else:
            parts.append(f' {name}="{escape(value)}"')
    return ''.join(parts)


def check_tag_name(tag):
    """
    Check a tag name and answer its lowercase form.

    Args:
        tag (str): Tag name, any case

    Returns:
        str: The tag name in lowercase

    Raises:
        TypeError: If the name is not a text
        ValueError: If the name is not an html tag name
    """
    if not isinstance(tag, str):
        raise TypeError(f'An html tag name is not a text: {type(tag).__name__}')
    name = tag.lower()
    if not TAG_NAME.match(name):
        raise ValueError(f'Invalid html tag name: {tag!r}')
    return name


def describe(obj):
    """
    Name an object the way an error message should show it.

    Args:
        obj (HtmlObject): Object to name

    Returns:
        str: The tag of an element, the class name otherwise
    """
    tag = getattr(obj, 'tag', '')
    if tag:
        return f'<{tag}>'
    return f'<{type(obj).__name__}>'


class HtmlObject:
    """
    One object of a page: a tag, a text, a fragment, a whole document.

    Args:
        gen (HtmlGenBase): Generator the object belongs to, the object takes
            the current indent level and renders with the indent size of it

    Attributes:
        gen (HtmlGenBase): Generator of the object
    """

    def __init__(self, gen):
        self.gen = gen
        # the indent level the object was made at, set by the container that
        # takes it (see _reindent) when the two disagree
        self._indent = gen.indent
        # the layout of the block form: 'inline', 'newline', 'auto' or a width
        self._wrap = 'inline'
        # True once wrap() was called, so __enter__ does not change the mode
        self._wrap_explicit = False
        # True once the object is in the tree of a page: a child of a new
        # container must be detached, see HtmlContainer.add_all
        self._attached = False
        # (context, indent) of every with block this object is in, newest last
        self._entered = []

    # -- layout -----------------------------------------------------------

    @property
    def indent_str(self):
        """
        The indentation of the object at its level.

        Returns:
            str: One space per column of the level
        """
        return ' ' * (self.gen.indent_size * self._indent)

    def wrap(self, wrap='auto'):
        """
        Set the layout of the block form.

        Args:
            wrap (str | int): 'inline' renders the whole element on one line,
                'newline' puts the opening tag, every child and the closing tag
                on a line of their own, 'auto' picks 'inline' while the line
                fits DEFAULT_WIDTH and 'newline' after it, and an int picks
                between the two at that width

        Returns:
            HtmlObject: self for chaining

        Raises:
            ValueError: If the mode is not one of the above
        """
        if wrap == 'always':
            # kept for the callers of the python generator, same meaning
            wrap = 'newline'
        if isinstance(wrap, bool):
            raise ValueError(f'Unknown wrap mode: {wrap!r}')
        if isinstance(wrap, int):
            if wrap < 1:
                raise ValueError(f'The width of a wrap mode must be positive: {wrap!r}')
        elif wrap not in ('inline', 'newline', 'auto'):
            raise ValueError(f'Unknown wrap mode: {wrap!r}')
        self._wrap = wrap
        self._wrap_explicit = True
        return self

    def _is_newline(self):
        """
        Whether the block form must break the element into lines, without
        looking at the content.

        Returns:
            bool: True for 'newline', False for 'inline' and 'auto'
        """
        return self._wrap == 'newline'

    def _fits(self, inline):
        """
        Whether a rendered line keeps the object in the inline form.

        Args:
            inline (str): The object rendered on one line

        Returns:
            bool: True when the current mode keeps the line
        """
        if self._wrap == 'newline':
            return False
        if self._wrap == 'inline':
            return True
        width = self._wrap if isinstance(self._wrap, int) else DEFAULT_WIDTH
        return len(self.indent_str) + len(inline) <= width

    # -- the tree ---------------------------------------------------------

    @property
    def child_indent(self):
        """
        The indent level of the content of the object.

        Returns:
            int: One level below the object
        """
        return self._indent + 1

    def __enter__(self):
        """
        Enter the object as a container of the objects made in the block.

        Entering a container means the caller is about to put items in it, so a
        container that was not given a wrap mode switches to 'newline': one
        item per line is the layout a block of content is written in.

        Returns:
            HtmlObject: self
        """
        if not self._wrap_explicit and self._wrap == 'inline':
            self._wrap = 'newline'
        # a stack and not one pair: entering an object twice (a with block in
        # the with block of the same object) must restore level by level
        self._entered.append((self.gen.context, self.gen.indent))
        self.gen.indent = self.child_indent
        self.gen.context = self
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Leave the container, restoring the context and the indent of the parent.

        The restore runs on an exception too, so a page that fails half way
        leaves the generator in the state its caller found it in.
        """
        context, indent = self._entered.pop()
        self.gen.context = context
        self.gen.indent = indent
        return False

    def _iter_children(self):
        """
        The children of the object, for the operations that walk the tree.

        Returns:
            tuple: The children, empty for a leaf
        """
        return ()

    def _reindent(self, indent):
        """
        Move the object and its whole subtree to an indent level.

        A container calls it when it takes an object that was made at another
        level, e.g. an element built at the module level and passed to a
        document body: the object and its children keep their relative depth.

        Args:
            indent (int): New indent level

        Returns:
            HtmlObject: self for chaining
        """
        delta = indent - self._indent
        if delta == 0:
            return self
        self._indent = indent
        for child in self._iter_children():
            child._reindent(child._indent + delta)
        return self

    # -- rendering --------------------------------------------------------

    def generate(self):
        """
        Render the block form of the object.

        Yields:
            str: One line of the page, indentation included
        """
        raise NotImplementedError

    def inline_str(self):
        """
        Render the object as one line, without indentation.

        Returns:
            str: The object on one line

        Raises:
            NotInline: If the object cannot be rendered on one line
        """
        raise NotInline(f'{describe(self)} is not rendered on one line')

    def __repr__(self):
        return describe(self)


class HtmlContainer(HtmlObject):
    """
    An object that holds child objects.

    A container takes a child at the indent level below its own, and refuses a
    child of another generator: the child renders with the indent size of its
    own generator, so it would break the indentation of the page.
    """

    def __init__(self, gen):
        super().__init__(gen)
        self.children = []

    def _iter_children(self):
        return tuple(self.children)

    def add(self, item):
        """
        Take an object as a child of the container.

        Args:
            item (HtmlObject | str | int | float): Child to take, a text or a
                number is taken as a text node of the container

        Returns:
            HtmlObject: The child, for chaining

        Raises:
            TypeError: If the item is not an html object, a text or a number
            ValueError: If the item belongs to another generator
        """
        if not isinstance(item, HtmlObject):
            item = self._coerce(item)
        if item.gen is not self.gen:
            raise ValueError(
                f'The child of {describe(self)} belongs to another generator, '
                f'a page cannot mix the indent of two of them')
        item._reindent(self.child_indent)
        item._attached = True
        self.children.append(item)
        return item

    def _coerce(self, item):
        """
        Turn a value that is not an html object into one.

        Args:
            item (Any): Child value

        Returns:
            HtmlObject: The value as an object

        Raises:
            TypeError: If the value cannot be a child of the container
        """
        raise TypeError(
            f'The child of {describe(self)} is not an html object: {type(item).__name__}')

    def add_all(self, children):
        """
        Take several objects as children, flattening the lists and tuples.

        A value of None is skipped, so a conditional child needs no branch
        around it: Element('p', 'a', None, 'b').

        A child that is already in the tree of the page is refused: it was made
        by a call like gen.P('x') that already put it at the current level, and
        taking it again would render it twice, once where nobody asked for it.
        A block of content is built with a with block, a detached object with the
        class (Element(gen, 'p', 'x')).

        Args:
            children (Iterable): Children, a list or a tuple is flattened

        Returns:
            HtmlContainer: self for chaining

        Raises:
            TypeError: If a child is not an html object, a text or a number
            HtmlError: If a child is already in the tree of the page
        """
        for child in children:
            if child is None:
                continue
            if isinstance(child, (list, tuple)):
                self.add_all(child)
                continue
            if isinstance(child, HtmlObject) and child._attached:
                raise HtmlError(
                    f'The child of {describe(self)} is already in the page: a call like '
                    f'gen.P(...) takes the object into the current level as it makes it. '
                    f'Build the block with a with block, or the object detached with the class')
            self.add(child)
        return self
