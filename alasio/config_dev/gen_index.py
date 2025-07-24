from alasio.config.const import Const
from alasio.config.entry.const import DICT_MOD_ENTRY, ModEntryInfo
from alasio.config_dev.gen_config import ConfigGenerator
from alasio.config_dev.parse.base import DefinitionError
from alasio.ext import env
from alasio.ext.cache import cached_property, del_cached_property
from alasio.ext.deep import deep_exist, deep_get, deep_iter_depth2, deep_set
from alasio.ext.file.jsonfile import write_json
from alasio.ext.file.msgspecfile import read_msgspec
from alasio.ext.path import PathStr
from alasio.logger import logger


class IndexGenerator:
    def __init__(self, entry: ModEntryInfo):
        """
        维护一个MOD下所有设置文件的数据一致性，生成json索引文件。

        1. "model.index.json" 指导当前MOD下的task有哪些group，具有 task.group 二级结构。
            这个文件会在脚本运行时被使用。
            当Alasio脚本实例启动时，它需要读取与某个task相关的设置：
            - 在"model.index.json"中查询当前task
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

        # {path_config}/model.index.json
        self.model_index_file = self.path_config.joinpath('model.index.json')
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
                        file=config.tasks_file,
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
                value:
                    - file (endswith ".py") for basic group,
                        reference file={file}, class={group_name}
                    - ref_task_name (only contains A-Za-z0-9), for cross-task reference,
                        reference {ref_task_name}.{group_name} in output
                    - {'file': file, 'cls': class_name}, for override task group
                        reference file={file}, class={class_name}
                        class_name is "{task_name}_{group_name}" and inherits from class {group_name}
        """
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
                        deep_set(out, [task_name, group.group], ref)

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
            file = config.config_file.subpath_to(self.root).replace('\\', '/')
            if file == config.model_file:
                raise DefinitionError(
                    f'gui_file is not a subpath of root, model_file={config.config_file}, root={self.root}')
            # iter group models
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

    @cached_property
    def config_index_data(self):
        """
        data in config.index.json

        Returns:
            dict[str, dict[str, dict[str, str | dict[str, str]]]]:
                key: {nav}.{display_task}.{display_group}
                value:
                    - group_file, for basic group, task_name is display_group
                    - {'task': task_name, 'file': group_file}, if display group from another task
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
                # all groups within this task
                all_groups = set(group.group for group in task.group)

                for display_list in task.display:
                    for display in display_list:
                        # If display an in-task group, group must within this task
                        if not display.task and display.group not in all_groups:
                            raise DefinitionError(
                                f'In-task display ref "{display.group}" is not in task "{task_name}"',
                                file=config.tasks_file,
                                keys=[task_name, 'display']
                            )
                        # If display a cross-task group, group must exist
                        if display.task and not deep_exist(self.model_index_data, keys=[display.task, display.group]):
                            raise DefinitionError(
                                f'Cross-task display ref "{display.task}.{display.group}" does not exist',
                                file=config.tasks_file,
                                keys=[task_name, 'display']
                            )
                        # display group must be defined
                        try:
                            group_file = self.dict_group2file[display.group]
                        except KeyError:
                            raise DefinitionError(
                                f'Display ref "{display.group}" of task "{task_name}" does not exist',
                                file=config.tasks_file,
                                keys=[task_name, 'display']
                            )

                        if display.task and display.task != task_name:
                            # display group from another task
                            data = {'task': display.task, 'file': group_file}
                        else:
                            # display group from current task
                            data = group_file
                        # set
                        keys = [nav, task_name, display.group]
                        deep_set(out, keys=keys, value=data)

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
        _ = self.model_index_data
        if not self.model_index_data:
            return
        op = write_json(self.model_index_file, self.model_index_data, skip_same=True)
        if op:
            logger.info(f'Write file {self.model_index_file}')

        # config.index.json
        op = write_json(self.config_index_file, self.config_index_data, skip_same=True)
        if op:
            logger.info(f'Write file {self.config_index_file}')

        # nav.index.json
        op = write_json(self.nav_index_file, self.nav_index_data, skip_same=True)
        if op:
            logger.info(f'Write file {self.nav_index_file}')


if __name__ == '__main__':
    _entry = DICT_MOD_ENTRY['alasio'].copy()
    _entry.root = env.PROJECT_ROOT.joinpath('alasio')
    self = IndexGenerator(_entry)
    self.generate()
