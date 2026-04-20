from typing import Optional

from alasio.config.entry.const import ModEntryInfo
from alasio.config_dev.gen_config import ConfigGenerator
from alasio.config_dev.parse.base import DefinitionError
from alasio.config_dev.parse.build_mro import build_mro
from alasio.config_dev.parse.parse_groups import GroupData
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
        self.entry = entry
        self.root = PathStr.new(entry.root).abspath()
        self.path_config: PathStr = self.root.joinpath(entry.path_config)

        # Alasio global
        alasio = ModEntryInfo.alasio()
        self.alasio: "Optional[CrossNavGenerator]" = None
        if entry.root != alasio.root:
            self.alasio = self.__class__(alasio)

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
        # no alasio global loading
        for folder in self.path_config.iter_folders():
            # skip hidden folder
            if folder.name.startswith('_'):
                continue
            for file in folder.iter_files(ext='.args.yaml'):
                parser = ConfigGenerator(self.entry, file)
                parser.folder = folder.name
                # nav
                nav = parser.nav_name
                if self.alasio and nav in self.alasio.dict_nav_config:
                    raise DefinitionError(
                        f'Conflict nav name: "{nav}", which is already used in alasio',
                        file=file,
                    )
                if nav in out:
                    raise DefinitionError(
                        f'Duplicate nav name: "{nav}"',
                        file=file,
                    )
                out[nav] = parser
        return out

    """
    Group variant
    """

    @cached_property
    def groups_data(self) -> "dict[str, GroupData]":
        out: "dict[str, GroupData]" = {}
        if self.alasio:
            out = self.alasio.groups_data
        for config in self.dict_nav_config.values():
            # iter group data
            for group_name, group_data in config.groups_data.items():
                # group must be unique
                if self.alasio and group_name in self.alasio.groups_data:
                    raise DefinitionError(
                        f'Conflict group name: "{group_name}", which is already used in alasio',
                        file=config.file, keys=group_name,
                    )
                if group_name in out:
                    raise DefinitionError(
                        f'Duplicate group name: "{group_name}"',
                        file=config.file, keys=group_name,
                    )
                out[group_name] = group_data
        return out

    @cached_property
    def dict_group_mro(self) -> "dict[str, tuple[str, ...]]":
        """
        Returns:
            dict[str, tuple[str, ...]]:
                key: class name
                value: MRO chain
        """
        hierarchy = {}
        for group in self.groups_data.values():
            for parent in group.parent:
                if parent not in self.groups_data:
                    config = self.dict_group2configgen[group.name]
                    raise DefinitionError(
                        f'Invalid group parent: "{parent}", no such group',
                        file=config.file, keys=[group.name, 'parent'], value=parent,
                    )
            hierarchy[group.name] = group.parent

        dict_mro = build_mro(hierarchy)
        return dict_mro

    @cached_property
    def dict_group_variant2base(self) -> "dict[str, str]":
        """
        Convert variant name to base name
        """
        out = {}
        for group_name, mro in self.dict_group_mro.items():
            try:
                base = mro[-1]
            except IndexError:
                # this shouldn't happen, should have at lease one parent
                continue
            out[group_name] = base
        return out

    def build_group_mro(self):
        # copy mro to group object
        for group_name, mro in self.dict_group_mro.items():
            try:
                group = self.groups_data[group_name]
            except KeyError:
                continue  # this shouldn't happen
            group.mro = mro

            # check args override
            base_name = self.dict_group_variant2base.get(group_name, group_name)
            if base_name != group_name:
                try:
                    base = self.groups_data[base_name]
                except KeyError:
                    continue  # this shouldn't happen
                for arg_name in group.override_args:
                    if arg_name not in base.args:
                        config = self.dict_group2configgen[group_name]
                        raise DefinitionError(
                            f'Cannot add new arg in group variant, maybe add it to base group and be static in '
                            f'variant?',
                            file=config.file, keys=[group_name, 'args'], value=arg_name)

            # build args
            args = {}
            for parent_name in reversed(mro):
                try:
                    parent = self.groups_data[parent_name]
                except KeyError:
                    continue  # this shouldn't happen
                if parent.parent:
                    # variant group, pick overrides only
                    args.update(parent.override_args)
                else:
                    # not a variant group
                    args.update(parent.args)

            group.args = args

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
        if self.alasio:
            out = self.alasio.dict_group_ref
        for config in self.dict_nav_config.values():
            # calculate module file
            file = config.model_file.subpath_to(self.path_config)
            if file == config.model_file:
                raise DefinitionError(
                    f'model_file is not a subpath of root, model_file={config.model_file}, root={self.root}')
            file = to_posix(file)
            # iter group models
            for group_name in config.groups_data:
                # build model reference
                ref = {'file': file, 'cls': group_name}
                out[group_name] = ref

        return out

    @cached_property
    def tasks_data(self):
        out = {}
        for config in self.dict_nav_config.values():
            for task_name, task_data in config.tasks_data.items():
                # task name must be unique
                if self.alasio and task_name in self.alasio.model_data:
                    raise DefinitionError(
                        f'Conflict task name: "{task_name}", which is already used in alasio',
                        file=config.tasks_file, keys=task_name,
                    )
                if task_name in out:
                    raise DefinitionError(
                        f'Duplicate task name: "{task_name}"',
                        file=config.tasks_file, keys=task_name,
                    )
                out[task_name] = task_data
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
        # load alasio global_bind only
        if self.alasio:
            global_bind = self.alasio.model_data.get('_global_bind', {})
        all_groups = set()
        _ = self.tasks_data
        for config in self.dict_nav_config.values():
            for task_name, task in config.tasks_data.items():
                # task name must be unique
                if self.alasio and task_name in self.alasio.model_data:
                    raise DefinitionError(
                        f'Conflict task name: "{task_name}", which is already used in alasio',
                        file=config.tasks_file, keys=task_name,
                    )
                if task_name in out:
                    raise DefinitionError(
                        f'Duplicate task name: "{task_name}"',
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
                    base_name = self.dict_group_variant2base.get(group.group, group.group)
                    deep_set(out, [task_name, base_name], ref)
                    # add global bind
                    if task.global_bind:
                        if base_name in global_bind:
                            raise DefinitionError(
                                f'Duplicate global bind group: {group.group}',
                                file=config.tasks_file, keys=[task_name, 'group']
                            )
                        if base_name in all_groups:
                            raise DefinitionError(
                                f'Global bind group "{group.group}" is already used by non global bind, '
                                f'maybe remove the use of non global bind?'
                            )
                        global_bind[base_name] = ref
                    else:
                        if base_name in global_bind:
                            raise DefinitionError(
                                f'Group "{group.group}" is already global bind, '
                                f'maybe remove the use of non global bind?'
                            )
                        all_groups.add(base_name)

        # check if {ref_task_name}.{group_name} reference has corresponding value
        for _, group, ref in deep_iter_depth2(out):
            ref_task = ref['task']
            if deep_exist(out, [ref_task, group]):
                continue
            if self.alasio and deep_exist(self.alasio.model_data, [ref_task, group]):
                continue
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
        if self.alasio:
            out = self.alasio.dict_group2file
        for config in self.dict_nav_config.values():
            # calculate module file
            file = config.i18n_file.subpath_to(self.path_config)
            if file == config.model_file:
                raise DefinitionError(
                    f'gui_file is not a subpath of root, model_file={config.i18n_file}, root={self.root}')
            # iter group models
            file = to_posix(file)
            for group_name in config.i18n_data.keys():
                # no need to check if group is unique, dict_group_ref checked it already
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
        if self.alasio:
            out = self.alasio.dict_group2configgen
        for config in self.dict_nav_config.values():
            for group_name in config.groups_data.keys():
                out[group_name] = config
        return out

    def _validate_task_group(self, task: str, group: str):
        try:
            return self.model_data[task][group]['cls']
        except KeyError:
            pass
        if self.alasio:
            try:
                return self.alasio.model_data[task][group]['cls']
            except KeyError:
                pass
        raise DefinitionError(
            f'Display group has no corresponding task group: "{task}.{group}"')

    def _generate_nav_config_json(self, config: ConfigGenerator):
        """
        Generate {nav}_config.json from one nav config

        Returns:
            dict[str, dict[str, dict]]:
                key: {card_name}.{group_name}.{arg_name}
                value:
                    {"group": group, "arg": "_info"} for _info
                    {"task": task, "group": group, "arg": arg, **ArgData.to_dict()} for normal args
                        which is arg path appended with ArgData
        """
        out = {}
        for task_name, task in config.tasks_data.items():
            for card_index, card in enumerate(task.displays):
                # check if card.info valid
                if card.info not in self.groups_data:
                    raise DefinitionError(f'No such group "{card.info}"',
                                          file=config.file, keys=[task_name, 'displays', 'info'], value=card.info)
                # gen _info
                card_info = self.dict_group_variant2base.get(card.info, card.info)
                card_name = f'card-{card.task}-{card_info}'
                if config.nav_name != 'dashboard':
                    # No card._info in dashboard, for simpler data structure
                    row = {'group': card_info, 'arg': '_info', 'card': card_name}
                    deep_set(out, keys=[card_name, '_info'], value=NoIndent(row))
                # gen args
                for group in card.groups:
                    # validate if display_group refs a task group.
                    # accept both variant name and base name, here convert to base name first, then query its variant
                    base_name = self.dict_group_variant2base.get(group.group, group.group)
                    try:
                        cls = self._validate_task_group(group.task, base_name)
                    except DefinitionError as e:
                        e.file = config.tasks_file
                        e.keys = [group.task, 'displays']
                        e.value = group
                        raise
                    try:
                        group_data = self.groups_data[cls]
                    except KeyError:
                        continue  # this shouldn't happen

                    is_variant = base_name != cls
                    args = {}
                    for arg_name, arg in group_data.args.items():
                        if arg.hide:
                            continue
                        row = {'task': group.task, 'group': base_name, 'arg': arg_name}
                        # set cls on variant override
                        if is_variant:
                            row['cls'] = cls
                        # i18ngroup
                        if group_data.parent:
                            i18ngroup = ''
                            for parent_name in group_data.mro:
                                try:
                                    parent = self.groups_data[parent_name]
                                except KeyError:
                                    continue
                                if arg_name in parent.override_args:
                                    i18ngroup = parent.name
                            if i18ngroup and i18ngroup != base_name:
                                row['i18ngroup'] = i18ngroup

                        row.update(arg.to_dict())
                        args[arg_name] = row
                        # arg data post-process
                        for key in ['value', 'option']:
                            if key in row:
                                row[key] = NoIndent(row[key])
                    # add
                    if group_data.dashboard:
                        # wrap dashboard group as arg
                        row = {
                            'task': group.task, 'group': base_name, 'arg': '_info',
                            'dt': f'dashboard-{group_data.dashboard}',
                        }
                        if group_data.dashboard_color:
                            row['dashboard_color'] = group_data.dashboard_color
                        row['value'] = args
                        deep_set(out, keys=[card_name, card_index, group.group], value=row)
                    else:
                        # add args
                        for arg_name, row in args.items():
                            deep_set(out, keys=[card_name, base_name, arg_name], value=row)

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
