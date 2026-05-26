from alasio.config_dev.gen.gen_cross import CrossNavGenerator
from alasio.config_dev.parse.base import DefinitionError
from alasio.ext.cache import cached_property
from alasio.ext.deep import deep_get, deep_set
from alasio.ext.file.msgspecfile import read_msgspec


class GenNavIndex(CrossNavGenerator):
    """Generator for nav.index.json."""

    @cached_property
    def nav_index_file(self):
        return self.path_config.joinpath('nav.index.json')

    @cached_property
    def dict_group_name_i18n(self):
        """
        Returns:
            dict[str, dict[str, str]]:
                key: {group_name}.{lang}
                value: translation
        """
        out = {}

        def iter_configs():
            yield from self.dict_nav_config.values()
            if self.alasio:
                yield from self.alasio.dict_nav_config.values()

        for config in iter_configs():
            for group_name, group_data in config.i18n_data.items():
                # get "name" from a nested i18n dict
                for lang in self.entry.gui_language:
                    name = deep_get(group_data, keys=['_info', lang, 'name'], default=group_name)
                    deep_set(out, [group_name, lang], name)

        return out

    @cached_property
    def nav_index_data(self):
        """
        data of nav.index.json

        Returns:
            dict[str, dict[str, dict[str, str]]]:
                key: {nav_name}.{card_name}.{lang}
                value: i18n translation
        """
        old = read_msgspec(self.nav_index_file)
        out = {}
        for nav_name, config in self.dict_nav_config.items():
            # skip dashboard on nav
            if nav_name == 'dashboard':
                continue
            # nav name, which must not empty
            empty = True
            for group in config.tasks_data.values():
                if group.displays:
                    empty = False
                    break
            if config.tasks_data and not empty:
                for lang in self.entry.gui_language:
                    key = [nav_name, '_info', lang]
                    value = deep_get(old, key, default='')
                    if not value:
                        value = nav_name
                    deep_set(out, key, value)
            for card_name, data in config.config_data.items():
                # card name
                if card_name.startswith('_'):
                    continue
                try:
                    info = data['_info']
                except KeyError:
                    raise DefinitionError(f'Card "{nav_name}.{card_name}" has no "_info"')
                try:
                    group_name = info['group']
                except KeyError:
                    raise DefinitionError(f'Card "{nav_name}.{card_name}._info" has no "group"')
                try:
                    name = self.dict_group_name_i18n[group_name]
                except KeyError:
                    raise DefinitionError(
                        f'Card "{nav_name}.{card_name}._info" reference a non-exist group: "{group_name}"')
                deep_set(out, [nav_name, card_name], name)

        return out
