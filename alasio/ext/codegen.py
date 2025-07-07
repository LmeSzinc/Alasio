from typing import Any

from .path.atomic import atomic_read_text, atomic_write

_EMPTY = object()


class TabWrapper:
    def __init__(self, codegen: "CodeGen", prefix='', suffix='', line_ending='', tab_type=''):
        """
        Context manager to auto handle indent
        {prefix}
            {line}{line_ending}
            {line}{line_ending}
            {line}{line_ending}
        {suffix}{line_ending_of_father_tab}
        """
        self.codegen = codegen
        self.prefix = prefix
        self.suffix = suffix
        self.line_ending = line_ending
        self.tab_type = tab_type

    def __enter__(self):
        gen = self.codegen
        if self.prefix:
            gen.add(self.prefix, line_ending=False)
        gen.indent += 1
        gen.dict_line_ending[gen.indent] = self.line_ending
        gen.dict_tab_type[gen.indent] = self.tab_type
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        gen = self.codegen
        gen.dict_line_ending.pop(gen.indent, None)
        gen.dict_tab_type.pop(gen.indent, None)
        gen.indent -= 1
        if self.suffix:
            gen.add(self.suffix)

    def __repr__(self):
        return self.prefix


class CodeGen:
    """
    Simple python code generator, using context manager to handle indent
    """

    def __init__(self):
        # Current indent
        self.indent = 0
        # All generated lines
        self.lines: "list[str]" = []
        # Line endings of each indent level
        self.dict_line_ending: "dict[int, str]" = {}
        self.dict_tab_type: "dict[int, str]" = {}

    def __bool__(self):
        return True

    def gen(self) -> str:
        """
        Get generated code
        """
        content = ''.join(self.lines)
        # python code should have new line at file end
        content = content.strip() + '\n'
        return content

    def print(self):
        """
        Print generated code
        """
        print(self.gen())

    def write(self, file, gitadd=None, skip_same=True):
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
        data = self.gen()
        if skip_same:
            try:
                old = atomic_read_text(file)
                old = old.replace('\r\n', '\n')
            except FileNotFoundError:
                old = object()
            if data == old:
                return False
            else:
                atomic_write(file, data)
                if gitadd:
                    gitadd.stage_add(file)
                return True
        else:
            atomic_write(file, data)
            if gitadd:
                gitadd.stage_add(file)
            return True

    def add(self, line: str, line_ending=True, newline=True) -> str:
        """
        Add one line of python code, with auto indent
        """
        if self.indent > 0:
            line = '    ' * self.indent + line
        if line_ending:
            end = self.dict_line_ending.get(self.indent, None)
            if end:
                line += end
        if newline:
            line += '\n'
        self.lines.append(line)
        return line

    def predict_length_exceed(self, line, line_ending=True, max_length=120) -> bool:
        """
        Predict if one line of python code will exceed max_length after adding indent
        """
        if self.indent > 0:
            line = '    ' * self.indent + line
        if line_ending:
            end = self.dict_line_ending.get(self.indent, None)
            if end:
                line += end
        return len(line) > max_length

    def tab(self, prefix='', suffix='', line_ending='', tab_type='') -> "TabWrapper":
        """
        Context manager to auto handle indent

        Examples:
            with gen.tab():
                # indent +1 here
            # indent rollback

            # Will generate
            {prefix}
                {line}{line_ending}
                {line}{line_ending}
                {line}{line_ending}
            {suffix}{line_ending_of_father_tab}
        """
        return TabWrapper(self, prefix=prefix, suffix=suffix, line_ending=line_ending, tab_type=tab_type)

    def Empty(self, lines=1):
        """
        Add empty line
        """
        for _ in range(lines):
            self.add('')

    def Pass(self):
        """
        Add a "pass"
        """
        self.add('pass')

    def Comment(self, text: str):
        """
        Add comment
        """
        for line in text.splitlines():
            line = line.strip()
            if line:
                self.add(f'# {line}')

    def CommentCodeGen(self, file):
        """
        Args:
            file (str): Path to code generator, such as "dev_tools.button_extract"
        """
        # Only leave one blank line at above
        if len(self.lines) >= 2:
            if self.lines[-2:] == ['\n', '\n']:
                self.lines.pop(-1)
        self.Comment('This file was auto-generated, do not modify it manually. To generate:')
        self.Comment(f'``` python -m {file} ```')
        self.Empty()

    def Import(self, module: str) -> str:
        """
        import {module}
        """
        return self.add(f'import {module}')

    def FromImport(
            self,
            module_from: str,
            module_import: "str | list[str] | tuple[str]",
            multiline=False,
    ):
        """
        from {module_from} import {module_import}

        Args:
            module_from:
            module_import: Imports will be sorted if it's a list
            multiline: True to have each module_import on newline
                from {module_from} import (
                    aaa,
                    bbb,
                )
        """
        if multiline:
            if isinstance(module_import, (list, tuple)):
                module_import = sorted(module_import)
            else:
                module_import = [module_import]
            with self.tab(prefix=f'from {module_from} import (', suffix=')', line_ending=','):
                for imp in module_import:
                    self.add(imp)
        else:
            if isinstance(module_import, (list, tuple)):
                module_import = sorted(module_import)
                module_import = ', '.join(module_import)
            self.add(f'from {module_from} import {module_import}')

    def RawImport(self, multiline_import: str, empty=2):
        """
        Args:
            multiline_import:
            empty: Empty lines after import
        """
        for line in multiline_import.strip().splitlines():
            line = line.strip()
            # Keep empty lines
            self.add(line)
        self.Empty(empty)

    @property
    def tab_type(self) -> str:
        return self.dict_tab_type.get(self.indent, '')

    def _get_prefix(self, name: str, prefix: str, anno: str = '') -> str:
        if name:
            tab = self.tab_type
            if tab == 'dict':
                return f'{repr(name)}: {prefix}'
            elif tab == 'object':
                return f'{name}={prefix}'
            else:
                if anno:
                    return f'{name}: {anno} = {prefix}'
                else:
                    return f'{name} = {prefix}'
        else:
            return prefix

    def merge_items(
            self,
            items: "list[Any] | tuple[Any] | dict[Any, Any]",
            max_length=120
    ) -> "list[str]":
        # Convert to list[str] of code
        typ = type(items)
        if typ is dict:
            items = [f'{repr(k)}={repr(v)}' for k, v in items.items()]
        else:
            items = [repr(v) for v in items]
        # Merge items into rows
        max_length = max_length - self.indent * 4
        output = []
        row = ''
        for item in items:
            # row item,
            if not row:
                row = f'{item},'
                continue
            if len(row) + len(item) + 2 <= max_length:
                row = f'{row} {item},'
            else:
                output.append(row)
                row = f'{item},'
        output.append(row)
        return output

    def Var(self, name: str, value, anno: str = '', auto_multiline=0):
        """
        Define a variable in line
        {name} = {value}
        {name}: {anno} = {value}

        Args:
            name:
            value:
            anno:
            auto_multiline: max_length, if length of code > max_length, reformat code to fitin max_length
        """
        line = self._get_prefix(name, repr(value), anno=anno)
        if auto_multiline and self.predict_length_exceed(line, max_length=auto_multiline):
            # Try to merge multiline
            typ = type(value)
            if typ is list:
                with self.List(name, anno=anno):
                    for line in self.merge_items(value, auto_multiline):
                        self.add(line, line_ending=False)
                return
            elif typ is tuple:
                with self.Tuple(name, anno=anno):
                    for line in self.merge_items(value, auto_multiline):
                        self.add(line, line_ending=False)
                return
            elif typ is dict:
                with self.Dict(name, anno=anno):
                    for line in self.merge_items(value, auto_multiline):
                        self.add(line, line_ending=False)
                return

        self.add(line)

    def Anno(self, name: str, anno: str, value=_EMPTY):
        """
        Define a variable in line
        {name}: {anno}
        {name}: {anno} = {value}
        """
        if value is _EMPTY:
            line = f'{name}: {anno}'
        else:
            line = self._get_prefix(name, repr(value), anno=anno)
        self.add(line)

    def String(self, text: str):
        """
        Define a multiline string
        """
        self.add('"""', line_ending=False)
        for line in text.splitlines():
            line = line.strip()
            if line:
                self.add(line, line_ending=False)
        self.add('"""')

    def List(self, name: str = '', anno: str = '') -> "TabWrapper":
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
        prefix = self._get_prefix(name, '[', anno=anno)
        return self.tab(prefix=prefix, suffix=']', line_ending=',', tab_type='list')

    def Tuple(self, name: str = '', anno: str = '') -> "TabWrapper":
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
        prefix = self._get_prefix(name, '(', anno=anno)
        return self.tab(prefix=prefix, suffix=')', line_ending=',', tab_type='tuple')

    def Item(self, value):
        """
        Define an item of list or tuple or set
        """
        self.add(f'{repr(value)}')

    def Dict(self, name: str = '', anno: str = '') -> "TabWrapper":
        """
        Define a tuple with each item on newline
        {name} = {
            key1=value1,
            key2=value2,
        }

        Examples:
            with gen.Dict('all_items'):
                gen.Var('key1', 'value1')
                gen.Var('key2', 'value2')
        """
        prefix = self._get_prefix(name, '{', anno=anno)
        return self.tab(prefix=prefix, suffix='}', line_ending=',', tab_type='dict')

    Set = Dict

    def Object(self, cls: str, name: str = '', anno: str = '') -> "TabWrapper":
        """
        Define an object with each args and kwargs on newline
        {name} = {cls}(
            arg,
            key=value,
        }

        Examples:
            with gen.Object(cls='Button', name='button', anno='Button'):
                gen.Item('arg')
                gen.Var('key1', 'value1')
            # Output
            button: Button = Button(
                'arg',
                key1='value1',
            )
        """
        prefix = self._get_prefix(name, f'{cls}(', anno=anno)
        return self.tab(prefix=prefix, suffix=')', line_ending=',', tab_type='object')

    def Class(self, name: str, inherit: "str | list[str] | tuple[str]" = '') -> "TabWrapper":
        """
        Define a class.
        class {name}({inherit}):
            # content

        Examples:
            with gen.Class('ResponseModel', inherit='BaseModel'):
                gen.Anno('uid', 'int', 123)
            # Output
            class ResponseModel(BaseModel):
                uid: int = 123
        """
        if inherit:
            if isinstance(inherit, (list, tuple)):
                if len(inherit) == 1:
                    inherit = inherit[0]
                else:
                    inherit = ', '.join(inherit)
            prefix = f'class {name}({inherit}):'
        else:
            prefix = f'class {name}:'
        return self.tab(prefix=prefix, tab_type='class')

    def Def(self, name: str, args: str = '') -> "TabWrapper":
        """
        Define a function.
        def {name}({args}):
            # content
        If function is defined within a class, "self" will be auto added
        def {name}(self, {args}):
            # content

        Examples:
            with gen.Def('convert', 'name'):
                gen.add('name = name.upper()')
                gen.Return('name')
            # Output
            def convert(name):
                name = name.upper()
                return name
        """
        if self.tab_type == 'class':
            if args:
                if not args.startswith('self,') and not args.startswith('self, '):
                    args = f'self, {args}'
            else:
                args = 'self'
        prefix = f'def {name}({args}):'
        return self.tab(prefix=prefix, tab_type='def')
