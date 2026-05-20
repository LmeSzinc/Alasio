from alasio.codegen.python.base import CodeDefinitionError
from alasio.codegen.python.codeobj import *
from alasio.ext.path import PathStr


class CodeGenerator(ClosureObject):
    """
    A python code generator
    """

    def __init__(self):
        self.context: "t.Any | None" = None
        self.context_name = ''
        self.indent = 0
        super().__init__(self)
        self._import_registry: "dict[str, Import]" = {}

    def _add_item(self, item: CodeObject):
        if isinstance(self.context, ClosureObject):
            self.context.items.append(item)
        else:
            self.items.append(item)

    def Var(self, name, value):
        """
        Define a variable in line
        {name} = {value}
        Or define a key-value in dict, function call

        Examples:
            gen.Var(name='john')
            # name = 'john'
            gen.Var(name='john').set_anno('str')
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
        """
        item = Anno(self, name, anno)
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
        Define an item in List/Tuple/Set
        {value},
        """
        if isinstance(self.context, (List, Tuple, Set)):
            obj = Item(self, value)
            self.context.items.append(obj)
        else:
            raise CodeDefinitionError(f'Item can only be used in List/Tuple/Set')

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
        self._import_registry[module] = item
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

    def use_import(self, module):
        """
        Mark a lazy import as used
        """
        try:
            self._import_registry[module].use()
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


    def print(self):
        for row in self.generate():
            print(row)

    def write(self, file=''):
        """
        Write generated code to file
        if file not provided, output code only

        Args:
            file (str):

        Returns:
            str:
        """
        content = [row for row in self.generate()]
        data = '\n'.join(content)
        if data:
            data += '\n'
        if file:
            PathStr.new(file).atomic_write(data)
        return data
