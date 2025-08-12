from alasio.config.const import Const
from alasio.config.entry.const import DICT_MOD_ENTRY
from alasio.config_dev.gen_cross import CrossNavGenerator
from alasio.config_dev.parse.base import DefinitionError
from alasio.ext import env
from alasio.ext.cache import cached_property
from alasio.ext.deep import deep_get, deep_iter_depth2, deep_set
from alasio.ext.file.jsonfile import write_json_custom_indent
from alasio.ext.file.msgspecfile import read_msgspec
from alasio.logger import logger


class IndexGenerator(CrossNavGenerator):
    def __init__(self, *args, **kwargs):
        """
        维护一个MOD下所有设置文件的数据一致性，生成json索引文件。

        1. "model.index.json" 指导当前MOD下的task有哪些group，具有 task.group 二级结构。
            这个文件会在脚本运行时被使用。
            当Alasio脚本实例启动时，它需要读取与某个task相关的设置：
            - 在"model.index.json"中查询当前task
            - 遍历task下有哪些group，得到group所在的{nav}_model.py文件路径
            - 载入python文件，查询{nav}_model.py中的group对应的msgspec模型
            - 读取用户数据，使用msgspec模型校验数据

        2. "config.index.json" 指导当前MOD有哪些设置，具有 nav.card 三级结构。
            这个文件会在前端显示时被使用。
            当前端需要显示某个nav下的用户设置时：
            - 在"config.index.json"中查询当前nav，遍历card
            - 加载info ref和display ref 指向的{nav}_config.json，查询其中的group_name
            - 按display ref指示，加载用户设置中的{task}.{group}设置组
            - 聚合所有内容

        3. "nav.index.json" 是任务和任务导航的i18n，具有 component.name.lang 三级结构。
            这个文件会在前端显示时被使用。
            注意这是一个半自动生成文件，Alasio会维护它的数据结构，但是需要人工编辑nav对应的i18n，
            当前端需要显示导航组件时：
            - 读取"nav.index.json"返回给前端
        """
        super().__init__(*args, **kwargs)

        # {path_config}/model.index.json
        self.model_index_file = self.path_config.joinpath('model.index.json')
        # {path_config}/config.index.json
        self.config_index_file = self.path_config.joinpath('config.index.json')
        # {path_config}/nav.index.json
        self.nav_index_file = self.path_config.joinpath('nav.index.json')

    """
    Generate nav.index.json
    """

    @cached_property
    def dict_group_name_i18n(self):
        """
        Returns:
            dict[str, dict[str, str]]:
                key: {group_name}.{lang}
                value: translation
        """
        out = {}
        for config in self.dict_nav_config.values():
            for group_name, group_data in config.i18n_data.items():
                i18n = deep_get(group_data, ['_info', 'i18n'], default={})
                name = self._get_i18n_name(i18n)
                out[group_name] = name
        return out

    @staticmethod
    def _get_i18n_name(i18n):
        """
        Get "name" from a nested i18n dict

        Args:
            i18n (dict[str, dict[str, str]]):
                key: {lang}.{field}
                value: translation

        Returns:
            dict[str, str]:
                key: {lang}
                value: translation
        """
        out = {}
        for lang, field_data in i18n.items():
            try:
                name = field_data['name']
            except KeyError:
                name = ''
            out[lang] = name
        return out

    @cached_property
    def nav_index_data(self):
        """
        data of nav.index.json

        Returns:
            dict[str, dict[str, dict[str, str]]]:
                key: {component}.{name}.{lang}
                    component can be "nav", "task"
                value: i18n translation
        """
        old = read_msgspec(self.nav_index_file)
        out = {}

        for nav_name, config in self.dict_nav_config.items():
            # nav name, which must not empty
            if config.tasks_data:
                for lang in Const.GUI_LANGUAGE:
                    key = ['nav', nav_name, lang]
                    value = deep_get(old, key, default='')
                    if not value:
                        value = nav_name
                    deep_set(out, key, value)
            # task name, which must not empty
            for task_name in config.tasks_data:
                for lang in Const.GUI_LANGUAGE:
                    key = ['task', task_name, lang]
                    value = deep_get(old, key, default='')
                    if not value:
                        value = task_name
                    deep_set(out, key, value)
        # card name
        for nav_name, card_name, data in deep_iter_depth2(self.config_index_data):
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
                raise DefinitionError(f'Card "{nav_name}.{card_name}._info" reference a non-exist group: {group_name}')
            deep_set(out, ['card', nav_name, card_name], name)

        return out

    """
    Generate all
    """

    def generate(self):
        # check path
        if not self.root.exists():
            logger.warning(f'ConfigGen root not exist: {self.root}')
        if not self.path_config.exists():
            logger.warning(f'ConfigGen path_config not exist: {self.path_config}')

        # update nav configs
        for nav in self.dict_nav_config.values():
            nav.generate()

        # model.index.json
        _ = self.model_index_data
        if not self.model_index_data:
            return
        op = write_json_custom_indent(self.model_index_file, self.model_index_data, skip_same=True)
        if op:
            logger.info(f'Write file {self.model_index_file}')

        # {nav}_config.json
        self.generate_config_json()

        # nav.index.json
        # op = write_json_custom_indent(self.nav_index_file, self.nav_index_data, skip_same=True)
        # if op:
        #     logger.info(f'Write file {self.nav_index_file}')


if __name__ == '__main__':
    _entry = DICT_MOD_ENTRY['alasio'].copy()
    _entry.root = env.PROJECT_ROOT.joinpath('alasio')
    self = IndexGenerator(_entry)
    self.generate()
