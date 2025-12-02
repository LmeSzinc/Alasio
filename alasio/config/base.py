from contextlib import contextmanager

import msgspec

from alasio.config.const import DataInconsistent
from alasio.config.entry.const import ModEntryInfo
from alasio.config.entry.mod import ConfigSetEvent, Mod
from alasio.config.table.config import AlasioConfigTable, ConfigRow
from alasio.ext.msgspec_error import load_msgpack_with_default
from alasio.logger import logger


class ModelProxy(msgspec.Struct):
    """
    A proxy object upon group model to capture property set event
    So you can set property directly and trigger auto save:
        config.Campaign.Name = "12-4"
    """
    _obj: msgspec.Struct
    _config: "AlasioConfigBase"
    _task: str
    _group: str

    def __getattr__(self, item):
        # proxy attribute access
        return getattr(self._obj, item)

    def __setattr__(self, key, value):
        obj = self._obj
        setattr(obj, key, value)
        # register modify
        self._config.register_modify(task=self._task, group=self._group, arg=key, value=value)

    def __repr__(self):
        return repr(self._obj)

    def __str__(self):
        return str(self._obj)


class AlasioConfigBase:
    """

    """
    # subclasses must override this
    entry: ModEntryInfo

    def __init__(self, config_name, task=''):
        """
        Args:
            config_name (str): config name
            task (str): task name
        """
        # This will read ./config/{config_name}.db
        self.config_name = config_name
        try:
            entry = self.__class__.entry
        except AttributeError:
            raise DataInconsistent(f'{self.__class__.__name__} has no "entry" defined') from None
        self.mod = Mod(entry)
        # Task to run and bind.
        # Task means the name of the function to run in AzurLaneAutoScript class.
        self.task = task
        # A snapshot of config table, updated on task start and every init_task() call.
        # Key: (task, group). Value: group data in messagepack
        # We don't update config realtime, otherwise things will get really complex if user modify configs
        # when task is running.
        self.dict_value: "dict[tuple[str, str], bytes]" = {}
        # Modified configs. Key: (task, group, arg). Value: ConfigSetEvent.
        # All variable modifications will be record here and saved in method `save()`.
        self.modified: "dict[tuple[str, str, str], ConfigSetEvent]" = {}
        # If write after every variable modification.
        self.auto_save = True
        # Batch depth counter. 0 means immediate save mode.
        self._batch_depth = 0
        # Template config is used for dev tools
        self.is_template_config = config_name.startswith("template")
        if self.is_template_config:
            # For dev tools
            logger.info("Using template config, which is read only")
            self.auto_save = False

        self.init_task()

    def init_task(self):
        # clear existing cache
        for key in self.__class__.__annotations__:
            if key in self.__dict__:
                self.__dict__.pop(key, None)
        self.dict_value.clear()
        self.modified.clear()

        if not self.task:
            return
        if self.is_template_config:
            return

        index = self.mod.task_index_data()
        try:
            task_ref = index[self.task]
        except KeyError:
            raise KeyError(f'No such task "{self.task}"')

        table = AlasioConfigTable(self.config_name)
        rows: "list[ConfigRow]" = table.select()

        dict_value = {}
        for row in rows:
            key = (row.task, row.group)
            dict_value[key] = row.value
        self.dict_value = dict_value

        for group, group_ref in task_ref.group.items():
            key = (group_ref.task, group)
            model = self.mod.get_group_model(file=group_ref.file, cls=group_ref.cls)
            if model is None:
                # DataInconsistent error, ignore to reduce runtime crash
                continue
            # using dict.get() as user config is more likely to be the same as default
            # b'\x80' is {} in messagepack
            value = dict_value.get(key, b'\x80')
            obj, errors = load_msgpack_with_default(value, model=model)
            # for error in errors:
            #     logger.warning(f'Config data inconsistent at {row.task}.{row.group}: {error}')
            if obj is msgspec.NODEFAULT:
                # Failed to convert
                continue

            # create proxy on groups, so we can catch arg set
            obj = ModelProxy(_obj=obj, _config=self, _task=group_ref.task, _group=group)
            setattr(self, group, obj)

    def _group_construct(self, group) -> "msgspec.Struct":
        """
        Convert group annotation like "opsi.OpsiGeneral", convert to msgspec validation model

        Args:
            group (str):
        """
        anno = self.__class__.__annotations__.get(group)
        if not anno:
            raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{group}'")
        nav, sep, cls = anno.partition('.')
        if not sep:
            raise DataInconsistent(f'Config annotation {self.__class__.__name__}.{group} has no dot "."')

        # get validation model
        file = f'{nav}/{nav}_model.py'
        model = self.mod.get_group_model(file, cls)
        if model is None:
            # details are logged
            raise DataInconsistent('Failed to load group model')

        try:
            obj = model()
        except TypeError:
            raise DataInconsistent(f'Class "{cls}" in "{file}" cannot be default constructed') from None

        return obj

    def register_modify(self, task, group, arg, value):
        """
        Callback function when arg gets modified

        Args:
            task (str):
            group (str):
            arg (str):
            value:
        """
        event = ConfigSetEvent(task=task, group=group, arg=arg, value=value)
        self.modified[(task, group, arg)] = event

        if self.auto_save and self._batch_depth == 0:
            self.save()

    def __getattr__(self, item):
        """
        A fallback to access unbound groups.
        Default values are available but set event will be ignored
        """
        try:
            if not item[0].isupper():
                # not a group access
                raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{item}'")
        except IndexError:
            # this shouldn't happen
            raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{item}'")

        obj = self._group_construct(item)
        # no proxy on unbound groups
        setattr(self, item, obj)
        return obj

    def save(self):
        """
        Save modifications, No need to call this method manually.
        Modifications are auto saved when you do:
            config.OpsiFleet.Fleet = 1
        """
        if not self.modified:
            return False

        events = list(self.modified.values())
        self.modified.clear()
        if len(events) == 1:
            # single set event
            self.mod.config_set(self.config_name, events[0])
        else:
            # batch set
            self.mod.config_batch_set(self.config_name, events)

    @contextmanager
    def batch_set(self):
        """
        Context manager to suppress auto-save for batch modifications.
        Saves are triggered only when the outermost context exits.

        Usage:
            with config.batch_set():
                config.Group.Arg1 = 1
                config.Group.Arg2 = 2
            # Saved here
        """
        self._batch_depth += 1
        try:
            yield
        finally:
            self._batch_depth -= 1
            # Only save if we are back at the top level and auto_save is enabled
            if self._batch_depth == 0 and self.auto_save:
                self.save()
