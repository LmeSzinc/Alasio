from alasio.codegen.python.obj_base import GatherItems
from alasio.codegen.python.obj_closure import ClosureObject
from alasio.codegen.python.obj_simple import *


class AutoBlankLineMixin(CodeObject):
    def _get_auto_blank_lines(self, prev, curr):
        """
        Calculate how many blank lines needed between prev and curr
        """
        indent = curr._indent

        if prev is None:
            # Leading blank lines before the first item
            if indent == 0 and isinstance(curr, (Class, Def)):
                return 0
            return 0

        # If manual empty lines, skip auto
        if isinstance(prev, Empty) or isinstance(curr, Empty):
            return 0

        # A Raw item preceding a Class/Def is treated as the definition
        # header (e.g. gen.Raw('def foo():', indent=False) inside RawDef).
        if isinstance(prev, Raw) and isinstance(curr, (Class, Def)):
            return 0

        # PEP8: 2 blank lines between top-level definitions
        if indent == 0:
            # 2 lines before Class/Def; 1 line if prev is a Comment (associated with the definition)
            if isinstance(curr, (Class, Def)):
                if isinstance(prev, (Comment, MultilineComment)):
                    return 1
                return 2
            # 2 lines after Class/Def
            if isinstance(prev, (Class, Def)) and not isinstance(curr, (Comment, MultilineComment)):
                return 2
            from alasio.codegen.python.obj_import import Import, FromImport
            # 1 line after Import block or between top-level variables
            if isinstance(prev, (Import, FromImport)) and not isinstance(curr, (Import, FromImport)):
                return 1

        # PEP8: 1 blank line between definitions in a class
        else:
            if isinstance(curr, (Class, Def)):
                return 1
            if isinstance(prev, (Class, Def)) and not isinstance(curr, (Comment, MultilineComment)):
                return 1

        return 0


class Class(AutoBlankLineMixin, ClosureObject):
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


class Def(AutoBlankLineMixin, ClosureObject):
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


class RawClass(Class):
    """
    A code block that acts like a class (auto blank lines, indentation behavior)
    but without the ``class Name(...):`` header line.

    Use together with ``gen.Raw()`` to write a custom class header while keeping
    proper indentation and PEP8 blank-line behavior for the body.

    Examples::

        gen.Raw('class MyClass(BaseModel):')
        with gen.RawClass('MyClass'):
            gen.Var('name', 'john')
            gen.Var('age', 0)

        # Output:
        # class MyClass(BaseModel):
        #     name = 'john'
        #     age = 0
    """
    def __init__(self, gen, name=''):
        super().__init__(gen, name)

    def generate(self):
        # content only — no class ...: header
        if self.items:
            yield from self.generate_items()


class RawDef(Def):
    """
    A code block that acts like a function (auto blank lines, indentation behavior)
    but without the ``def name(...):`` header line.

    Use together with ``gen.Raw()`` to write a custom function header while keeping
    proper indentation and PEP8 blank-line behavior for the body.

    Examples::

        gen.Raw('def run(self, timeout=10):')
        with gen.RawDef('run'):
            gen.Var('name', 'john')

        # Output:
        # def run(self, timeout=10):
        #     name = 'john'
    """

    def __init__(self, gen, name=''):
        super().__init__(gen, name)

    def generate(self):
        # content only — no def name(...): header
        if self.items:
            yield from self.generate_items()
