from alasio.codegen.python.gen_base import CodeGenBase
from alasio.codegen.python.obj_base import CodeDefinitionError
from alasio.codegen.python.obj_class import *
from alasio.codegen.python.obj_closure import *
from alasio.codegen.python.obj_if import *
from alasio.codegen.python.obj_import import *


class CodeGen(CodeGenBase):
    """
    A python code generator
    """

    def Var(self, name, value):
        """
        Define a variable in line
        {name} = {value}
        Or define a key-value in dict, function call

        Examples:
            gen.Var(name='john')
            # name = 'john'
            gen.Var(name='john').Anno('str')
            # name: str = 'john'
        """
        item = Var(self, name, value)
        self._add_item(item)
        return item

    def Anno(self, name, anno):
        """
        Define a annotation in line
        {name}: {anno}

        Examples:
            gen.Anno('id', 'str')
            # id: str
            gen.Anno('id', 'str').Var('john')
            # id: str = 'john'
        """
        item = Anno(self, name, anno)
        self._add_item(item)
        return item

    def Literal(self, name=''):
        """
        Define a variable with a Literal type annotation.
        {name}: Literal['item1', 'item2'] = 'item1'

        Examples:
            with gen.Literal('fruit'):
                gen.Item('apple')
                gen.Item('banana')
            # fruit: Literal['apple', 'banana'] = 'apple'

            gen.Literal('color').set_literal('t.Literal')
            # color: t.Literal[...] = ...
        """
        item = Literal(self, name)
        self._add_item(item)
        return item

    def List(self, name: str = ''):
        """
        Define a list with each item on newline
        {name} = [
            item1,
            item2,
        ]

        Examples:
            with gen.List('all_items'):
                gen.Item('item1')
                gen.Item('item2')
        """
        item = List(self, name)
        self._add_item(item)
        return item

    def Item(self, value):
        """
        Define an item in List/Tuple/Set/Literal
        {value},
        """
        if isinstance(self.context, (List, Tuple, Set, Literal, Object, CustomTab)):
            obj = Item(self, value)
            self.context.items.append(obj)
        else:
            raise CodeDefinitionError(f'Item can only be used in List/Tuple/Set/Literal/Object')

    def Object(self, name: str = '', cls: str = ''):
        """
        Define an object instantiation.
        {name} = {cls}({args})
        {name}: {anno} = {cls}({args})

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
        item = Object(self, name, cls)
        self._add_item(item)
        return item

    def Dict(self, name):
        """
        Define a tuple with each item on newline
        {name} = {
            'key1': 'value1',
            'key2': 'value2',
        }

        Examples:
            with gen.Dict('all_items'):
                gen.Var('key1', 'value1')
                gen.Var('key2', 'value2')
            # Output
            all_items = {
                'key1': 'value1',
                'key2': 'value2',
            }
        """
        item = Dict(self, name)
        self._add_item(item)
        return item

    def Tuple(self, name: str = ''):
        """
        Define a tuple with each item on newline
        {name} = (
            item1,
            item2,
        )

        Examples:
            with gen.Tuple('all_items'):
                gen.Item('item1')
                gen.Item('item2')
        """
        item = Tuple(self, name)
        self._add_item(item)
        return item

    def Set(self, name: str = ''):
        """
        Define a set with each item on newline
        {name} = {
            item1,
            item2,
        }

        Examples:
            with gen.Set('all_items'):
                gen.Item('item1')
                gen.Item('item2')
        """
        item = Set(self, name)
        self._add_item(item)
        return item

    def Comment(self, text):
        """
        Define a comment
        # {text}
        """
        item = Comment(self, text)
        self._add_item(item)
        return item

    def MultilineComment(self, text):
        """
        Define a multiline comment
        \"\"\"
        {text}
        \"\"\"
        """
        item = MultilineComment(self, text)
        self._add_item(item)
        return item

    def CommentCodeGen(self, file):
        """
        Args:
            file (str): Path to code generator, such as "dev_tools.button_extract"
        """
        self.Comment('This file was auto-generated, do not modify it manually. To generate:')
        self.Comment(f'``` python -m {file} ```')

    def Class(self, name):
        """
        Define a class.
        class {name}({inherit}):
            # content

        Examples:
            with g.Class('UserModel').set_inherit('BaseModel', metaclass='Singleton'):
                g.Var('name', 'john')
            # Output
            class UserModel(BaseModel, metaclass=Singleton):
                name = 'john'
        """
        item = Class(self, name)
        self._add_item(item)
        return item

    def Def(self, name):
        """
        Define a function.
        def {name}({args}):
            # content

        Examples:
            with g.Def('run').set_args('self', timeout=10):
                g.Var('name', 'john')
            # Output
            def run(self, timeout=10):
                name = 'john'
        """
        item = Def(self, name)
        self._add_item(item)
        return item

    def Import(self, module):
        """
        import {module}
        """
        if module in self._import_registry:
            return self._import_registry[module]

        item = Import(self, module)
        self._add_item(item)
        self._import_registry[item.varname] = item
        return item

    def FromImport(self, module):
        """
        from {module} import {items}
        """
        item = FromImport(self, module)
        self._add_item(item)
        return item

    def Raw(self, text):
        """
        Raw string content
        """
        item = Raw(self, text)
        self._add_item(item)
        return item

    def use_import(self, varname):
        """
        Mark a lazy import as used
        See Import.use()
        """
        try:
            self._import_registry[varname].use()
        except KeyError:
            pass
        return self

    def Empty(self, lines=1):
        """
        Generate blank lines
        """
        item = Empty(self, lines)
        self._add_item(item)
        return item

    def Pass(self):
        """
        Define a pass
        """
        item = Pass(self)
        self._add_item(item)
        return item

    def If(self, condition):
        """
        Define an if block.
        if {condition}:
            # content

        Examples:
            with gen.If('x > 0'):
                gen.Var('result', 'positive')
            # if x > 0:
            #     result = 'positive'
        """
        item = If(self, condition)
        self._add_item(item)
        return item

    def Elif(self, condition):
        """
        Define an elif block.
        elif {condition}:
            # content

        Examples:
            with gen.Elif('x == 0'):
                gen.Var('result', 'zero')
            # elif x == 0:
            #     result = 'zero'
        """
        item = Elif(self, condition)
        self._add_item(item)
        return item

    def Else(self):
        """
        Define an else block.
        else:
            # content

        Examples:
            with gen.Else():
                gen.Var('result', 'negative')
            # else:
            #     result = 'negative'
        """
        item = Else(self)
        self._add_item(item)
        return item

    @property
    def has_content(self):
        """
        Return True if generator contains any meaningful code object.
        Imports, empty lines, and comments are not considered meaningful content.
        """
        non_content_types = (Import, FromImport, Empty, Comment, MultilineComment)
        for item in self.items:
            if not isinstance(item, non_content_types):
                return True
        return False

    def tab(self, indent: int = 1, prefix: str = '', suffix: str = '', line_ending: str = ''):
        """
        Context manager that adds indentation to content inside the block.
        When prefix/suffix/line_ending are provided, it creates a CustomTab that
        emits prefix/suffix lines and applies line_ending to items inside.

        Examples:
            with gen.tab():
                gen.Var('x', 1)
            #     x = 1

            with gen.tab(2):
                gen.Var('x', 1)
            #         x = 1

            with gen.tab(prefix='lambda: (', suffix=')', line_ending=','):
                gen.Item('x')
                gen.Item('y')
            # lambda: (
            #     'x',
            #     'y',
            # )
        """
        item = CustomTab(self, indent, prefix, suffix, line_ending)
        if not item._passthrough:
            self._add_item(item)
        return item
