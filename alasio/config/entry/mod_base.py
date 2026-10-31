import importlib
import os

from msgspec import DecodeError
from msgspec.json import decode as json_decode

from alasio.config.entry.const import ModEntryInfo
from alasio.config.entry.model import (
    DECODER_CACHE, JSON_PACK_CACHE, MOD_JSON_CACHE, MODEL_CONFIG_INDEX, MODEL_MODEL_INDEX, MODEL_TASK_INDEX
)
from alasio.ext import env
from alasio.ext.cache import cached_property
from alasio.ext.file.loadpy import LOADPY_CACHE
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_bytes
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

    def model_index_data(self) -> MODEL_MODEL_INDEX:
        """
        Read _index/model.index.json

        The file is only read to load the model files, so it is neither packed
        by the warmup nor kept in the json cache: the loader decodes it once
        per load pass and drops it with the call.

        Returns:
            dict[str, dict[str, list[str]]]:
                key: {nav}_model.py, relative to the config folder
                value: {dependency model file: [class names to inject]}
                    The key order of the file is the load order, a dependency
                    always comes before the file that imports it.
        """
        file = self.path_config.joinpath('_index/model.index.json')
        try:
            data = atomic_read_bytes(file)
        except FileNotFoundError:
            logger.error(f'Failed to read model json: "{file}": no such file')
            return {}
        try:
            return json_decode(data, type=MODEL_MODEL_INDEX)
        except DecodeError as e:
            logger.error(f'Failed to read model json: "{file}": {e}')
            return {}

    def resolve_config_path(self, file):
        """
        Resolve a path recorded in an index json.

        A mod names its own files relative to its config folder, and the files
        of alasio with an "alasio/" prefix.

        Args:
            file (str): Relative path, e.g. "gems/gems_config.json" or
                "alasio/alasio_i18n.json"

        Returns:
            PathStr: Absolute path
        """
        if file.startswith('alasio/'):
            return self.path_alasio_config / file
        else:
            return self.path_config / file

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
        file = self.resolve_config_path(file)
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
        file = self.resolve_config_path(file)
        decoder = DECODER_CACHE.MODEL_DICT_DEPTH3_ANY
        return MOD_JSON_CACHE.get(file, decoder=decoder)

    @cached_property
    def package_name(self):
        """
        Package this mod is imported as, in a worker process.

        A worker puts the mod root on sys.path and imports the entry file of
        the game script (``path_main``), so the first part of that name is the
        package the code of the mod lives in. A model file must be imported
        through the same package: an injected load would execute the file a
        second time, and the classes of the config would not be the classes
        the game script imports.

        Returns:
            str: Package name, empty when the entry file is not in a package
        """
        path = self.entry.path_main.replace('\\', '/')
        first, sep, _ = path.partition('/')
        if not sep:
            return ''
        return first.partition('.')[0]

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
        name = self.package_name
        if not name:
            return None
        # the path of the model file from the mod root, e.g.
        # module/config/main/main_model.py, is already the dotted name of the
        # package the worker imports the game script with
        relative = os.path.relpath(str(file), str(self.root))
        if relative.startswith(os.pardir):
            return None
        relative = relative.replace(os.sep, '/')
        if not relative.endswith('.py'):
            return None
        parts = relative[:-3].split('/')
        if parts[0] != name:
            # the mod root and path_main disagree on the package layout
            logger.warning(
                f'DataInconsistent: model file "{file}" is not in the package "{name}"')
            return None
        module_name = '.'.join(parts)
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

    def load_model(self, file):
        """
        Load {nav}_model.py, with the classes of its parents injected.

        A model file that inherits a group of another nav imports it at the top
        of the file, guarded by "not in globals()", which a standalone file
        cannot do: it has no parent package. The classes named by
        _index/model.index.json are put into the namespace before the file runs,
        so the guard skips the relative import and the file loads on its own.

        Args:
            file (str): Relative path to {nav}_model.py, e.g. "gems/gems_model.py"

        Returns:
            ModuleType:

        Raises:
            ImportError: When the file cannot be loaded
        """
        module = LOADPY_CACHE.peek(self.resolve_config_path(file))
        if module is not None:
            # the warmup loaded every model file, a request only reads the
            # class and never touches the index
            return module
        return self._load_model(file, self.model_index_data(), set())

    def _load_model(self, file, index, loading):
        """
        Load one model file of a decoded index, a parent before its user

        Args:
            file (str): Relative path to {nav}_model.py
            index (dict[str, dict[str, list[str]]]): Decoded _index/model.index.json
            loading (set[str]): Files being loaded, guards a cyclic index

        Returns:
            ModuleType:

        Raises:
            ImportError: When the file cannot be loaded
        """
        path = self.resolve_config_path(file)
        if path in loading:
            # an index that makes two files each other's parent would recurse
            # until the recursion limit, the parsed cycle is reported instead
            raise ImportError(f'DataInconsistent: Circular model import of "{file}"')
        module = LOADPY_CACHE.peek(path)
        if module is not None:
            return module
        loading.add(path)
        try:
            inject = {}
            for dep, classes in index.get(file, {}).items():
                module = self._load_model(dep, index, loading)
                for cls in classes:
                    try:
                        inject[cls] = getattr(module, cls)
                    except AttributeError:
                        raise ImportError(
                            f'DataInconsistent: "{dep}" has no class "{cls}", inherited by "{file}"') from None
        finally:
            loading.discard(path)
        return LOADPY_CACHE.get(path, inject=inject)

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
        path = self.resolve_config_path(file)
        module = None
        if env.PROCESS_ROLE != 'backend':
            module = self.import_model_module(path)
        if module is None:
            try:
                module = self.load_model(file)
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

    def preload_models(self):
        """
        Load every model file of this mod, a dependency before its user.

        A file that cannot be loaded is skipped and reported: one broken model
        must not leave the rest of the mod cold.

        Returns:
            int: Number of model files loaded
        """
        index = self.model_index_data()
        for file in index:
            try:
                self._load_model(file, index, set())
            except ImportError as e:
                logger.warning(f'DataInconsistent: failed to preload model.py "{file}": {e}')
        return len(index)

    def preload_json(self):
        """
        Read every json this mod serves, so a request decodes it once.

        The nav / queue / config / task index and the config / i18n json of
        every nav of config.index.json are turned into msgpack packs, see
        JSON_PACK_CACHE. model.index.json is left out: only the model load
        reads it, once. A missing file is skipped, the request path reports it
        itself.

        Returns:
            int: Number of files packed
        """
        files = [self.path_config.joinpath(f'_index/{name}')
                 for name in ('nav.index.json', 'queue.index.json', 'config.index.json',
                              'task.index.json')]
        for nav_ref in self.config_index_data().values():
            files.append(self.resolve_config_path(nav_ref.file))
            files.extend(self.resolve_config_path(name) for name in nav_ref.i18n)
        packed = 0
        # the same i18n of alasio is named by every nav that uses it
        for file in dict.fromkeys(files):
            try:
                JSON_PACK_CACHE.get(file)
            except (FileNotFoundError, DecodeError):
                continue
            packed += 1
        return packed
