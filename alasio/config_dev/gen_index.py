from collections import defaultdict

from alasio.config.const import Const
from alasio.config.entry.const import DICT_MOD_ENTRY
from alasio.config_dev.gen_config import ConfigGenerator
from alasio.config_dev.gen_cross import CrossNavGenerator
from alasio.config_dev.parse.base import DefinitionError
from alasio.ext import env
from alasio.ext.cache import cached_property
from alasio.ext.codegen import CodeGen
from alasio.ext.deep import deep_get, deep_iter_depth2, deep_set
from alasio.ext.file.jsonfile import NoIndent, write_json_custom_indent
from alasio.ext.file.msgspecfile import read_msgspec
from alasio.ext.path.calc import to_posix
from alasio.git.stage.gitadd import GitAdd
from alasio.logger import logger


class IndexGenerator(CrossNavGenerator):
    def __init__(self, *args, **kwargs):
        """
        维护一个MOD下所有设置文件的数据一致性，生成json索引文件。

        1. "tasks.index.json" 指导当前MOD下有哪些task。
            这个文件会在脚本运行时被使用。
            当Alasio脚本实例启动时，它需要读取与某个task相关的设置：
            - 在"model.index.json"中查询当前task
            - 遍历task下有哪些group，得到group所在的{nav}_model.py文件路径
            - 载入python文件，查询{nav}_model.py中的group对应的msgspec模型
            - 读取用户数据，使用msgspec模型校验数据

        2. "config.index.json" 指导当前MOD有哪些设置，具有 nav 一级结构。
            这个文件会在前端显示时被使用。
            当前端需要显示某个nav下的用户设置时：
            - 按file指示，加载{nav}_config.json作为结构
            - 按i18n指示，加载{nav}_i18n.json
            - 按config指示，加载用户设置中的task和group
            - 聚合所有内容

        3. "nav.index.json" 是任务和任务导航的i18n，具有 nav_name.card_name.lang 三级结构。
            这个文件会在前端显示时被使用。
            注意这是一个半自动生成文件，Alasio会维护它的数据结构，但是需要人工编辑nav对应的i18n，
            当前端需要显示导航组件时：
            - 读取"nav.index.json"返回给前端
        """
        super().__init__(*args, **kwargs)

        # {path_config}/task.index.json
        self.task_index_file = self.path_config.joinpath('task.index.json')
        # {path_config}/config.index.json
        self.config_index_file = self.path_config.joinpath('config.index.json')
        # {path_config}/nav.index.json
        self.nav_index_file = self.path_config.joinpath('nav.index.json')
        # {path_config}/queue.index.json
        self.queue_index_file = self.path_config.joinpath('queue.index.json')

    """
    Generate model.index.json
    """

    @cached_property
    def dict_intask_group(self):
        """
        Returns:
            dict[str, set[str]]:
                key: task_name
                value: A set of group name in task
        """
        out = defaultdict(set)
        for task_name, group_name, ref in deep_iter_depth2(self.model_data):
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
        out = {}

        for task_name, group_data in self.model_data.items():
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
        for nav, card, arg in deep_iter_depth2(config.config_data):
            try:
                group_name = arg['group']
            except KeyError:
                # this shouldn't happen, because dict is build at above
                raise DefinitionError(f'Missing "group" in {nav}.{card}', file=config.config_file)
            try:
                read = self.dict_group2file[group_name]
            except KeyError:
                # this shouldn't happen, because group_name is already validated
                raise DefinitionError(
                    f'Group "{group_name}" is not defined in any file', file=config.config_file)
            i18n[read] = None
        return list(i18n)

    def _get_nav_config_task(self, config: ConfigGenerator):
        """
        Returns:
            dict: {"task": list[str], "group": list[tuple[str, str]]}
                # indicates to read task and taskgroups in user config
        """
        all_task_groups = []
        for _, arg_name, arg_data in deep_iter_depth2(config.config_data):
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

    """
    Generate nav.index.json
    """

    @cached_property
    def dict_group_name_i18n(self):
        """
        Returns:
            dict[str, dict[str, str]]:
                key: {group_name}.{lang}
                value: translation
        """
        out = {}
        for config in self.dict_nav_config.values():
            for group_name, group_data in config.i18n_data.items():
                group_name = self.dict_group_variant2base.get(group_name, group_name)
                i18n = deep_get(group_data, ['_info'], default={})
                # get "name" from a nested i18n dict
                for lang, field_data in i18n.items():
                    try:
                        name = field_data['name']
                    except KeyError:
                        name = ''
                    deep_set(out, [group_name, lang], name)

        return out

    @cached_property
    def nav_index_data(self):
        """
        data of nav.index.json

        Returns:
            dict[str, dict[str, dict[str, str]]]:
                key: {nav_name}.{card_name}.{lang}
                value: i18n translation
        """
        old = read_msgspec(self.nav_index_file)
        out = {}
        for nav_name, config in self.dict_nav_config.items():
            # nav name, which must not empty
            empty = True
            for group in config.tasks_data.values():
                if group.displays:
                    empty = False
                    break
            if config.tasks_data and not empty:
                for lang in Const.GUI_LANGUAGE:
                    key = [nav_name, '_info', lang]
                    value = deep_get(old, key, default='')
                    if not value:
                        value = nav_name
                    deep_set(out, key, value)
            for card_name, data in config.config_data.items():
                # card name
                if card_name.startswith('_'):
                    continue
                try:
                    info = data['_info']
                except KeyError:
                    raise DefinitionError(f'Card "{nav_name}.{card_name}" has no "_info"')
                try:
                    group_name = info['group']
                except KeyError:
                    raise DefinitionError(f'Card "{nav_name}.{card_name}._info" has no "group"')
                group_name = self.dict_group_variant2base.get(group_name, group_name)
                try:
                    name = self.dict_group_name_i18n[group_name]
                except KeyError:
                    raise DefinitionError(
                        f'Card "{nav_name}.{card_name}._info" reference a non-exist group: {group_name}')
                deep_set(out, [nav_name, card_name], name)

        return out

    """
    Generate queue.index.json
    """

    @cached_property
    def queue_index_data(self):
        """
        data of queue.index.json

        Returns:
            dict[str, dict[str, str]]:
                key: {task_name}.{lang}
                value: i18n translation
        """
        old = read_msgspec(self.nav_index_file)
        out = {}
        for nav_name, config in self.dict_nav_config.items():
            # task name, which must not empty
            for task_name, task_data in config.tasks_data.items():
                if not task_data.groups:
                    continue
                for lang in Const.GUI_LANGUAGE:
                    key = [task_name, lang]
                    value = deep_get(old, key, default='')
                    if not value:
                        value = task_name
                    deep_set(out, key, value)
        return out

    """
    Generate config_generated.py
    """

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
        gen.Import('typing')
        gen.Empty()
        gen.FromImport('alasio.config.base', 'AlasioConfigBase')
        gen.FromImport('const', 'entry')
        gen.Empty(1)

        # TYPE_CHECKING block - imports only used for type hints
        gen.add('if typing.TYPE_CHECKING:')
        with gen.tab():
            # Sort nav names for stable output
            for nav_name, config in self.dict_nav_config.items():
                # from .{nav} import {nav}_model as {nav}
                gen.add(f'from .{config.folder} import {nav_name}_model as {nav_name}')

        gen.Empty(2)

        # Class definition
        with gen.Class('ConfigGenerated', inherit='AlasioConfigBase'):
            gen.Comment('A generated config struct to fool IDE\'s type-predict and auto-complete')
            gen.add('entry = entry')
            gen.Empty()

            # Generate group attributes organized by nav
            # Sort nav names for stable output, but keep group order as defined
            for nav_name, config in self.dict_nav_config.items():
                # groups defined but not used in task, they should be generated
                # if not config.config_data:
                #     continue

                # Nav comment
                gen.Comment(nav_name)

                # Generate type hints for each group (keep definition order)
                for group_name, group in config.groups_data.items():
                    # skip groups without args (inforef groups)
                    if not group.args:
                        continue
                    # skip variants
                    if group.base:
                        continue
                    gen.Anno(group.name, anno=f'"{nav_name}.{group.name}"')

                gen.Empty()

        # Write to file
        file = self.path_config.joinpath('config_generated.py')
        op = gen.write(file, skip_same=True)
        if op:
            logger.info(f'Write file {file}')
            if gitadd:
                gitadd.stage_add(file)

    """
    Generate all
    """

    def _generate(self, gitadd=None):
        # check path
        if not self.root.exists():
            logger.warning(f'ConfigGen root not exist: {self.root}')
        if not self.path_config.exists():
            logger.warning(f'ConfigGen path_config not exist: {self.path_config}')

        # update nav configs
        for nav in self.dict_nav_config.values():
            nav.generate(gitadd=gitadd)

        # model.index.json
        _ = self.model_data
        if not self.model_data:
            return

        # {nav}_config.json
        self.generate_config_json(gitadd=gitadd)

        def write(f, d):
            op = write_json_custom_indent(f, d, skip_same=True)
            if op:
                logger.info(f'Write file {f}')
                if gitadd:
                    gitadd.stage_add(f)

        # task.index.json
        write(self.task_index_file, self.task_index_data)

        # config.index.json
        write(self.config_index_file, self.config_index_data)

        # nav.index.json
        write(self.nav_index_file, self.nav_index_data)

        # queue.index.json
        write(self.queue_index_file, self.queue_index_data)

        # config_generated.py
        self.generate_config_generated_file()

    def generate(self):
        with GitAdd(env.PROJECT_ROOT) as gitadd:
            self._generate(gitadd)


if __name__ == '__main__':
    _entry = DICT_MOD_ENTRY['alasio'].copy()
    _entry.root = env.PROJECT_ROOT.joinpath('alasio')
    self = IndexGenerator(_entry)
    self.generate()
