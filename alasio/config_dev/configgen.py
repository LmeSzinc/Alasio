from alasio.config_dev.parse import DefinitionError, ParseConfig
from alasio.config_dev.tasks_model import TaskRefModel
from alasio.ext.cache import cached_property
from alasio.ext.deep import deep_set
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
            dict[PathStr, ParseConfig]: key: filepath, value: parser
        """
        dict_parser = {}
        for path in self.config_path:
            for file in iter_files(path, ext='.args.yaml', recursive=True):
                parser = ParseConfig(PathStr(file))
                dict_parser[file] = parser
        return dict_parser

    @cached_property
    def dict_group_ref(self):
        """
        Returns:
            dict[str, dict[str, str]]:
                # where the msgspec model class is defined
                <group_name>:
                    # {'file': file, 'cls': class_name}
                    GroupModelRef
        """
        out = {}
        for file, config in self.dict_config.items():
            # calculate module file
            file = config.model_file.subpath_to(self.root)
            if file == config.model_file:
                raise DefinitionError(
                    f'model_file is not a subpath of root, model_file={config.model_file}, root={self.root}')
            file = file.to_python_import()
            # iter group models
            for group_name, class_name in config.dict_group2class.items():
                # group must be unique
                if group_name in out:
                    raise DefinitionError(f'Duplicate group name: {group_name}', file=config.tasks_file)
                # build model reference
                ref = {'file': file, 'cls': class_name}
                out[group_name] = ref

        return out

    @cached_property
    def tasks_data(self) -> TaskRefModel:
        """
        tasks data to be merged into global task.index.json

        Returns:
            TaskRefModel:
                <task_name>:
                    <group_name>:
                        GroupModelRef
        """
        out = {}
        for file, config in self.dict_config.items():
            for task_name, task_data in config.tasks_data.items():
                # task name must be unique
                if task_name in out:
                    raise DefinitionError(f'Duplicate task name: {task_name}', file=config.tasks_file)
                for group_name in task_data.group:
                    # group ref
                    try:
                        ref = self.dict_group_ref[group_name]
                    except KeyError:
                        raise DefinitionError(
                            f'Group ref "{group_name}" of task "{task_name}" does not exist', file=config.tasks_file)
                    deep_set(out, [task_name, group_name], ref)

        return out

    @cached_property
    def tasks_file(self):
        # {index_path}/tasks.index.json
        return self.index_path.joinpath('tasks.index.json')

    def generate(self):
        self._path_check()

        # update configs
        for parser in self.dict_config.values():
            parser.write()

        # tasks.index.json
        if self.tasks_data:
            op = write_json(self.tasks_file, self.tasks_data, skip_same=True)
            if op:
                logger.info(f'Write file {self.tasks_file}')


if __name__ == '__main__':
    self = ConfigGen(PathStr.new(__file__).uppath(3))
    self.set_index_path('alasio/config_alasio')
    self.add_config_path('alasio/config_alasio')
    self.generate()
