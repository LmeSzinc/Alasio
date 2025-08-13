from alasio.config.entry.const import ModEntryInfo
from alasio.config.entry.model import DECODER_CACHE
from alasio.ext.file.msgspecfile import JSON_CACHE_TTL
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
        return f'{self.__class__.__name__}(name="{self.name}"", root="{self.root.to_posix()}")'

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
