import importlib
import inspect
from collections import defaultdict

from alasio.codegen.python import CodeGen
from alasio.config_dev.gen.gen_nav_index import GenNavIndex
from alasio.config_dev.parse.base import DefinitionError
from alasio.logger import logger


class GenConfigGenerated(GenNavIndex):
    """Generator for config_generated.py and group_export.py."""

    def _iter_nav_config_by_order(self):
        """
        Yields:
            tuple[str, ConfigGenerator]:
        """
        dashboard = 'dashboard'
        visited = set()

        # 1. iter from nav_order_data
        for nav_name in self.nav_order_data:
            if nav_name == dashboard:
                continue
            config = self.dict_nav_config.get(nav_name, None)
            if config is not None:
                visited.add(nav_name)
                yield nav_name, config

        # 2. iter any unknown navs
        for nav_name, config in self.dict_nav_config.items():
            if nav_name == dashboard:
                continue
            if nav_name not in visited:
                yield nav_name, config

        # 3. iter dashboard navs
        nav_name = dashboard
        config = self.dict_nav_config.get(dashboard, None)
        if config is not None:
            yield nav_name, config

    def _is_related_model(self, models, model):
        """
        Whether a model bound to an already declared group name still fits it.

        A model of the same inheritance chain (a subclass or a base class of the
        models already bound) is a variant of the group, which is what variant
        groups are for: the declared one still gives the IDE a usable type.

        Args:
            models (dict[str, str]): Models already bound to the group name,
                key: class name, value: reference in the generated file
            model (str): Class name of the model of the binding to check

        Returns:
            bool: True if the model is related to every model already bound
        """

        def mro_of(name):
            group = self.groups_data.get(name, None)
            if group is None or not group.mro:
                # unknown group, or a chain that is not built: it stands alone
                return (name,)
            return group.mro

        mro = mro_of(model)
        return all(
            other in mro or model in mro_of(other)
            for other in models
        )

    def generate_config_generated_file(self, gitadd=None):
        """
        Generate config_generated.py file with type hints for IDE auto-completion

        This method directly uses dict_nav_config without extra data preparation layer.
        It leverages the fixed file structure {nav}/{nav}_model.py to simplify
        import path generation.
        """
        # Check if we have any configs
        if not self.dict_nav_config:
            logger.warning('No navigation configs found for config_generated.py')
            return

        # Generate code using CodeGen
        gen = CodeGen()

        # Basic imports
        gen.Import('typing').as_('t')
        if self.alasio:
            gen.FromImport('alasio.config.base').Import('AlasioConfigBase')
            gen.FromImport('alasio.config.entry.mod_entry').Import('ModEntry')
        # TYPE_CHECKING block - imports only used for type hints
        with gen.If('t.TYPE_CHECKING'):
            gen.Import('alasio.config.alasio.group_export').as_('alasio').lazy()
            # Sort nav names for stable output: the order of dict_nav_config comes
            # from the nav folders, sorting keeps the file stable when a folder is
            # added or removed
            for nav_name in sorted(self.dict_nav_config):
                config = self.dict_nav_config[nav_name]
                # from .{nav} import {nav}_model as {nav}
                gen.FromImport(f'..{config.folder}').Import(f'{nav_name}_model').as_(nav_name).lazy()

        gen.Empty(2)
        gen.CommentCodeGen('module.config.gen')

        # Class definition
        if self.alasio:
            cls = gen.Class('ConfigGenerated').set_inherit('AlasioConfigBase')
        else:
            cls = gen.Class('AlasioConfigGenerated')
        with cls:
            gen.MultilineComment('A generated config struct to fool IDE\'s type-predict and auto-complete')
            if self.alasio:
                # The file lives in <mod root>/module/config/_index/, so the
                # mod root is 4 levels above it
                gen.Raw('entry = ModEntry.create(__file__, uppath=4)')
            gen.Empty()

            # Generate group attributes organized by nav
            # Sort nav names for stable output, but keep group order as defined
            collected_groups = defaultdict(dict)
            for nav_name, config in self._iter_nav_config_by_order():
                # Nav comment
                gen.MultilineComment(f'========== nav: {nav_name} ==========')
                # having at lease one Scheduler group
                if nav_name == 'alasio' and not self.alasio:
                    gen.Anno('Scheduler', anno=f'"{nav_name}.Scheduler"')
                    gen.use_import(nav_name)

                # Generate type hints for each group (keep definition order)
                for index, (task_name, task) in enumerate(config.tasks_data.items()):
                    if not task.groups:
                        continue
                    if index:
                        gen.Empty()
                    gen.Comment(f'----- {task_name} -----')
                    for group_name, ref in task.groups.items():
                        group = self.groups_data[ref.model]
                        # skip groups without args (inforef groups)
                        if not group.args:
                            continue
                        cls_name = group.name
                        # the class of a group is generated in the model file
                        # of the nav declaring the group
                        parser = group.parser
                        if parser is None:
                            # this shouldn't happen, groups are parsed from a nav
                            raise DefinitionError(
                                f'Group "{group_name}" does not have a parser',
                                file=config.tasks_file, keys=[task_name, 'groups'], value=group_name)
                        model_nav = parser.nav_name
                        anno = f'{model_nav}.{cls_name}'
                        # special match that convert any Scheduler child group to Scheduler
                        # because we maintain the consistency between them
                        is_scheduler = 'Scheduler' in group.mro
                        if is_scheduler:
                            # scheduler: Scheduler is defined in alasio ConfigGenerated
                            gen.Comment(f'{group_name}: "{anno}"')
                            continue
                        # generate group_name: anno
                        # A group name may be bound by several tasks: the first
                        # binding declares the attribute, the later ones only add
                        # a comment. Binding it to a model of another inheritance
                        # chain is reported: the same input would be validated by
                        # two unrelated models, and the IDE reads a wrong type.
                        # A variant of the declared model is what variant groups
                        # are for, so it is not reported.
                        models = collected_groups[group_name]
                        if not models:
                            gen.use_import(model_nav)
                            gen.Anno(group_name, anno=f'"{anno}"')
                        else:
                            gen.Comment(f'{group_name}: "{anno}"')
                            if cls_name not in models and not self._is_related_model(models, cls_name):
                                logger.warning(
                                    f'Multiple validation model bound on group {task_name}.{group_name}, '
                                    f'might cause unexpected behaviour, '
                                    f'models: {sorted(list(models.values()) + [anno])}')
                        models[cls_name] = anno

                gen.Empty()

        # Write to file
        file = self.path_config.joinpath('_index/config_generated.py')
        op = gen.write(file, skip_same=True)
        if op:
            logger.info(f'Write file {file}')
            if gitadd:
                gitadd.stage_add(file)

    """
    Generate alasio/config/alasio/group_export.py
    """

    def generate_group_export(self, gitadd=None):
        gen = CodeGen()
        # This module only re-exports group classes, every import is part of the
        # public API. Without the file-level noqa, ruff F401 would delete them
        # all when the generated file is formatted.
        gen.Comment('ruff: noqa: F401')
        for module_name in [
            'alasio.config.alasio.group_base',
            'alasio.config.alasio.store_model',
            'alasio.config.alasio.mixin_model',
        ]:
            module = importlib.import_module(module_name)
            with gen.FromImport(module_name).wrap():
                for name in dir(module):
                    if name.startswith('_'):
                        continue
                    # skip imported modules
                    value = getattr(module, name)
                    if inspect.ismodule(value):
                        continue
                    # Only class, function, annotation alias
                    if not name[0].isupper():
                        continue
                    gen.Import(name)

        gen.FromImport('alasio.config.alasio.group_proxy').Import('batch_set')
        gen.FromImport('alasio.base.timer').Import('getnow')

        # group models
        for nav in self.dict_nav_config.values():
            with gen.FromImport(f'alasio.config.alasio.{nav.model_file.rootstem}').wrap():
                for group_name, group in nav.groups_data.items():
                    # _info group has no model
                    if not group.args:
                        continue
                    gen.Import(group_name)

        gen.CommentCodeGen('alasio.config_dev.gen_alasio')

        # Write to file
        file = self.path_config.joinpath('alasio/group_export.py')
        op = gen.write(file, skip_same=True)
        if op:
            logger.info(f'Write file {file}')
            if gitadd:
                gitadd.stage_add(file)
