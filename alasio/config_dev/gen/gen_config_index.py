from collections import defaultdict

from alasio.config_dev.gen.gen_config import ConfigGenerator
from alasio.config_dev.gen.gen_cross import CrossNavGenerator
from alasio.config_dev.parse.base import DefinitionError
from alasio.ext.cache import cached_property
from alasio.ext.deep import deep_iter, deep_iter_depth2
from alasio.ext.file.jsonfile import NoIndent
from alasio.ext.path.calc import to_posix


class GenConfigIndex(CrossNavGenerator):
    """Generator for task.index.json and config.index.json."""

    @cached_property
    def task_index_file(self):
        return self.path_config.joinpath('_index/task.index.json')

    @cached_property
    def config_index_file(self):
        return self.path_config.joinpath('_index/config.index.json')

    @cached_property
    def dict_intask_group(self):
        """
        Returns:
            dict[str, set[str]]:
                key: task_name
                value: A set of group name in task
        """

        def iter_model_data():
            if self.alasio:
                yield from deep_iter_depth2(self.alasio.model_data)
            yield from deep_iter_depth2(self.model_data)

        out = defaultdict(set)
        for task_name, group_name, ref in iter_model_data():
            # drop _global_bind
            if task_name.startswith('_'):
                continue
            try:
                task = ref['task']
            except KeyError:
                # this shouldn't happen, because ref is generated
                raise DefinitionError(f'Group ref of {task_name}.{group_name} does not have "task": {ref}')
            if task_name != task:
                # skip cross task ref
                continue
            out[task_name].add(group_name)
        return out

    def _regroup_intask_group(self, all_task_groups, current_task=''):
        """
        Regroup task groups to simplify database query condition
        Convert:
            [("Alas", "Emulator"), ("OpsiDaily", "Scheduler"),
             ("OpsiDaily", "OpsiDaily"), ("OpsiGeneral", "OpsiGeneral")]
        to:
            tasks=[OpsiDaily, OpsiGeneral]
            task_groups=[("Alas", "Emulator")]

        Args:
            all_task_groups (list[tuple[str, str]] | iterator[tuple[str, str]]]):
                A list of (task, group)
            current_task (str):

        Returns:
            tuple[list[str], list[tuple[str, str]]]: tasks, task_groups
        """
        unique_task_group = defaultdict(set)
        for task, group in all_task_groups:
            unique_task_group[task].add(group)

        tasks = []
        task_groups = []
        for task_name, group_set in unique_task_group.items():
            try:
                intask_group = self.dict_intask_group[task_name]
            except KeyError:
                # this shouldn't happen, because task_name is already validated
                raise DefinitionError(f'No such task "{task_name}"')
            if task_name == current_task:
                # in current task, visit the entire task
                tasks.append(task_name)
            elif group_set == intask_group:
                # given `groups` equals intask groups,
                # convert individual group queries to one task query
                tasks.append(task_name)
            else:
                # visit individual groups
                for group_name in sorted(group_set):
                    task_groups.append([task_name, group_name])

        return tasks, task_groups

    @cached_property
    def task_index_data(self):
        """
        Returns:
            dict[str, dict]:
                key: task_name
                value: {"group": dict[str, dict], "config": dict}
                    "group" is a dict of:
                    - key: group_name
                    - value: {'file': file, 'cls': class_name, 'task': ref_task_name}
                    which indicates:
                    - read config from task={ref_task_name} and group={group_name}
                    - validate with model file={file}, class {class_name}
                    class_name can be:
                    - {group_name} for normal group
                    - {task_name}_{group_name} that inherits from class {group_name}, for override task group

                    "config" is {"task": list[str], "group": list[tuple[str, str]]}
                    which indicates to read task and taskgroups in user config
        """

        def iter_model_data():
            if self.alasio:
                for name, v in self.alasio.model_data.items():
                    # drop _global_bind
                    if name.startswith('_'):
                        continue
                    yield name, v
            # but keep _global_bind of self
            for name, v in self.model_data.items():
                yield name, v

        out = {}
        for task_name, group_data in iter_model_data():
            all_task_groups = []
            for group_name, ref in group_data.items():
                try:
                    task = ref['task']
                except KeyError:
                    # this shouldn't happen, because arg_data is generated
                    raise DefinitionError(f'Group ref does not have "task": {ref}')
                all_task_groups.append((task, group_name))
            tasks, groups = self._regroup_intask_group(all_task_groups, current_task=task_name)
            config = {'task': NoIndent(tasks), 'group': NoIndent(groups)}
            if not groups or not tasks:
                config = NoIndent(config)
            group_data = {k: NoIndent(v) for k, v in group_data.items()}
            out[task_name] = {
                'group': group_data,
                'config': config,
            }
        return out

    """
    Generate config.index.json
    """

    def _get_nav_config_i18n(self, config: ConfigGenerator):
        """
        Returns:
            list[str]: indicates to read {nav}_i18n.json
        """
        i18n = {}
        for keys, arg in deep_iter(config.config_data, depth=3):
            nav, card, group = keys
            # skip hidden
            if group.startswith('_'):
                continue
            group_name = arg.get('i18ngroup', '')
            if not group_name:
                group_name = arg.get('group', '')
            if not group_name:
                # this shouldn't happen, because dict is build at above
                raise DefinitionError(f'Missing "group" in {nav}.{card}', file=config.config_file)
            try:
                group = self.groups_data[group_name]
            except KeyError:
                # this shouldn't happen, because group_name is already validated
                raise DefinitionError(
                    f'Group "{group_name}" is not defined in any file', file=config.config_file)
            if self.alasio and group_name in self.alasio.groups_data:
                file = group.parser.i18n_file.subpath_to(self.alasio.path_config)
            else:
                file = group.parser.i18n_file.subpath_to(self.path_config)
            read = to_posix(file)
            i18n[read] = None

        # sort i18n to load, for consistent behaviour
        # alasio first, then mod's
        alasio_i18n = []
        mod_i18n = []
        for file in i18n:
            if file.startswith('alasio/'):
                alasio_i18n.append(file)
            else:
                mod_i18n.append(file)
        return sorted(alasio_i18n) + sorted(mod_i18n)

    def _get_nav_config_task(self, config: ConfigGenerator):
        """
        Returns:
            dict: {"task": list[str], "group": list[tuple[str, str]]}
                # indicates to read task and taskgroups in user config
        """
        all_task_groups = []
        for keys, arg_data in deep_iter(config.config_data, depth=3):
            _, _, arg_name = keys
            if arg_name.startswith('_'):
                continue
            try:
                task = arg_data['task']
                group = arg_data['group']
            except KeyError:
                # this shouldn't happen, because arg_data is generated
                raise DefinitionError(f'arg_data does not have "task" or "group": {arg_data}')
            all_task_groups.append((task, group))

        tasks, task_groups = self._regroup_intask_group(all_task_groups)
        return {'task': tasks, 'group': task_groups}

    @cached_property
    def config_index_data(self):
        """
        Returns:
            dict[str, dict]:
                key: {nav_name}
                value: {
                    "file": str,  # indicates to read {nav}_config.json
                    "i18n": list[str],  # indicates to read {nav}_i18n.json
                    "config": {"task": list[str], "group": list[tuple[str, str]]}
                        # indicates to read task and taskgroups in user config
                }
        """
        out = {}
        for nav_name, config in self.dict_nav_config.items():
            if not config.config_data:
                continue
            file = config.config_file.subpath_to(self.path_config)
            file = to_posix(file)
            i18n = self._get_nav_config_i18n(config)
            config = self._get_nav_config_task(config)
            out[nav_name] = {
                'file': file,
                'i18n': i18n,
                'config': {k: NoIndent(v) for k, v in config.items()}
            }
        return out
