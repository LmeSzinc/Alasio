import inspect

from alasio.codegen.python.obj_base import CodeObject
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

    def __init__(self, gen, text, indent=True):
        super().__init__(gen)
        if indent:
            # trim leading/trailing empty lines and dedent
            text = inspect.cleandoc(text)
        else:
            # keep indent from input, only trim leading/trailing empty lines
            text = text.lstrip('\n').rstrip()
        self.lines = text.splitlines() if text else ['']
        self._add_indent = indent

    def generate(self):
        if self._add_indent:
            for line in self.lines:
                yield f'{self.indent_str}{line}'
        else:
            yield from self.lines


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
