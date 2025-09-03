from alasio.config.entry.const import ModEntryInfo
from alasio.config.entry.model import DECODER_CACHE, MODEL_CONFIG_INDEX
from alasio.ext.cache import cached_property
from alasio.ext.file.msgspecfile import JSON_CACHE_TTL, read_msgspec
from alasio.ext.path import PathStr


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

    @cached_property
    def mod_index_data(self):
        """
        Returns:
            dict[str, Any]:
        """
        return read_msgspec(self.path_config / 'mod.index.json')

    def nav_index_data(self):
        """
        Returns:
            dict[str, dict[str, dict[str, str]]]:
                key: {component}.{name}.{lang}
                    component can be "nav", "task"
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
