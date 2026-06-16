from alasio.codegen.python import CodeGen
from alasio.config.entry.const import ModEntryInfo
from alasio.config_dev.format.extract_method import extract_method
from alasio.config_dev.format.format_i18n import format_i18n
from alasio.config_dev.format.format_yaml import yaml_formatter
from alasio.config_dev.parse.base import DefinitionError
from alasio.config_dev.parse.cache_alasio import CacheAlasio
from alasio.config_dev.parse.load_alas_i18n import LoadAlasI18n
from alasio.config_dev.parse.parse_args import ArgData, TYPE_ARG_LITERAL, TYPE_ARG_TUPLE
from alasio.config_dev.parse.parse_groups import ParseGroups
from alasio.config_dev.parse.parse_tasks import ParseTasks
from alasio.ext.cache import cached_property
from alasio.ext.deep import deep_get, deep_set
from alasio.ext.file.jsonfile import NoIndent, write_json_custom_indent
from alasio.ext.file.yamlfile import format_yaml
from alasio.ext.path import PathStr
from alasio.logger import logger


class ConfigGenerator(ParseGroups, ParseTasks):
    def __init__(self, entry, file):
        """
        维护一个nav下所有文件的数据一致性，一个nav对应前端导航组件中的一个组。
        数据文件夹的目录结构应该像这样：
        <folder>
            - <nav>.args.yaml
            - <nav>.tasks.yaml
            - <nav>_config.json
            - <nav>_model.py

        概念解释：
        1. "arg" 是一个字段，它具有双重含义，一个arg对应前端上的一个用户输入，也对应一个可以在代码中作为变量访问的用户设置
            self.config.{group}_{arg}。你只需要定义一次，就可以在前端/后端/数据库/脚本运行时 访问它，Alasio框架会维护一致性。
            它可以有msgspec字段约束，并且可以有额外自定义内容传递给前端。
        2. "group" 是一个设置组，一个 "group" 包含一个或多个 "args"。
            注意：在同一个MOD中group name必须是唯一的。
        3. "task" 是一个在调度器中运行的任务。一个 "task" 包含一个或多个 "group"。
            注意：在同一个MOD中task name必须是唯一的。
            在数据库中，用户设置具有 task.group.arg 三个层级，而在任务运行时只有 group.arg 两个层级。
            这个降维操作称为绑定（bind），一个 task 可以绑定它旗下的 group，也可以绑定其他任务中的 group。
            Alasio会将变量self.config.{group}_{arg}与数据库中的对应值双向绑定，这个变量的值就是数据库中的真实值，可以直接使用，
            当你改变这个变量的时候，会触发数据库写入。
        4. "model" 是msgspec校验模型。当你编写 "group" 之后，Alasio会生成对应的msgspec模型，校验模型只有 group.arg 两个层级。
            当用户在前端修改设置的时候，Alasio会找到 group 对应的校验模型校验用户输入。
        5. "card" 是前端设置界面的设置组。
            这是容易与 "task" "group" 混淆的概念，如果我们完全展示底层的 "task" "group" 给用户很可能造成垃圾信息多 重点不突出。
            所以在Alasio中，你可以自由地将任何group聚合为card。前端会展示list[card]，而每个card内可以定义display group。
            card中可以包含一个或多个group，group.arg会被flatten并在卡片中显示为一维的输入组件。
            当card中包含多个group时，_info是第一个非Scheduler的group，你也可以使用inforef:{group}手动指定。
        6. "nav" 是前端导航组件中的组。用户选择这组，然后前端展示相关设置。

        文件解释：
        1. <nav>.args.yaml 是一个手动定义的文件。它有group.arg两个层级。
            你需要在里面定义"group"，定义每个"group"包含的"arg"，定义每个"arg"的属性。
        2. <nav>.tasks.yaml 是一个手动定义的文件，它具有task一个层级。
            你需要定义每个task包含的group，定义前端如何展示这些group。
        3. <nav>_i18n.json 是一个半自动生成的文件，它有group.arg.lang三个层级。
            Alasio会维护其中的数据结构，
            你只需要编辑其中的i18n文本。
        4. <nav>_config.json 是一个全自动生成的文件，它有group.arg两个层级。
            Alasio会将<nav>.args.yaml中的简易定义扩充为具有固定结构的数据。
            当前端请求nav content的时候，Alasio会返回<nav>_config.json，
            并且根据 "_readi18n" 指示加载i18n, 根据 "_readconfig" 加载用户设置。
        5. <nav>_model.py 是一个全自动生成的文件，里面定义了msgspec校验模型。
            你不应该修改这个文件。

        Args:
            entry (ModEntryInfo):
            file (PathStr): Absolute filepath to {nav}.args.yaml
        """
        super().__init__(file)
        self.entry = entry
        self.folder = ''
        # real data will be set in _generate_nav_config_json()
        # key: {card_name}.{group_name}.{arg_name}
        # value:
        #     {"group": group, "arg": "_info"} for _info
        #     {"task": task, "group": group, "arg": arg, **ArgData.to_dict()} for normal args
        #         which is arg path appended with ArgData
        self.config_data: "dict[str, dict[str, dict]]" = {}
        self.alasio = CacheAlasio().get(entry)

    """
    Generate model
    """

    def get_inherit_name(self, parent):
        """
        Args:
            parent (str):

        Returns:
            str:
        """
        # a dashboard group
        if parent.startswith('Dashboard'):
            return f'a.{parent}'
        # an alasio group
        if self.alasio and parent in self.alasio.groups_data:
            return f'a.{parent}'
        return parent

    @cached_property
    def model_methods(self):
        """
        Returns:
            dict[str, dict[str, list[str]]]:
                key: {class_name}.{method_name}
                value: list of code
        """
        try:
            source = self.model_file.atomic_read_text()
        except FileNotFoundError:
            return {}
        try:
            methods = extract_method(source)
        except SyntaxError as e:
            raise DefinitionError(f'SyntaxError in model file {self.model_file}: {e}')
        return methods

    @cached_property
    def model_gen(self):
        """
        Generate msgspec models

        Returns:
            CodeGen | None:
        """
        _ = self.model_methods
        gen = CodeGen()
        # no datetime because we have alias: a.T_DATETIME, a.DEFAULT_TIME
        # gen.Import('datetime').as_('d')
        gen.Import('typing').as_('t')
        gen.Import('msgspec').as_('m')
        gen.Import('typing_extensions').as_('e')
        if self.alasio:
            gen.Import('alasio.config.alasio.group_export').as_('a')
            gen.CommentCodeGen('module.config.gen')
        else:
            gen.Import('alasio.config.alasio.group_base').as_('a')
            gen.CommentCodeGen('alasio.config_dev.gen_alasio')

        for group_name, group in self.groups_data.items():
            # Skip empty group
            if not group.args:
                continue
            # Define model class
            cls = gen.Class(group_name)
            if group.parent:
                parent = [self.get_inherit_name(p) for p in group.parent]
                cls.set_inherit(*parent)
            else:
                cls.set_inherit('a.GroupBase')
            with cls:
                for arg_name, arg in group.override_args.items():
                    arg: ArgData
                    # Expand tuple
                    if arg.dt in TYPE_ARG_TUPLE:
                        with gen.Tuple(arg_name).Anno(arg.get_anno()).wrap():
                            for item in arg.value:
                                gen.Item(item)
                        continue
                    # Expand literal
                    if arg.dt in TYPE_ARG_LITERAL:
                        with gen.Literal(arg_name).set_literal('t.Literal').Var(arg.value).wrap():
                            for option in arg.option:
                                gen.Item(option)
                        continue
                    # inline
                    gen.Anno(arg_name, arg.get_anno()).Var(arg.get_value())

                # keep manual methods
                methods = self.model_methods.get(group_name)
                if methods:
                    for method_name, lines in methods.items():
                        with gen.RawDef():
                            for line in lines:
                                gen.Raw(line, indent=False)

        # gen.print()
        if gen.has_content:
            return gen
        else:
            return None

    """
    Generate i18n
    """

    @cached_property
    def _i18n_old(self):
        """
        Old {nav}_i18n.json, with manual written i18n
        """
        return self.read_i18n_json()

    def _update_arg_i18n(self, group_name, arg_name, arg: ArgData):
        """
        Update i18n of {group_name}.{arg_name}

        Returns:
            dict[str, dict[str, Any]]:
                key: {lang}.{field}
                    where field is "name", "help", "option_i18n", etc.
                value: translation
        """
        old = deep_get(self._i18n_old, [group_name, arg_name], default={})
        old = LoadAlasI18n().load_arg(
            old, group_name, arg_name, languages=self.entry.gui_language, options=arg.option)
        new = {}
        for lang in self.entry.gui_language:
            # name, name must not be empty, default to {group_name}.{arg_name}
            key = [lang, 'name']
            value = deep_get(old, key, default='')
            if not value:
                value = f'{group_name}.{arg_name}'
            value = format_i18n(value)
            deep_set(new, key, value)
            # help, help can be empty
            key = [lang, 'help']
            value = deep_get(old, key, default='')
            value = format_i18n(value)
            deep_set(new, key, value)
        # option
        if arg.option:
            for lang in self.entry.gui_language:
                inline = True
                for option in arg.option:
                    default = str(option)
                    if not default.isdigit():
                        inline = False
                    # option name must not be empty, default to {option}
                    key = [lang, 'option_i18n', default]
                    value = deep_get(old, key, default='')
                    if not value:
                        value = default
                    if default != value:
                        inline = False
                    deep_set(new, key, value)
                # if options are all digit and option name is same as option, make it inline
                if inline:
                    key = [lang, 'option_i18n']
                    i18n_option = deep_get(new, key, default=None)
                    deep_set(new, key, NoIndent(i18n_option))

        # if help is empty, make name-help inline
        for lang, data in new.items():
            if len(data) != 2:
                continue
            try:
                value = data['help']
            except KeyError:
                # this shouldn't happen
                continue
            if not value:
                new[lang] = NoIndent(data)
        return new

    def _update_info_i18n(self, group_name, arg_name='_info'):
        """
        Update i18n of {group_name}._info

        Returns:
            dict[str, dict[str, Any]]:
                key: {lang}.{field}
                    where field is "name", "help"
                value: translation
        """
        old = deep_get(self._i18n_old, [group_name, arg_name], default={})
        old = LoadAlasI18n().load_arg(
            old, group_name, arg_name, languages=self.entry.gui_language)
        new = {}
        for lang in self.entry.gui_language:
            # name, name must not be empty, default to {group_name}
            key = [lang, 'name']
            value = deep_get(old, key, default='')
            if not value:
                value = group_name
            deep_set(new, key, str(value))
            # help, help can be empty
            key = [lang, 'help']
            value = deep_get(old, key, default='')
            value = str(value)
            deep_set(new, key, value)
            # if help is empty, make name-help inline
            if not value:
                new[lang] = NoIndent(new[lang])
        return new

    @cached_property
    def i18n_data(self):
        """
        data of {nav}_i18n.json

        Returns:
            dict[str, dict[str, dict[str, dict[str, Any]]]]:
                key: {group_name}.{arg_name}.{lang}.{field}
                    where field is "name", "help", "option_i18n", etc.
                value: translation
        """
        _ = self._i18n_old
        new = {}
        for group_name, group in self.groups_data.items():
            # {group}._info
            row = self._update_info_i18n(group_name, '_info')
            deep_set(new, [group_name, '_info'], row)
            # {group}.{arg}
            for arg_name, arg in group.args.items():
                if arg.hide:
                    continue
                # skip the base args
                if group.parent and arg_name not in group.override_args:
                    continue
                row = self._update_arg_i18n(group_name, arg_name, arg)
                deep_set(new, [group_name, arg_name], row)

        cached_property.pop(self, '_i18n_old')
        return new

    """
    Generate all
    """

    def generate(self, gitadd=None):
        """
        Generate configs and msgspec models
        """
        # Auto create {nav}.tasks.yaml
        if self.file.exists():
            file = self.tasks_file
            if file.ensure_exist():
                logger.info(f'Write file {file}')
                if gitadd:
                    gitadd.stage_add(file)

        # {nav}_model.py
        if self.model_gen:
            file = self.model_file
            op = self.model_gen.write(file, skip_same=True)
            if op:
                logger.info(f'Write file {file}')
                if gitadd:
                    gitadd.stage_add(file)

        # {nav}_i18n.json
        if self.i18n_data:
            file = self.i18n_file
            op = write_json_custom_indent(file, self.i18n_data, skip_same=True)
            if op:
                logger.info(f'Write file {file}')
                if gitadd:
                    gitadd.stage_add(file)

        # format yaml
        op = format_yaml(self.file, yaml_formatter)
        if op:
            logger.info(f'Write file {self.file}')
            if gitadd:
                gitadd.stage_add(self.file)
        op = format_yaml(self.tasks_file, yaml_formatter)
        if op:
            logger.info(f'Write file {self.tasks_file}')
            if gitadd:
                gitadd.stage_add(self.tasks_file)
