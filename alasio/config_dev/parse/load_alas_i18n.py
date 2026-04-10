from alasio.ext.cache import cached_property
from alasio.ext.deep import *
from alasio.ext.file.msgspecfile import read_msgspec
from alasio.ext.path import PathStr
from alasio.ext.singleton import Singleton


class LoadAlasI18n(metaclass=Singleton):
    """
    Load from old alas format i18n file

    Examples:
        # do before generate
        LoadAlasI18n().register(folder)
    """
    def __init__(self):
        self.inited = False
        self.folder: PathStr = PathStr.new('')

    def register(self, folder: str):
        self.folder = PathStr.new(folder)
        self.inited = True
        cached_property.pop(self, 'old_i18n')

    @cached_property
    def old_i18n(self):
        """
        Returns:
            dict:
                key: {group}.{arg}.{lang}.{key}
                value: text
        """
        if not self.inited:
            return {}
        # Load from old alas format i18n file
        # Each module/config/i18n/{lang}.json has {group}.{arg}.{key} = text
        out = {}
        for file in self.folder.iter_files(ext='.json'):
            data = read_msgspec(file)
            if not data:
                continue
            lang = file.stem
            for group, arg, row in deep_iter_depth2(data):
                deep_set(out, keys=[group, arg, lang], value=row)
        return out

    @staticmethod
    def merge_old_i18n(old, new):
        """
        Merge old arg i18n to new

        Args:
            old (dict[str, Any]):
            new (dict[str, Any]):
        """
        # Update name and help
        for key in ['name', 'help']:
            value = old.get(key, None)
            if value is not None:
                new[key] = value
        # Update options if they exist
        options = new.get('option_i18n', None)
        if options:
            options = list(deep_keys(options))
            for key in options:
                value = old.get(key, None)
                if value is not None:
                    options[key] = value

    def process(self, data):
        """
        Postprocess a i18n data
        """
        if not self.inited:
            return
        for keys, new in deep_iter(data, depth=3):
            old = deep_get(self.old_i18n, keys)
            if old:
                self.merge_old_i18n(old, new)
