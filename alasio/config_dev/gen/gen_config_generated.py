import importlib
import inspect
from collections import defaultdict

from alasio.codegen.python import CodeGen
from alasio.config_dev.gen.gen_cross import CrossNavGenerator
from alasio.logger import logger


class GenConfigGenerated(CrossNavGenerator):
    """Generator for config_generated.py and group_export.py."""

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
            gen.FromImport('..const').Import('entry')
        # TYPE_CHECKING block - imports only used for type hints
        with gen.If('t.TYPE_CHECKING'):
            gen.Import('alasio.config.alasio.group_export').as_('alasio').lazy()
            # Sort nav names for stable output
            for nav_name, config in self.dict_nav_config.items():
                # from .{nav} import {nav}_model as {nav}
                gen.FromImport(f'..{config.folder}').Import(f'{nav_name}_model').as_(nav_name).lazy()

        # Class definition
        if self.alasio:
            cls = gen.Class('ConfigGenerated').set_inherit('AlasioConfigBase')
        else:
            cls = gen.Class('AlasioConfigGenerated')
        with cls:
            gen.Comment('A generated config struct to fool IDE\'s type-predict and auto-complete')
            if self.alasio:
                gen.Raw('entry = entry')
            gen.Empty()

            # Generate group attributes organized by nav
            # Sort nav names for stable output, but keep group order as defined
            collected_groups = defaultdict(set)
            for nav_name, config in self.dict_nav_config.items():
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
                        anno = f'{group.parser.nav_name}.{cls_name}'
                        # special match that convert any Scheduler child group to Scheduler
                        # because we maintain the consistency between them
                        is_scheduler = 'Scheduler' in group.mro
                        if is_scheduler:
                            # scheduler: Scheduler is defined in alasio ConfigGenerated
                            gen.Comment(f'{group_name}: "{anno}"')
                            continue
                        # generate group_name: anno
                        if group_name in collected_groups:
                            gen.Comment(f'{group_name}: "{anno}"')
                        else:
                            gen.use_import(group.parser.nav_name)
                            gen.Anno(group_name, anno=f'"{anno}"')
                            # validate if Multiple validation model bound on the same group
                            collected_groups[group_name].add(anno)
                            if len(collected_groups[group_name]) > 1:
                                logger.warning(
                                    f'Multiple validation model bound on group {task_name}.{group_name}, '
                                    f'might cause unexpected behaviour, models: {collected_groups[group_name]}')

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
        for module_name in [
            'alasio.config.alasio.group_base',
            'alasio.config.alasio.store_model',
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
