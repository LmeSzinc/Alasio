from alasio.config_dev.parse import DefinitionError, ParseConfig
from alasio.ext.cache import cached_property
from alasio.ext.deep import deep_exist, deep_iter_depth2, deep_set
from alasio.ext.file.jsonfile import write_json
from alasio.ext.path import PathStr
from alasio.ext.path.calc import is_abspath
from alasio.ext.path.iter import iter_files
from alasio.logger import logger


class ConfigGen:
    def __init__(self, root=''):
        self.root = PathStr.new(root).abspath()
        self.config_path: "list[PathStr]" = []
        self.index_path: PathStr = self.root.joinpath('module/config')

        # key: filepath, value: parser
        self.file_parser: "dict[str, ParseConfig]" = {}
        self._path_checked = False

    def set_index_path(self, path):
        """
        Args:
            path (str):
        """
        if not is_abspath(path):
            path = self.root.joinpath(path)
        self.index_path = path
        self._path_checked = False

    def add_config_path(self, path):
        """
        Args:
            path (list[str] | str):
        """
        if isinstance(path, list):
            path = [p if is_abspath(p) else self.root.joinpath(p) for p in path]
            self.config_path += path
        else:
            if not is_abspath(path):
                path = self.root.joinpath(path)
            self.config_path.append(path)
        self._path_checked = False

    def _path_check(self):
        """
        Log if path not exist
        """
        if self._path_checked:
            return True
        if not self.root.exists():
            logger.warning(f'ConfigGen root not exist: {self.root}')
        if not self.index_path.exists():
            logger.warning(f'ConfigGen index_path not exist: {self.index_path}')
        for path in self.config_path:
            if not path.exists():
                logger.warning(f'ConfigGen config_path not exist: {path}')
        self._path_checked = True

    @cached_property
    def dict_config(self):
        """
        All yaml parser objects

        Returns:
            dict[PathStr, ParseConfig]:
                key: filepath
                value: parser
        """
        dict_parser = {}
        for path in self.config_path:
            for file in iter_files(path, ext='.args.yaml', recursive=True):
                file = PathStr(file)
                parser = ParseConfig(file)
                dict_parser[file] = parser
        return dict_parser

    """
    {index_path}/tasks.index.json
    """

    @cached_property
    def model_index_file(self):
        # {index_path}/model.index.json
        return self.index_path.joinpath('model.index.json')

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
        for file, config in self.dict_config.items():
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
    def model_index(self):
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
        for file, config in self.dict_config.items():
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
    {index_path}/gui.index.json
    """

    @cached_property
    def gui_file(self):
        # {index_path}/gui.index.json
        return self.index_path.joinpath('gui.index.json')

    @cached_property
    def dict_group2file(self):
        """
        Convert group name to {aside}.gui.json to read

        Returns:
            dict[str, str]:
                key: {group_name}
                value: relative path to {aside}.gui.json
        """
        out = {}
        for file, config in self.dict_config.items():
            # calculate module file
            file = config.gui_file.subpath_to(self.root).replace('\\', '/')
            if file == config.model_file:
                raise DefinitionError(
                    f'gui_file is not a subpath of root, model_file={config.gui_file}, root={self.root}')
            # iter group models
            for group_name in config.gui.keys():
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
    def gui_index(self):
        """
        gui data in gui.index.json

        Returns:
            dict[str, dict[str, dict[str, str | dict[str, str]]]]:
                key: {aside}.{display_group}.{display_flatten}
                value:
                    - group_file, for basic group, task_name is display_group
                    - {'task': task_name, 'file': group_file}, if display group from another task
        """
        out = {}
        for file, config in self.dict_config.items():
            # aside
            aside = file.rootstem
            if aside in out:
                raise DefinitionError(
                    f'Duplicate aside name: {aside}',
                    file=file,
                )
            for task_name, task in config.tasks_data.items():
                for display_group in task.display:
                    for group in display_group:
                        group_file = self.dict_group2file[group.group]
                        if group.task and group.task != task_name:
                            # display group from another task
                            data = {'task': task_name, 'file': group_file}
                        else:
                            # display group from current task
                            data = group_file
                        # set
                        keys = [aside, task_name, group.group]
                        deep_set(out, keys=keys, value=data)

        return out

    """
    generate
    """

    def generate(self):
        self._path_check()

        # update configs
        for parser in self.dict_config.values():
            parser.write()

        # tasks.index.json
        if self.model_index:
            op = write_json(self.model_index_file, self.model_index, skip_same=True)
            if op:
                logger.info(f'Write file {self.model_index_file}')

        # gui.index.json
        if self.model_index:
            op = write_json(self.gui_file, self.gui_index, skip_same=True)
            if op:
                logger.info(f'Write file {self.gui_file}')


if __name__ == '__main__':
    self = ConfigGen(PathStr.new(__file__).uppath(3))
    self.set_index_path('alasio/config_alasio')
    self.add_config_path('alasio/config_alasio')
    self.generate()
