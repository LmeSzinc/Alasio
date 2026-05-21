import inspect

from alasio.codegen.python.base import CodeObject, GatherItems
from alasio.ext.cache import cached_property


class Pass(CodeObject):
    """
    Define a `pass`
    """

    def generate(self):
        yield f'{self.indent_str}pass'


class Empty(CodeObject):
    """
    Generate blank lines
    """

    def __init__(self, gen, lines=1):
        super().__init__(gen)
        self.lines = lines

    def generate(self):
        for _ in range(self.lines):
            yield ''


class Raw(CodeObject):
    """
    Raw string content
    """

    def __init__(self, gen, text):
        super().__init__(gen)
        # trim leading/trailing empty lines and dedent
        text = inspect.cleandoc(text)
        self.lines = text.splitlines()

    def generate(self):
        for line in self.lines:
            yield f'{self.indent_str}{line}'


class Comment(CodeObject):
    """
    Define a comment
    """

    def __init__(self, gen, text):
        super().__init__(gen)
        self.text = text

    def generate(self):
        yield f'{self.indent_str}# {self.text}'


class MultilineComment(CodeObject):
    """
    Define a multiline comment using triple quotes
    """

    def __init__(self, gen, text):
        super().__init__(gen)
        self.text = text

    def generate(self):
        yield f'{self.indent_str}"""'
        for line in self.text.splitlines():
            yield f'{self.indent_str}{line}'
        yield f'{self.indent_str}"""'


class Var(CodeObject):
    """
    Define a variable in line
    {name} = {value}
    """

    def __init__(self, gen, name, value):
        super().__init__(gen)
        if self._context_name in ['Dict']:
            name = repr(name)
        self.name = name
        self.value = ''
        self.Var(value)

    @cached_property
    def item_str(self):
        name = self.name
        anno = self._anno
        # name: anno = value,
        return f'{name}{anno}{self.between_kv}{self.value}{self.line_ending}'

    def generate(self):
        yield f'{self.indent_str}{self.item_str}'


class Anno(Var):
    """
    Define a annotation in line
    {name}: {anno}
    """

    def __init__(self, gen, name, anno):
        super().__init__(gen, name, None)
        self.Anno(anno)
        self.value = None

    @cached_property
    def item_str(self):
        name = self.name
        anno = self._anno
        if self.value is not None:
            # name: anno = value,
            return f'{name}{anno}{self.between_kv}{self.value}{self.line_ending}'
        # name: anno
        return f'{name}{anno}'


class Item(CodeObject):
    """
    Define an item in List/Tuple/Set
    {value},
    """

    def __init__(self, gen, value):
        super().__init__(gen)
        if self.gen.context_name in ['ClassInherit', 'FuncArgs']:
            self.value = value
        else:
            self.value = repr(value)

    @cached_property
    def item_str(self):
        return f'{self.value}{self.line_ending}'

    def generate(self):
        yield f'{self.indent_str}{self.item_str}'


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
        """
        # If manual empty lines, skip auto
        if isinstance(prev, Empty) or isinstance(curr, Empty):
            return 0

        indent = curr._indent
        # PEP8: 2 blank lines between top-level definitions
        if indent == 0:
            # 2 lines before Class/Def
            if isinstance(curr, (Class, Def)):
                return 2
            # 2 lines after Class/Def
            if isinstance(prev, (Class, Def)) and not isinstance(curr, (Comment, MultilineComment, Import, FromImport)):
                return 2
            # 1 line after Import block or between top-level variables
            if isinstance(prev, (Import, FromImport)) and not isinstance(curr, (Import, FromImport)):
                return 1

        # PEP8: 1 blank line between methods in a class
        else:
            if isinstance(curr, Def):
                return 1
            if isinstance(prev, Def) and not isinstance(curr, (Comment, MultilineComment)):
                return 1

        return 0

    def generate(self):
        yield from self.generate_items()


class Import(CodeObject):
    """
    import {module} [as {alias}]
    """

    def __init__(self, gen, module, alias=None):
        super().__init__(gen)
        self.module = module
        self.alias = alias
        self._lazy = False
        self._used = False
        self.import_from = ''

    def as_(self, alias):
        prev_name = self.varname
        self.alias = alias
        # Clear cached property
        cached_property.pop(self, 'varname')
        cached_property.pop(self, 'item_str')

        # Update registry
        if self.gen._import_registry.get(prev_name) is self:
            del self.gen._import_registry[prev_name]
        self.gen._import_registry[self.varname] = self
        return self

    def lazy(self):
        """
        Mark import object as lazy generate

        imp = gen.Import('typing').lazy()
        # this won't be generated, unless you call `imp.use()` or `gen.use_import('typing')`
        imp = gen.Import('typing').as_('t').lazy()
        # this won't be generated, unless you call `imp.use()` or `gen.use_import('t')`
        imp = gen.FromImport('typing').Import('List').lazy()
        # this won't be generated, unless you call `imp.use()` or `gen.use_import('List')`
        """
        self._lazy = True
        return self

    def use(self):
        """
        Mark import object as used
        """
        self._used = True
        return self

    @cached_property
    def lib(self):
        """
        Get library of module
        import lzma -> "lzma"
        from pydantic import BaseModel -> "pydantic"
        from module.xxx import Class -> "module"  # local module
        from .relative import func -> ""  # relative import
        """
        if self.import_from:
            return self.import_from.partition('.')[0]
        return self.module.partition('.')[0]

    @cached_property
    def varname(self):
        """
        Get variable name of import result
        import lzma -> "lzma"
        import typing as t -> "t"
        from pydantic import BaseModel -> "BaseModel"
        from .relative import func -> "func"
        """
        if self.alias:
            return self.alias
        return self.module

    @cached_property
    def module_type(self):
        """
        Get module type (standard_library, third_party, local_project)
        """
        return self.gen.scanner._classify_single_module(self.lib)

    @cached_property
    def item_str(self):
        if self.alias:
            return f'{self.module} as {self.alias}'
        return self.module

    def generate(self):
        if self._lazy and not self._used:
            return
        if self.import_from or self._context_name == 'FromImport':
            yield f'{self.indent_str}{self.item_str}{self.line_ending}'
        else:
            yield f'{self.indent_str}import {self.item_str}'


class FromImport(ClosureObject):
    """
    from {module} import {items}
    """

    def __init__(self, gen, module):
        super().__init__(gen)
        self.module = module
        self._is_with = False

    def __enter__(self):
        self._is_with = True
        return super().__enter__()

    @cached_property
    def module_type(self):
        """
        Get module type (standard_library, third_party, local_project)
        """
        lib = self.module.partition('.')[0]
        return self.gen.scanner._classify_single_module(lib)

    def Import(self, name, alias=None):
        item = Import(self.gen, name, alias=alias)
        item.import_from = self.module
        self.items.append(item)
        self.gen._import_registry[name] = item
        return item

    def generate(self):
        # Filter used items
        items = [i for i in self.items if isinstance(i, Import) and (not i._lazy or i._used)]
        if not items:
            return

        # Sort items by name
        items.sort(key=lambda x: x.module)

        if len(items) == 1:
            # from module import item
            yield f'{self.indent_str}from {self.module} import {items[0].item_str}'
        else:
            # from module import (
            yield f'{self.indent_str}from {self.module} import ('
            #     item1,
            #     item2,
            with self.apply_context_name('List'):
                for item in items:
                    # Items already have _is_from_item=True, but we need to ensure correct indent
                    item._indent = self.gen.indent + self.tab
                    # In multiline from-import, we need commas
                    yield f'{item.indent_str}{item.item_str},'
            # )
            yield f'{self.indent_str})'


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


class Class(ClosureObject):
    def __init__(self, gen, name):
        super().__init__(gen)
        self.name = name
        self._inherit_args = []
        self._inherit_kwargs = {}

    def set_inherit(self, *args: "str | list[str] | tuple[str] | set[str]", **kwargs):
        """
        Examples:
            with gen.Class('User').set_inherit('BaseModel', metaclass='Singleton'):
                ...
        """
        with self.apply_context_name('ClassInherit'):
            inherit_args = []
            for arg in args:
                if isinstance(arg, str):
                    item = Item(self.gen, arg)
                    inherit_args.append(item)
                elif isinstance(arg, (list, tuple, set)):
                    for sub_arg in arg:
                        item = Item(self.gen, sub_arg)
                        inherit_args.append(item)

            inherit_kwargs = {}
            for key, value in kwargs.items():
                inherit_kwargs[key] = Var(self.gen, key, value)

        self._inherit_args = inherit_args
        self._inherit_kwargs = inherit_kwargs
        return self

    def generate(self):
        # class Name(inherit, key=value):
        if self._inherit_args or self._inherit_kwargs:
            inherit = GatherItems().add(self._inherit_args).add(self._inherit_kwargs.values())
            args = inherit.get_inline().rstrip(',')
            yield f'{self.indent_str}class {self.name}({args}):'
        else:
            yield f'{self.indent_str}class {self.name}:'
        # content
        if self.items:
            yield from self.generate_items()
        else:
            with self:
                yield from Pass(self.gen).generate()


class Def(ClosureObject):
    def __init__(self, gen, name):
        super().__init__(gen)
        self.name = name
        self._args = []
        self._kwargs = {}

    def set_args(self, *args, **kwargs):
        """
        Examples:
            with gen.Def('run').set_args('self', timeout=10):
                ...
        """
        with self.apply_context_name('FuncArgs'):
            args_list = []
            for arg in args:
                if isinstance(arg, str):
                    item = Item(self.gen, arg)
                    args_list.append(item)

            kwargs_dict = {}
            for key, value in kwargs.items():
                kwargs_dict[key] = Var(self.gen, key, value)

        self._args = args_list
        self._kwargs = kwargs_dict
        return self

    def generate(self):
        # def name(args):
        args = GatherItems().add(self._args).add(self._kwargs.values())
        args_str = args.get_inline().rstrip(',')
        yield f'{self.indent_str}def {self.name}({args_str}):'
        # content
        if self.items:
            yield from self.generate_items()
        else:
            with self:
                yield from Pass(self.gen).generate()
