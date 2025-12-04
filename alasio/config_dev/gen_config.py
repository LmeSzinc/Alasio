from alasio.config.const import Const
from alasio.ext.cache import cached_property
from alasio.ext.codegen import CodeGen
from alasio.ext.deep import deep_get, deep_iter_depth1, deep_set
from alasio.ext.file.jsonfile import NoIndent, write_json_custom_indent
from alasio.ext.file.msgspecfile import read_msgspec
from alasio.ext.file.yamlfile import format_yaml
from alasio.ext.path import PathStr
from alasio.logger import logger
from .format.format_yaml import yaml_formatter
from .parse.parse_args import ArgData, ParseArgs, TYPE_ARG_LITERAL, TYPE_ARG_TUPLE
from .parse.parse_tasks import ParseTasks


class ConfigGenerator(ParseArgs, ParseTasks):
    def __init__(self, file):
        """
        维护一个nav下所有文件的数据一致性，一个nav对应前端导航组件中的一个组。
        数据文件夹的目录结构应该像这样：
        <nav>
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
            file (PathStr): Absolute filepath to {nav}.args.yaml
        """
        super().__init__(file)
        # real data will be set in _generate_nav_config_json()
        # key: {card_name}.{arg_name}
        # value:
        #     {"group": group, "arg": "_info"} for _info
        #     {"task": task, "group": group, "arg": arg, **ArgData.to_dict()} for normal args
        #         which is arg path appended with ArgData
        self.config_data: "dict[str, dict[str, dict]]" = {}

    """
    Generate model
    """

    @cached_property
    def dict_group2class(self):
        """
        A dict that convert group name to class name of msgspec model.
        class name is default to group name but may vary when having "override", "default", etc

        Returns:
            dict[str, str]:
        """
        data = {}
        for group_name, _ in deep_iter_depth1(self.args_data):
            data[group_name] = group_name
        return data

    @cached_property
    def model_gen(self):
        """
        Generate msgspec models

        Returns:
            CodeGen | None:
        """
        gen = CodeGen()
        gen.RawImport("""
        import datetime as d
        import typing as t

        import msgspec as m
        import typing_extensions as e
        """)
        gen.Empty()
        gen.CommentCodeGen('alasio.config.dev.configgen')
        has_content = False
        for group_name, arg_data in deep_iter_depth1(self.args_data):
            # Skip empty group
            if not arg_data:
                continue
            has_content = True
            # args_data and dict_group2class should have the same keys
            class_name = self.dict_group2class[group_name]
            # Define model class
            with gen.Class(class_name, inherit='m.Struct, omit_defaults=True'):
                for arg_name, arg in deep_iter_depth1(arg_data):
                    arg: ArgData
                    # Expand list
                    if arg.dt in TYPE_ARG_TUPLE:
                        gen.Var(arg_name, anno=arg.get_anno(), value=arg.value, auto_multiline=120)
                        continue
                    # Expand literal
                    if arg.dt in TYPE_ARG_LITERAL:
                        anno = arg.get_anno()
                        if len(anno) > 60:
                            # {name}: t.Literal[
                            #     ...
                            # } = ...
                            with gen.tab(prefix=f'{arg_name}: t.Literal[', suffix=f'] = {repr(arg.value)}',
                                         line_ending=',', tab_type='list'):
                                for option in arg.option:
                                    gen.Item(option)
                            continue
                    # inline
                    gen.Anno(arg_name, anno=arg.get_anno(), value=arg.get_value())
            gen.Empty(2)

        # gen.print()
        if has_content:
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
        return read_msgspec(self.i18n_file)

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
        new = {}
        for lang in Const.GUI_LANGUAGE:
            # name, name must not be empty, default to {group_name}.{arg_name}
            key = [lang, 'name']
            value = deep_get(old, key, default='')
            if not value:
                value = f'{group_name}.{arg_name}'
            deep_set(new, key, str(value))
            # help, help can be empty
            key = [lang, 'help']
            value = deep_get(old, key, default='')
            deep_set(new, key, str(value))
        # option
        if arg.option:
            for lang in Const.GUI_LANGUAGE:
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
        new = {}
        for lang in Const.GUI_LANGUAGE:
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
        for group_name, arg_data in deep_iter_depth1(self.args_data):
            # {group}._info
            row = self._update_info_i18n(group_name, '_info')
            deep_set(new, [group_name, '_info'], row)
            for arg_name, arg in deep_iter_depth1(arg_data):
                # {group}.{arg}
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
