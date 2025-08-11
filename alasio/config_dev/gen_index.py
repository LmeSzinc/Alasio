from alasio.config.const import Const
from alasio.config.entry.const import DICT_MOD_ENTRY, ModEntryInfo
from alasio.config_dev.gen_config import ConfigGenerator
from alasio.config_dev.parse.base import DefinitionError
from alasio.config_dev.parse.parse_tasks import TaskGroup
from alasio.ext import env
from alasio.ext.cache import cached_property
from alasio.ext.deep import deep_exist, deep_get, deep_iter_depth2, deep_set
from alasio.ext.file.jsonfile import NoIndent, write_json_custom_indent
from alasio.ext.file.msgspecfile import read_msgspec
from alasio.ext.path import PathStr
from alasio.ext.path.calc import to_posix
from alasio.logger import logger


class IndexGenerator:
    def __init__(self, entry: ModEntryInfo):
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
        self.root = PathStr.new(entry.root).abspath()
        self.path_config: PathStr = self.root.joinpath(entry.path_config)

        # {path_config}/model.index.json
        self.model_index_file = self.path_config.joinpath('model.index.json')
        # {path_config}/config.index.json
        self.config_index_file = self.path_config.joinpath('config.index.json')
        # {path_config}/nav.index.json
        self.nav_index_file = self.path_config.joinpath('nav.index.json')

    @cached_property
    def dict_nav_config(self):
        """
        All ParseNavConfig objects

        Returns:
            dict[str, ConfigGenerator]:
                key: nav_name
                value: generator
        """
        out = {}
        for file in self.path_config.iter_files(ext='.args.yaml', recursive=True):
            parser = ConfigGenerator(file)
            # nav
            nav = parser.nav_name
            if nav in out:
                raise DefinitionError(
                    f'Duplicate nav name: {nav}',
                    file=file,
                )
            out[nav] = parser
        return out

    """
    Generate model.index.json
    """

    @cached_property
    def dict_group_ref(self):
        """
        convert group name to where the msgspec model class is defined

        Returns:
            dict[str, dict[str, str]]:
                key: {group_name}
                value: {'file': file, 'cls': class_name}
        """
        out = {}
        for config in self.dict_nav_config.values():
            # calculate module file
            file = config.model_file.subpath_to(self.root)
            if file == config.model_file:
                raise DefinitionError(
                    f'model_file is not a subpath of root, model_file={config.model_file}, root={self.root}')
            file = file.to_posix()
            # iter group models
            for group_name, class_name in config.dict_group2class.items():
                # group must be unique
                if group_name in out:
                    raise DefinitionError(
                        f'Duplicate group name: {group_name}',
                        file=config.file,
                        keys=group_name,
                    )
                # build model reference
                ref = {'file': file, 'cls': class_name}
                out[group_name] = ref

        return out

    @cached_property
    def model_index_data(self):
        """
        model data in model.index.json

        Returns:
             dict[str, dict[str, str | dict[str, str]]]:
                key: {task_name}.{group_name}
                value: {'file': file, 'cls': class_name, 'task': ref_task_name}
                    which indicates:
                    - read config from task={ref_task_name} and group={group_name}
                    - validate with model file={file}, class {class_name}
                    class_name can be:
                    - {group_name} for normal group
                    - {task_name}_{group_name} that inherits from class {group_name}, for override task group
        """
        out = {}
        for config in self.dict_nav_config.values():
            for task_name, task_data in config.tasks_data.items():
                # task name must be unique
                if task_name in out:
                    raise DefinitionError(
                        f'Duplicate task name: {task_name}',
                        file=config.tasks_file,
                        keys=task_name,
                    )
                # generate groups
                for group in task_data.group:
                    if group.task:
                        # reference {ref_task_name}.{group_name}
                        ref_task = group.task
                    else:
                        # reference task self
                        ref_task = task_name
                    # check if group exists
                    try:
                        ref = self.dict_group_ref[group.group]
                    except KeyError:
                        raise DefinitionError(
                            f'Group ref "{group.group}" of task "{ref_task}" does not exist',
                            file=config.tasks_file,
                            keys=[task_name, 'group']
                        )
                    # copy ref, set ref_task
                    ref = {k: v for k, v in ref.items()}
                    ref['task'] = ref_task
                    deep_set(out, [task_name, group.group], NoIndent(ref))

        # check if {ref_task_name}.{group_name} reference has corresponding value
        for _, group, ref in deep_iter_depth2(out):
            if isinstance(ref, NoIndent):
                ref = ref.value
            ref_task = ref['task']
            if not deep_exist(out, [ref_task, group]):
                raise DefinitionError(
                    f'Cross-task group ref does not exist: {ref_task}.{group}',
                )

        return out

    """
    Generate config.index.json
    """

    @cached_property
    def dict_group2file(self):
        """
        Convert group name to {nav}.config.json to read

        Returns:
            dict[str, str]:
                key: {group_name}
                value: relative path to {nav}.config.json
        """
        out = {}
        for config in self.dict_nav_config.values():
            # calculate module file
            file = config.config_file.subpath_to(self.root)
            if file == config.model_file:
                raise DefinitionError(
                    f'gui_file is not a subpath of root, model_file={config.config_file}, root={self.root}')
            # iter group models
            file = to_posix(file)
            for group_name in config.config_data.keys():
                # group must be unique
                if group_name in out:
                    raise DefinitionError(
                        f'Duplicate group name: {group_name}',
                        file=config.tasks_file,
                        keys=group_name,
                    )
                out[group_name] = file

        return out

    @cached_property
    def dict_group2configgen(self):
        """
        Convert group name to ConfigGenerator object

        Returns:
            dict[str, ConfigGenerator]:
        """
        out = {}
        for config in self.dict_nav_config.values():
            for group_name in config.config_data.keys():
                out[group_name] = config
        return out

    def _get_display_info(
            self, config: ConfigGenerator, task_name: str, display_flat: "list[TaskGroup]"
    ) -> dict:
        """
        Predict info reference from a list of display_flat
        """
        try:
            first = display_flat[0]
        except IndexError:
            raise DefinitionError(
                f'Empty display_flat: {display_flat}',
                file=config.tasks_file, keys=[task_name, 'display']
            )
        # use info ref first
        if first.inforef:
            try:
                file = self.dict_group2file[first.inforef]
            except KeyError:
                raise DefinitionError(
                    f'inforef "{first.inforef}" does not exists',
                    file=config.tasks_file, keys=[task_name, 'display']
                )
            return {'group': first.inforef, 'file': file}

        # no info ref, use the first group that is not Scheduler
        for group in display_flat:
            group_name = group.group
            if group_name == 'Scheduler':
                continue
            try:
                file = self.dict_group2file[group_name]
            except KeyError:
                raise DefinitionError(
                    f'Display group "{group_name}" does not exists',
                    file=config.tasks_file, keys=[task_name, 'display']
                )
            return {'group': group_name, 'file': file}

        # no luck, just use the first group
        try:
            file = self.dict_group2file[first.group]
        except KeyError:
            raise DefinitionError(
                f'Display group "{first.group}" does not exists',
                file=config.tasks_file, keys=[task_name, 'display']
            )
        return {'group': first.group, 'file': file}

    def _iter_display(
            self, config: ConfigGenerator, task_name: str, display_flat
    ):
        """
        Iter display reference from a list of display_flat

        Args：
            config ():
            display_flat (list[TaskGroup]):

        Yields:
            dict:
        """
        for display in display_flat:
            # skip info ref
            if display.inforef:
                continue
            # display group must be defined
            try:
                file = self.dict_group2file[display.group]
            except KeyError:
                raise DefinitionError(
                    f'Display ref "{display.group}" of task "{task_name}" does not exist',
                    file=config.tasks_file, keys=[task_name, 'display']
                )
            if display.task:
                # If display a cross-task group, group must exist
                if not deep_exist(self.model_index_data, keys=[display.task, display.group]):
                    raise DefinitionError(
                        f'Cross-task display ref "{display.task}.{display.group}" does not exist',
                        file=config.tasks_file, keys=[task_name, 'display']
                    )
                yield {'task': display.task, 'group': display.group, 'file': file}
            else:
                # If display an in-task group, group must within this task
                if not deep_exist(self.model_index_data, keys=[task_name, display.group]):
                    raise DefinitionError(
                        f'In-task display ref "{display.group}" is not in task "{task_name}"',
                        file=config.tasks_file, keys=[task_name, 'display']
                    )
                yield {'task': task_name, 'group': display.group, 'file': file}

    @cached_property
    def config_index_data(self):
        """
        data in config.index.json

        Returns:
            dict[str, dict[str, dict[str, dict[str, str]]]]:
                key: {nav_name}.{card_name}
                value: {"_info": info_ref, "display": list[display_ref]}
                    - info_ref is: {"group": group_name, "file": file}
                        where file is {nav}_config.json and will reference key {group_name}._info in the file
                    - display_ref is {"task": task_name, "group": group_name, "file": file}
                        where file is {nav}_config.json and will reference key {group_name} in the file
                        "task" indicates to read {task_name}.{group_name} in user config
        """
        out = {}
        for config in self.dict_nav_config.values():
            nav = config.nav_name
            for task_name, task in config.tasks_data.items():
                is_flat = len(task.display) == 1
                for display_flat in task.display:
                    # generate display
                    display = list(self._iter_display(config, task_name, display_flat))
                    if is_flat:
                        card_name = f'card-{task_name}'
                    else:
                        card_name = '_'.join([d['group'] for d in display])
                        card_name = f'card-{task_name}-{card_name}'
                    # generate _info
                    info = self._get_display_info(config, task_name, display_flat)
                    deep_set(out, keys=[nav, card_name, '_info'], value=NoIndent(info))
                    display = [NoIndent(d) for d in display]
                    deep_set(out, keys=[nav, card_name, 'display'], value=display)

        return out

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
            for group_name, group_data in config.config_data.items():
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
            if isinstance(info, NoIndent):
                info = info.value
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

        # config.index.json
        op = write_json_custom_indent(self.config_index_file, self.config_index_data, skip_same=True)
        if op:
            logger.info(f'Write file {self.config_index_file}')

        # nav.index.json
        op = write_json_custom_indent(self.nav_index_file, self.nav_index_data, skip_same=True)
        if op:
            logger.info(f'Write file {self.nav_index_file}')


if __name__ == '__main__':
    _entry = DICT_MOD_ENTRY['alasio'].copy()
    _entry.root = env.PROJECT_ROOT.joinpath('alasio')
    self = IndexGenerator(_entry)
    self.generate()
