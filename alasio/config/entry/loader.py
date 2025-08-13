from collections import defaultdict

import alasio.config.entry.const as const
from alasio.config.entry.mod import Mod
from alasio.ext import env
from alasio.ext.cache import cached_property
from alasio.ext.deep import deep_iter, deep_iter_depth2
from alasio.ext.path import PathStr
from alasio.ext.path.calc import is_abspath, joinpath
from alasio.logger import logger


class ModLoader:
    def __init__(self, root, dict_mod_entry=None):
        """
        Args:
            root (PathStr): Absolute path to run path
            dict_mod_entry (dict[str, ModEntryInfo):
                see const.DICT_MOD_ENTRY
        """
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
        for name, entry in self.dict_mod_entry.items():
            if not entry.root:
                # logger.warning(f'Mod entry root empty: name={name}, entry={entry.root}')
                # continue
                entry.root = env.PROJECT_ROOT
            elif not is_abspath(entry.root):
                entry.root = joinpath(env.PROJECT_ROOT, entry.root)
            mod = Mod(entry)
            out[name] = mod
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
            raise KeyError(f'No such mod: mod="{mod_name}"') from None

        data = mod.nav_index_data()
        out = defaultdict(dict)
        for nav_name, card_name, i18n_data in deep_iter_depth2(data):
            # there shouldn't be KeyError, because data is validated
            i18n = i18n_data[lang]
            out[nav_name][card_name] = i18n
        return out


if __name__ == '__main__':
    self = ModLoader(PathStr.new(r'E:\ProgramData\Pycharm\Alasio'))
    self.build()
    d = self.get_gui_nav('alasio', 'zh-CN')
    for k, v in deep_iter(d, depth=2):
        print(k, v)
