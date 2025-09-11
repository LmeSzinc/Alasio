from msgspec import NODEFAULT
from msgspec.structs import asdict

from alasio.config.entry.const import ModEntryInfo
from alasio.config.entry.model import DECODER_CACHE, MODEL_CONFIG_INDEX, MODEL_TASK_INDEX, ModelConfigRef
from alasio.config.table.config import AlasioConfigTable
from alasio.ext.deep import deep_set
from alasio.ext.file.loadpy import LOADPY_CACHE
from alasio.ext.file.msgspecfile import JSON_CACHE_TTL
from alasio.ext.msgspec_error import load_msgpack_with_default
from alasio.ext.path import PathStr
from alasio.logger import logger


class Mod:
    def __init__(self, entry: ModEntryInfo):
        """
        Args:
            entry: Mod entry info
        """
        self.entry = entry
        self.name = entry.name
        self.root = PathStr.new(entry.root)
        self.path_config = self.root / entry.path_config

    def __str__(self):
        """
        Mod(name="alasio", root="E:/ProgramData/Pycharm/Alasio/alasio")
        """
        return f'{self.__class__.__name__}(name="{self.name}", root="{self.root.to_posix()}")'

    """
    Index json
    """

    def nav_index_data(self):
        """
        Returns:
            dict[str, dict[str, dict[str, str]]]:
                key: {nav_name}.{card_name}.{lang}
                value: i18n translation
        """
        file = self.path_config / 'nav.index.json'
        decoder = DECODER_CACHE.MODEL_DICT_DEPTH3_ANY
        return JSON_CACHE_TTL.get(file, decoder=decoder)

    def config_index_data(self) -> MODEL_CONFIG_INDEX:
        """
        """
        file = self.path_config / 'config.index.json'
        decoder = DECODER_CACHE.MODEL_CONFIG_INDEX
        return JSON_CACHE_TTL.get(file, decoder=decoder)

    def task_index_data(self) -> MODEL_TASK_INDEX:
        """
        """
        file = self.path_config / 'task.index.json'
        decoder = DECODER_CACHE.MODEL_TASK_INDEX
        return JSON_CACHE_TTL.get(file, decoder=decoder)

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
        file = self.root / file
        decoder = DECODER_CACHE.MODEL_DICT_DEPTH3_ANY
        return JSON_CACHE_TTL.get(file, decoder=decoder)

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
        file = self.root / file
        decoder = DECODER_CACHE.MODEL_DICT_DEPTH3_ANY
        return JSON_CACHE_TTL.get(file, decoder=decoder)

    def task_model_py(self, file):
        """
        Args:
            file (str): relative path to {nav}_model.json
        """
        file = self.root / file
        return LOADPY_CACHE.get(file)

    """
    Config read/write
    """

    def config_read(self, config_name: str, ref: ModelConfigRef):
        """
        Args:
            config_name:
            ref:

        Returns:
            dict[str, dict[str, dict[str, Any]]]:
                key: task.group.arg, value: arg value
        """
        table = AlasioConfigTable(config_name)
        rows = table.read_task_rows(tasks=ref.task, groups=ref.group)

        # prepare model and default
        task_index_data = self.task_index_data()
        # key: (task_name, group_name), value: Any
        dict_value = {}
        for task_name, task_info in task_index_data.items():
            for group_name, model_ref in task_info.group.items():
                # skip cross-task ref
                if model_ref.task != task_name:
                    continue

                key = (task_name, group_name)
                # get model
                try:
                    module = self.task_model_py(model_ref.file)
                except ImportError as e:
                    # DataInconsistent error.
                    # We just log warnings to reduce runtime crash, missing config will fall back to default
                    logger.warning(
                        f'DataInconsistent: failed to import model.py "{model_ref.file}": {e}')
                    continue
                model = getattr(module, model_ref.cls, None)
                if model is None:
                    logger.warning(
                        f'DataInconsistent: Missing class "{model_ref.cls}" in "{model_ref.file}"')
                    continue
                try:
                    default = model()
                except TypeError:
                    logger.warning(
                        f'DataInconsistent: Class "{model_ref.cls}" in "{model_ref.file}" '
                        f'cannot be default constructed')
                    continue
                except Exception as e:
                    logger.warning(
                        f'DataInconsistent: Class "{model_ref.cls}" in "{model_ref.file}" '
                        f'failed to construct: {e}')
                    continue
                # insert default value
                dict_value[key] = default

        # load config
        for row in rows:
            key = (row.task, row.group)
            default = dict_value.get(key, NODEFAULT)
            if default == NODEFAULT:
                # old group in config db that not being used anymore
                continue
            model = default.__class__
            # validate value
            data, errors = load_msgpack_with_default(row.value, model=model)
            # for error in errors:
            #     logger.warning(f'Config data inconsistent at {row.task}.{row.group}: {error}')
            if data is NODEFAULT:
                # Failed to convert
                continue
            # set
            dict_value[key] = data

        # construct output
        out = {}
        for key, value in dict_value.items():
            value = asdict(value)
            deep_set(out, keys=key, value=value)

        return out
