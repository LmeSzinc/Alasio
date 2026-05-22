from alasio.codegen.python.obj_class import AutoBlankLineMixin, ClosureObject
from alasio.ext.cache import cached_property


class Import(AutoBlankLineMixin):
    """
    import {module} [as {alias}]
    """

    def __init__(self, gen, module, alias=None):
        super().__init__(gen)
        self.module = module
        self.alias = alias
        self._lazy = False
        self._used = False
        self.import_from = ''

    def as_(self, alias):
        prev_name = self.varname
        self.alias = alias
        # Clear cached property
        cached_property.pop(self, 'varname')
        cached_property.pop(self, 'item_str')

        # Update registry
        if self.gen._import_registry.get(prev_name) is self:
            del self.gen._import_registry[prev_name]
        self.gen._import_registry[self.varname] = self
        return self

    def lazy(self):
        """
        Mark import object as lazy generate

        imp = gen.Import('typing').lazy()
        # this won't be generated, unless you call `imp.use()` or `gen.use_import('typing')`
        imp = gen.Import('typing').as_('t').lazy()
        # this won't be generated, unless you call `imp.use()` or `gen.use_import('t')`
        imp = gen.FromImport('typing').Import('List').lazy()
        # this won't be generated, unless you call `imp.use()` or `gen.use_import('List')`
        """
        self._lazy = True
        return self

    def use(self):
        """
        Mark import object as used
        """
        self._used = True
        return self

    @cached_property
    def lib(self):
        """
        Get library of module
        import lzma -> "lzma"
        from pydantic import BaseModel -> "pydantic"
        from module.xxx import Class -> "module"  # local module
        from .relative import func -> ""  # relative import
        """
        if self.import_from:
            return self.import_from.partition('.')[0]
        return self.module.partition('.')[0]

    @cached_property
    def varname(self):
        """
        Get variable name of import result
        import lzma -> "lzma"
        import typing as t -> "t"
        from pydantic import BaseModel -> "BaseModel"
        from .relative import func -> "func"
        """
        if self.alias:
            return self.alias
        return self.module

    @cached_property
    def module_type(self):
        """
        Get module type (standard_library, third_party, local_project)
        """
        return self.gen.scanner._classify_single_module(self.lib)

    @cached_property
    def item_str(self):
        if self.alias:
            return f'{self.module} as {self.alias}'
        return self.module

    def generate(self):
        if self._lazy and not self._used:
            return
        if self.import_from or self._context_name == 'FromImport':
            yield f'{self.indent_str}{self.item_str}{self.line_ending}'
        else:
            yield f'{self.indent_str}import {self.item_str}'


class FromImport(ClosureObject, AutoBlankLineMixin):
    """
    from {module} import {items}
    """

    def __init__(self, gen, module):
        super().__init__(gen)
        self.module = module
        self._is_with = False

    def __enter__(self):
        self._is_with = True
        return super().__enter__()

    @cached_property
    def module_type(self):
        """
        Get module type (standard_library, third_party, local_project)
        """
        lib = self.module.partition('.')[0]
        return self.gen.scanner._classify_single_module(lib)

    def Import(self, name, alias=None):
        item = Import(self.gen, name, alias=alias)
        item.import_from = self.module
        self.items.append(item)
        self.gen._import_registry[name] = item
        return item

    def generate(self):
        # Filter used items
        items = [i for i in self.items if isinstance(i, Import) and (not i._lazy or i._used)]
        if not items:
            return

        # Sort items by name
        items.sort(key=lambda x: x.module)

        if len(items) == 1:
            # from module import item
            yield f'{self.indent_str}from {self.module} import {items[0].item_str}'
        else:
            # from module import (
            yield f'{self.indent_str}from {self.module} import ('
            #     item1,
            #     item2,
            with self.apply_context_name('List'):
                for item in items:
                    # Items already have _is_from_item=True, but we need to ensure correct indent
                    item._indent = self.gen.indent + self._indent_tab
                    # In multiline from-import, we need commas
                    yield f'{item.indent_str}{item.item_str},'
            # )
            yield f'{self.indent_str})'
