from collections import defaultdict
from datetime import datetime
from typing import Any, Optional

from msgspec import DecodeError, NODEFAULT, Struct, ValidationError, convert
from msgspec.json import Decoder
from msgspec.msgpack import encode
from msgspec.structs import asdict

from alasio.base.filter import parse_filter
from alasio.config.const import DataInconsistent
from alasio.config.entry.const import ModEntryInfo
from alasio.config.entry.model import DECODER_CACHE, MODEL_CONFIG_INDEX, MODEL_TASK_INDEX, ModelConfigRef
from alasio.config.table.config import AlasioConfigTable, ConfigRow
from alasio.ext.cache import cached_property
from alasio.ext.cache.resource import T
from alasio.ext.deep import deep_set
from alasio.ext.file.loadpy import LOADPY_CACHE
from alasio.ext.file.msgspecfile import JsonCacheTTL
from alasio.ext.msgspec_error import MsgspecError, load_msgpack_with_default
from alasio.ext.msgspec_error.error import parse_msgspec_error
from alasio.ext.msgspec_error.parse_struct import get_field_default
from alasio.ext.path import PathStr
from alasio.logger import logger


class ConfigSetEvent(Struct):
    task: str
    group: str
    arg: str
    value: Any
    error: Optional[MsgspecError] = None


class Task(Struct):
    TaskName: str
    NextRun: datetime


class ModJsonCacheTTL(JsonCacheTTL):
    def load_resource(self, file: str, decoder: Decoder = None, default_factory=dict) -> T:
        try:
            return super().load_resource(file, decoder=decoder)
        except (FileNotFoundError, DecodeError) as e:
            logger.error(f'Failed to read model json: "{file}": {e}')
            return default_factory()
        except Exception as e:
            logger.exception(e)
            return default_factory()


MOD_JSON_CACHE = ModJsonCacheTTL()


class Mod:
    def __init__(self, entry: ModEntryInfo):
        """
        Args:
            entry: Mod entry info
        """
        self.entry = entry
        self.name = entry.name
        self.root = PathStr.new(entry.root)
        self.path_config = self.root.joinpath(entry.path_config)
        self.path_assets = self.root.joinpath(entry.path_assets)

    def __str__(self):
        """
        Mod(name="alasio", root="E:/ProgramData/Pycharm/Alasio/alasio")
        """
        return f'{self.__class__.__name__}(name="{self.name}", root="{self.root.to_posix()}")'

    """
    Index json
    """

    def nav_index_data(self):
        """
        Returns:
            dict[str, dict[str, dict[str, str]]]:
                key: {nav_name}.{card_name}.{lang}
                value: i18n translation
        """
        file = self.path_config / 'nav.index.json'
        decoder = DECODER_CACHE.MODEL_DICT_DEPTH3_ANY
        return MOD_JSON_CACHE.get(file, decoder=decoder)

    def config_index_data(self) -> MODEL_CONFIG_INDEX:
        """
        """
        file = self.path_config / 'config.index.json'
        decoder = DECODER_CACHE.MODEL_CONFIG_INDEX
        return MOD_JSON_CACHE.get(file, decoder=decoder)

    def task_index_data(self) -> MODEL_TASK_INDEX:
        """
        """
        file = self.path_config / 'task.index.json'
        decoder = DECODER_CACHE.MODEL_TASK_INDEX
        return MOD_JSON_CACHE.get(file, decoder=decoder)

    def nav_config_json(self, file):
        """
        Args:
            file (str): relative path to {nav}_config.json

        Returns:
            dict[str, dict[str, dict]]:
                key: {card_name}.{arg_name}
                value:
                    {"group": group, "arg": "_info"} for _info
                    {"task": task, "group": group, "arg": arg, **ArgData.to_dict()} for normal args
                        which is arg path appended with ArgData
        """
        file = self.path_config / file
        decoder = DECODER_CACHE.MODEL_DICT_DEPTH3_ANY
        return MOD_JSON_CACHE.get(file, decoder=decoder)

    def nav_i18n_json(self, file):
        """
        Args:
            file (str): relative path to {nav}_i18n.json

        Returns:
            dict[str, dict[str, dict[str, dict[str, Any]]]]:
                key: {group_name}.{arg_name}.{lang}.{field}
                    where field is "name", "help", "option_i18n", etc.
                value: translation
        """
        file = self.path_config / file
        decoder = DECODER_CACHE.MODEL_DICT_DEPTH3_ANY
        return MOD_JSON_CACHE.get(file, decoder=decoder)

    def get_group_model(self, file, cls):
        """
        Get msgspec validation model

        Args:
            file (str): relative path to {nav}_model.py from path_config
            cls (str): class name

        Returns:
            Type[msgspec.Struct] | None:
        """
        file = self.path_config / file
        try:
            module = LOADPY_CACHE.get(file)
        except ImportError as e:
            # DataInconsistent error.
            # We just log warnings to reduce runtime crash, missing config will fall back to default
            logger.warning(
                f'DataInconsistent: failed to import model.py "{file}": {e}')
            return None
        model = getattr(module, cls, None)
        if model is None:
            logger.warning(
                f'DataInconsistent: Missing class "{cls}" in "{file}"')
            return None
        return model

    """
    Config read/write
    """

    def config_read(self, config_name: str, ref: ModelConfigRef):
        """
        Args:
            config_name:
            ref:

        Returns:
            dict[str, dict[str, dict[str, Any]]]:
                key: task.group.arg, value: arg value
        """
        dict_row = {}
        table = AlasioConfigTable(config_name)
        for row in table.read_task_rows(tasks=ref.task, groups=ref.group):
            dict_row[(row.task, row.group)] = row.value

        # prepare model and default
        task_index_data = self.task_index_data()
        # key: (task_name, group_name), value: Any
        out = {}
        for task_name, task_info in task_index_data.items():
            for group_name, model_ref in task_info.group.items():
                # skip cross-task ref
                if model_ref.task != task_name:
                    continue

                key = (task_name, group_name)
                # get model
                model = self.get_group_model(file=model_ref.file, cls=model_ref.cls)
                if model is None:
                    continue
                value = dict_row.get(key, NODEFAULT)
                if value is NODEFAULT:
                    # construct a default value from model
                    try:
                        data = model()
                    except Exception as e:
                        logger.warning(
                            f'DataInconsistent: Class "{model_ref.cls}" in "{model_ref.file}" '
                            f'failed to construct: {e}')
                        continue
                else:
                    # validate value
                    data, errors = load_msgpack_with_default(value, model=model)
                    # for error in errors:
                    #     logger.warning(f'Config data inconsistent at {row.task}.{row.group}: {error}')
                    if data is NODEFAULT:
                        # Failed to convert
                        continue
                # set
                deep_set(out, keys=key, value=asdict(data))

        return out

    def config_batch_set(self, config_name, events):
        """
        Batch set config.
        If any event failed to validate, entire config_set is consider failed.

        Args:
            config_name (str):
            events (list[ConfigSetEvent]):

        Returns:
            tuple[bool, list[ConfigSetEvent]]:
                note that the return type of event.value will match the model definition,
                which might differ from input.
                If success, returns True and a list of set event.
                    - event.error is None
                If failed, returns False and a list of rollback event.
                    - event.error is parsed error message

        Raises:
            DataInconsistent:
        """
        # prepare model and default
        task_index_data = self.task_index_data()

        dict_model = {}
        dict_value = defaultdict(dict)
        for event in events:
            key = (event.task, event.group)
            if key not in dict_model:
                try:
                    model_ref = task_index_data[event.task].group[event.group]
                except KeyError:
                    logger.warning(f'Cannot set non-exist group {event.task}.{event.group}')
                    continue
                # get model
                model = self.get_group_model(file=model_ref.file, cls=model_ref.cls)
                if model is None:
                    continue
                dict_model[key] = model
            dict_value[key][event.arg] = event.value

        # validate
        rollback: "list[ConfigSetEvent]" = []
        success: "list[ConfigSetEvent]" = []
        for key, value in dict_value.items():
            try:
                model = dict_model[key]
            except KeyError:
                # this shouldn't happen, as dict_model is paired with dict_value
                continue
            task, group = key
            try:
                # may raise error
                value_obj = convert(value, model)
                # note that value type might change after convert
            except ValidationError as e:
                error = parse_msgspec_error(e)
                try:
                    arg = error.loc[0]
                except IndexError:
                    # can't parse
                    raise DataInconsistent(f'Failed to parse error.loc from "{e}"') from None
                try:
                    default = get_field_default(model, arg)
                except AttributeError:
                    # no default
                    default = NODEFAULT
                # note that default might be NODEFAULT
                if default == NODEFAULT:
                    raise DataInconsistent(f'Failed to default from {model} field "{arg}"')
                rollback.append(ConfigSetEvent(task=task, group=group, arg=arg, value=default, error=error))
                continue
            # validate success
            for arg in value:
                arg_value = getattr(value_obj, arg, NODEFAULT)
                if arg_value is NODEFAULT:
                    raise DataInconsistent(
                        f'Missing arg "{arg}" after converting value, value={value}, value_obj={value_obj}')
                success.append(ConfigSetEvent(task=task, group=group, arg=arg, value=arg_value))
            # we will do dict.update later
            dict_value[key] = asdict(value_obj)

        # if any event failed to validate, entire config_set is consider failed
        if rollback:
            return False, rollback

        # init table
        show = [f'{e.task}.{e.group}.{e.arg}={e.value}' for e in events]
        logger.info(f'config_set "{config_name}": {", ".join(show)}')
        table = AlasioConfigTable(config_name)

        # write after validation
        # so invalid value won't lock up the entire database
        with table.exclusive_transaction() as c:
            # read
            dict_old = {}
            rows = table.read_rows(events, _cursor_=c)
            for row in rows:
                key = (row.task, row.group)
                dict_old[key] = row.value
            # update
            rows = []
            for key, new in dict_value.items():
                # parse existing value
                old = dict_old.get(key, b'\x80')
                try:
                    model = dict_model[key]
                except KeyError:
                    # this shouldn't happen, as dict_model is paired with dict_value
                    continue
                data, errors = load_msgpack_with_default(old, model=model)
                # for error in errors:
                #     logger.warning(f'Config data inconsistent at {row.task}.{row.group}: {error}')
                if data is NODEFAULT:
                    # Failed to convert
                    continue
                # update new value onto old value
                # note that we merge modification onto existing value
                # meaning that model-level post init validation won't work and each args are separately validated
                for arg, value in new.items():
                    try:
                        setattr(data, arg, value)
                    except AttributeError:
                        # this shouldn't happen, as arg is validated
                        continue
                task, group = key
                data = encode(data)
                rows.append(ConfigRow(task=task, group=group, value=data))
            # write
            table.upsert_row(rows, conflicts=('task', 'group'), updates='value', _cursor_=c)

        return True, success

    def config_set(self, config_name, event):
        """
        Set a single config value.
        Optimized for single event without batch overhead.

        Args:
            config_name (str):
            event (ConfigSetEvent): Single event to set

        Returns:
            tuple[bool, ConfigSetEvent]:
                If success, returns (True, event) where event.error is None
                If failed, returns (False, rollback_event) where rollback_event.error contains error message

        Raises:
            DataInconsistent:
        """
        # get model
        task_index_data = self.task_index_data()
        try:
            model_ref = task_index_data[event.task].group[event.group]
        except KeyError:
            logger.warning(f'Cannot set non-exist group {event.task}.{event.group}')
            raise DataInconsistent(f'Group {event.task}.{event.group} does not exist')

        model = self.get_group_model(file=model_ref.file, cls=model_ref.cls)
        if model is None:
            raise DataInconsistent(f'Cannot load model for {event.task}.{event.group}')

        # validate
        value = {event.arg: event.value}
        try:
            value_obj = convert(value, model)
        except ValidationError as e:
            error = parse_msgspec_error(e)
            try:
                arg = error.loc[0]
            except IndexError:
                raise DataInconsistent(f'Failed to parse error.loc from "{e}"') from None
            try:
                default = get_field_default(model, arg)
            except AttributeError:
                default = NODEFAULT
            if default == NODEFAULT:
                raise DataInconsistent(f'Failed to default from {model} field "{arg}"')
            rollback = ConfigSetEvent(task=event.task, group=event.group, arg=arg, value=default, error=error)
            return False, rollback

        # get validated value
        arg_value = getattr(value_obj, event.arg, NODEFAULT)
        if arg_value is NODEFAULT:
            raise DataInconsistent(
                f'Missing arg "{event.arg}" after converting value, value={value}, value_obj={value_obj}')

        # init table
        logger.info(f'config_set "{config_name}": {event.task}.{event.group}.{event.arg}={event.value}')
        table = AlasioConfigTable(config_name)

        # write after validation
        with table.exclusive_transaction() as c:
            # read existing data
            rows = table.read_rows([event], _cursor_=c)
            old = b'\x80'  # empty msgpack dict
            for row in rows:
                old = row.value
                break

            # parse existing value
            data, errors = load_msgpack_with_default(old, model=model)
            if data is NODEFAULT:
                raise DataInconsistent(f'Failed to load existing config for {event.task}.{event.group}')

            # update value
            try:
                setattr(data, event.arg, arg_value)
            except AttributeError:
                raise DataInconsistent(f'Cannot set attribute {event.arg} on {model}')

            # encode and write
            data = encode(data)
            row = ConfigRow(task=event.task, group=event.group, value=data)
            table.upsert_row([row], conflicts=('task', 'group'), updates='value', _cursor_=c)

        success_event = ConfigSetEvent(task=event.task, group=event.group, arg=event.arg, value=arg_value)
        return True, success_event

    def config_batch_reset(self, config_name, events):
        """
        Batch reset config args.
        Reset by deleting corresponding keys from messagepack data,
        so they will be filled with default values when reading.

        Args:
            config_name (str):
            events (list[ConfigSetEvent]):

        Returns:
            list[ConfigSetEvent]:
                If success, returns a list of reset events with default values.
                    - event.error is None
                If failed, returns an empty list.
        """
        # prepare model
        task_index_data = self.task_index_data()

        dict_model = {}
        dict_args = defaultdict(list)
        for event in events:
            key = (event.task, event.group)
            if key not in dict_model:
                try:
                    model_ref = task_index_data[event.task].group[event.group]
                except KeyError:
                    logger.warning(f'Cannot reset non-exist group {event.task}.{event.group}')
                    continue
                # get model
                model = self.get_group_model(file=model_ref.file, cls=model_ref.cls)
                if model is None:
                    continue
                dict_model[key] = model
            dict_args[key].append(event.arg)

        # init table
        show = [f'{e.task}.{e.group}.{e.arg}' for e in events]
        logger.info(f'config_reset "{config_name}": {", ".join(show)}')
        table = AlasioConfigTable(config_name)

        # write
        success = []
        with table.exclusive_transaction() as c:
            # read
            dict_old = {}
            rows = table.read_rows(events, _cursor_=c)
            for row in rows:
                key = (row.task, row.group)
                dict_old[key] = row.value

            # update
            rows = []
            for key, args in dict_args.items():
                # parse existing value
                old = dict_old.get(key, b'\x80')
                try:
                    model = dict_model[key]
                except KeyError:
                    # this shouldn't happen, as dict_model is paired with dict_args
                    continue
                data, errors = load_msgpack_with_default(old, model=model)
                if data is NODEFAULT:
                    # Failed to convert, skip this group
                    continue

                # delete args from data
                # construct default model to get default values
                try:
                    default_model = model()
                except (TypeError, Exception) as e:
                    logger.warning(
                        f'Cannot construct default model for {key}: {e}')
                    continue

                # get dict from msgpack struct
                data_dict = asdict(data)
                for arg in args:
                    # delete key from dict
                    try:
                        del data_dict[arg]
                    except KeyError:
                        # arg not in data, already at default
                        pass
                    # get default value for success event
                    default_value = getattr(default_model, arg, NODEFAULT)
                    if default_value is NODEFAULT:
                        logger.warning(
                            f'Cannot get default value for {key}.{arg}')
                        continue
                    task, group = key
                    success.append(ConfigSetEvent(task=task, group=group, arg=arg, value=default_value))

                # convert back to struct and encode
                try:
                    data = convert(data_dict, model)
                except ValidationError as e:
                    logger.warning(
                        f'Failed to convert after reset for {key}: {e}')
                    return []

                task, group = key
                data = encode(data)
                rows.append(ConfigRow(task=task, group=group, value=data))

            # write
            table.upsert_row(rows, conflicts=('task', 'group'), updates='value', _cursor_=c)

        return success

    def config_reset(self, config_name, event):
        """
        Reset a single config value to default.
        Optimized for single event without batch overhead.

        Args:
            config_name (str):
            event (ConfigSetEvent): Single event to reset

        Returns:
            ConfigSetEvent | None:
                If success, returns reset event with default value where event.error is None
                If failed, returns None
        """
        # get model
        task_index_data = self.task_index_data()
        try:
            model_ref = task_index_data[event.task].group[event.group]
        except KeyError:
            logger.warning(f'Cannot reset non-exist group {event.task}.{event.group}')
            return None

        model = self.get_group_model(file=model_ref.file, cls=model_ref.cls)
        if model is None:
            return None

        # init table
        logger.info(f'config_reset "{config_name}": {event.task}.{event.group}.{event.arg}')
        table = AlasioConfigTable(config_name)

        # write
        with table.exclusive_transaction() as c:
            # read existing data
            rows = table.read_rows([event], _cursor_=c)
            old = b'\x80'  # empty msgpack dict
            for row in rows:
                old = row.value
                break

            # parse existing value
            data, errors = load_msgpack_with_default(old, model=model)
            if data is NODEFAULT:
                # Failed to convert, skip
                return None

            # construct default model to get default value
            try:
                default_model = model()
            except (TypeError, Exception) as e:
                logger.warning(
                    f'Cannot construct default model for {event.task}.{event.group}: {e}')
                return None

            # get default value
            default_value = getattr(default_model, event.arg, NODEFAULT)
            if default_value is NODEFAULT:
                logger.warning(
                    f'Cannot get default value for {event.task}.{event.group}.{event.arg}')
                return None

            # delete arg from data
            data_dict = asdict(data)
            try:
                del data_dict[event.arg]
            except KeyError:
                # arg not in data, already at default
                pass

            # convert back to struct and encode
            try:
                data = convert(data_dict, model)
            except ValidationError as e:
                logger.warning(
                    f'Failed to convert after reset for {event.task}.{event.group}: {e}')
                return None

            # encode and write
            data = encode(data)
            row = ConfigRow(task=event.task, group=event.group, value=data)
            table.upsert_row([row], conflicts=('task', 'group'), updates='value', _cursor_=c)

        return ConfigSetEvent(task=event.task, group=event.group, arg=event.arg, value=default_value)

    """
    Scheduler
    """

    @cached_property
    def _dict_task_priority(self) -> "dict[str, int]":
        """
        Returns:
            key: task_name, value: priority, starting from 1, smaller for higher priority
        """
        file = self.path_config / 'const.py'
        try:
            # one-time load, use load_resource instead
            module = LOADPY_CACHE.load_resource(file)
        except ImportError as e:
            # DataInconsistent error.
            # We just log warnings to reduce runtime crash, fallback to no task priority
            logger.warning(
                f'DataInconsistent: failed to import model.py "{file}": {e}')
            return {}
        try:
            priority = module.ConfigConst.SCHEDULER_PRIORITY
        except AttributeError:
            logger.warning(
                f'DataInconsistent: Missing ConfigConst.SCHEDULER_PRIORITY in "{file}"')
            return {}
        return self.parse_scheduler_priority(priority)

    @staticmethod
    def parse_scheduler_priority(priority: str) -> "dict[str, int]":
        priority = parse_filter(priority)
        dict_priority = {}
        for i, task in enumerate(priority, start=1):
            dict_priority[task] = i
        return dict_priority

    def get_task_schedule(
            self,
            config_name,
            dict_row: "dict[tuple[str, str], bytes]" = None,
            dict_task_priority: "dict[str, int]" = None,

    ) -> "tuple[list[Task], list[Task]]":
        """
        Args:
            config_name:
            dict_row:
            dict_task_priority:

        Returns:
            list_pending, list_waiting
        """
        if not dict_row:
            table = AlasioConfigTable(config_name)
            rows: "list[ConfigRow]" = table.select(group='Scheduler')
            dict_row = {}
            for row in rows:
                dict_row[(row.task, row.group)] = row.value

        list_pending = []
        list_waiting = []
        group_name = 'Scheduler'
        if not dict_task_priority:
            dict_task_priority = self._dict_task_priority
        now = datetime.now().astimezone()
        for task_name, task_data in self.task_index_data().items():
            model_ref = task_data.group.get(group_name, None)
            if not model_ref:
                continue
            # skip cross-task ref
            if model_ref.task != task_name:
                continue
            if task_name not in dict_task_priority:
                continue

            model = self.get_group_model(file=model_ref.file, cls=model_ref.cls)
            if model is None:
                continue

            value = dict_row.get((task_name, group_name), NODEFAULT)
            if value is NODEFAULT:
                # construct a default value from model
                try:
                    data = model()
                except Exception as e:
                    logger.warning(
                        f'DataInconsistent: Class "{model_ref.cls}" in "{model_ref.file}" '
                        f'failed to construct: {e}')
                    continue
            else:
                # validate value
                data, errors = load_msgpack_with_default(value, model=model)
                # for error in errors:
                #     logger.warning(f'Config data inconsistent at {row.task}.{row.group}: {error}')
                if data is NODEFAULT:
                    # Failed to convert
                    continue
            # set
            try:
                if not data.Enable:
                    continue
                is_waiting = data.NextRun > now
            except AttributeError:
                # this shouldn't happen, unless Scheduler is not properly defined
                continue
            except TypeError:
                # TypeError: can't compare offset-naive and offset-aware datetimes
                continue
            if is_waiting:
                list_waiting.append(Task(TaskName=task_name, NextRun=data.NextRun))
            else:
                list_pending.append(Task(TaskName=task_name, NextRun=data.NextRun))

        list_waiting.sort(key=lambda x: (x.NextRun, dict_task_priority.get(x.TaskName, 0)))
        list_pending.sort(key=lambda x: dict_task_priority.get(x.TaskName, 0))
        return list_pending, list_waiting
