from alasio.codegen.python.base import CodeObject, GatherItems
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
            # Auto blank lines
            if prev_item is not None:
                lines = self._get_auto_blank_lines(prev_item, item)
                for _ in range(lines):
                    yield ''

            yield from item.generate()
            prev_item = item

    def _get_auto_blank_lines(self, prev, curr):
        """
        Calculate how many blank lines needed between prev and curr
        Default to 0, will be override in ClosureWithAutoBlankLine
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

        if self._wrap == 'always':
            yield f'{self.indent_str}{prefix}{self.closure_start}'
            for item in self.items:
                yield from item.generate()
            yield f'{self.indent_str}{self.closure_end}{ending}'
            return

        # wrap=False or wrap=int: use GatherItems for width-aware compact output
        items = GatherItems(max_width=self._wrap if self._wrap is not False else False).add(self.items)
        rows = list(items.iter_multiline())
        if len(rows) == 1:
            yield f'{self.indent_str}{prefix}{self.closure_start}{rows[0]}{self.closure_end}{ending}'
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
