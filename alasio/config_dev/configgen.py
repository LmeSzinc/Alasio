from alasio.config.entry.const import DICT_MOD_ENTRY, ModEntryInfo
from alasio.config_dev.parse import DefinitionError, NavConfig
from alasio.ext import env
from alasio.ext.cache import cached_property
from alasio.ext.deep import deep_exist, deep_iter_depth2, deep_set
from alasio.ext.file.jsonfile import write_json
from alasio.ext.path import PathStr
from alasio.logger import logger


class ConfigGen:
    def __init__(self, entry: ModEntryInfo):
        self.root = PathStr.new(entry.root).abspath()
        self.path_config: PathStr = self.root.joinpath(entry.path_config)

        # key: filepath, value: parser
        self.file_parser: "dict[str, NavConfig]" = {}
        self._path_checked = False

    @cached_property
    def dict_nav_config(self):
        """
        All ParseNavConfig objects

        Returns:
            dict[PathStr, NavConfig]:
                key: filepath
                value: parser
        """
        dict_parser = {}
        for file in self.path_config.iter_files(ext='.args.yaml', recursive=True):
            parser = NavConfig(file)
            dict_parser[file] = parser
        return dict_parser

    """
    {path_config}/tasks.index.json
    """

    @cached_property
    def model_index_file(self):
        # {path_config}/model.index.json
        return self.path_config.joinpath('model.index.json')

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
    {path_config}/config.index.json
    """

    @cached_property
    def config_index_file(self):
        # {path_config}/config.index.json
        return self.path_config.joinpath('config.index.json')

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
    generate
    """

    def generate(self):
        # check path
        if not self.root.exists():
            logger.warning(f'ConfigGen root not exist: {self.root}')
        if not self.path_config.exists():
            logger.warning(f'ConfigGen path_config not exist: {self.path_config}')

        # update configs
        for parser in self.dict_nav_config.values():
            parser.write()

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
    self = ConfigGen(_entry)
    self.generate()
