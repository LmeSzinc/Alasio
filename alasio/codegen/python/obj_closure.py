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
    def __init__(self, gen, name):
        super().__init__(gen)
        if self._context_name in ['Dict']:
            name = repr(name)
        self.name = name


class List(ClosureWithName):
    @cached_property
    def item_str(self):
        # Inline representation, used when nested in another collection
        ending = self.line_ending
        if not self.items:
            return f'[]{ending}'
        items = GatherItems().add(self.items).get_inline()
        return f'[{items}]{ending}'

    def generate(self):
        ending = self.line_ending
        prefix = f'{self.name}{self._anno}{self.between_kv}' if self.name else ''

        if not self.items:
            yield f'{self.indent_str}{prefix}[]{ending}'
            return

        if self._wrap == 'always':
            yield f'{self.indent_str}{prefix}['
            for item in self.items:
                yield from item.generate()
            yield f'{self.indent_str}]{ending}'
            return

        # wrap=False or wrap=int: use GatherItems for width-aware compact output
        items = GatherItems(max_width=self._wrap if self._wrap is not False else False).add(self.items)
        rows = list(items.iter_multiline())
        if len(rows) == 1:
            yield f'{self.indent_str}{prefix}[{rows[0]}]{ending}'
            return

        # Multi row
        yield f'{self.indent_str}{prefix}['
        for row in rows:
            yield f'{self.indent_str}    {row}'
        yield f'{self.indent_str}]{ending}'


class Dict(ClosureWithName):
    @cached_property
    def item_str(self):
        # Inline representation, used when nested in another collection
        ending = self.line_ending
        if not self.items:
            return f'{{}}{ending}'
        items = GatherItems().add(self.items).get_inline()  # type: ignore
        return f'{{{items}}}{ending}'

    def generate(self):
        ending = self.line_ending
        prefix = f'{self.name}{self._anno}{self.between_kv}' if self.name else ''

        if not self.items:
            yield f'{self.indent_str}{prefix}{{}}{ending}'
            return

        if self._wrap == 'always':
            yield f'{self.indent_str}{prefix}{{'
            for item in self.items:
                yield from item.generate()
            yield f'{self.indent_str}}}{ending}'
            return

        # wrap=False or wrap=int: use GatherItems for width-aware compact output
        items = GatherItems(max_width=self._wrap if self._wrap is not False else False).add(self.items)
        rows = list(items.iter_multiline())
        if len(rows) == 1:
            yield f'{self.indent_str}{prefix}{{{rows[0]}}}{ending}'
            return

        # Multi row
        yield f'{self.indent_str}{prefix}{{'
        for row in rows:
            yield f'{self.indent_str}    {row}'
        yield f'{self.indent_str}}}{ending}'


class Tuple(ClosureWithName):
    @cached_property
    def item_str(self):
        # Inline representation, used when nested in another collection
        ending = self.line_ending
        if not self.items:
            return f'(){ending}'
        items = GatherItems().add(self.items).get_inline()  # type: ignore
        return f'({items}){ending}'

    def generate(self):
        ending = self.line_ending
        prefix = f'{self.name}{self._anno}{self.between_kv}' if self.name else ''

        if not self.items:
            yield f'{self.indent_str}{prefix}(){ending}'
            return

        if self._wrap == 'always':
            yield f'{self.indent_str}{prefix}('
            for item in self.items:
                yield from item.generate()
            yield f'{self.indent_str}){ending}'
            return

        # wrap=False or wrap=int: use GatherItems for width-aware compact output
        items = GatherItems(max_width=self._wrap if self._wrap is not False else False).add(self.items)
        rows = list(items.iter_multiline())
        if len(rows) == 1:
            yield f'{self.indent_str}{prefix}({rows[0]}){ending}'
            return

        # Multi row
        yield f'{self.indent_str}{prefix}('
        for row in rows:
            yield f'{self.indent_str}    {row}'
        yield f'{self.indent_str}){ending}'


class Set(ClosureWithName):
    @cached_property
    def item_str(self):
        # Inline representation, used when nested in another collection
        ending = self.line_ending
        if not self.items:
            return f'set(){ending}'
        items = GatherItems().add(self.items).get_inline()  # type: ignore
        return f'{{{items}}}{ending}'

    def generate(self):
        ending = self.line_ending
        prefix = f'{self.name}{self._anno}{self.between_kv}' if self.name else ''

        if not self.items:
            yield f'{self.indent_str}{prefix}set(){ending}'
            return

        if self._wrap == 'always':
            yield f'{self.indent_str}{prefix}{{'  # noqa: E123
            for item in self.items:
                yield from item.generate()
            yield f'{self.indent_str}}}{ending}'
            return

        # wrap=False or wrap=int: use GatherItems for width-aware compact output
        items = GatherItems(max_width=self._wrap if self._wrap is not False else False).add(self.items)
        rows = list(items.iter_multiline())
        if len(rows) == 1:
            yield f'{self.indent_str}{prefix}{{{rows[0]}}}{ending}'
            return

        # Multi row
        yield f'{self.indent_str}{prefix}{{'  # noqa: E123
        for row in rows:
            yield f'{self.indent_str}    {row}'
        yield f'{self.indent_str}}}{ending}'
