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
        prefix = f'{self.name}{self._anno}{self.between_kv}' if self.name else ''

        if not self.items:
            yield f'{self.indent_str}{prefix}{self.closure_empty}{ending}'
            return

        if self._wrap == 'newline':
            # Each item on its own line
            yield f'{self.indent_str}{prefix}{self.closure_start}'
            for item in self.items:
                yield from item.generate()
            yield f'{self.indent_str}{self.closure_end}{ending}'
            return

        if self._wrap == 'expand':
            # Brackets on separate lines, items wrapped inline
            yield f'{self.indent_str}{prefix}{self.closure_start}'
            items = GatherItems(max_width=True).add(self.items)
            for row in items.iter_multiline():
                yield f'{self.indent_str}    {row}'
            yield f'{self.indent_str}{self.closure_end}{ending}'
            return

        # wrap=False or wrap=int: use GatherItems for width-aware compact output
        items = GatherItems(max_width=self._wrap if self._wrap is not False else False).add(self.items)
        rows = list(items.iter_multiline())
        if len(rows) == 1:
            # Strip trailing comma in inline single-row output
            row = rows[0].rstrip(',')
            yield f'{self.indent_str}{prefix}{self.closure_start}{row}{self.closure_end}{ending}'
            return

        # Multi row
        yield f'{self.indent_str}{prefix}{self.closure_start}'
        for row in rows:
            yield f'{self.indent_str}    {row}'
        yield f'{self.indent_str}{self.closure_end}{ending}'


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

    def __init__(self, gen, name):
        super().__init__(gen, name)
        self._literal_module = 'Literal'
        self.value = None
        self._wrap = False  # Default to inline, unlike List/Dict which default to 'newline'

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

    def generate(self):
        ending = self.line_ending

        if self.name:
            prefix = f'{self.name}: {self._literal_module}'
        else:
            prefix = self._literal_module

        suffix = ''
        default_val = self._get_default_value()
        if default_val is not None:
            suffix = f' = {default_val}'

        if not self.items:
            yield f'{self.indent_str}{prefix}[]{suffix}{ending}'
            return

        if self._wrap == 'newline':
            # Each item on its own line
            yield f'{self.indent_str}{prefix}['
            for item in self.items:
                yield from item.generate()
            yield f'{self.indent_str}]{suffix}{ending}'
            return

        if self._wrap == 'expand':
            # Brackets on separate lines, items wrapped inline
            yield f'{self.indent_str}{prefix}['
            items = GatherItems(max_width=self._wrap if self._wrap is not False else False).add(self.items)
            rows = list(items.iter_multiline())
            for row in rows:
                yield f'{self.indent_str}    {row}'
            yield f'{self.indent_str}]{suffix}{ending}'
            return

        items = GatherItems(max_width=self._wrap if self._wrap is not False else False).add(self.items)
        rows = list(items.iter_multiline())
        if len(rows) == 1:
            yield f'{self.indent_str}{prefix}[{rows[0].rstrip(",")}]{suffix}{ending}'
            return

        yield f'{self.indent_str}{prefix}['
        for row in rows:
            yield f'{self.indent_str}    {row}'
        yield f'{self.indent_str}]{suffix}{ending}'
