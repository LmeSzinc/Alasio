from alasio.config.entry.const import DICT_MOD_ENTRY, ModEntryInfo
from alasio.config_dev.gen_config import ConfigGenerator
from alasio.config_dev.parse.base import DefinitionError
from alasio.ext import env
from alasio.ext.cache import cached_property
from alasio.ext.deep import deep_exist, deep_iter_depth2, deep_set
from alasio.ext.file.jsonfile import write_json
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
                    - file, for basic group, class_name is group_name
                    - {'file': file, 'cls': class_name}, if group override
                    - {'task': task_name}, if reference a group from another task
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
                        ref = {'task': group.task}
                        deep_set(out, [task_name, group.group], ref)
                    else:
                        # reference a group
                        if ref['cls'] == group.group:
                            # class name is the same as group name
                            ref = ref['file']
                        deep_set(out, [task_name, group.group], ref)

        # check if {task_ref}.{group} reference has corresponding value
        for _, group, ref in deep_iter_depth2(out):
            if 'task' in ref:
                task = ref["task"]
                if not deep_exist(out, [task, group]):
                    raise DefinitionError(
                        f'Cross-task ref has no corresponding value: {task}.{group}',
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
                key: {nav}.{display_group}.{display_flatten}
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
                for display_group in task.display:
                    for group in display_group:
                        group_file = self.dict_group2file[group.group]
                        if group.task and group.task != task_name:
                            # display group from another task
                            data = {'task': group.task, 'file': group_file}
                        else:
                            # display group from current task
                            data = group_file
                        # set
                        keys = [nav, task_name, group.group]
                        deep_set(out, keys=keys, value=data)

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
        if self.model_index_data:
            op = write_json(self.model_index_file, self.model_index_data, skip_same=True)
            if op:
                logger.info(f'Write file {self.model_index_file}')

        # config.index.json
        if self.model_index_data:
            op = write_json(self.config_index_file, self.config_index_data, skip_same=True)
            if op:
                logger.info(f'Write file {self.config_index_file}')


if __name__ == '__main__':
    _entry = DICT_MOD_ENTRY['alasio'].copy()
    _entry.root = env.PROJECT_ROOT.joinpath('alasio')
    self = IndexGenerator(_entry)
    self.generate()
