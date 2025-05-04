from alasio.config.const import Const
from alasio.config.dev.parse_args import ArgData, DefinitionError, ParseArgs, TYPE_ARG_LIST, TYPE_ARG_LITERAL
from alasio.config.dev.parse_tasks import ParseTasks
from alasio.config.dev.tasks_model import TaskRefModel
from alasio.ext.cache import cached_property, del_cached_property
from alasio.ext.codegen import CodeGen
from alasio.ext.deep import deep_get, deep_iter_depth1, deep_set
from alasio.ext.file.jsonfile import write_json
from alasio.ext.file.msgspecfile import read_msgspec
from alasio.ext.path import PathStr


class GenMsgspec(ParseArgs, ParseTasks):
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

    @cached_property
    def tasks(self) -> TaskRefModel:
        """
        Returns:
            <task_name>:
                <group_name>:
                    GroupModelRef
        """
        # Calculate module file
        cwd = PathStr.cwd()
        file = self.model_file.subpath_to(cwd)
        if file == self.model_file:
            raise DefinitionError(
                f'model_file is not a subpath of cwd, model_file={self.model_file}, cwd={cwd}')
        file = file.to_python_import()
        # Generate tasks model ref
        output = {}
        for task_name, task in self.tasks_data.items():
            # Skip tasks with no groups
            if not task.group:
                continue
            # Task name must be unique
            if task_name in output:
                raise DefinitionError(f'Duplicate task name: {task_name}', file=self.tasks_file)
            # Set ref
            for group_name in task.group:
                ref = {'file': file, 'cls': group_name}
                deep_set(output, [task_name, group_name], ref)

        return output

    def write(self):
        """
        Generate and write msgspec models
        """
        # Auto create {aside}.tasks.yaml
        if self.file.exists():
            self.tasks_file.ensure_exist()
        # {aside}_model.py
        if self.model_py:
            self.model_py.write(self.model_file)
        # {aside}_gui.json
        if self.gui:
            write_json(self.gui_file, self.gui)
        # write_json(self.file.with_name('taskref.json'), self.tasks)


if __name__ == '__main__':
    PathStr(__file__).uppath(4).chdir_here()
    f = PathStr.new(r'E:\ProgramData\Pycharm\Alasio\alasio\config\alasio\opsi.args.yaml')
    self = GenMsgspec(f)
    self.write()
