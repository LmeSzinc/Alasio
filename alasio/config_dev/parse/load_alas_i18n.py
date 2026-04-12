from alasio.ext.cache import cached_property
from alasio.ext.deep import *
from alasio.ext.file.msgspecfile import read_msgspec
from alasio.ext.path import PathStr
from alasio.ext.singleton import Singleton


def may_default_name(name):
    """
    Args:
        name (str):

    Returns:
        bool: True if name is like {group}.{arg}
            False if name is like "Reward Settings" or "大舰队作战"
    """
    if ' ' in name:
        return False
    if '.' not in name:
        return False
    return True


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

    def load_arg(self, old, group_name, arg_name, languages, options=None):
        """
        Args:
            old (dict):
            group_name (str):
            arg_name (str):
            languages (list[str] | dict[str, Any]):
            options (list):
        """
        i18n = deep_get(self.old_i18n, [group_name, arg_name])
        if not i18n:
            return old
        for lang in languages:
            i18n_data = i18n.get(lang, None)
            if not i18n_data:
                continue

            # Update name and help
            for key in ['name', 'help']:
                value = i18n_data.get(key, None)
                if value and isinstance(value, str) and not may_default_name(value):
                    old = deep_set(old, keys=[lang, key], value=value)
            # Update options if they exist
            if options:
                for option in options:
                    value = i18n_data.get(option, None)
                    if value:
                        old = deep_set(old, keys=[lang, 'option_i18n', option], value=value)

        return old
