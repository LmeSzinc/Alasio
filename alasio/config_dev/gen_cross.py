from alasio.config.entry.const import ModEntryInfo
from alasio.config_dev.gen_config import ConfigGenerator
from alasio.config_dev.parse.base import DefinitionError
from alasio.config_dev.parse.parse_args import ArgData
from alasio.ext.cache import cached_property
from alasio.ext.deep import deep_exist, deep_iter_depth2, deep_set
from alasio.ext.file.jsonfile import NoIndent, write_json_custom_indent
from alasio.ext.path import PathStr
from alasio.ext.path.calc import to_posix
from alasio.logger import logger


class CrossNavGenerator:
    def __init__(self, entry: ModEntryInfo):
        """
        维护带跨nav引用的数据
        model.index.json
        {nav}_i18n.json
        """
        self.root = PathStr.new(entry.root).abspath()
        self.path_config: PathStr = self.root.joinpath(entry.path_config)

    @cached_property
    def dict_nav_config(self):
        """
        All ParseNavConfig objects

        Returns:
            dict[str, ConfigGenerator]:
                key: nav_name
                value: generator
        """
        out = {}
        for folder in self.path_config.iter_folders():
            # skip hidden folder
            if folder.name.startswith('_'):
                continue
            for file in folder.iter_files(ext='.args.yaml'):
                parser = ConfigGenerator(file)
                parser.folder = folder.name
                # nav
                nav = parser.nav_name
                if nav in out:
                    raise DefinitionError(
                        f'Duplicate nav name: {nav}',
                        file=file,
                    )
                out[nav] = parser
        return out

    """
    Generate model.index.json
    """

    @cached_property
    def dict_group_ref(self):
        """
        convert group name to where the msgspec model class is defined

        Returns:
            dict[str, dict[str, str]]:
                key: {group_name}
                value: {'file': file, 'cls': class_name}
        """
        out = {}
        for config in self.dict_nav_config.values():
            # calculate module file
            file = config.model_file.subpath_to(self.path_config)
            if file == config.model_file:
                raise DefinitionError(
                    f'model_file is not a subpath of root, model_file={config.model_file}, root={self.root}')
            file = to_posix(file)
            # iter group models
            for group, class_name in config.args_data.items():
                # group must be unique
                full_name = group.full_name
                if full_name in out:
                    raise DefinitionError(
                        f'Duplicate group name: {full_name}',
                        file=config.file,
                        keys=full_name,
                    )
                # build model reference
                ref = {'file': file, 'cls': group.class_name}
                out[group.class_name] = ref

        return out

    @cached_property
    def model_data(self):
        """
        Returns:
             dict[str, dict[str, str | dict[str, str]]]:
                key: {task_name}.{group_name}
                value: {'file': file, 'cls': class_name, 'task': ref_task_name}
                    which indicates:
                    - read config from task={ref_task_name} and group={group_name}
                    - validate with model file={file}, class {class_name}
                    class_name can be:
                    - {group_name} for normal group
                    - {task_name}_{group_name} that inherits from class {group_name}, for override task group
        """
        out = {}
        global_bind = {}
        all_groups = set()
        for config in self.dict_nav_config.values():
            for task_name, task in config.tasks_data.items():
                # task name must be unique
                if task_name in out:
                    raise DefinitionError(
                        f'Duplicate task name: {task_name}',
                        file=config.tasks_file, keys=task_name,
                    )
                # generate groups
                for group in task.groups:
                    if group.task:
                        # reference {ref_task_name}.{group_name}
                        ref_task = group.task
                    else:
                        # reference task self
                        ref_task = task_name
                    # check if group exists
                    try:
                        ref = self.dict_group_ref[group.group]
                    except KeyError:
                        raise DefinitionError(
                            f'No such group "{group.group}"',
                            file=config.tasks_file, keys=[task_name, 'group'], value=group.group
                        )
                    # copy ref, set ref_task
                    ref = {k: v for k, v in ref.items()}
                    ref['task'] = ref_task
                    ref = NoIndent(ref)
                    deep_set(out, [task_name, group.group], ref)
                    # add global bind
                    if task.global_bind:
                        if group.group in global_bind:
                            raise DefinitionError(
                                f'Duplicate global bind group: {group.group}',
                                file=config.tasks_file, keys=[task_name, 'group']
                            )
                        if group.group in all_groups:
                            raise DefinitionError(
                                f'Global bind group "{group.group}" is already used by non global bind, '
                                f'maybe remove the use of non global bind?'
                            )
                        global_bind[group.group] = ref
                    else:
                        if group.group in global_bind:
                            raise DefinitionError(
                                f'Group "{group.group}" is already global bind, '
                                f'maybe remove the use of non global bind?'
                            )
                        all_groups.add(group.group)

        # check if {ref_task_name}.{group_name} reference has corresponding value
        for _, group, ref in deep_iter_depth2(out):
            ref_task = ref['task']
            if not deep_exist(out, [ref_task, group]):
                raise DefinitionError(
                    f'Cross-task group ref does not exist: {ref_task}.{group}',
                )

        out['_global_bind'] = global_bind

        return out

    """
    Generate {nav}_i18n.json
    """

    @cached_property
    def dict_group2file(self):
        """
        Convert group name to {nav}.config.json to read

        Returns:
            dict[str, str]:
                key: {group_name}
                value: relative path to {nav}.config.json
        """
        out = {}
        for config in self.dict_nav_config.values():
            # calculate module file
            file = config.i18n_file.subpath_to(self.path_config)
            if file == config.model_file:
                raise DefinitionError(
                    f'gui_file is not a subpath of root, model_file={config.i18n_file}, root={self.root}')
            # iter group models
            file = to_posix(file)
            for group_name in config.i18n_data.keys():
                # group must be unique
                if group_name in out:
                    raise DefinitionError(
                        f'Duplicate group name: {group_name}',
                        file=config.tasks_file,
                        keys=group_name,
                    )
                out[group_name] = file

        return out

    @cached_property
    def dict_group2configgen(self):
        """
        Convert group name to ConfigGenerator object

        Returns:
            dict[str, ConfigGenerator]:
        """
        out = {}
        for config in self.dict_nav_config.values():
            for group_name in config.group_data.keys():
                out[group_name] = config
        return out

    def _group_name_to_data(self, group_name: str) -> "dict[str, ArgData]":
        """
        Convert group_name to group data
        """
        try:
            config = self.dict_group2configgen[group_name]
        except KeyError:
            raise DefinitionError(f'No such group to display: {group_name}')
        try:
            data = config.group_data[group_name]
        except KeyError:
            # this shouldn't happen, because dict_group2configgen is build from config.args_data
            raise DefinitionError(f'Nav args "{config.file}" has no group_name={group_name}')
        return data

    def _generate_nav_config_json(self, config: ConfigGenerator):
        """
        Generate {nav}_config.json from one nav config

        Returns:
            dict[str, dict[str, dict]]:
                key: {card_name}.{arg_name}
                value:
                    {"group": group, "arg": "_info"} for _info
                    {"task": task, "group": group, "arg": arg, **ArgData.to_dict()} for normal args
                        which is arg path appended with ArgData
        """
        out = {}
        for task_name, task in config.tasks_data.items():
            for card in task.displays:
                # gen _info
                row = {'group': card.info, 'arg': '_info'}
                _ = self._group_name_to_data(card.info)  # check if card.info valid
                deep_set(out, keys=[card.card_name, '_info'], value=NoIndent(row))
                # gen args
                for display_group in card.groups:
                    group_data = self._group_name_to_data(display_group.group)
                    for arg_name, arg_data in group_data.items():
                        row = {'task': display_group.task, 'group': display_group.group, 'arg': arg_name}
                        row.update(arg_data.to_dict())
                        arg_name = f'{display_group.group}_{arg_name}'
                        deep_set(out, keys=[card.card_name, arg_name], value=row)
                        # arg data post-process
                        for key in ['value', 'option']:
                            if key in row:
                                row[key] = NoIndent(row[key])

        # store in config object, so other methods can reuse
        config.config_data = out
        return out

    def generate_config_json(self, gitadd=None):
        """
        Generate {nav}_config.json for all nav
        """
        for config in self.dict_nav_config.values():
            data = self._generate_nav_config_json(config)
            # {nav}_i18n.json
            file = config.config_file
            if data:
                op = write_json_custom_indent(file, data, skip_same=True)
                if op:
                    logger.info(f'Write file {file}')
                    if gitadd:
                        gitadd.stage_add(file)
            else:
                if config.config_file.atomic_remove():
                    logger.info(f'Delete file {file}')
