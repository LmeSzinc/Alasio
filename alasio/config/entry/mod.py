from collections import defaultdict
from typing import Any, Optional

from msgspec import NODEFAULT, Struct, ValidationError, convert
from msgspec.structs import asdict

from alasio.config.const import DataInconsistent
from alasio.config.entry.const import ModEntryInfo
from alasio.config.entry.model import DECODER_CACHE, MODEL_CONFIG_INDEX, MODEL_TASK_INDEX, ModelConfigRef, ModelGroupRef
from alasio.config.table.config import AlasioConfigTable, ConfigRow
from alasio.ext.deep import deep_set, dict_update
from alasio.ext.file.loadpy import LOADPY_CACHE
from alasio.ext.file.msgspecfile import JsonCacheTTL
from alasio.ext.msgspec_error import MsgspecError, load_msgpack_with_default
from alasio.ext.msgspec_error.error import parse_msgspec_error
from alasio.ext.msgspec_error.parse_struct import get_field_default
from alasio.ext.path import PathStr
from alasio.logger import logger

MOD_JSON_CACHE = JsonCacheTTL()


class ConfigSetEvent(Struct):
    task: str
    group: str
    arg: str
    value: Any
    error: Optional[MsgspecError] = None


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
        file = self.root / file
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
        file = self.root / file
        decoder = DECODER_CACHE.MODEL_DICT_DEPTH3_ANY
        return MOD_JSON_CACHE.get(file, decoder=decoder)

    def task_model_py(self, file):
        """
        Args:
            file (str): relative path to {nav}_model.json
        """
        file = self.root / file
        return LOADPY_CACHE.get(file)

    def _get_task_model(self, model_ref: ModelGroupRef):
        """
        Get msgspec validation model from model_ref

        Returns:
            Type[msgspec.Struct] | None:
        """
        try:
            module = self.task_model_py(model_ref.file)
        except ImportError as e:
            # DataInconsistent error.
            # We just log warnings to reduce runtime crash, missing config will fall back to default
            logger.warning(
                f'DataInconsistent: failed to import model.py "{model_ref.file}": {e}')
            return None
        model = getattr(module, model_ref.cls, None)
        if model is None:
            logger.warning(
                f'DataInconsistent: Missing class "{model_ref.cls}" in "{model_ref.file}"')
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
        table = AlasioConfigTable(config_name)
        rows = table.read_task_rows(tasks=ref.task, groups=ref.group)

        # prepare model and default
        task_index_data = self.task_index_data()
        # key: (task_name, group_name), value: Any
        dict_value = {}
        for task_name, task_info in task_index_data.items():
            for group_name, model_ref in task_info.group.items():
                # skip cross-task ref
                if model_ref.task != task_name:
                    continue

                key = (task_name, group_name)
                # get model
                model = self._get_task_model(model_ref)
                if model is None:
                    continue
                # construct a default value from model
                try:
                    default = model()
                except TypeError:
                    logger.warning(
                        f'DataInconsistent: Class "{model_ref.cls}" in "{model_ref.file}" '
                        f'cannot be default constructed')
                    continue
                except Exception as e:
                    logger.warning(
                        f'DataInconsistent: Class "{model_ref.cls}" in "{model_ref.file}" '
                        f'failed to construct: {e}')
                    continue
                # insert default value
                dict_value[key] = default

        # load config
        for row in rows:
            key = (row.task, row.group)
            default = dict_value.get(key, NODEFAULT)
            if default == NODEFAULT:
                # old group in config db that not being used anymore
                continue
            model = default.__class__
            # validate value
            data, errors = load_msgpack_with_default(row.value, model=model)
            # for error in errors:
            #     logger.warning(f'Config data inconsistent at {row.task}.{row.group}: {error}')
            if data is NODEFAULT:
                # Failed to convert
                continue
            # set
            dict_value[key] = data

        # construct output
        out = {}
        for key, value in dict_value.items():
            value = asdict(value)
            deep_set(out, keys=key, value=value)

        return out

    def config_set(self, config_name, events):
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
            if key in dict_model:
                continue
            try:
                model_ref = task_index_data[event.task].group[event.group]
            except KeyError:
                logger.warning(f'Cannot set non-exist group {event.task}.{event.group}')
                continue
            # get model
            model = self._get_task_model(model_ref)
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
                # this shouldn't happen
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
        del dict_model

        # init table
        show = [f'{e.task}.{e.group}.{e.arg}={e.value}' for e in events]
        logger.info(f'config_set "{config_name}": {", ".join(show)}')
        table = AlasioConfigTable(config_name)
        decode = DECODER_CACHE.MSGPACK_DECODER.decode
        encode = DECODER_CACHE.MSGPACK_ENCODER.encode

        # write after validation
        # so invalid value won't lock up the entire database
        with table.exclusive_transaction() as c:
            # read
            dict_old = {}
            rows = table.read_rows(events, _cursor_=c)
            for row in rows:
                dict_old[(row.task, row.group)] = decode(row.value)
            # update
            rows = []
            for key, value in dict_value.items():
                # update new value onto old value
                # note that extra fields on old value will be kept, we will need cleanup in another way
                old = dict_old.get(key, {})
                value = dict_update(old, value)
                value = encode(value)
                task, group = key
                rows.append(ConfigRow(task=task, group=group, value=value))
            # write
            table.upsert_row(rows, conflicts=('task', 'group'), updates='value', _cursor_=c)

        return True, success
