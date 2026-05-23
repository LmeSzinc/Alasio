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

        # Strip leading Empty items from remaining so auto blank lines can work.
        while remaining_items and isinstance(remaining_items[0], Empty):
            remaining_items.pop(0)

        # Detect Comment(s) + [Empty*] + Class/Def at the start of remaining:
        # the 2 PEP8 blanks go before the first comment (associated with the definition),
        # and manual Empty items between comment and definition are preserved.
        if remaining_items and isinstance(remaining_items[0], (Comment, MultilineComment)):
            for j in range(1, len(remaining_items)):
                if isinstance(remaining_items[j], (Comment, MultilineComment, Empty)):
                    continue
                if isinstance(remaining_items[j], (Class, Def)):
                    new_items.append(Empty(self, 2))
                    while remaining_items and isinstance(remaining_items[0], (Comment, MultilineComment)):
                        new_items.append(remaining_items.pop(0))
                break

        # Auto blank lines from _get_auto_blank_lines will handle
        # the spacing between imports and subsequent code.
        self.items = new_items + remaining_items
        return self

    def generate(self):
        self.sort_import()
        self.newline_ending()
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
        return data

    def newline_ending(self):
        """
        Ensure the items list ends with exactly one Empty line (a blank line).

        Traces the chain of last non-Empty items (using reversed iteration)
        and cleans trailing Empty items at each level along that chain.
        CodeGenBase itself is a ClosureObject, so the same _clean_last_item_trailing
        function handles both root and nested levels.
        This prevents double trailing newlines when a nested
        Class/Def already ends with Empty items, while not touching sibling
        items outside the last-item chain.

        Returns:
            CodeGenBase: self for chaining
        """
        if not self.items:
            return self

        # Reuse _clean_last_item_trailing to clean root and nested levels.
        # CodeGenBase is a ClosureObject, so the same logic applies.
        self._clean_last_item_trailing(self.items)

        # Add exactly one empty line
        self.items.append(Empty(self, 1))

        return self

    @staticmethod
    def _clean_last_item_trailing(items):
        """
        Trace the chain of last non-Empty items and remove trailing Empties
        at each level along that chain, including the current level.
        Uses reversed iteration for performance.

        Args:
            items (list[CodeObject]): Items list to clean
        """
        if not items:
            return

        # Find the last non-Empty item using reversed iteration
        last_item = None
        for item in reversed(items):
            if not isinstance(item, Empty):
                last_item = item
                break

        if last_item is not None:
            # Recurse into the last item if it's a ClosureObject
            if isinstance(last_item, ClosureObject):
                CodeGenBase._clean_last_item_trailing(last_item.items)

        # Now clean trailing Empties at THIS level
        while items and isinstance(items[-1], Empty):
            items.pop()
        # Clean trailing empty objects
        while items and CodeGenBase._is_empty_object(items[-1]):
            items.pop()
            while items and isinstance(items[-1], Empty):
                items.pop()

    @staticmethod
    def _is_empty_object(item):
        """
        Check if an item is an empty object (AutoBlankLineMixin + ClosureObject
        with only Empty or empty-object items).

        Args:
            item (CodeObject): Item to check

        Returns:
            bool: True if the item is an empty object
        """
        if not isinstance(item, AutoBlankLineMixin) or not isinstance(item, ClosureObject):
            return False
        if not item.items:
            return False
        for sub_item in item.items:
            if isinstance(sub_item, Empty):
                continue
            if isinstance(sub_item, AutoBlankLineMixin) and isinstance(sub_item, ClosureObject) \
                    and CodeGenBase._is_empty_object(sub_item):
                continue
            return False
        return True

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
