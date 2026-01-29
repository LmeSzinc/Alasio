from collections import defaultdict
from typing import Any

from msgspec import NODEFAULT, Struct

from alasio.config.const import DataInconsistent
from alasio.config.entry.const import ModEntryInfo
from alasio.config.entry.mod import ConfigSetEvent, Mod, Task
from alasio.config.entry.utils import validate_task_name
from alasio.config.exception import RequestHumanTakeover, TaskEnd
from alasio.config.table.config import AlasioConfigTable, ConfigRow
from alasio.ext.cache import cached_property
from alasio.ext.deep import deep_iter_depth2
from alasio.ext.msgspec_error import load_msgpack_with_default
from alasio.ext.msgspec_error.parse_anno import get_annotations
from alasio.logger import logger


class ModelProxy(Struct):
    """
    A proxy object upon group model to capture property set event
    So you can set property directly and trigger auto save:
        config.Campaign.Name = "12-4"
    """
    _obj: Struct
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


class BatchSetContext:
    def __init__(self, config: "AlasioConfigBase"):
        self.config = config

    def __enter__(self):
        self.config._batch_depth += 1
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.config._batch_depth -= 1
        # Only save if we are back at the top level and auto_save is enabled
        if self.config._batch_depth == 0 and self.config.auto_save:
            self.config.save()


class TemporaryContext:
    def __init__(self, config: "AlasioConfigBase", **kwargs):
        self.config = config
        self.kwargs = kwargs
        self.prev_config = None
        self.prev_const = None

    def __enter__(self):
        self.prev_config, self.prev_const = self.config.override(**self.kwargs)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # rollback
        for key, value in self.prev_const.items():
            self.config._apply_override_const(key, value)
        for group, arg, value in deep_iter_depth2(self.prev_config):
            self.config._apply_override_config(group, arg, value)


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
        # Memory-only overrides. Key: Group.Arg. Value: value
        # These override DB values but are not saved to DB.
        self._override_config: "dict[str, dict[str, Any]]" = defaultdict(dict)
        # Const overrides, consts are in uppercase like "USE_DATA_KEY"
        self._override_const: "dict[str, Any]" = {}

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

    @cached_property
    def _annotations(self):
        return get_annotations(self.__class__)

    @cached_property
    def _annotations_alasio(self):
        from .config_generated import ConfigGenerated
        return get_annotations(ConfigGenerated)

    def init_task(self):
        # clear existing cache
        # Note: self._overrides is persisted across init_task
        for key in self._annotations:
            if key in self.__dict__:
                self.__dict__.pop(key, None)
        self.dict_value.clear()
        self.modified.clear()

        if self.is_template_config:
            return

        table = AlasioConfigTable(self.config_name)
        rows: "list[ConfigRow]" = table.select()

        dict_value = {}
        for row in rows:
            key = (row.task, row.group)
            dict_value[key] = row.value
        self.dict_value = dict_value

        for group, group_ref in self._iter_task_groups(self.task):
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
            if obj is NODEFAULT:
                # Failed to convert
                continue

            # create proxy on groups, so we can catch arg set
            obj = ModelProxy(_obj=obj, _config=self, _task=group_ref.task, _group=group)
            setattr(self, group, obj)
        # Apply config overrides
        for key, value in self._override_const.items():
            self._apply_override_const(key, value)
        for group, arg, value in deep_iter_depth2(self._override_config):
            self._apply_override_config(group, arg, value)

    def _iter_task_groups(self, task):
        """
        Args:
            task (str):

        Yields:
            tuple[str, ModelGroupRef]:
        """
        index = self.mod.task_index_data()

        # iter global bind groups first
        global_bind = index.get('_global_bind', None)
        if global_bind is not None:
            for group, group_ref in global_bind.group.items():
                yield group, group_ref

        # then task groups
        if task:
            if not validate_task_name(task):
                raise KeyError(f'Task name format invalid: "{task}"')
            try:
                task_ref = index[task]
            except KeyError:
                raise KeyError(f'No such task "{task}"')
            for group, group_ref in task_ref.group.items():
                yield group, group_ref

    @staticmethod
    def task_stop(message=''):
        """
        Helper method to stop current task.
        """
        raise TaskEnd(message)

    def get_next_task(self) -> Task:
        pending_task, waiting_task = self.mod.get_task_schedule(self.config_name)
        if pending_task:
            logger.info(f'Pending tasks: {[f.TaskName for f in pending_task]}')
            task = pending_task[0]
            logger.attr('Task', task)
            return task
        if waiting_task:
            logger.info('No task pending')
            task = waiting_task[0]
            logger.attr('Task', task)
            return task
        raise RequestHumanTakeover('No task waiting or pending, please enable at least on task')

    def task_switched(self):
        """
        Check if scheduler needs to switch task.

        Returns:
            bool: If task switched

        Examples:
            if self.config.task_switched():
                # do task specific cleanup
                self.campaign.ensure_auto_search_exit()
                self.config.task_stop()
        """
        # Update event
        # if self.stop_event is not None:
        #     if self.stop_event.is_set():
        #         return True
        prev = self.task
        self.load()
        new = self.get_next_task().TaskName
        if prev == new:
            logger.info(f'Continue task `{new}`')
            return False
        else:
            logger.info(f'Switch task `{prev}` to `{new}`')
            return True

    def _group_construct(self, group) -> Struct:
        """
        Convert group annotation like "opsi.OpsiGeneral", convert to msgspec validation model

        Args:
            group (str):
        """
        anno = self._annotations.get(group)
        if not anno:
            raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{group}'")
        nav, sep, cls = anno.partition('.')
        if not sep:
            raise DataInconsistent(f'Config annotation {self.__class__.__name__}.{group} has no dot "."')

        # get validation model
        if group in self._annotations_alasio:
            file = f'alasio/{nav}_model.py'
        else:
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
                raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{item}'") from None
        except IndexError:
            # this shouldn't happen
            raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{item}'") from None

        obj = self._group_construct(item)
        # no proxy on unbound groups
        setattr(self, item, obj)
        # Apply config overrides
        for group, arg, value in deep_iter_depth2(self._override_config):
            self._apply_overide_config(group, arg, value)
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

    def batch_set(self) -> BatchSetContext:
        """
        Context manager to suppress auto-save for batch modifications.
        Saves are triggered only when the outermost context exits.

        Usage:
            with config.batch_set():
                config.Group.Arg1 = 1
                config.Group.Arg2 = 2
            # Saved here
        """
        return BatchSetContext(self)

    def _apply_override_config(self, group, arg, value):
        """
        Returns:
            Any: prev value, or NODEFAULT if failed
        """
        # get group object, might get a proxy object or init an unbound group
        try:
            obj = getattr(self, group)
        except AttributeError:
            logger.warning(f'Trying to override {group}.{arg}={value} but no such group')
            return NODEFAULT
        # unwrap proxy
        if type(obj) is ModelProxy:
            obj = obj._obj
        # set arg
        try:
            prev = getattr(obj, arg)
        except AttributeError:
            logger.warning(f'Trying to override {group}.{arg}={value} but no such group.arg')
            return NODEFAULT
        try:
            setattr(obj, arg, value)
        except TypeError:
            logger.warning(f'Trying to override {group}.{arg}={value} but no such group.arg')
            return NODEFAULT
        return prev

    def _apply_override_const(self, key, value):
        """
        Returns:
            Any: prev value, or NODEFAULT if failed
        """
        try:
            prev = getattr(self, key)
        except AttributeError:
            logger.warning(f'Trying to override {key}={value} but const not exist')
            return NODEFAULT
        # set key
        setattr(self, key, value)
        return prev

    def override(self, **kwargs):
        """
        Permanently override config values in memory.
        These overrides persist across init_task() calls but are not saved to file.

        Args:
            **kwargs: Key format is Group_Arg=Value, or CONST_NAME=Value

        Returns:
            tuple[dict[str, dict[str, Any]], dict[str, Any]]: prev_config, prev_const

        Usage:
            config.override(
                OpsiGeneral_DoRandomMapEvent=False,
                HOMO_EDGE_DETECT=False,
                STORY_OPTION=0
            )
            # override persists
        """
        prev_config = defaultdict(dict)
        prev_const = {}
        for key, value in kwargs.items():
            if key.isupper():
                # const override
                prev = self._apply_override_const(key, value)
                if prev is NODEFAULT:
                    continue
                self._override_const[key] = value
                prev_const[key] = prev
            else:
                # config override
                group, sep, arg = key.partition('_')
                if not sep:
                    logger.warning(f'Trying to override {key}={value} but format invalid')
                    continue
                prev = self._apply_override_config(group, arg, value)
                if prev is NODEFAULT:
                    continue
                self._override_config[group][arg] = value
                prev_config[group][arg] = prev

        return prev_config, prev_const

    def temporary(self, **kwargs) -> TemporaryContext:
        """
        Temporarily override config values in memory within a context.
        Restores previous values (from DB or previous overrides) upon exit.

        Args:
            **kwargs: Key format is Group_Arg=Value

        Usage:
            with config.temporary(
                OpsiGeneral_DoRandomMapEvent=False,
                HOMO_EDGE_DETECT=False,
                STORY_OPTION=0
            ):
                # override persists
            # override rollback
        """
        return TemporaryContext(self, **kwargs)
