import importlib
import os
import sys

from alasio.config.entry.const import ModEntryInfo
from alasio.config.entry.model import DECODER_CACHE, MOD_JSON_CACHE, MODEL_CONFIG_INDEX, MODEL_TASK_INDEX
from alasio.ext import env
from alasio.ext.cache import cached_property
from alasio.ext.file.loadpy import LOADPY_CACHE
from alasio.ext.path import PathStr
from alasio.logger import logger


class ModBase:
    def __init__(self, entry: ModEntryInfo):
        """
        Args:
            entry: Mod entry info
        """
        self.entry = entry
        self.name = entry.name
        self.root = PathStr.new(entry.root)
        self.path_config = self.root.joinpath(entry.path_config)
        self.path_assets = self.root.joinpath(entry.path_assets)
        alasio = ModEntryInfo.alasio()
        self.path_alasio_config = env.ALASIO_ROOT.joinpath(alasio.path_config)

    def __str__(self):
        """
        Mod(name="alasio", root="E:/ProgramData/Pycharm/Alasio/alasio")
        """
        return f'{self.__class__.__name__}(name="{self.name}", root="{self.root.to_posix()}")'

    def __bool__(self):
        return True

    """
    Index json
    """

    def nav_index_data(self):
        """
        Returns:
            dict[str, dict[str, dict[str, Any]]]:
                key: {nav_name}.{card_name}
                value:
                    {"i18n": {lang: name}} for normal cards
                    {"scheduler": True, "i18n": {lang: name}} for cards with scheduler
        """
        file = self.path_config.joinpath('_index/nav.index.json')
        decoder = DECODER_CACHE.MODEL_DICT_DEPTH3_ANY
        return MOD_JSON_CACHE.get(file, decoder=decoder)

    def queue_index_data(self):
        """
        Returns:
            dict[str, dict[str, str]]:
                key: {task_name}.{lang}
                value: i18n translation
        """
        file = self.path_config.joinpath('_index/queue.index.json')
        decoder = DECODER_CACHE.MODEL_DICT_DEPTH2_ANY
        return MOD_JSON_CACHE.get(file, decoder=decoder)

    def config_index_data(self) -> MODEL_CONFIG_INDEX:
        """
        """
        file = self.path_config.joinpath('_index/config.index.json')
        decoder = DECODER_CACHE.MODEL_CONFIG_INDEX
        return MOD_JSON_CACHE.get(file, decoder=decoder)

    def task_index_data(self) -> MODEL_TASK_INDEX:
        """
        """
        file = self.path_config.joinpath('_index/task.index.json')
        decoder = DECODER_CACHE.MODEL_TASK_INDEX
        return MOD_JSON_CACHE.get(file, decoder=decoder)

    def nav_config_json(self, file):
        """
        Args:
            file (str): relative path to {nav}_config.json

        Returns:
            dict[str, dict[str, dict]]:
                key: {card_name}.{arg_name}
                value:
                    {"group": group, "arg": "_info"} for _info
                    {"task": task, "group": group, "arg": arg, **ArgData.to_dict()} for normal args
                        which is arg path appended with ArgData
        """
        if file.startswith('alasio/'):
            file = self.path_alasio_config / file
        else:
            file = self.path_config / file
        decoder = DECODER_CACHE.MODEL_DICT_DEPTH3_ANY
        return MOD_JSON_CACHE.get(file, decoder=decoder)

    def nav_i18n_json(self, file):
        """
        Args:
            file (str): relative path to {nav}_i18n.json

        Returns:
            dict[str, dict[str, dict[str, dict[str, Any]]]]:
                key: {group_name}.{arg_name}.{lang}.{field}
                    where field is "name", "help", "option_i18n", etc.
                value: translation
        """
        if file.startswith('alasio/'):
            file = self.path_alasio_config / file
        else:
            file = self.path_config / file
        decoder = DECODER_CACHE.MODEL_DICT_DEPTH3_ANY
        return MOD_JSON_CACHE.get(file, decoder=decoder)

    @cached_property
    def package_dir(self):
        """
        Package name and folder of this mod in this process.

        A worker imports the game script as a package (``module.main``), so a
        model file must be imported through the same package: an injected load
        would execute the file a second time, and the classes of the config
        would not be the classes the game script imports.

        The package name is read from sys.modules instead of assumed, because
        the example mod uses ``ExampleMod.module...`` while a real mod uses
        ``module...``. The shortest module of this mod gives both the package
        name and, by stripping its own path parts, the folder of that package.

        Returns:
            tuple[str, str] | None:
                Package name and its folder, None when this mod is not imported
                as a package in this process (the backend never does)
        """
        root = os.path.normcase(os.path.abspath(self.root)) + os.sep
        best = None
        for name, module in list(sys.modules.items()):
            file = getattr(module, '__file__', None)
            if not file:
                continue
            file = os.path.normcase(os.path.abspath(file))
            if not file.startswith(root):
                continue
            if best is None or len(name) < len(best[0]):
                best = (name, file)
        if best is None:
            return None
        # strip the path parts of the package name, the rest is the package folder
        folder = best[1]
        for _ in best[0].split('.'):
            folder = os.path.dirname(folder)
        return best[0].partition('.')[0], folder

    def import_model_module(self, file):
        """
        Import a model file as a module of this mod, in a worker process.

        Returns the same module object the game script gets from
        ``from module.config.main.main_model import Campaign``, so the class of
        a config group is the class the script checks against.

        Args:
            file (PathStr): Absolute path to {nav}_model.py

        Returns:
            ModuleType | None: None when the file is not part of a package
                imported in this process, the caller falls back to loadpy()
        """
        package = self.package_dir
        if package is None:
            return None
        name, folder = package
        relative = os.path.relpath(str(file), folder)
        if relative.startswith(os.pardir):
            return None
        relative = relative.replace(os.sep, '/')
        if not relative.endswith('.py'):
            return None
        module_name = '.'.join([name, *relative[:-3].split('/')])
        try:
            module = importlib.import_module(module_name)
        except ImportError as e:
            logger.warning(f'DataInconsistent: failed to import "{module_name}": {e}')
            return None
        module_file = getattr(module, '__file__', None)
        if not module_file or os.path.normcase(os.path.abspath(module_file)) != \
                os.path.normcase(os.path.abspath(str(file))):
            # the package resolved to another file, importing it again would
            # give a second set of classes
            logger.warning(f'DataInconsistent: module "{module_name}" is not "{file}"')
            return None
        return module

    def get_group_model(self, file, cls):
        """
        Get msgspec validation model

        The loader depends on the role of the process. A worker runs the game
        script, so the model file is imported as a package of the mod and the
        class is the one the script imports; the backend serves every mod and
        only validates values, so the model file is executed on its own and
        never enters sys.modules, which lets a whole generation be dropped.

        Args:
            file (str): relative path to {nav}_model.py from path_config
            cls (str): class name

        Returns:
            Type[msgspec.Struct] | None:
        """
        if file.startswith('alasio/'):
            file = self.path_alasio_config / file
        else:
            file = self.path_config / file
        module = None
        if env.PROCESS_ROLE != 'backend':
            module = self.import_model_module(file)
        if module is None:
            try:
                module = LOADPY_CACHE.get(file)
            except ImportError as e:
                # DataInconsistent error.
                # We just log warnings to reduce runtime crash, missing config will fall back to default
                logger.warning(
                    f'DataInconsistent: failed to import model.py "{file}": {e}')
                return None
        model = getattr(module, cls, None)
        if model is None:
            logger.warning(
                f'DataInconsistent: Missing class "{cls}" in "{file}"')
            return None
        return model
