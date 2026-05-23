from alasio.codegen.python.obj_base import CodeObject, GatherItems
from alasio.ext.cache import cached_property


class ClosureObject(CodeObject):
    def __init__(self, gen):
        super().__init__(gen)
        self.items: "list[CodeObject]" = []

    def generate_items(self):
        """
        Generate items with automatic blank lines
        """
        prev_item = None
        for item in self.items:
            # Auto blank lines (including leading blanks for the first item)
            lines = self._get_auto_blank_lines(prev_item, item)
            for _ in range(lines):
                yield ''

            yield from item.generate()
            prev_item = item

    def _get_auto_blank_lines(self, prev, curr):
        """
        Calculate how many blank lines needed between prev and curr
        Default to 0, will be override in AutoBlankLineMixin
        """
        return 0

    def generate(self):
        yield from self.generate_items()


class ClosureWithName(ClosureObject):
    # Subclasses override these class attributes for different bracket types.
    closure_start = ''
    closure_end = ''
    closure_empty = ''

    def __init__(self, gen, name):
        super().__init__(gen)
        if self._context_name in ['Dict']:
            name = repr(name)
        self.name = name

    def _get_prefix(self):
        """
        Build the prefix before closure brackets.
        For name=obj, anno=': Type' this returns 'obj: Type = '.
        Override in subclasses (e.g. Object) for a different prefix format.
        """
        if self.name:
            return f'{self.name}{self._anno}{self.between_kv}'
        return ''

    def _get_suffix(self):
        """
        Optional suffix appended after closure_end.
        Override in subclasses (e.g. Literal with default value) for extra content.
        Returns:
            str: Empty string by default.
        """
        return ''

    @cached_property
    def item_str(self):
        """Inline representation, used when nested in another collection."""
        ending = self.line_ending
        if not self.items:
            return f'{self.closure_empty}{ending}'
        items = GatherItems().add(self.items).get_inline()
        return f'{self.closure_start}{items}{self.closure_end}{ending}'

    def generate(self):
        ending = self.line_ending
        suffix = self._get_suffix()
        prefix = self._get_prefix()

        if not self.items:
            yield f'{self.indent_str}{prefix}{self.closure_empty}{suffix}{ending}'
            return

        if self._wrap == 'newline':
            # Each item on its own line
            yield f'{self.indent_str}{prefix}{self.closure_start}'
            for item in self.items:
                yield from item.generate()
            yield f'{self.indent_str}{self.closure_end}{suffix}{ending}'
            return

        # expand: brackets on separate lines, items wrapped inline
        if self._wrap == 'expand':
            items = GatherItems(max_width=True).add(self.items)
            rows = list(items.iter_multiline())
            yield f'{self.indent_str}{prefix}{self.closure_start}'
            for row in rows:
                yield f'{self.indent_str}    {row}'
            yield f'{self.indent_str}{self.closure_end}{suffix}{ending}'
            return

        # wrap() values: 'inline', 'auto' (True), or int: use GatherItems
        items = GatherItems(max_width=self._wrap if self._wrap != 'inline' else False).add(self.items)
        rows = list(items.iter_multiline())

        if len(rows) == 1:
            row = rows[0].rstrip(',')
            yield f'{self.indent_str}{prefix}{self.closure_start}{row}{self.closure_end}{suffix}{ending}'
        else:
            yield f'{self.indent_str}{prefix}{self.closure_start}'
            for row in rows:
                yield f'{self.indent_str}    {row}'
            yield f'{self.indent_str}{self.closure_end}{suffix}{ending}'


class CustomTab(ClosureObject):
    """
    Context manager that generates a prefix line, indented items, and suffix line.
    Items inside the block use the specified line_ending.

    When no prefix/suffix/line_ending is given, passthrough-mode is used:
    items flow into the parent context (backward compatible with the old ApplyTab).

    Examples:
        with gen.tab():                     # simple indent (passthrough)
            gen.Var('x', 1)

        with gen.tab(prefix='lambda: (', suffix=')', line_ending=','):
            gen.Item('x')
            gen.Item('y')
        # lambda: (
        #     'x',
        #     'y',
        # )
    """

    def __init__(self, gen, indent, prefix, suffix, line_ending):
        super().__init__(gen)
        self._indent_tab = indent
        self._custom_prefix = prefix
        self._custom_suffix = suffix
        self._custom_line_ending = line_ending
        self.context_name = 'CustomTab'
        # Passthrough mode: no prefix/suffix/line_ending → items go to parent context
        self._passthrough = not (prefix or suffix or line_ending)

    def __enter__(self):
        self.indent_prev = self.gen.indent
        self.gen.indent = self.indent_prev + self._indent_tab
        if not self._passthrough:
            # Capture items
            self.context_name_prev = self.gen.context_name
            self.context_prev = self.gen.context
            self.gen.context = self
            self.gen.context_name = self.context_name
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.gen.indent = self.indent_prev
        if not self._passthrough:
            self.gen.context = self.context_prev
            self.gen.context_name = self.context_name_prev

    @cached_property
    def line_ending(self):
        return self._custom_line_ending or ','

    def generate(self):
        if self._passthrough:
            return
        if self._custom_prefix:
            yield f'{self.indent_str}{self._custom_prefix}'
        yield from self.generate_items()
        if self._custom_suffix:
            yield f'{self.indent_str}{self._custom_suffix}'


class List(ClosureWithName):
    closure_start = '['
    closure_end = ']'
    closure_empty = '[]'


class Dict(ClosureWithName):
    closure_start = '{'
    closure_end = '}'
    closure_empty = '{}'


class Tuple(ClosureWithName):
    closure_start = '('
    closure_end = ')'
    closure_empty = '()'


class Set(ClosureWithName):
    closure_start = '{'
    closure_end = '}'
    closure_empty = 'set()'


class Object(ClosureWithName):
    """
    Define an object instantiation, e.g. Button('text', key=value).

    Items are function arguments (positional via Item, keyword via Var).

    Examples:
        with gen.Object('button', 'Button'):
            gen.Item('Click me')
            gen.Var('timeout', 10)
        # button = Button(
        #     'Click me',
        #     timeout=10,
        # )
    """

    closure_start = '('
    closure_end = ')'
    closure_empty = '()'

    def __init__(self, gen, name, cls):
        super().__init__(gen, name)
        self.cls = cls
        # Items inside Object use FuncArgs context for correct line_ending=','
        # and between_kv='='
        self.context_name = 'Object'

    def _get_prefix(self):
        """Build prefix: name: Anno = cls  or  name = cls  or just cls."""
        if self.name:
            if self._anno:
                return f'{self.name}{self._anno}{self.between_kv}{self.cls}'
            return f'{self.name}{self.between_kv}{self.cls}'
        return self.cls


class Literal(ClosureWithName):
    """
    Define a variable with a Literal type annotation.
    A default value is only emitted when explicitly set via .Var().

    Examples:
        with gen.Literal('fruit'):
            gen.Item('apple')
            gen.Item('banana')
        # fruit: Literal['apple', 'banana']

        gen.Literal('color').set_literal('t.Literal').Var('red')
        # color: t.Literal[] = 'red'

        gen.Literal('status').wrap('newline')
        # status: Literal[
        #     'active',
        #     'inactive',
        # ]

        gen.Literal('mode').wrap('expand')
        # mode: Literal['a', 'b']
    """

    closure_start = '['
    closure_end = ']'
    closure_empty = '[]'

    def __init__(self, gen, name):
        super().__init__(gen, name)
        self._literal_module = 'Literal'
        self.value = None
        # Literal type annotations are typically inline, even in with blocks
        self._wrap_explicit = True

    def set_literal(self, module):
        """
        Set the Literal type module prefix.

        Args:
            module (str): Module prefix, e.g. 't.Literal' or 'typing.Literal'

        Returns:
            Literal: self for chaining
        """
        self._literal_module = module
        return self

    def _get_prefix(self):
        """Literal prefix: name: module  or just module."""
        if self.name:
            return f'{self.name}: {self._literal_module}'
        return self._literal_module

    def _get_suffix(self):
        """Optional default value suffix."""
        default_val = self._get_default_value()
        if default_val is not None:
            return f'{self.between_kv}{default_val}'
        return ''

    def _get_default_value(self):
        """Return the explicit default value, or None."""
        return self.value

    def _build_items_str(self):
        """
        Build comma-separated items string for the Literal type annotation.
        """
        items = GatherItems().add(self.items)
        inline = items.get_inline()
        return inline.rstrip(',')

    @cached_property
    def item_str(self):
        """Inline representation."""
        ending = self.line_ending
        items_str = self._build_items_str()

        if self.name:
            value = f'{self.name}: {self._literal_module}[{items_str}]'
        else:
            value = f'{self._literal_module}[{items_str}]'

        default_val = self._get_default_value()
        if default_val is not None:
            value += f' = {default_val}'
        return f'{value}{ending}'
