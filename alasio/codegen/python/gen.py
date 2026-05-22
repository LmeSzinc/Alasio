from alasio.codegen.python.base import CodeDefinitionError
from alasio.codegen.python.libscan import EnvLibraryScanner, ModuleType
from alasio.codegen.python.obj_class import *
from alasio.codegen.python.obj_closure import *
from alasio.codegen.python.obj_import import *
from alasio.ext.path import PathStr


class CodeGenerator(AutoBlankLineMixin, ClosureObject):
    """
    A python code generator
    """

    def __init__(self):
        self.context: "t.Any | None" = None
        self.context_name = ''
        self.indent = 0
        super().__init__(self)
        self._import_registry: "dict[str, Import]" = {}
        self._scanner = None

    @property
    def scanner(self) -> EnvLibraryScanner:
        if self._scanner is None:
            self._scanner = EnvLibraryScanner()
        return self._scanner

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
        if isinstance(self.context, (List, Tuple, Set, Literal)):
            obj = Item(self, value)
            self.context.items.append(obj)
        else:
            raise CodeDefinitionError(f'Item can only be used in List/Tuple/Set/Literal')

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

    def sort_import(self):
        """
        Sort imports in PEP8 order
        """
        # Find header imports
        header_end = 0
        imports = []
        for i, item in enumerate(self.items):
            if isinstance(item, (Import, FromImport)):
                imports.append(item)
                header_end = i + 1
            elif isinstance(item, (Comment, MultilineComment, Empty)):
                # Still in header if followed by imports
                continue
            else:
                # Code started
                header_end = i
                break

        if not imports:
            return self

        # Extract all items in header
        header_items = self.items[:header_end]
        remaining_items = self.items[header_end:]

        # Classify imports
        std_lib = []
        third_party = []
        local_project = []

        # Collect all Import/FromImport objects from the header.
        real_imports = [item for item in header_items if isinstance(item, (Import, FromImport))]

        for imp in real_imports:
            mtype = imp.module_type
            if mtype == ModuleType.STANDARD_LIBRARY:
                std_lib.append(imp)
            elif mtype == ModuleType.THIRD_PARTY:
                third_party.append(imp)
            else:
                local_project.append(imp)

        # Sort each group by module name
        def sort_key(imp):
            return imp.module.lower()

        std_lib.sort(key=sort_key)
        third_party.sort(key=sort_key)
        local_project.sort(key=sort_key)

        # Assemble new items
        new_items = []
        if std_lib:
            new_items.extend(std_lib)
        if third_party:
            if new_items:
                new_items.append(Empty(self, 1))
            new_items.extend(third_party)
        if local_project:
            if new_items:
                new_items.append(Empty(self, 1))
            new_items.extend(local_project)

        # Auto blank lines from _get_auto_blank_lines will handle
        # the spacing between imports and subsequent code.
        self.items = new_items + remaining_items
        return self

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
