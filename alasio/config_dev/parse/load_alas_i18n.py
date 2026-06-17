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
        # {old_group: new_group}
        self._redirect_group = {}
        # {(old_group, old_arg): (new_group, new_arg)}
        self._redirect_arg = {}

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

    def redirect_group(self, old_group, new_group):
        """
        Redirect old group to new group

        Args:
            old_group (str):
            new_group (str):
        """
        self._redirect_group[new_group] = old_group

    def redirect_arg(self, old_arg, new_arg):
        """
        Redirect old arg to new arg

        Args:
            old_arg (str): {group}.{arg}
            new_arg (str): {group}.{arg}
        """
        old_group, _, old_arg = old_arg.partition('.')
        new_group, _, new_arg = new_arg.partition('.')
        self._redirect_arg[(new_group, new_arg)] = (old_group, old_arg)

    def _get_arg_i18n(self, group_name, arg_name):
        """
        Get arg i18n

        Args:
            group_name (str):
            arg_name (str):
        """
        # try literal match
        try:
            return deep_get_with_error(self.old_i18n, [group_name, arg_name])
        except KeyError:
            pass
        # try group redirect match
        new_group_name = self._redirect_group.get(group_name, None)
        if new_group_name:
            try:
                return deep_get_with_error(self.old_i18n, [new_group_name, arg_name])
            except KeyError:
                pass
        # try arg redirect match
        new = self._redirect_arg.get((group_name, arg_name), None)
        if new:
            new_group_name, new_arg_name = new
            try:
                return deep_get_with_error(self.old_i18n, [new_group_name, new_arg_name])
            except KeyError:
                pass
        return None

    def load_arg(self, old, group_name, arg_name, languages, options=None):
        """
        Args:
            old (dict):
            group_name (str):
            arg_name (str):
            languages (list[str] | dict[str, Any]):
            options (list):
        """
        i18n = self._get_arg_i18n(group_name, arg_name)
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
