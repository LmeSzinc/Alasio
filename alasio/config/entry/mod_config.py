from collections import defaultdict

from msgspec import NODEFAULT, ValidationError, convert
from msgspec.msgpack import encode
from msgspec.structs import asdict
from msgspecerror import get_field_default, get_model_changes, load_msgpack_with_default, parse_msgspec_error

from alasio.base.pretty import pretty_value
from alasio.config.const import DataInconsistent
from alasio.config.entry.mod_base import ModBase
from alasio.config.entry.model import ConfigSetEvent, ModelConfigRef
from alasio.config.table.config import AlasioConfigTable, ConfigRow
from alasio.ext.deep import deep_set
from alasio.logger import logger


def get_field_default_with_error(model, arg):
    try:
        default = get_field_default(model, arg)
    except AttributeError:
        # no default
        default = NODEFAULT
    # note that default might be NODEFAULT
    if default == NODEFAULT:
        raise DataInconsistent(f'Failed to default from {model} field "{arg}"')
    return default


class ModConfig(ModBase):
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
                    data, errors = load_msgpack_with_default(value, model)
                    # for error in errors:
                    #     logger.warning(f'Config data inconsistent at {row.task}.{row.group}: {error}')
                    if data is NODEFAULT:
                        # Failed to convert
                        continue
                # set
                deep_set(out, keys=key, value=asdict(data))

        return out

    def config_batch_set(self, config_name, events, post_edit=False):
        """
        Batch set config.
        If any event failed to validate, entire config_set is consider failed.

        Args:
            config_name (str):
            events (list[ConfigSetEvent]):
            post_edit (bool): True to call post_edit() of validation model

        Returns:
            tuple[bool, list[ConfigSetEvent]]:
                note that the return type of event.value will match the model definition,
                which might differ from input.
                If success, returns True and a list of success_event.
                    - event.error is None
                If failed, returns False and a list of rollback_event.
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
                    raise DataInconsistent(f'Cannot load model for {event.task}.{event.group}, '
                                           f'file="{model_ref.file}", cls="{model_ref.cls}"')
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
                default = get_field_default_with_error(model, arg)
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
        show = [f'{e.task}.{e.group}.{e.arg}={pretty_value(e.value)}' for e in events]
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
                task, group = key
                # parse existing value
                old = dict_old.get(key, b'\x80')
                try:
                    model = dict_model[key]
                except KeyError:
                    # this shouldn't happen, as dict_model is paired with dict_value
                    continue
                obj, errors = load_msgpack_with_default(old, model)
                for error in errors:
                    logger.warning(f'Config data inconsistent at {task}.{group}: {error}')
                if obj is NODEFAULT:
                    raise DataInconsistent(f'Failed to load existing config for {task}.{group}')
                # update new value onto old value
                # note that we merge modification onto existing value
                # meaning that model-level post init validation won't work and each args are separately validated
                obj_old = obj.__copy__()
                for arg, value in new.items():
                    try:
                        setattr(obj, arg, value)
                    except AttributeError:
                        raise DataInconsistent(f'Cannot set attribute {arg} on {model}')
                # call post_edit()
                if post_edit:
                    obj_patch = obj.__copy__()
                    try:
                        obj.post_edit(obj_old, new)
                    except ValidationError as e:
                        # rollback all args from this group
                        for arg in new:
                            default = getattr(obj_old, arg)
                            rollback.append(
                                ConfigSetEvent(task=task, group=group, arg=arg, value=default, error=e))
                        show = [f'{e.task}.{e.group}.{e.arg}={pretty_value(e.value)}' for e in new]
                        logger.info(f'config_set "{config_name}" rejected by post_edit: {", ".join(show)}')
                        break
                    except Exception as e:
                        # wrap internal error
                        raise DataInconsistent(f'Failed to call {model.__name__}.post_edit(): {e}')
                    # log changes and collect post_edit side effects
                    if obj != obj_patch:
                        try:
                            dict_diff = get_model_changes(obj_patch, obj)
                        except (TypeError, AttributeError) as e:
                            logger.error(f'Failed to get model changes: {e}')
                        else:
                            show = [f'{task}.{group}.{arg}={pretty_value(value)}' for arg, value in dict_diff.items()]
                            logger.info(f'config_set "{config_name}" post_edit effect: {show}')
                            for arg, value in dict_diff.items():
                                success.append(ConfigSetEvent(task=task, group=group, arg=arg, value=value))

                data = encode(obj)
                rows.append(ConfigRow(task=task, group=group, value=data))
            # write
            table.upsert_row(rows, conflicts=('task', 'group'), updates='value', _cursor_=c)

        return True, success

    def config_set(self, config_name, event, post_edit=False):
        """
        Set a single config value.
        Optimized for single event without batch overhead.

        Args:
            config_name (str):
            event (ConfigSetEvent): Single event to set
            post_edit (bool): True to call post_edit() of validation model

        Returns:
            tuple[bool, list[ConfigSetEvent]]:
                note that the return type of event.value will match the model definition,
                which might differ from input.
                If success, returns True and a list of success_event.
                    - event.error is None
                If failed, returns False and a list of rollback_event.
                    - event.error is parsed error message
                note that there might be multiple success_event if post_edit has side effect changes
                but there always be one rollback_event

        Raises:
            DataInconsistent:
        """
        task = event.task
        group = event.group

        # get model
        task_index_data = self.task_index_data()
        try:
            model_ref = task_index_data[task].group[group]
        except KeyError:
            logger.warning(f'Cannot set non-exist group {task}.{group}')
            raise DataInconsistent(f'Group {task}.{group} does not exist')

        model = self.get_group_model(file=model_ref.file, cls=model_ref.cls)
        if model is None:
            raise DataInconsistent(f'Cannot load model for {task}.{group}, '
                                   f'file="{model_ref.file}", cls="{model_ref.cls}"')

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
            default = get_field_default_with_error(model, arg)
            rollback = ConfigSetEvent(task=task, group=group, arg=arg, value=default, error=error)
            return False, [rollback]

        # get validated value
        arg_value = getattr(value_obj, event.arg, NODEFAULT)
        if arg_value is NODEFAULT:
            raise DataInconsistent(
                f'Missing arg "{event.arg}" after converting value, value={value}, value_obj={value_obj}')
        success: "list[ConfigSetEvent]" = [
            ConfigSetEvent(task=task, group=group, arg=event.arg, value=arg_value)]

        # init table
        logger.info(f'config_set "{config_name}": {task}.{group}.{event.arg}={pretty_value(event.value)}')
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
            obj, errors = load_msgpack_with_default(old, model)
            for error in errors:
                logger.warning(f'Config data inconsistent at {task}.{group}: {error}')
            if obj is NODEFAULT:
                raise DataInconsistent(f'Failed to load existing config for {task}.{group}')
            obj_old = obj.__copy__()

            # update value
            try:
                setattr(obj, event.arg, arg_value)
            except AttributeError:
                raise DataInconsistent(f'Cannot set attribute {event.arg} on {model}')

            # call post_edit()
            if post_edit:
                obj_patch = obj.__copy__()
                try:
                    obj.post_edit(obj_old, {event.arg: arg_value})
                except ValidationError as e:
                    # rollback all args from this group
                    default = getattr(obj_old, event.arg)
                    rollback = ConfigSetEvent(task=task, group=group, arg=event.arg, value=default, error=e)
                    show = f'{task}.{group}.{event.arg}={pretty_value(event.value)}'
                    logger.info(f'config_set "{config_name}" rejected by post_edit: {show}')
                    return False, [rollback]
                except Exception as e:
                    # wrap internal error
                    raise DataInconsistent(f'Failed to call {model.__name__}.post_edit(): {e}')
                # log changes and collect post_edit side effects
                if obj != obj_patch:
                    try:
                        dict_diff = get_model_changes(obj_patch, obj)
                    except (TypeError, AttributeError) as e:
                        logger.error(f'Failed to get model changes: {e}')
                    else:
                        show = [f'{task}.{group}.{arg}={pretty_value(value)}' for arg, value in dict_diff.items()]
                        logger.info(f'config_set "{config_name}" post_edit effect: {show}')
                        for arg, value in dict_diff.items():
                            success.append(ConfigSetEvent(task=task, group=group, arg=arg, value=value))

            # encode and write
            data = encode(obj)
            row = ConfigRow(task=task, group=group, value=data)
            table.upsert_row([row], conflicts=('task', 'group'), updates='value', _cursor_=c)

        return True, success

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
                data, errors = load_msgpack_with_default(old, model)
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
            data, errors = load_msgpack_with_default(old, model)
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

    def config_group_batch_reset(self, config_name, events):
        """
        Batch reset entire groups and return all args' reset events.
        Reset by writing an empty msgpack map, so they will be filled
        with default values when reading.

        Args:
            config_name (str):
            events (list[ConfigSetEvent]):

        Returns:
            list[ConfigSetEvent]:
                List of reset events with default values.
        """
        # prepare model
        task_index_data = self.task_index_data()

        dict_model = {}
        # key: (task, group), value: None
        dict_groups = {}
        for event in events:
            key = (event.task, event.group)
            if key in dict_groups:
                continue
            dict_groups[key] = None

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

        # init table
        show = [f'{task}.{group}' for task, group in dict_groups]
        logger.info(f'config_group_reset "{config_name}": {", ".join(show)}')
        table = AlasioConfigTable(config_name)

        # write
        success = []
        rows = []
        for key in dict_groups:
            try:
                model = dict_model[key]
            except KeyError:
                continue

            task, group = key
            # construct default model to get default values
            try:
                default_model = model()
            except (TypeError, Exception) as e:
                logger.warning(
                    f'Cannot construct default model for {task}.{group}: {e}')
                continue

            # return events
            default_dict = asdict(default_model)
            for arg, value in default_dict.items():
                success.append(ConfigSetEvent(task=task, group=group, arg=arg, value=value))

            # prepare row
            rows.append(ConfigRow(task=task, group=group, value=b'\x80'))

        if rows:
            with table.exclusive_transaction() as c:
                table.upsert_row(rows, conflicts=('task', 'group'), updates='value', _cursor_=c)

        return success

    def config_group_reset(self, config_name, event):
        """
        Reset an entire group and return all args' reset events.
        Reset by writing an empty msgpack map, so they will be filled
        with default values when reading.

        Args:
            config_name (str):
            event (ConfigSetEvent): Event containing task and group to reset

        Returns:
            list[ConfigSetEvent]:
                List of reset events with default values.
        """
        task, group = event.task, event.group
        # prepare model
        task_index_data = self.task_index_data()
        try:
            model_ref = task_index_data[task].group[group]
        except KeyError:
            logger.warning(f'Cannot reset non-exist group {task}.{group}')
            return []

        # get model
        model = self.get_group_model(file=model_ref.file, cls=model_ref.cls)
        if model is None:
            return []

        # construct default model to get default values
        try:
            default_model = model()
        except (TypeError, Exception) as e:
            logger.warning(
                f'Cannot construct default model for {task}.{group}: {e}')
            return []

        # init table
        logger.info(f'config_group_reset "{config_name}": {task}.{group}')
        table = AlasioConfigTable(config_name)

        # write
        with table.exclusive_transaction() as c:
            row = ConfigRow(task=task, group=group, value=b'\x80')
            table.upsert_row([row], conflicts=('task', 'group'), updates='value', _cursor_=c)

        # return events
        default_dict = asdict(default_model)
        return [
            ConfigSetEvent(task=task, group=group, arg=arg, value=value)
            for arg, value in default_dict.items()
        ]
