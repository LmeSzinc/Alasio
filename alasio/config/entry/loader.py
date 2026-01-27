from collections import defaultdict

from msgspec import ValidationError

import alasio.config.entry.const as const
from alasio.config.entry.mod import ConfigSetEvent, Mod
from alasio.config.entry.utils import validate_nav_name
from alasio.ext import env
from alasio.ext.cache import cached_property
from alasio.ext.deep import deep_get, deep_get_with_error, deep_iter_depth2, deep_values_depth2
from alasio.ext.file.msgspecfile import deepcopy_msgpack
from alasio.ext.path import PathStr
from alasio.ext.path.calc import is_abspath, joinnormpath
from alasio.logger import logger


class ModLoader:
    def __init__(self, root=None, dict_mod_entry=None):
        """
        Args:
            root (PathStr): Absolute path to run path
            dict_mod_entry (dict[str, ModEntryInfo]):
                see const.DICT_MOD_ENTRY
        """
        if root is None:
            root = env.PROJECT_ROOT
        self.root = root
        if dict_mod_entry is None:
            # dynamic use, just maybe someone want to monkeypatch it
            dict_mod_entry = const.DICT_MOD_ENTRY
        self.dict_mod_entry = dict_mod_entry

    @cached_property
    def dict_mod(self):
        """
        Returns:
            dict[str, Mod]:
                key: mod_name
                value: Mod
        """
        out = {}
        for entry in self.dict_mod_entry.values():
            if not entry.root:
                # logger.warning(f'Mod entry root empty: name={name}, entry={entry.root}')
                # continue
                entry.root = env.PROJECT_ROOT
            elif not is_abspath(entry.root):
                entry.root = joinnormpath(env.PROJECT_ROOT, entry.root)
            # folder must be mod like
            if not entry.exist():
                continue
            # set
            mod = Mod(entry)
            out[entry.name] = mod

        return out

    def show(self):
        """
        ModLoader(root="E:/ProgramData/Pycharm/Alasio", mod=1):
        - Mod(name="alasio", root="E:/ProgramData/Pycharm/Alasio/alasio", nav=2, card=10, task=5, com=2)

        Returns:
            list[str]:
        """
        mod = len(self.dict_mod)
        lines = [
            'Show all mods',
            f'{self.__class__.__name__}(root="{self.root.to_posix()}", mod={mod}):'
        ]
        for entry in self.dict_mod.values():
            lines.append(f'- {entry}')
        text = '\n'.join(lines)
        logger.info(text)
        return lines

    def build(self):
        """
        Build all mods
        """
        _ = self.dict_mod
        self.show()

    def get_gui_nav(self, mod_name, lang):
        """
        Get the data to display as GUI navigation

        Args:
            mod_name (str):
            lang (str):

        Returns:
            dict[str, dict[str, str]]:
                key: {nav_name}.{card_name}
                value: translation

        Raises:
            KeyError:
        """
        try:
            mod = self.dict_mod[mod_name]
        except KeyError:
            # raise KeyError(f'No such mod: "{mod_name}"') from None
            return {}

        data = mod.nav_index_data()
        out = defaultdict(dict)
        for nav_name, card_name, i18n_data in deep_iter_depth2(data):
            # there shouldn't be KeyError, because data is validated
            i18n = i18n_data[lang]
            out[nav_name][card_name] = i18n
        return out

    def get_gui_config(self, mod_name, config_name, nav_name, lang):
        """
        Args:
            mod_name (str):
            config_name (str):
            nav_name (str):
            lang (str):

        Returns:

        """
        try:
            mod = self.dict_mod[mod_name]
        except KeyError:
            # raise KeyError(f'No such mod: "{mod_name}"') from None
            return {}
        if not validate_nav_name(nav_name):
            # raise KeyError(f'Nav name format invalid: "{nav_name}"')
            return {}

        # prepare output dict
        config_index_data = mod.config_index_data()
        try:
            nav_ref = config_index_data[nav_name]
        except KeyError:
            # raise KeyError(f'No such nav: "{mod_name}"') from None
            return {}
        out = mod.nav_config_json(nav_ref.file)
        # copy as output, so we can safely modify
        out = deepcopy_msgpack(out)

        # prepare i18n reference
        i18n = {}
        for file in nav_ref.i18n:
            group_i18n = mod.nav_i18n_json(file)
            i18n.update(group_i18n)
        # prepare config
        config = mod.config_read(config_name, nav_ref.config)

        for arg_data in deep_values_depth2(out):
            try:
                task_name = arg_data.get('task', '')
                group_name = arg_data['group']
                arg_name = arg_data['arg']
            except KeyError:
                # this shouldn't happen
                continue
            cls_name = arg_data.get('cls', group_name)

            # insert i18n
            i18n_data = deep_get(i18n, [cls_name, arg_name, lang])
            try:
                arg_data.update(i18n_data)
            except TypeError:
                # this shouldn't happen, as i18n_data should be dict
                continue
            if arg_name == '_info':
                continue
            # insert config
            try:
                value = deep_get_with_error(config, keys=[task_name, group_name, arg_name])
            except KeyError:
                # this shouldn't happen
                logger.warning(f'DataInconsistent: Missing config of "{task_name}.{group_name}.{arg_name}" '
                               f'when getting mod="{mod_name}", nav="{nav_name}"')
                continue
            arg_data['value'] = value

        return out

    def gui_config_set(self, mod_name, config_name, task_name, group_name, arg_name, value):
        """
        See Mod.config_set()

        Args:
            mod_name (str):
            config_name (str):
            task_name (str):
            group_name (str):
            arg_name (str):
            value (Any):

        Returns:
            tuple[bool, ConfigSetEvent]:
        """
        try:
            mod = self.dict_mod[mod_name]
        except KeyError:
            raise ValidationError(f'No such mod: "{mod_name}"') from None

        event = ConfigSetEvent(task=task_name, group=group_name, arg=arg_name, value=value)
        success, response = mod.config_set(config_name, event)
        return success, response

    def gui_config_reset(self, mod_name, config_name, task_name, group_name, arg_name):
        """
        Reset config arg to default value.
        See Mod.config_reset()

        Args:
            mod_name (str):
            config_name (str):
            task_name (str):
            group_name (str):
            arg_name (str):

        Returns:
            ConfigSetEvent | None:
        """
        try:
            mod = self.dict_mod[mod_name]
        except KeyError:
            logger.warning(f'No such mod: "{mod_name}"')
            return None

        event = ConfigSetEvent(task=task_name, group=group_name, arg=arg_name, value=None)
        response = mod.config_reset(config_name, event)
        return response


MOD_LOADER = ModLoader(env.PROJECT_ROOT)
