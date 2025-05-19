from alasio.config.const import Const
from alasio.ext.cache import cached_property, del_cached_property
from alasio.ext.codegen import CodeGen
from alasio.ext.deep import deep_get, deep_iter_depth1, deep_set
from alasio.ext.file.jsonfile import write_json
from alasio.ext.file.msgspecfile import read_msgspec
from alasio.ext.path import PathStr
from alasio.logger import logger
from .parse_args import ArgData, ParseArgs, TYPE_ARG_LIST, TYPE_ARG_LITERAL
from .parse_tasks import ParseTasks


class ParseConfig(ParseArgs, ParseTasks):
    def __init__(self, file):
        """
        Args:
            file (PathStr): Path to *.args.yaml
        """
        self.file = file

    @cached_property
    def model_file(self):
        # {aside}.args.yaml -> {aside}_model.py
        # Use "_" in file name because python can't import filename with "." easily
        return self.file.with_name(f'{self.file.rootstem}_model.py')

    @cached_property
    def dict_group2class(self):
        """
        A dict that convert group name to class name of msgspec model.
        class name is default to group name but may vary when having override, default, etc

        Returns:
            dict[str, str]:
        """
        data = {}
        for group_name, _ in deep_iter_depth1(self.args_data):
            data[group_name] = group_name
        return data

    @cached_property
    def model_py(self):
        """
        Generate msgspec models

        Returns:
            CodeGen | None:
        """
        gen = CodeGen()
        gen.RawImport("""
        import typing as t

        import msgspec as m
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
                    # Expand list
                    if arg.dt in TYPE_ARG_LIST:
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
                    gen.Anno(arg_name, anno=arg.get_anno(), value=arg.default)
            gen.Empty(2)

        # gen.print()
        if has_content:
            return gen
        else:
            return None

    @cached_property
    def gui_file(self):
        # {aside}.args.yaml -> {aside}_gui.json
        return self.file.with_name(f'{self.file.rootstem}_gui.json')

    @cached_property
    def _gui_old(self):
        """
        Old {aside}_gui.json, with manual written i18n
        """
        return read_msgspec(self.gui_file)

    def _update_gui_arg(self, group_name, arg_name, arg: ArgData):
        """
        Update {group_name}.{arg_name} in {aside}_gui.json
        """
        old = deep_get(self._gui_old, [group_name, arg_name], default={})
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

    def _update_gui_info(self, group_name, arg_name):
        """
        Update {group_name}._info in {aside}_gui.json
        """
        old = deep_get(self._gui_old, [group_name, arg_name], default={})
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
    def gui(self):
        """
        data for {aside}_gui.json

        Returns:
            dict[str, dict[str, Any]]:
                <group_name>:
                    <arg_name>:
                        populated ArgData with i18n and i18n_option
        """
        _ = self._gui_old
        new = {}
        for group_name, arg_data in deep_iter_depth1(self.args_data):
            # {group}._info
            row = self._update_gui_info(group_name, '_info')
            deep_set(new, [group_name, '_info'], row)
            for arg_name, arg in deep_iter_depth1(arg_data):
                # {group}.{arg}
                row = self._update_gui_arg(group_name, arg_name, arg)
                deep_set(new, [group_name, arg_name], row)
        del_cached_property(self, '_gui_old')
        return new

    def write(self):
        """
        Generate configs and write msgspec models
        """
        # Auto create {aside}.tasks.yaml
        if self.file.exists():
            op = self.tasks_file.ensure_exist()
            if op:
                logger.info(f'Write file {self.tasks_file}')

        # {aside}_model.py
        if self.model_py:
            op = self.model_py.write(self.model_file, skip_same=True)
            if op:
                logger.info(f'Write file {self.model_file}')

        # {aside}_gui.json
        if self.gui:
            op = write_json(self.gui_file, self.gui, skip_same=True)
            if op:
                logger.info(f'Write file {self.gui_file}')
