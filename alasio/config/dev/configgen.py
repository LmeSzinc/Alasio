from alasio.config.const import Const
from alasio.config.dev.parse_args import ArgsData, ParseArgs, TYPE_ARG_LIST, TYPE_ARG_LITERAL
from alasio.ext.cache import cached_property, del_cached_property
from alasio.ext.codegen import CodeGen
from alasio.ext.deep import deep_get, deep_iter_depth1, deep_set
from alasio.ext.path import PathStr
from alasio.ext.path.jsonread import read_msgspec, write_json


class GenMsgspec(ParseArgs):
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
    def model(self) -> CodeGen:
        """
        Generate msgspec models
        """
        gen = CodeGen()
        gen.RawImport("""
        import typing as t

        import msgspec as m
        """)
        gen.Empty()
        gen.CommentCodeGen('alasio.config.dev.configgen')
        for group_name, arg_data in deep_iter_depth1(self.args):
            with gen.Class(group_name, inherit='m.Struct, omit_defaults=True'):
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
        return gen

    @cached_property
    def gui_file(self):
        # {aside}.args.yaml -> {aside}_gui.json
        return self.file.with_name(f'{self.file.rootstem}_gui.json')

    @cached_property
    def gui_old(self):
        return read_msgspec(self.gui_file)

    def _update_gui_arg(self, group_name, arg_name, arg: ArgsData):
        old = deep_get(self.gui_old, [group_name, arg_name], default={})
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
        old = deep_get(self.gui_old, [group_name, arg_name], default={})
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
        _ = self.gui_old
        new = {}
        for group_name, arg_data in deep_iter_depth1(self.args):
            # {group}._info
            row = self._update_gui_info(group_name, '_info')
            deep_set(new, [group_name, '_info'], row)
            for arg_name, arg in deep_iter_depth1(arg_data):
                # {group}.{arg}
                row = self._update_gui_arg(group_name, arg_name, arg)
                deep_set(new, [group_name, arg_name], row)
        del_cached_property(self, 'i18n_old')
        return new

    def write(self):
        """
        Generate and write msgspec models
        """
        self.model.write(self.model_file)
        write_json(self.gui_file, self.gui)


if __name__ == '__main__':
    f = PathStr.new(r'E:\ProgramData\Pycharm\Alasio\alasio\config\alasio\opsi.args.yaml')
    self = GenMsgspec(f)
    self.write()
