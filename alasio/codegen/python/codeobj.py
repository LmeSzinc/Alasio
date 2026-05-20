from alasio.codegen.python.base import CodeObject, GatherItems
from alasio.ext.cache import cached_property


class Pass(CodeObject):
    """
    Define a `pass`
    """
    def generate(self):
        yield f'{self.indent_str}pass'


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
        if self.gen.context_name in ['ClassInherit', 'FuncArgs']:
            self.value = value
        else:
            self.value = repr(value)

    @cached_property
    def item_str(self):
        anno = ''
        name = self.name
        if self._context_name in ['FuncArg']:
            anno = self._anno
        # name: anno = value,
        return f'{name}{anno}{self.between_kv}{self.value}{self.line_ending}'

    def generate(self):
        yield f'{self.indent_str}{self.item_str}'


class Anno(CodeObject):
    """
    Define a annotation in line
    {name}: {anno}
    """

    def __init__(self, gen, name, anno):
        super().__init__(gen)
        if self._context_name in ['Dict']:
            name = repr(name)
        self.name = name
        self.set_anno(anno)

    @cached_property
    def item_str(self):
        anno = self._anno
        name = self.name
        # name: anno
        return f'{name}{anno}'

    def generate(self):
        yield f'{self.indent_str}{self.item_str}'


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


class ClosureWithName(ClosureObject):
    def __init__(self, gen, name):
        super().__init__(gen)
        if self._context_name in ['Dict']:
            name = repr(name)
        self.name = name


class List(ClosureWithName):
    def generate(self):
        if not self.items:
            if self.name:
                # name: anno = [],
                yield f'{self.indent_str}{self.name}{self._anno}{self.between_kv}[]{self.line_ending}'
            else:
                # [],
                yield f'{self.indent_str}[]{self.line_ending}'
            return

        # name: anno = [
        if self.name:
            yield f'{self.indent_str}{self.name}{self._anno}{self.between_kv}['
        else:
            yield f'{self.indent_str}['
        # item,
        for item in self.items:
            yield from item.generate()
        # ],
        yield f'{self.indent_str}]{self.line_ending}'


class Dict(ClosureWithName):
    def generate(self):
        if not self.items:
            if self.name:
                # name: anno = {}, 
                yield f'{self.indent_str}{self.name}{self._anno}{self.between_kv}{{}}{self.line_ending}'
            else:
                # {}, 
                yield f'{self.indent_str}{{}}{self.line_ending}'
            return

        # name: anno = {
        if self.name:
            yield f'{self.indent_str}{self.name}{self._anno}{self.between_kv}{{'
        else:
            yield f'{self.indent_str}{{'
        # key: value,
        for item in self.items:
            yield from item.generate()
        # },
        yield f'{self.indent_str}}}{self.line_ending}'


class Tuple(ClosureWithName):
    def generate(self):
        if not self.items:
            if self.name:
                # name: anno = (),
                yield f'{self.indent_str}{self.name}{self._anno}{self.between_kv}(){self.line_ending}'
            else:
                # (),
                yield f'{self.indent_str}(){self.line_ending}'
            return

        # name: anno = (
        if self.name:
            yield f'{self.indent_str}{self.name}{self._anno}{self.between_kv}('
        else:
            yield f'{self.indent_str}('
        # item,
        for item in self.items:
            yield from item.generate()
        # ),
        yield f'{self.indent_str}){self.line_ending}'


class Set(ClosureWithName):
    def generate(self):
        # set()
        if not self.items:
            if self.name:
                yield f'{self.indent_str}{self.name}{self._anno}{self.between_kv}set(){self.line_ending}'
            else:
                yield f'{self.indent_str}set(){self.line_ending}'
            return

        # name: anno = {
        if self.name:
            yield f'{self.indent_str}{self.name}{self._anno}{self.between_kv}{{'
        else:
            yield f'{self.indent_str}{{'
        # item,
        for item in self.items:
            yield from item.generate()
        # },
        yield f'{self.indent_str}}}{self.line_ending}'


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
            yield f'{self.indent_str}class {self.name}({inherit.get_inline()}):'
        else:
            yield f'{self.indent_str}class {self.name}:'
        # content
        if self.items:
            for item in self.items:
                yield from item.generate()
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
        yield f'{self.indent_str}def {self.name}({args.get_inline()}):'
        # content
        if self.items:
            for item in self.items:
                yield from item.generate()
        else:
            with self:
                yield from Pass(self.gen).generate()
