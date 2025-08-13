from alasio.config.entry.const import ModEntryInfo
from alasio.config_dev.gen_config import ConfigGenerator
from alasio.config_dev.parse.base import DefinitionError
from alasio.config_dev.parse.parse_args import ArgData
from alasio.config_dev.parse.parse_tasks import TaskGroup
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
        for file in self.path_config.iter_files(ext='.args.yaml', recursive=True):
            parser = ConfigGenerator(file)
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
            file = config.model_file.subpath_to(self.root)
            if file == config.model_file:
                raise DefinitionError(
                    f'model_file is not a subpath of root, model_file={config.model_file}, root={self.root}')
            file = to_posix(file)
            # iter group models
            for group_name, class_name in config.dict_group2class.items():
                # group must be unique
                if group_name in out:
                    raise DefinitionError(
                        f'Duplicate group name: {group_name}',
                        file=config.file,
                        keys=group_name,
                    )
                # build model reference
                ref = {'file': file, 'cls': class_name}
                out[group_name] = ref

        return out

    @cached_property
    def model_index_data(self):
        """
        model data in model.index.json

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
        for config in self.dict_nav_config.values():
            for task_name, task_data in config.tasks_data.items():
                # task name must be unique
                if task_name in out:
                    raise DefinitionError(
                        f'Duplicate task name: {task_name}',
                        file=config.tasks_file,
                        keys=task_name,
                    )
                # generate groups
                for group in task_data.group:
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
                            f'Group ref "{group.group}" of task "{ref_task}" does not exist',
                            file=config.tasks_file,
                            keys=[task_name, 'group']
                        )
                    # copy ref, set ref_task
                    ref = {k: v for k, v in ref.items()}
                    ref['task'] = ref_task
                    deep_set(out, [task_name, group.group], NoIndent(ref))

        # check if {ref_task_name}.{group_name} reference has corresponding value
        for _, group, ref in deep_iter_depth2(out):
            ref_task = ref['task']
            if not deep_exist(out, [ref_task, group]):
                raise DefinitionError(
                    f'Cross-task group ref does not exist: {ref_task}.{group}',
                )

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
            file = config.i18n_file.subpath_to(self.root)
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
            for group_name in config.args_data.keys():
                out[group_name] = config
        return out

    def _group_name_to_data(self, group_name: str) -> "dict[str, ArgData]":
        """
        Convert group_name to group data
        """
        try:
            config = self.dict_group2configgen[group_name]
        except KeyError:
            raise DefinitionError(f'Group name is not defined: {group_name}')
        try:
            data = config.args_data[group_name]
        except KeyError:
            # this shouldn't happen, because dict_group2configgen is build from config.args_data
            raise DefinitionError(f'Nav args "{config.file}" has no group_name={group_name}')
        return data

    def _get_display_info(
            self, config: ConfigGenerator, task_name: str, display_flat: "list[TaskGroup]"
    ) -> TaskGroup:
        """
        Predict info reference from a list of display_flat
        """
        try:
            first = display_flat[0]
        except IndexError:
            raise DefinitionError(
                f'Empty display_flat: {display_flat}',
                file=config.tasks_file, keys=[task_name, 'display']
            )
        # use info ref first
        if first.inforef:
            try:
                _ = self.dict_group2file[first.inforef]
            except KeyError:
                raise DefinitionError(
                    f'inforef "{first.inforef}" does not exists',
                    file=config.tasks_file, keys=[task_name, 'display']
                )
            return TaskGroup(task='', group=first.inforef)

        # no info ref, use the first group that is not Scheduler
        for group in display_flat:
            group_name = group.group
            if group_name == 'Scheduler':
                continue
            try:
                _ = self.dict_group2file[group_name]
            except KeyError:
                raise DefinitionError(
                    f'Display group "{group_name}" does not exists',
                    file=config.tasks_file, keys=[task_name, 'display']
                )
            return TaskGroup(task='', group=group_name)

        # no luck, just use the first group
        try:
            _ = self.dict_group2file[first.group]
        except KeyError:
            raise DefinitionError(
                f'Display group "{first.group}" does not exists',
                file=config.tasks_file, keys=[task_name, 'display']
            )
        return TaskGroup(task='', group=first.group)

    def _iter_display(
            self, config: ConfigGenerator, task_name: str, display_flat: "list[TaskGroup]",
    ):
        """
        Iter display reference from a list of display_flat

        Yields:
            TaskGroup:
        """
        for display in display_flat:
            # skip info ref
            if display.inforef:
                continue
            # display group must be defined
            try:
                _ = self.dict_group2file[display.group]
            except KeyError:
                raise DefinitionError(
                    f'Display ref "{display.group}" of task "{task_name}" does not exist',
                    file=config.tasks_file, keys=[task_name, 'display']
                )
            if display.task:
                # If display a cross-task group, group must exist
                if not deep_exist(self.model_index_data, keys=[display.task, display.group]):
                    raise DefinitionError(
                        f'Cross-task display ref "{display.task}.{display.group}" does not exist',
                        file=config.tasks_file, keys=[task_name, 'display']
                    )
                yield TaskGroup(task=display.task, group=display.group)
            else:
                # If display an in-task group, group must within this task
                if not deep_exist(self.model_index_data, keys=[task_name, display.group]):
                    raise DefinitionError(
                        f'In-task display ref "{display.group}" is not in task "{task_name}"',
                        file=config.tasks_file, keys=[task_name, 'display']
                    )
                yield TaskGroup(task=task_name, group=display.group)

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
            is_flat = len(task.display) == 1
            for display_flat in task.display:
                # get display
                display = list(self._iter_display(config, task_name, display_flat))
                if is_flat:
                    card_name = f'card-{task_name}'
                else:
                    card_name = '_'.join([d.group for d in display])
                    card_name = f'card-{task_name}-{card_name}'
                # get _info
                info_group = self._get_display_info(config, task_name, display_flat)
                # gen _info
                row = {'group': info_group.group, 'arg': '_info'}
                deep_set(out, keys=[card_name, '_info'], value=NoIndent(row))
                # gen args
                for display_group in display:
                    group_data = self._group_name_to_data(display_group.group)
                    for arg_name, arg_data in group_data.items():
                        row = {'task': display_group.task, 'group': display_group.group, 'arg': arg_name}
                        row.update(arg_data.to_dict())
                        arg_name = f'{display_group.group}_{arg_name}'
                        deep_set(out, keys=[card_name, arg_name], value=row)
                        # arg data post-process
                        # no default to frontend
                        row.pop('default', None)
                        # inline option
                        if 'option' in row:
                            row['option'] = NoIndent(row['option'])

        # store in config object, so other methods can reuse
        config.config_data = out
        return out

    def generate_config_json(self):
        """
        Generate {nav}_config.json for all nav
        """
        for config in self.dict_nav_config.values():
            data = self._generate_nav_config_json(config)
            # {nav}_i18n.json
            if data:
                op = write_json_custom_indent(config.config_file, data, skip_same=True)
                if op:
                    logger.info(f'Write file {config.config_file}')
            else:
                if config.config_file.atomic_remove():
                    logger.info(f'Delete file {config.config_file}')
