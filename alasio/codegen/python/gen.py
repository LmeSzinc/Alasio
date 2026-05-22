from alasio.codegen.python.base import CodeDefinitionError
from alasio.codegen.python.libscan import EnvLibraryScanner, ModuleType
from alasio.codegen.python.obj_class import *
from alasio.codegen.python.obj_closure import *
from alasio.codegen.python.obj_import import *
from alasio.ext.path.atomic import atomic_read_text, atomic_write


class CodeGen(AutoBlankLineMixin, ClosureObject):
    """
    A python code generator
    """

    def __init__(self):
        self.context: "t.Any | None" = None
        self.context_name = ''
        self.indent = 0
        super().__init__(self)
        self._import_registry: "dict[str, Import]" = {}

    @property
    def scanner(self) -> EnvLibraryScanner:
        return EnvLibraryScanner()

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

    def sort_import(self):
        """
        Sort imports in PEP8 order

        Comments before the first import are preserved before the import block.
        Comments between or after imports are moved after the entire import block.
        Empty lines in the header are discarded.
        """
        # Find header boundary: where code (non-header) starts
        header_end = 0
        for i, item in enumerate(self.items):
            if isinstance(item, (Import, FromImport)):
                header_end = i + 1
            elif isinstance(item, (Comment, MultilineComment, Empty)):
                # Still in header if followed by imports
                continue
            else:
                # Code started
                header_end = i
                break

        # Capture all header items
        header_items = self.items[:header_end]
        remaining_items = self.items[header_end:]

        # If no imports found, return early — header items (comments etc.) stay as-is
        real_imports = [item for item in header_items if isinstance(item, (Import, FromImport))]
        if not real_imports:
            return self

        # Classify imports
        std_lib = []
        third_party = []
        local_project = []

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

        # Separate comments: pre-import vs post-import
        #   pre-import: comments before the first Import/FromImport
        #   post-import: comments at or after the first Import/FromImport
        pre_import_comments = []
        post_import_comments = []
        found_first_import = False

        for item in header_items:
            if isinstance(item, (Import, FromImport)):
                found_first_import = True
            elif isinstance(item, (Comment, MultilineComment)):
                if found_first_import:
                    post_import_comments.append(item)
                else:
                    pre_import_comments.append(item)
            # Empty items are dropped (existing behavior)

        # Assemble new items
        new_items = []
        new_items.extend(pre_import_comments)

        has_imports = False
        if std_lib:
            new_items.extend(std_lib)
            has_imports = True
        if third_party:
            if has_imports:
                new_items.append(Empty(self, 1))
            new_items.extend(third_party)
            has_imports = True
        if local_project:
            if has_imports:
                new_items.append(Empty(self, 1))
            new_items.extend(local_project)
            has_imports = True

        if post_import_comments:
            # 2 blank lines between the import block and post-import comments
            new_items.append(Empty(self, 2))
            new_items.extend(post_import_comments)

        # Auto blank lines from _get_auto_blank_lines will handle
        # the spacing between imports and subsequent code.
        self.items = new_items + remaining_items
        return self

    def generate(self):
        self.sort_import()
        yield from super().generate()

    def print(self):
        for row in self.generate():
            print(row)

    def generate_str(self):
        """
        Write generated code to file
        if file not provided, output code only

        Returns:
            str:
        """
        content = [row for row in self.generate()]
        data = '\n'.join(content)
        if data:
            data += '\n'
        return data

    def write(self, file='', gitadd=None, skip_same=True):
        """
        Write generated code to file

        Args:
            file (str):
            gitadd (GitAdd): Input a GitAdd object to track the generated files
            skip_same (bool):
                True to skip writing if existing content is the same as content to write.
                This would reduce disk write but add disk read

        Returns:
            bool: if write
        """
        data = self.generate_str()
        if skip_same:
            try:
                old = atomic_read_text(file)
                old = old.replace('\r\n', '\n')
                if data == old:
                    return False
            except FileNotFoundError:
                pass

        atomic_write(file, data)
        if gitadd:
            gitadd.stage_add(file)
        return True
