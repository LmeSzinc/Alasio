from typing import Optional

from alasio.backport import removeprefix
from alasio.config.entry.const import ModEntryInfo
from alasio.config_dev.gen.gen_config import ConfigGenerator
from alasio.config_dev.parse.base import DefinitionError
from alasio.config_dev.parse.build_mro import build_mro
from alasio.config_dev.parse.cache_alasio import CacheAlasio
from alasio.config_dev.parse.parse_groups import GroupData
from alasio.config_dev.parse.parse_store import ParseStore
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
        self.alasio: "Optional[CrossNavGenerator]" = CacheAlasio().get(entry)

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
                # alasio store uses specific generator class
                if not self.alasio and parser.nav_name == 'store':
                    parser = ParseStore(self.entry, file)
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
        # insert alasio groups
        if self.alasio:
            out = self.alasio.groups_data.copy()
        # insert mod groups
        for config in self.dict_nav_config.values():
            # iter group data
            for group_name, group_data in config.groups_data.items():
                # group name cannot be GroupBase
                if group_name == 'GroupBase':
                    raise DefinitionError(
                        f'Group name cannot be "GroupBase"',
                        file=config.file, keys=[group_name],
                    )
                # group must be unique
                if self.alasio and group_name in self.alasio.groups_data:
                    raise DefinitionError(
                        f'Conflict group name: "{group_name}", which is already used in alasio',
                        file=config.file, keys=[group_name],
                    )
                if group_name in out:
                    raise DefinitionError(
                        f'Duplicate group name: "{group_name}"',
                        file=config.file, keys=[group_name],
                    )
                # group parent can only be the group defined above in the same file, or in alasio
                if self.alasio:
                    for parent in group_data.parent:
                        if parent in self.alasio.groups_data:
                            continue
                        if parent in out and parent in config.groups_data:
                            continue
                        raise DefinitionError(
                            f'Group {group_name} parent {parent} must be defined in alasio or above in the same file',
                            file=config.file, keys=[group_name, 'parent'], value=parent,
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
                    raise DefinitionError(
                        f'Invalid group parent: "{parent}", no such group',
                        file=group.parser.file, keys=[group.name, 'parent'], value=parent,
                    )
            hierarchy[group.name] = group.parent

        dict_mro = build_mro(hierarchy)
        return dict_mro

    def build_group_mro(self):
        # copy mro to group object
        for group_name, mro in self.dict_group_mro.items():
            try:
                group = self.groups_data[group_name]
            except KeyError:
                continue  # this shouldn't happen
            group.mro = mro

            # build args
            args = {}
            dashboard = ''
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
                # check if parent or any ancestor is dashboard group
                # use the last dashboard group (the first Dashboard ancestor)
                if parent.name.startswith('Dashboard'):
                    dashboard = removeprefix(parent.name, 'Dashboard')

            group.args = args
            group.dashboard = dashboard

        # build group model
        for task_name, task in self.tasks_data.items():
            for ref in task.groups.values():
                try:
                    parent_ref = self.tasks_data[ref.task].groups[ref.group]
                except KeyError:
                    raise DefinitionError(
                        f'Invalid cross-task group reference: {ref.task}.{ref.group}, no such group',
                        file=task.parser.tasks_file, keys=[task_name, 'groups'], value=ref)
                if not ref.model:
                    ref.model = parent_ref.model
            for card in task.displays.values():
                for ref in card.groups.values():
                    # validate if display_group refs a task group.
                    try:
                        parent_ref = self.tasks_data[ref.task].groups[ref.group]
                    except KeyError:
                        raise DefinitionError(
                            f'Invalid cross-task display reference: {ref.task}.{ref.group}, no such group',
                            file=task.parser.tasks_file, keys=[task_name, 'displays'], value=ref)
                    if not ref.model:
                        ref.model = parent_ref.model

        # build card info
        for task_name, task in self.tasks_data.items():
            for card in task.displays.values():
                info = card.raw_info
                if info in card.groups:
                    group = card.groups[info]
                    info = group.model
                card.info = info
                if card.info not in self.groups_data:
                    raise DefinitionError(
                        f'Invalid display info group: {card.info}, no such group',
                        file=task.parser.tasks_file, keys=[task_name, 'displays'], value=card.info)

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
            for group_name, group in config.groups_data.items():
                # build model reference
                ref = {'file': file, 'cls': group_name}
                out[group_name] = ref

        return out

    @cached_property
    def tasks_data(self):
        out = {}
        if self.alasio:
            out = self.alasio.tasks_data
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
             dict[str, dict[str, dict[str, str]]]:
                key: {task_name}.{group_name}
                value: {'file': file, 'cls': class_name, 'task': ref_task_name}
                    which indicates:
                    - read config from task={ref_task_name} and group={group_name}
                    - validate with model file={file}, class {class_name}
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
                for group_name, group in task.groups.items():
                    if group.task:
                        # reference {ref_task_name}.{group_name}
                        ref_task = group.task
                    else:
                        # reference task self
                        ref_task = task_name
                    # check if group exists
                    try:
                        ref = self.dict_group_ref[group.model]
                    except KeyError:
                        raise DefinitionError(
                            f'No such group model "{group.model}"',
                            file=config.tasks_file, keys=[task_name, 'groups', group.group], value=group.model
                        )
                    # copy ref, set ref_task
                    ref = {k: v for k, v in ref.items()}
                    ref['task'] = ref_task
                    deep_set(out, [task_name, group.group], ref)
                    # add global bind
                    if task.global_bind:
                        if group_name in global_bind:
                            raise DefinitionError(
                                f'Duplicate global bind group: {group_name}',
                                file=config.tasks_file, keys=[task_name, 'groups']
                            )
                        if group_name in all_groups:
                            raise DefinitionError(
                                f'Global bind group "{group_name}" is already used by non global bind, '
                                f'maybe remove the use of non global bind?'
                            )
                        global_bind[group_name] = ref
                    else:
                        if group_name in global_bind:
                            raise DefinitionError(
                                f'Group "{group_name}" is already global bind, '
                                f'maybe remove the use of non global bind?'
                            )
                        all_groups.add(group_name)

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
    Generate {nav}_config.json
    """

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
            for card_name, card in task.displays.items():
                # check if card.info valid
                if card.info not in self.groups_data:
                    raise DefinitionError(f'No such group "{card.info}"',
                                          file=config.file, keys=[task_name, 'displays'], value=card)
                # gen _info
                if config.nav_name != 'dashboard':
                    # No card._info in dashboard, for simpler data structure
                    row = {'group': card.info, 'arg': '_info', 'card': card_name}
                    deep_set(out, keys=[card_name, '_info'], value=NoIndent(row))
                # gen args
                for group_name, ref in card.groups.items():
                    group = self.groups_data[ref.model]
                    args = {}
                    if group.dashboard:
                        info = {'group': group_name, 'arg': '_info', 'dashboard': group.dashboard}
                        if group.dashboard_color:
                            info['dashboard_color'] = group.dashboard_color
                        args['_info'] = NoIndent(info)
                    for arg_name, arg in group.args.items():
                        if arg.hide:
                            continue
                        row = {'task': ref.task, 'group': ref.group, 'arg': arg_name}
                        if ref.group != ref.model:
                            row['cls'] = ref.model
                        # i18ngroup
                        i18ngroup = ref.model
                        if group.parent:
                            i18ngroup = ''
                            for parent_name in group.mro:
                                try:
                                    parent = self.groups_data[parent_name]
                                except KeyError:
                                    continue
                                if arg_name in parent.override_args:
                                    i18ngroup = parent.name
                                    break
                        if i18ngroup and i18ngroup != ref.group:
                            row['i18ngroup'] = i18ngroup

                        row.update(arg.to_dict())
                        args[arg_name] = row
                        # arg data post-process
                        for key in ['value', 'option']:
                            if key in row:
                                row[key] = NoIndent(row[key])
                        option_dict = row.get('option_dict')
                        if option_dict:
                            row['option_dict'] = {k: NoIndent(v) for k, v in option_dict.items()}
                    # add args
                    for arg_name, row in args.items():
                        deep_set(out, keys=[card_name, group_name, arg_name], value=row)

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
