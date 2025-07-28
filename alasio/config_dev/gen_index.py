from alasio.config.const import Const
from alasio.config.entry.const import DICT_MOD_ENTRY, ModEntryInfo
from alasio.config_dev.gen_config import ConfigGenerator
from alasio.config_dev.parse.base import DefinitionError
from alasio.config_dev.parse.parse_tasks import TaskGroup
from alasio.ext import env
from alasio.ext.cache import cached_property, del_cached_property
from alasio.ext.deep import deep_exist, deep_get, deep_iter_depth2, deep_set
from alasio.ext.file.jsonfile import NoIndent, write_json_custom_indent
from alasio.ext.file.msgspecfile import read_msgspec
from alasio.ext.path import PathStr
from alasio.ext.path.calc import to_posix
from alasio.logger import logger


class IndexGenerator:
    def __init__(self, entry: ModEntryInfo):
        """
        维护一个MOD下所有设置文件的数据一致性，生成json索引文件。

        1. "tasks.index.json" 指导当前MOD下的task有哪些group，具有 task.group 二级结构。
            这个文件会在脚本运行时被使用。
            当Alasio脚本实例启动时，它需要读取与某个task相关的设置：
            - 在"tasks.index.json"中查询当前task
            - 遍历task下有哪些group，得到group所在的{nav}_model.py文件路径
            - 载入python文件，查询{nav}_model.py中的group对应的msgspec模型
            - 读取用户数据，使用msgspec模型校验数据

        2. "config.index.json" 指导当前MOD有哪些设置，具有 nav.display_task.display_group 三级结构。
            这个文件会在前端显示时被使用。
            当前端需要显示某个nav下的用户设置时：
            - 在"config.index.json"中查询当前nav
            - 以depth=2遍历display_task.display_group，得到group所在的{nav}_config.json文件路径
            - 查询{nav}_config.json中的group
            - 聚合所有内容

        3. "nav.index.json" 指导当前MOD的导航组件应该显示什么内容，具有nav.display_task二级结构。
            这个文件会在前端显示时被使用。
            注意这是一个半自动生成文件，Alasio会维护它的数据结构，但是需要人工编辑nav对应的i18n，
            display_task的i18n会从{nav}_config.json生成，不需要编辑。
            当前端需要显示导航组件时：
            - 读取"nav.index.json"返回给前端
        """
        self.root = PathStr.new(entry.root).abspath()
        self.path_config: PathStr = self.root.joinpath(entry.path_config)

        # {path_config}/tasks.index.json
        self.tasks_index_file = self.path_config.joinpath('tasks.index.json')
        # {path_config}/config.index.json
        self.config_index_file = self.path_config.joinpath('config.index.json')
        # {path_config}/nav.index.json
        self.nav_index_file = self.path_config.joinpath('nav.index.json')

    @cached_property
    def dict_nav_config(self):
        """
        All ParseNavConfig objects

        Returns:
            dict[PathStr, ConfigGenerator]:
                key: filepath
                value: generator
        """
        dict_parser = {}
        for file in self.path_config.iter_files(ext='.args.yaml', recursive=True):
            parser = ConfigGenerator(file)
            dict_parser[file] = parser
        return dict_parser

    """
    Generate tasks.index.json
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
        for file, config in self.dict_nav_config.items():
            # calculate module file
            file = config.model_file.subpath_to(self.root)
            if file == config.model_file:
                raise DefinitionError(
                    f'model_file is not a subpath of root, model_file={config.model_file}, root={self.root}')
            file = file.to_posix()
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

    @staticmethod
    def _update_task_info(old, new, task_name):
        """
        Update {task_name}._info in tasks.index.json
        Generate with default name, then merge with the i18n from old configs
        """
        for lang in Const.GUI_LANGUAGE:
            # task name, name must not be empty
            keys = [task_name, '_info', 'i18n', lang, 'name']
            value = deep_get(old, keys=keys, default='')
            if not value:
                value = task_name
            deep_set(new, keys=keys, value=str(value))
            # task help, help can be empty
            keys = [task_name, '_info', 'i18n', lang, 'help']
            value = deep_get(old, keys=keys, default='')
            deep_set(new, keys=keys, value=str(value))

    @cached_property
    def tasks_index_data(self):
        """
        model data in tasks.index.json

        Returns:
             dict[str, dict[str, str | dict[str, str]]]:
                key: {task_name}.{group_name}
                value:
                    - file (endswith ".py") for basic group,
                        reference file={file}, class={group_name}
                    - ref_task_name (only contains A-Za-z0-9), for cross-task reference,
                        reference {ref_task_name}.{group_name} in output
                    - {'file': file, 'cls': class_name}, for override task group
                        reference file={file}, class={class_name}
                        class_name is "{task_name}_{group_name}" and inherits from class {group_name}
                    - {'i18n', ...} for task info
        """
        old = read_msgspec(self.tasks_index_file)
        out = {}

        for file, config in self.dict_nav_config.items():
            for task_name, task_data in config.tasks_data.items():
                # task name must be unique
                if task_name in out:
                    raise DefinitionError(
                        f'Duplicate task name: {task_name}',
                        file=config.tasks_file,
                        keys=task_name,
                    )
                # generate {task_name}._info
                self._update_task_info(old, out, task_name)
                # generate groups
                for group in task_data.group:
                    # check if group exists
                    try:
                        ref = self.dict_group_ref[group.group]
                    except KeyError:
                        raise DefinitionError(
                            f'Group ref "{group.group}" of task "{task_name}" does not exist',
                            file=config.tasks_file,
                            keys=[task_name, 'group']
                        )
                    if group.task:
                        # reference {task_ref}.{group}
                        ref = group.task
                        deep_set(out, [task_name, group.group], ref)
                    else:
                        # reference a group
                        if ref['cls'] == group.group:
                            # basic group, class_name is the same as group_name
                            ref = ref['file']
                        # else: just keep {'file': file, 'cls': class_name}
                        deep_set(out, [task_name, group.group], NoIndent(ref))

        # check if {ref_task_name}.{group_name} reference has corresponding value
        for _, group, ref in deep_iter_depth2(out):
            if type(ref) is not str:
                continue
            if ref.endswith('.py'):
                continue
            if not deep_exist(out, [ref, group]):
                raise DefinitionError(
                    f'Cross-task ref has no corresponding value: {ref}.{group}',
                )

        return out

    """
    Generate config.index.json
    """

    @cached_property
    def _old_config_index(self):
        """
        Old nav.index.json, with manual written i18n
        """
        return read_msgspec(self.config_index_file)

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
        for file, config in self.dict_nav_config.items():
            # calculate module file
            file = config.config_file.subpath_to(self.root)
            if file == config.model_file:
                raise DefinitionError(
                    f'gui_file is not a subpath of root, model_file={config.config_file}, root={self.root}')
            # iter group models
            file = to_posix(file)
            for group_name in config.config_data.keys():
                # group must be unique
                if group_name in out:
                    raise DefinitionError(
                        f'Duplicate group name: {group_name}',
                        file=config.tasks_file,
                        keys=group_name,
                    )
                out[group_name] = file

        return out

    def _get_display_info(
            self, config: ConfigGenerator, task_name: str, display_flat: "list[TaskGroup]"
    ) -> dict:
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
                file = self.dict_group2file[first.inforef]
            except KeyError:
                raise DefinitionError(
                    f'inforef "{first.inforef}" does not exists',
                    file=config.tasks_file, keys=[task_name, 'display']
                )
            return {'group': first.inforef, 'file': file}

        # no info ref, use the first group that is not Scheduler
        for group in display_flat:
            if group.group == 'Scheduler':
                continue
            try:
                file = self.dict_group2file[group.group]
            except KeyError:
                raise DefinitionError(
                    f'Display group "{group.group}" does not exists',
                    file=config.tasks_file, keys=[task_name, 'display']
                )
            return {'group': group.group, 'file': file}

        # no luck, just use the first group
        try:
            file = self.dict_group2file[first.group]
        except KeyError:
            raise DefinitionError(
                f'Display group "{first.group}" does not exists',
                file=config.tasks_file, keys=[task_name, 'display']
            )
        return {'group': first.group, 'file': file}

    def _iter_display(
            self, config: ConfigGenerator, task_name: str, display_flat: "list[TaskGroup]"
    ) -> dict:
        """
        Iter display reference from a list of display_flat
        """
        for display in display_flat:
            # skip info ref
            if display.inforef:
                continue
            # display group must be defined
            try:
                file = self.dict_group2file[display.group]
            except KeyError:
                raise DefinitionError(
                    f'Display ref "{display.group}" of task "{task_name}" does not exist',
                    file=config.tasks_file, keys=[task_name, 'display']
                )
            if display.task:
                # If display a cross-task group, group must exist
                if not deep_exist(self.tasks_index_data, keys=[display.task, display.group]):
                    raise DefinitionError(
                        f'Cross-task display ref "{display.task}.{display.group}" does not exist',
                        file=config.tasks_file, keys=[task_name, 'display']
                    )
                yield {'task': display.task, 'group': display.group, 'file': file}
            else:
                # If display an in-task group, group must within this task
                if not deep_exist(self.tasks_index_data, keys=[task_name, display.group]):
                    raise DefinitionError(
                        f'In-task display ref "{display.group}" is not in task "{task_name}"',
                        file=config.tasks_file, keys=[task_name, 'display']
                    )
                yield {'task': task_name, 'group': display.group, 'file': file}

    @cached_property
    def config_index_data(self):
        """
        data in config.index.json

        Returns:
            dict[str, dict[str, dict[str, dict[str, str]]]]:
                key: {nav}.{card_name}
                value: {"_info": info_ref, "display": list[display_ref]}
                    - info_ref is: {"group": group_name, "file": file}
                        where file is {nav}_config.json and will reference key {group_name}._info in the file
                    - display_ref is {"task": task_name, "group": group_name, "file": file}
                        where file is {nav}_config.json and will reference key {group_name} in the file
                        "task" indicates to read {task_name}.{group_name} in user config
        """
        out = {}
        for file, config in self.dict_nav_config.items():
            # nav
            nav = file.rootstem
            if nav in out:
                raise DefinitionError(
                    f'Duplicate nav name: {nav}',
                    file=file,
                )
            for task_name, task in config.tasks_data.items():
                is_flat = len(task.display) == 1
                for display_flat in task.display:
                    # generate display
                    display = list(self._iter_display(config, task_name, display_flat))
                    if is_flat:
                        card_name = f'card_{task_name}'
                    else:
                        card_name = '_'.join([d['group'] for d in display])
                        card_name = f'card_{task_name}-{card_name}'
                    # generate _info
                    info = self._get_display_info(config, task_name, display_flat)
                    deep_set(out, keys=[nav, card_name, '_info'], value=NoIndent(info))
                    display = [NoIndent(d) for d in display]
                    deep_set(out, keys=[nav, card_name, 'display'], value=display)

        return out

    """
    Generate nav.index.json
    """

    @cached_property
    def _nav_index_old(self):
        """
        Old nav.index.json, with manual written i18n
        """
        return read_msgspec(self.nav_index_file)

    def _update_nav_info(self, out, nav, display_task):
        """
        Update {nav}.{display_task} in {nav}_config.json
        Generate with default name, then merge with the i18n from old configs
        """
        old = deep_get(self._nav_index_old, [nav, display_task], default={})
        for lang in Const.GUI_LANGUAGE:
            # name
            key = [nav, display_task, lang]
            value = deep_get(old, key, default='')
            # navigation must have name
            if not value:
                if display_task == '_info':
                    value = nav
                else:
                    value = display_task
            deep_set(out, key, str(value))

    @cached_property
    def nav_index_data(self):
        """
        data of nav.index.json
        """
        _ = self._nav_index_old
        out = {}
        for nav, display_task, _ in deep_iter_depth2(self.config_index_data):
            self._update_nav_info(out, nav, '_info')
            self._update_nav_info(out, nav, display_task)
        del_cached_property(self, '_nav_index_old')
        return out

    """
    Generate all
    """

    def generate(self):
        # check path
        if not self.root.exists():
            logger.warning(f'ConfigGen root not exist: {self.root}')
        if not self.path_config.exists():
            logger.warning(f'ConfigGen path_config not exist: {self.path_config}')

        # update nav configs
        for nav in self.dict_nav_config.values():
            nav.generate()

        # tasks.index.json
        _ = self.tasks_index_data
        if not self.tasks_index_data:
            return
        op = write_json_custom_indent(self.tasks_index_file, self.tasks_index_data, skip_same=True)
        if op:
            logger.info(f'Write file {self.tasks_index_file}')

        # config.index.json
        op = write_json_custom_indent(self.config_index_file, self.config_index_data, skip_same=True)
        if op:
            logger.info(f'Write file {self.config_index_file}')

        # nav.index.json
        op = write_json_custom_indent(self.nav_index_file, self.nav_index_data, skip_same=True)
        if op:
            logger.info(f'Write file {self.nav_index_file}')


if __name__ == '__main__':
    _entry = DICT_MOD_ENTRY['alasio'].copy()
    _entry.root = env.PROJECT_ROOT.joinpath('alasio')
    self = IndexGenerator(_entry)
    self.generate()
