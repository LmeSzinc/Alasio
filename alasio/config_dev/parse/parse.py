from alasio.config.const import Const
from alasio.ext.cache import cached_property, del_cached_property
from alasio.ext.codegen import CodeGen
from alasio.ext.deep import deep_get, deep_iter_depth1, deep_set
from alasio.ext.file.jsonfile import write_json
from alasio.ext.file.msgspecfile import read_msgspec
from alasio.ext.path import PathStr
from alasio.logger import logger
from .parse_args import ArgData, ParseArgs, TYPE_ARG_LITERAL, TYPE_ARG_TUPLE
from .parse_tasks import ParseTasks


class NavConfig(ParseArgs, ParseTasks):
    def __init__(self, file):
        """
        维护数据一个nav下所有文件的数据一致性，一个nav对应前端导航组件中的一个组。
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
        3. "task" 是一个在调度器中运行的任务。一个 "task" 包含一个或多个 "group"。
            在数据库中，用户设置具有 task.group.arg 三个层级，而在任务运行时只有 group.arg 两个层级。
            这个降维操作称为绑定（bind），一个 task 可以绑定它旗下的 group，也可以绑定其他任务中的 group。
            Alasio会将变量self.config.{group}_{arg}与数据库中的对应值双向绑定，这个变量的值就是数据库中的真实值，可以直接使用，
            当你改变这个变量的时候，会触发数据库写入。
        4. "model" 是msgspec校验模型。当你编写 "group" 之后，Alasio会生成对应的msgspec模型，校验模型只有 group.arg 两个层级。
            当用户在前端修改设置的时候，Alasio会找到 group 对应的校验模型校验用户输入。
        5. "display", "display_task", "display_group" 它们是展示给用户的 "task" 和 "group"。
            这是容易与 "task" "group" 混淆的概念，如果我们完全展示底层的 "task" "group" 给用户很可能造成垃圾信息多 重点不突出。
            所以在Alasio中，你可以自由组合来展示给用户。
        6. "nav" 是前端导航组件中的行。用户选择这行，然后前端展示相关设置。

        文件解释：
        1. <nav>.args.yaml 是一个手动定义的文件。它有group.arg两个层级。
            你需要在里面定义"group"，定义每个"group"包含的"arg"，定义每个"arg"的属性。
        2. <nav>.tasks.yaml 是一个手动定义的文件，它具有task一个层级。
            你需要定义每个task包含的group，定义前端如何展示这些group。
        3. <nav>_config.json 是一个半自动生成的文件，它有group.arg两个层级。
            Alasio会将<nav>.args.yaml中的简易定义扩充为具有固定结构的数据，
            你只需要编辑其中的i18n属性。
        4. <nav>_model.py 是一个全自动生成的文件，里面定义了msgspec校验模型。
            你不应该修改这个文件。

        Args:
            file (PathStr): Absolute filepath to <nav>.args.yaml
        """
        self.file = file

    @cached_property
    def model_file(self):
        # {nav}.args.yaml -> {nav}_model.py
        # Use "_" in file name because python can't import filename with "." easily
        return self.file.with_name(f'{self.file.rootstem}_model.py')

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
                        gen.Var(arg_name, anno=arg.get_anno(), value=arg.default, auto_multiline=120)
                        continue
                    # Expand literal
                    if arg.dt in TYPE_ARG_LITERAL:
                        anno = arg.get_anno()
                        if len(anno) > 60:
                            # {name}: t.Literal[
                            #     ...
                            # } = ...
                            with gen.tab(prefix=f'{arg_name}: t.Literal[', suffix=f'] = {repr(arg.default)}',
                                         line_ending=',', tab_type='list'):
                                for option in arg.option:
                                    gen.Item(option)
                            continue
                    # inline
                    gen.Anno(arg_name, anno=arg.get_anno(), value=arg.get_default())
            gen.Empty(2)

        # gen.print()
        if has_content:
            return gen
        else:
            return None

    @cached_property
    def config_file(self):
        # {nav}.args.yaml -> {nav}_config.json
        return self.file.with_name(f'{self.file.rootstem}_config.json')

    @cached_property
    def _config_old(self):
        """
        Old {nav}_config.json, with manual written i18n
        """
        return read_msgspec(self.config_file)

    def _update_config_arg(self, group_name, arg_name, arg: ArgData):
        """
        Update {group_name}.{arg_name} in {nav}_config.json
        Generate data structure from `arg`, then merge with the i18n from old configs
        """
        old = deep_get(self._config_old, [group_name, arg_name], default={})
        new = arg.to_dict()
        for lang in Const.GUI_LANGUAGE:
            # name
            key = ['i18n', lang, 'name']
            value = deep_get(old, key, default='')
            if not value:
                value = f'{group_name}.{arg_name}'
            deep_set(new, key, value)
            # help
            key = ['i18n', lang, 'help']
            value = deep_get(old, key, default='')
            deep_set(new, key, value)
        # option
        if arg.option:
            for option in arg.option:
                for lang in Const.GUI_LANGUAGE:
                    key = ['i18n_option', lang, option]
                    value = deep_get(old, key, default=option)
                    deep_set(new, key, value)
        return new

    def _update_config_info(self, group_name, arg_name):
        """
        Update {group_name}._info in {nav}_config.json
        Generate group info with default name, then merge with the i18n from old configs
        """
        old = deep_get(self._config_old, [group_name, arg_name], default={})
        new = {}
        for lang in Const.GUI_LANGUAGE:
            # name
            key = ['i18n', lang, 'name']
            value = deep_get(old, key, default='')
            if not value:
                value = f'{group_name}.{arg_name}'
            deep_set(new, key, value)
            # help
            key = ['i18n', lang, 'help']
            value = deep_get(old, key, default='')
            deep_set(new, key, value)
        return new

    @cached_property
    def config_data(self):
        """
        data of {nav}_config.json

        Returns:
            dict[str, dict[str, ArgData]]:
                key: {group_name}.{arg_name}
                value: ArgData, that populated with i18n and i18n_option
        """
        _ = self._config_old
        new = {}
        for group_name, arg_data in deep_iter_depth1(self.args_data):
            # {group}._info
            row = self._update_config_info(group_name, '_info')
            deep_set(new, [group_name, '_info'], row)
            for arg_name, arg in deep_iter_depth1(arg_data):
                # {group}.{arg}
                row = self._update_config_arg(group_name, arg_name, arg)
                deep_set(new, [group_name, arg_name], row)
        del_cached_property(self, '_config_old')
        return new

    def write(self):
        """
        Generate configs and write msgspec models
        """
        # Auto create {nav}.tasks.yaml
        if self.file.exists():
            op = self.tasks_file.ensure_exist()
            if op:
                logger.info(f'Write file {self.tasks_file}')

        # {nav}_model.py
        if self.model_gen:
            op = self.model_gen.write(self.model_file, skip_same=True)
            if op:
                logger.info(f'Write file {self.model_file}')

        # {nav}_config.json
        if self.config_data:
            op = write_json(self.config_file, self.config_data, skip_same=True)
            if op:
                logger.info(f'Write file {self.config_file}')
