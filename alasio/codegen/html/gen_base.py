"""
The root generator of an html page: the item list, the context and the write.

The design is the one of alasio.codegen.python: a generator holds the objects
of the page, a method of it makes an object and takes it into the current
container, and the generator renders the page one line at a time.

    gen = HtmlGen()
    with gen.Div(class_='card'):
        gen.P('hello')
    gen.generate_str()

    # <div class="card">
    #   <p>hello</p>
    # </div>

The generator holds two pieces of state while a page is built:

- items: the objects of the root level;
- context: the container that takes the objects made now, None at the root.

and one that is read from the objects:

- indent: the level an object made now is at, an object takes it in __init__.

Usage:
    from alasio.codegen.html import HtmlGen

    gen = HtmlGen()
    gen.P('hello')
    gen.write('hello.html')
"""

from alasio.ext.path.atomic import atomic_write

from .obj_base import HtmlObject
from .write import write_if_changed

__all__ = [
    'HtmlGenBase',
]


class HtmlGenBase:
    """
    The state of one page: the objects, the current container, the indent.

    Build one generator per page: the objects of a run are added to it and are
    never removed, so a second page needs a second generator.

    Args:
        indent_size (int): Spaces per indent level. Defaults to 2, the usual of
            html

    Raises:
        ValueError: If the indent size is not positive
    """

    def __init__(self, indent_size=2):
        if indent_size < 1:
            raise ValueError(f'The indent size must be positive: {indent_size!r}')
        self.indent_size = indent_size
        # the level an object made now is at
        self.indent = 0
        # the container that takes the objects made now, None at the root
        self.context = None
        # the objects of the root level
        self.items = []

    def _add_item(self, item):
        """
        Take an object into the current container, or into the root.

        Args:
            item (HtmlObject): Object to take

        Returns:
            HtmlObject: The object, for chaining

        Raises:
            TypeError: If the item is not an html object
            ValueError: If the item belongs to another generator, or the
                current container takes no item (a document)
        """
        if not isinstance(item, HtmlObject):
            raise TypeError(f'The item of a generator is not an html object: {type(item).__name__}')
        if item.gen is not self:
            raise ValueError(
                'The item of a generator was made by another one: an object renders with the '
                'indent size of the generator that made it')
        context = self.context
        if context is None:
            item._attached = True
            self.items.append(item)
        else:
            context.add(item)
        return item

    @property
    def has_content(self):
        """
        Whether the page has at least one object.

        Returns:
            bool: True when the root level holds an object
        """
        return bool(self.items)

    def generate(self):
        """
        Render the page.

        Yields:
            str: One line of the page, indentation included
        """
        for item in self.items:
            yield from item.generate()

    def generate_str(self):
        """
        Render the page as one text.

        The text ends with exactly one newline, so a file written from it is a
        text file of the usual shape. A page with no object renders as the empty
        text, a file of nothing is not a page.

        Returns:
            str: The page, LF line endings
        """
        lines = list(self.generate())
        if not lines:
            return ''
        return '\n'.join(lines) + '\n'

    def print(self):
        """
        Print the page, for a look at it while the generator is written.
        """
        for line in self.generate():
            print(line)

    def write(self, file, gitadd=None, skip_same=True):
        """
        Write the page to a file.

        Args:
            file (str): Path of the file, the parent folder is created when it
                is missing
            gitadd (GitAdd, optional): Git add of the project, the file is
                staged when it was written. Defaults to None, nothing is staged
            skip_same (bool): True to read the file first and skip the write
                when it already holds the page. This costs a read and saves a
                write, the publish record of a rerun stays as it was. False to
                write whatever the file holds

        Returns:
            bool: True when the file was written

        Raises:
            OSError: If the file cannot be read or written
        """
        data = self.generate_str()
        if skip_same:
            written = write_if_changed(file, data)
        else:
            atomic_write(file, data)
            written = True
        if written and gitadd is not None:
            gitadd.stage_add(file)
        return written

    def __repr__(self):
        return f'<{type(self).__name__} {len(self.items)} item(s)>'
