from typing import Iterator

from alasio.config_dev.format.format_yaml import yaml_formatter
from alasio.config_dev.gen.gen_config import ConfigGenerator
from alasio.config_dev.gen.gen_cross import CrossNavGenerator
from alasio.config_dev.parse.base import DefinitionError
from alasio.ext.cache import cached_property
from alasio.ext.deep import deep_get, deep_keys_depth1, deep_set
from alasio.ext.file.msgspecfile import read_msgspec
from alasio.ext.file.yamlfile import read_yaml, write_yaml
from alasio.logger import logger


class GenNavIndex(CrossNavGenerator):
    """Generator for nav.index.json."""

    @cached_property
    def nav_index_file(self):
        return self.path_config.joinpath('_index/nav.index.json')

    @cached_property
    def dict_group_name_i18n(self):
        """
        Returns:
            dict[str, dict[str, str]]:
                key: {group_name}.{lang}
                value: translation
        """
        out = {}

        def iter_configs() -> "Iterator[ConfigGenerator]":
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
    def nav_order_file(self):
        return self.path_config.joinpath('_index/nav.order.yaml')

    @cached_property
    def nav_order_data(self):
        """
        data of nav.order.yaml

        nav.order.yaml defines the order of nav.
        GenNavIndex will maintain its data structure, adding new nav and removing old nav,
        then write to file if content changed.

        File content is like:
            alas:
            general:
            reward:
            daily:

        Returns:
            dict[str, None]:
                key: nav_name, ordered by existing file order then new navs appended
        """
        current_navs = {}
        for nav_name, config in self.dict_nav_config.items():
            # skip dashboard
            if nav_name == 'dashboard':
                continue
            # skip empty nav
            if not config.tasks_data:
                continue
            current_navs[nav_name] = None

        old = read_yaml(self.nav_order_file)
        out = {}
        for nav_name in deep_keys_depth1(old):
            if nav_name in current_navs:
                # respect pre-defined nav order
                out[nav_name] = None
            # else: drop old nav
        # add new nav
        for nav_name in sorted(current_navs):
            if nav_name not in out:
                out[nav_name] = None
        return out

    def generate_nav_order(self, gitadd=None):
        """
        Write nav.order.yaml if content changed, then format with yaml_formatter.
        """
        data = self.nav_order_data
        file = self.nav_order_file
        if data:
            op = write_yaml(file, data, skip_same=True, formatter=yaml_formatter)
            if op:
                logger.info(f'Write file {file}')
                if gitadd:
                    gitadd.stage_add(file)
        else:
            if file.exists():
                logger.info(f'Delete file {file}')
                file.atomic_remove()
                if gitadd:
                    gitadd.stage_add(file)

    @cached_property
    def nav_index_data(self):
        """
        data of nav.index.json

        Returns:
            dict[str, dict[str, dict[str, str]]]:
                key: {nav_name}.{card_name}.{lang}
                value: i18n translation

            {nav_name}._info.{lang} is manual maintained
            {nav_name}.{card_name}.{lang} is auto generated from card.info
        """
        old = read_msgspec(self.nav_index_file)
        out = {}
        for nav_name in self.nav_order_data:
            config = self.dict_nav_config[nav_name]
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
