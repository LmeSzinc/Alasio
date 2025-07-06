from collections import defaultdict
from typing import Any

import msgspec

import alasio.config.entry.const as const
from alasio.ext.deep import deep_get_with_error, deep_iter_depth1, deep_iter_depth2, deep_set, deep_values
from alasio.ext.file.loadpy import loadpy
from alasio.ext.file.msgspecfile import read_msgspec
from alasio.ext.path import PathStr
from alasio.ext.pool import WORKER_POOL, WaitJobsWrapper
from alasio.logger import logger


class GuiData(msgspec.Struct):
    task: str
    group: str
    # data is ArgData, but don't import it im production
    data: Any


class ModelData(msgspec.Struct):
    task: str
    group: str
    # model.py file
    file: str
    # model class
    cls: str


class ModEntryBase:
    def __init__(
            self,
            root,
            gui_index_file='',
            model_index_file='',
    ):
        """
        Args:
            root (PathStr): Absolute path to mod root folder
            gui_index_file (str): relative path from mod root to gui.index.json
            model_index_file (str): relative path from mod root to model.index.json
        """
        self.root: PathStr = root
        self.gui_index_file = gui_index_file
        self.model_index_file = model_index_file

        # Following dicts will be set in rebuild()

        # data of gui.index.json
        # key: {aside}.{display_group}.{display_flatten}
        # value:
        #     - group_file, for basic group, task_name is display_group
        #     - {'task': task_name, 'file': group_file}, if display group from another task
        self.gui_index = {}
        # data of tasks.index.json
        # key: {task_name}.{group_name}
        # value:
        #     - file, for basic group, class_name is group_name
        #     - {'file': file, 'cls': class_name}, if group override
        #     - {'task': task_name}, if reference a group from another task
        self.model_index = {}
        # All {aside}_gui.json
        # key: {filepath}.{group}.{arg}
        # value: ArgData
        self.dict_gui = {}
        # All {aside}_model.py
        # key: {filepath}
        # value: <python_module>
        self.dict_model = {}

    def __str__(self):
        """
        ModAlasio(root="E:/ProgramData/Pycharm/Alasio", aside=2, tasks=2)
        """
        aside = len(self.gui_index)
        tasks = len(self.model_index)
        return f'{self.__class__.__name__}(root="{self.root.to_posix()}", aside={aside}, tasks={tasks})'

    def load_gui_index(self):
        """
        task function for thread pool to load gui.index.json
        """
        if not self.gui_index_file:
            self.gui_index = {}
            return
        file = self.root.joinpath(self.gui_index_file)
        self.gui_index = read_msgspec(file)

    def load_model_index(self):
        """
        task function for thread pool to load tasks.index.json
        """
        if not self.model_index_file:
            self.model_index = {}
            return
        file = self.root.joinpath(self.model_index_file)
        self.model_index = read_msgspec(file)

    def iter_gui_json(self):
        """
        helper function to iter gui json to read

        Returns:
            dict[str, object]:
                key: relative filepath to {aside}.gui.json
                value: meaningless
        """
        data = {}
        for ref in deep_values(self.gui_index, depth=3):
            # reference should be the file itself or have 'file'
            if type(ref) is dict:
                try:
                    file = ref['file']
                except KeyError:
                    continue
            else:
                file = ref
            data[file] = object
        return data

    def iter_model_json(self):
        """
        helper function to iter tasks py to read

        Returns:
            dict[str, object]:
                key: relative filepath to {aside}.tasks.py
                value: meaningless
        """
        data = {}
        for ref in deep_values(self.model_index, depth=2):
            # reference should be the file itself or have 'file'
            if type(ref) is dict:
                try:
                    file = ref['file']
                except KeyError:
                    continue
            else:
                file = ref
            data[file] = object
        return data

    def load_gui_json(self, path):
        """
        Args:
            path (str): relative filepath to {aside}_gui.json
        """
        if not path:
            return
        file = self.root.joinpath(path)
        data = read_msgspec(file)
        self.dict_gui[path] = data

    def load_model_py(self, path):
        """
        Args:
            path (str): relative filepath to {aside}_model.py
        """
        if not path:
            return
        file = self.root.joinpath(path)
        try:
            module = loadpy(file)
        except ImportError as e:
            logger.exception(e)
            return
        self.dict_model[path] = module

    def rebuild_gui(self, pool=None):
        """
        Args:
            pool (WaitJobsWrapper):
        """
        self.load_gui_index()
        self.dict_gui = {}
        if pool is None:
            for path in self.iter_gui_json():
                self.load_gui_json(path)
        else:
            for path in self.iter_gui_json():
                pool.start_thread_soon(self.load_gui_json, path)

    def rebuild_model(self, pool=None):
        """
        Args:
            pool (WaitJobsWrapper):
        """
        self.load_model_index()
        self.dict_model = {}
        if pool is None:
            for path in self.iter_model_json():
                self.load_model_py(path)
        else:
            for path in self.iter_model_json():
                pool.start_thread_soon(self.load_model_py, path)

    def rebuild(self, pool=None, gui=True, model=True):
        """
        Args:
            pool (WaitJobsWrapper):
            gui (bool): True to build gui data
            model (bool): True to build model data
        """
        if pool is None:
            if gui:
                self.rebuild_gui()
            if model:
                self.rebuild_model()
        else:
            if gui:
                pool.start_thread_soon(self.rebuild_gui, pool)
            if model:
                pool.start_thread_soon(self.rebuild_model, pool)


class ModLoader:
    def __init__(self, root, dict_mod_entry=None):
        """
        Args:
            root (PathStr): Absolute path to run path
            dict_mod_entry (dict[str, dict[str, str]]):
                see const.DICT_MOD_ENTRY
        """
        self.root = root
        if dict_mod_entry is None:
            # dynamic use, just maybe someone want to monkeypatch it
            dict_mod_entry = const.DICT_MOD_ENTRY
        self.dict_mod_entry = dict_mod_entry

        # will be set in rebuild()
        self.dict_mod: "dict[str, ModEntryBase]" = {}

    def iter_mod_entry(self):
        """
        Yields:
            tuple[str, ModEntryBase]: name, mod
        """
        for name, info in self.dict_mod_entry.items():
            root = info.get('root', '')
            # check if root exist
            if root:
                root = self.root.joinpath(root)
                if not root.exists():
                    continue
            else:
                # current folder must exist, no need to check
                root = self.root
            # extract info
            gui_index_file = info.get('gui_index_file', 'module/config/gui.index.json')
            model_index_file = info.get('model_index_file', 'module/config/model.index.json')

            # build mod entry
            mod = ModEntryBase(
                root=root,
                gui_index_file=gui_index_file,
                model_index_file=model_index_file,
            )
            yield name, mod

    def rebuild(self, gui=True, model=True):
        """
        Args:
            gui (bool): True to build gui data
            model (bool): True to build model data
        """
        # clear existing build
        self.dict_mod = {}
        # load indexes
        with WORKER_POOL.wait_jobs() as pool:
            for name, mod in self.iter_mod_entry():
                mod.rebuild(pool=pool, gui=gui, model=model)
                self.dict_mod[name] = mod

    def show(self):
        """
        ModManager(root="E:/ProgramData/Pycharm/Alasio", mod=1):
          ModAlasio(root="E:/ProgramData/Pycharm/Alasio", aside=2, tasks=2)

        Returns:
            list[str]:
        """
        mod = len(self.dict_mod)
        lines = [f'{self.__class__.__name__}(root="{self.root.to_posix()}", mod={mod}):']
        for entry in self.dict_mod.values():
            lines.append(f'  {entry}')
            # for k, v in deep_iter(entry.dict_gui, depth=2):
            #     print(k, v)
            # for k, v in deep_iter(entry.dict_py, depth=1):
            #     print(k, v)
        print('\n'.join(lines))
        return lines

    def get_aside(self, mod_name, aside_name):
        """
        Args:
            mod_name:
            aside_name:

        Returns:
            dict[str, dict[str, GuiData]:
                key: {display_group}.{display_flatten}
                value: GuiData
        """
        try:
            mod = self.dict_mod[mod_name]
        except KeyError:
            raise KeyError(f'No such mod: mod="{mod_name}"') from None
        try:
            # key: {task_name}.{group_name}
            # value:
            #     - group_file, for basic group, task_name is display_group
            #     - {'task': task_name, 'file': group_file}, if display group from another task
            gui_data = mod.gui_index[aside_name]
        except KeyError:
            raise KeyError(f'No such aside in gui_index: mod="{mod_name}", aside="{aside_name}"') from None

        # build output
        out = {}
        for display_group, display_flatten, display_ref in deep_iter_depth2(gui_data):
            if type(display_ref) is dict:
                # display group from another task
                try:
                    file = display_ref['file']
                    task = display_ref['task']
                except KeyError:
                    raise KeyError(f'display_ref of gui index has no "file" or "task": mod="{mod_name}", '
                                   f'key="{aside_name}.{display_group}.{display_flatten}"') from None
            else:
                # populate omitted task_name
                task = display_group
                file = display_ref
                # display_ref = {'task': display_group, 'file': display_ref}

            # query full group data
            group = display_flatten
            try:
                group_data = deep_get_with_error(mod.dict_gui, [file, group])
            except KeyError:
                raise KeyError(f'No such group "{group}", mod={mod_name}, file={file}') from None

            # set
            data = GuiData(task=task, group=group, data=group_data)
            deep_set(out, [display_group, display_flatten], data)

        return out

    def get_task(self, mod_name, task_name):
        """
        Args:
            mod_name:
            task_name:

        Returns:
            dict[str, ModelData]:
                key: {group_name}
                value: ModelData
        """
        try:
            mod = self.dict_mod[mod_name]
        except KeyError:
            raise KeyError(f'No such mod: mod="{mod_name}"') from None
        try:
            # key: {group_name}
            # value:
            #     - file, for basic group, class_name is group_name
            #     - {'file': file, 'cls': class_name}, if group override
            #     - {'task': task_name}, if reference a group from another task
            task_data = mod.model_index[task_name]
        except KeyError:
            raise KeyError(f'No such task in model_index: mod="{mod_name}", task="{task_name}"') from None

        # build output
        out = {}
        for group_name, model_ref in deep_iter_depth1(task_data):
            if type(model_ref) is dict:
                task = model_ref.get('task', None)
                if task is None:
                    # override group class
                    task = task_name
                else:
                    # reference group from another task
                    try:
                        model_ref = deep_get_with_error(mod.model_index, [task, group_name])
                    except KeyError:
                        raise KeyError(f'task_ref of model index has no corresponding value: mod="{mod_name}", '
                                       f'key="{task}.{group_name}"') from None
                if type(model_ref) is dict:
                    # override group class
                    try:
                        file = model_ref['file']
                        cls = model_ref['cls']
                    except KeyError:
                        raise KeyError(f'task_ref of model index has no "file" or "cls": mod="{mod_name}", '
                                       f'key="{task_name}.{group_name}"') from None
                else:
                    file = model_ref
                    cls = group_name
            else:
                task = task_name
                file = model_ref
                cls = group_name

            # set
            data = ModelData(task=task, group=group_name, file=file, cls=cls)
            out[group_name] = data

        return out

    @staticmethod
    def get_intask_group(task_data):
        """
        Get a set of intask groups from task_data

        Args:
            task_data (dict):
                key: {group_name}
                value:
                    - file, for basic group, class_name is group_name
                    - {'file': file, 'cls': class_name}, if group override
                    - {'task': task_name}, if reference a group from another task

        Returns:
            set[str]:
        """
        out = set()
        for group_name, group_data in deep_iter_depth1(task_data):
            if type(group_data) is dict:
                if 'task' in group_data:
                    # reference a group from another task
                    continue
                else:
                    out.add(group_name)
            else:
                out.add(group_name)
        return out

    def regroup_task_group(self, mod_name, all_task_groups):
        """
        Regroup task groups to simplify database query condition
        Convert:
            [Alas.Emulator, OpsiDaily.Scheduler, OpsiDaily.OpsiDaily, OpsiGeneral.OpsiGeneral]
        to:
            tasks=[OpsiDaily, OpsiGeneral]
            task_groups=[Alas.Emulator]

        Args:
            mod_name:
            all_task_groups (list[GuiData] | list[ModelData] | iterator[GuiData] | iterator[ModelData]):

        Returns:
            tuple[list[str], list[tuple[str, str]]]: tasks, task_groups
        """
        try:
            mod = self.dict_mod[mod_name]
        except KeyError:
            raise KeyError(f'No such mod: mod="{mod_name}"') from None

        # de-redundancy
        unique_task_group = defaultdict(set)
        for tg in all_task_groups:
            unique_task_group[tg.task].add(tg.group)

        tasks = []
        task_groups = []
        for task_name, group_set in unique_task_group.items():
            try:
                # key: {group_name}
                # value:
                #     - file, for basic group, class_name is group_name
                #     - {'file': file, 'cls': class_name}, if group override
                #     - {'task': task_name}, if reference a group from another task
                task_data = mod.model_index[task_name]
            except KeyError:
                raise KeyError(f'No such task in model_index: mod="{mod_name}", task="{task_name}"') from None

            intask_group = self.get_intask_group(task_data)
            if group_set == intask_group:
                # given `groups` equals intask groups
                tasks.append(task_name)
            else:
                # visit standalone group
                for group_name in group_set:
                    task_groups.append([task_name, group_name])

        return tasks, task_groups


if __name__ == '__main__':
    self = ModLoader(PathStr.new(r'E:\ProgramData\Pycharm\Alasio'))
    self.rebuild()
    for t, g, v in deep_iter_depth2(self.get_aside('alasio', 'opsi')):
        print(t, g, v)
    # for g, v in deep_iter_depth1(self.get_task('alasio', 'OpsiDaily')):
    #     print(g, v)
    all_task_groups = self.get_task('alasio', 'OpsiDaily')
    tasks, task_groups = self.regroup_task_group('alasio', all_task_groups.values())
    print(tasks)
    print(task_groups)
