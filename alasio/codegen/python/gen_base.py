from alasio.codegen.python.libscan import EnvLibraryScanner, ModuleType
from alasio.codegen.python.obj_class import *
from alasio.codegen.python.obj_closure import *
from alasio.codegen.python.obj_import import *
from alasio.ext.path.atomic import atomic_read_text, atomic_write


class CodeGenBase(AutoBlankLineMixin, ClosureObject):
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
