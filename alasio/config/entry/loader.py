import alasio.config.entry.const as const
from alasio.ext.deep import deep_values
from alasio.ext.file.loadpy import loadpy
from alasio.ext.file.msgspecfile import read_msgspec
from alasio.ext.path import PathStr
from alasio.ext.pool import WORKER_POOL, WaitJobsWrapper
from alasio.logger import logger


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
        #     {display_group} startswith "g_" and {display_flatten} startswith "f_"
        # value: {'task': task, 'file': gui_file, 'path': path}
        self.gui_index = {}
        # data of tasks.index.json
        # key: {task_name}.{group_name}
        # value: {'file': file, 'cls': class_name}
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
            # reference should have 'file'
            try:
                file = ref['file']
            except KeyError:
                continue
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
            # reference should have 'file'
            try:
                file = ref['file']
            except KeyError:
                continue
            data[file] = object
        return data

    def load_gui_json(self, path):
        """
        Args:
            path (str): relative filepath to {aside}_gui.json
        """
        if not path:
            return
        path = self.root.joinpath(path)
        data = read_msgspec(path)
        self.dict_gui[path] = data

    def load_model_py(self, path):
        """
        Args:
            path (str): relative filepath to {aside}_model.py
        """
        if not path:
            return
        path = self.root.joinpath(path)
        try:
            module = loadpy(path)
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
