from typing import Optional

from alasio.backport import removeprefix
from alasio.config.entry.const import ModEntryInfo
from alasio.config_dev.gen.gen_config import ConfigGenerator
from alasio.config_dev.parse.base import DefinitionError
from alasio.config_dev.parse.build_mro import build_mro
from alasio.config_dev.parse.cache_alasio import CacheAlasio
from alasio.config_dev.parse.parse_groups import GroupData, ParseGroups
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

        # one nav folder can only have one nav defined, expect for alasio internal
        if self.alasio:
            dict_folder = {}
            for nav_name, parser in out.items():
                if parser.folder in dict_folder:
                    raise DefinitionError(
                        f'Cannot define multiple nav in the same nav folder',
                        file=parser.file,
                    )
                dict_folder[parser.folder] = nav_name

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
        # First pass: insert all groups from all navs to allow cross-nav
        # variant parents to be resolved regardless of nav iteration order
        for config in self.dict_nav_config.values():
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
                out[group_name] = group_data
        # Second pass: validate that all parents exist in the global registry
        for config in self.dict_nav_config.values():
            for group_name, group_data in config.groups_data.items():
                if self.alasio:
                    for parent in group_data.parent:
                        if parent in self.alasio.groups_data:
                            continue
                        if parent in out:
                            continue
                        raise DefinitionError(
                            f'Group {group_name} parent {parent} must be defined in one of the nav files',
                            file=config.file, keys=[group_name, 'parent'], value=parent,
                        )
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
    Cross-nav helpers
    """

    @cached_property
    def dict_group_to_ancestor_nav(self) -> "dict[str, ParseGroups]":
        """
        Maps each group name to the ConfigGenerator of the nav where its
        msgspec model class should be generated.

        Uses the already-resolved MRO on each group. For each group, the MRO
        is traversed in reverse (root ancestor first) to find the first
        non-alasio ancestor. That ancestor's parser is the ConfigGenerator
        that should host this group's model class.

        Groups with no parent or whose entire parent chain ends in an alaiso
        nav map to their own parser.

        Returns:
            dict[str, ConfigGenerator]:
                key: group_name
                value: ConfigGenerator of the nav that should host the model
        """
        out = {}

        for group_name, group in self.groups_data.items():
            # Default: model stays in own nav
            ancestor = group.parser
            # Traverse MRO in reverse (root ancestor first) to find the
            # first non-alasio ancestor
            for ancestor_name in reversed(group.mro):
                if ancestor_name == group.name:
                    continue  # skip self
                ancestor_group = self.groups_data.get(ancestor_name)
                if ancestor_group is None:
                    continue
                # Skip alasio framework groups
                if self.alasio and ancestor_group.parser.nav_name in self.alasio.dict_nav_config:
                    continue
                # First non-alasio ancestor found — its nav hosts this group's model
                ancestor = ancestor_group.parser
                break
            out[group_name] = ancestor

        return out

    def _setup_cross_nav_model_data(self):
        """
        Setup foreign model group data for cross-nav variant groups.

        For each nav's ConfigGenerator, this sets:
        - foreign_model_group_data: groups from other navs whose model should be
          generated in this nav's model file (because their parent is in this nav)
        - skip_model_group_names: groups that should NOT have their model generated
          in their own nav (because it will be generated in the parent nav instead)
        """
        # Clear
        for nav in self.dict_nav_config.values():
            nav.foreign_model_group_data.clear()
            nav.skip_model_group_names.clear()

        # Find cross-nav variants using the ancestor nav mapping
        for group_name, group in self.groups_data.items():
            ancestor_config = self.dict_group_to_ancestor_nav[group_name]
            if ancestor_config is group.parser:
                continue
            # Cross-nav variant: model follows parent chain to ancestor_config's nav
            # ancestor_config is guaranteed to be a mod nav (alasio case is
            # already handled in dict_group_to_ancestor_nav)
            ancestor_config.foreign_model_group_data[group_name] = group
            group.parser.skip_model_group_names.add(group_name)

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
            # iter group models
            for group_name, group in config.groups_data.items():
                ancestor_config = self.dict_group_to_ancestor_nav[group_name]
                file = ancestor_config.model_file.subpath_to(self.path_config)
                if file == config.model_file:
                    raise DefinitionError(
                        f'model_file is not a subpath of root, model_file={config.model_file}, root={self.root}')
                file = to_posix(file)
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
                model_data have extra key '_global_bind'
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

        # add _global_bind
        # move dashboard groups to the end, to reduce diff complexity when adding new groups
        groups = {}
        for group_name, ref in global_bind.items():
            if ref.get('task') != 'Dashboard':
                groups[group_name] = ref
        for group_name, ref in global_bind.items():
            if ref.get('task') == 'Dashboard':
                groups[group_name] = ref
        global_bind = groups
        out['_global_bind'] = global_bind

        return out

    """
    Generate {nav}_config.json
    """

    def _resolve_info_i18ngroup(self, group):
        """
        For a variant group without override_i18n, find the ancestor whose
        _info should be used. Returns the first ancestor with override_i18n=True
        along the MRO chain, or the root of the MRO chain if none found.

        Args:
            group (GroupData): The variant group

        Returns:
            str: Ancestor group name to use for _info
        """
        for parent_name in group.mro:
            if parent_name == group.name:
                continue
            parent = self.groups_data.get(parent_name)
            if parent is not None and parent.override_i18n:
                return parent_name
        # Fall back to the last in MRO (root/base group)
        if group.mro:
            return group.mro[-1]
        return group.name

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
                    # resolve group._info
                    info_group = self.groups_data[card.info]
                    if info_group.parent and not info_group.override_i18n and not info_group.dashboard:
                        i18ngroup = self._resolve_info_i18ngroup(info_group)
                        if i18ngroup and i18ngroup != card.info:
                            # group._info does not have i18ngroup, it just uses group to indicate
                            row['group'] = i18ngroup
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
