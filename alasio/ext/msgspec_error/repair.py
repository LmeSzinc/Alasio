from typing import Any, Dict, Type

from msgspec import DecodeError, NODEFAULT, ValidationError, convert
from msgspec.inspect import _is_struct
from msgspec.json import decode as decode_json

from alasio.ext.msgspec_error.const import ErrorType
from alasio.ext.msgspec_error.error import ErrorCtx, MsgspecError, parse_msgspec_error
from alasio.ext.msgspec_error.parse_struct import get_field_default, get_field_typehint
from alasio.ext.msgspec_error.parse_type import get_default, origin_args


def _repair_once(
        raw_obj, model, error: MsgspecError
) -> "tuple[Any, MsgspecError | Type[NODEFAULT]]":
    """
    Args:
        raw_obj:
        model:
        error:

    Returns:
        tuple[Any, MsgspecError | _NoDefault]: (obj, error) the repaired object and fixed error.
            If repair failed, obj will be NODEFAULT.
            If this error is not consider as an error, error will be NODEFAULT and won't be collected.
    """
    # handle root error
    if not error.loc:
        value = get_default(model)
        return value, error

    # handle errors in deep path
    obj = raw_obj
    list_loc = [loc for loc in error.loc]
    last_index = len(list_loc) - 1
    for i, part in enumerate(list_loc):
        is_last = i == last_index

        model_origin, model_args = origin_args(model)

        # 1. Invalid dict value
        # msgspec does not tell which key is invalid, just giving placeholder '...', we need to find the exact key
        if part == '...':
            if type(obj) is not dict:
                # Error path `...` implies a dict, but obj doesn't match.
                return NODEFAULT, error

            # Find the specific key at this level that causes the failure.
            try:
                child_model = model_args[1]
            except IndexError:
                # this shouldn't happen because a dict typehint should have a key and a value
                return NODEFAULT, error
            for key, value in obj.items():
                try:
                    # Attempt to convert just the value to see if it's the source.
                    convert(value, type=child_model)
                except ValidationError:
                    break
            else:
                # Could not identify the failing key. Unrecoverable.
                return NODEFAULT, error

            # fix loc
            loc = error.loc
            error.loc = loc[:i] + (key,) + loc[i + 1:]
            if is_last:
                # fix obj
                value = get_default(child_model)
                if value is NODEFAULT:
                    return NODEFAULT, error
                obj[key] = value
                return raw_obj, error
            else:
                # go deeper
                try:
                    obj = obj[key]
                except KeyError:
                    # this should happen, unless raw_obj and error.loc don't match
                    return NODEFAULT, error
                model = child_model
                continue

        # 2. Invalid dict key
        # '...key' is our special defined key to distinguish type error on key and on value
        if part == '...key':
            if type(obj) is not dict:
                # Error path `...` implies a dict, but obj doesn't match.
                return NODEFAULT, error

            # Find the specific key that causes the failure
            try:
                key_model = model_args[0]
            except IndexError:
                # this shouldn't happen because a dict typehint should have a key and a value
                return NODEFAULT, error
            for key in obj.keys():
                try:
                    convert(key, key_model)
                except ValidationError:
                    break
            else:
                # Could not identify the failing key. Unrecoverable.
                return NODEFAULT, error

            # fix loc
            loc = error.loc
            error.loc = loc[:i] + (key,) + loc[i + 1:]

            if is_last:
                # fix obj
                try:
                    value = obj.pop(key)
                except KeyError:
                    # this should happen, unless raw_obj and error.loc don't match
                    return NODEFAULT, error
                # here comes something tricky
                # In `msgspec.json.decode(..., Dict[int, int])`,
                #   the output key will be int or any other depending on your type
                # If `msgspec.json.decode(...)`, the output key will always be string,
                #   because json key can only be string
                # We create a temp dict and use `str_keys=True`, fooling msgspec to encode str to target type
                temp_model = Dict[key_model, int]
                temp_dict = {key: 1}
                try:
                    temp_dict = convert(temp_dict, temp_model, str_keys=True)
                except ValidationError:
                    pass
                else:
                    # set validated key to object
                    for temp_key in temp_dict:
                        obj[temp_key] = value
                        # we don't consider this an error, it's not a user input error
                        return raw_obj, NODEFAULT
                # failed to convert, key is removed as a fix
                return raw_obj, error
            else:
                # this shouldn't happen because '...key' should be the last key,
                # since json/msgpack key can't be objects.
                return NODEFAULT, error

        # 3. Invalid item in list
        if type(part) is int:
            if type(obj) is not list:
                # Error path like `0` implies a list, but obj doesn't match.
                return NODEFAULT, error
            if is_last:
                # fix obj, just pop the item
                try:
                    obj.pop(part)
                except IndexError:
                    # this should happen, unless raw_obj and error.loc don't match
                    return NODEFAULT, error
                return raw_obj, error
            else:
                # go deeper
                try:
                    obj = obj[part]
                except IndexError:
                    # this should happen, unless raw_obj and error.loc don't match
                    return NODEFAULT, error
                model = model_args[0]
                continue

        # 4. Invalid object field
        if _is_struct(model):
            if type(obj) is not dict:
                # Error path like `0` implies a list, but obj doesn't match.
                return NODEFAULT, error
            if is_last:
                # fix obj
                # try field default first
                default, default_factory = get_field_default(model, part)
                if default is not NODEFAULT:
                    value = default
                elif default_factory is not NODEFAULT:
                    try:
                        value = default_factory()
                    except Exception:
                        # Failed to run default_factory
                        value = NODEFAULT
                else:
                    value = NODEFAULT
                # if field doesn't have a default, try to get from type
                if value is NODEFAULT:
                    child_model = get_field_typehint(model, part)
                    value = get_default(child_model)
                    # still no default?
                    if value is NODEFAULT:
                        return NODEFAULT, error
                obj[part] = value
                return raw_obj, error
            else:
                # go deeper
                obj = obj[part]
                model = get_field_typehint(model, part)
                continue

        # 5. Fallback
        # This shouldn't happen unless raw_obj, model, error don't match
        # If it really happens, we trust `model` and do the best we can
        value = get_default(model)
        return value, error


def _handle_obj_repair(
        raw_obj, model, error: ValidationError
) -> "tuple[Any, list[MsgspecError]]":
    """
    Args:
        raw_obj (Any):
        model (type): The target type to decode into.
        error (ValidationError):

    Returns:
        tuple[any, list[MsgspecError]]:
    """
    error = parse_msgspec_error(error)
    collected_errors = []
    seen_errors = set()
    while 1:
        # repair once
        raw_error = error
        raw_obj, error = _repair_once(raw_obj, model, error)
        print(raw_obj, error)
        if error is NODEFAULT:
            # don't collect this error
            if raw_obj is NODEFAULT:
                # repair failed
                return NODEFAULT, collected_errors
            error_info = (raw_error.loc, raw_error.msg)
        else:
            collected_errors.append(error)
            if raw_obj is NODEFAULT:
                # repair failed
                return NODEFAULT, collected_errors
            error_info = (error.loc, error.msg)

        # check deadlock
        if error_info in seen_errors:
            # We are in a loop, probably because the field default doesn't match custom post init validation
            return NODEFAULT, collected_errors

        # try if all repaired
        try:
            return convert(raw_obj, model), collected_errors
        except ValidationError as e:
            error = parse_msgspec_error(e)

        # record this error and go on next try
        seen_errors.add(error_info)


def _handle_root_error(
        model: Any, error: Exception
) -> "tuple[Any, list[MsgspecError]]":
    """
    Args:
        model (type): The target type to decode into.
        error:

    Returns:
        tuple[any, list[MsgspecError]]:
    """
    error = MsgspecError(
        type=ErrorType.WRAPPED_ERROR, loc=(), msg=str(error), ctx=ErrorCtx()
    )
    raw_obj, error = _repair_once({}, model, error)
    if error is NODEFAULT:
        errors = []
    else:
        errors = [error]

    # try if all repaired
    if raw_obj is NODEFAULT:
        return NODEFAULT, errors
    try:
        return convert(raw_obj, model), errors
    except ValidationError as e:
        errors.append(parse_msgspec_error(e))
        return NODEFAULT, errors


def load_json_with_default(
        data, model
) -> "tuple[Any, list[MsgspecError]]":
    """
    Decodes bytes, substituting defaults for fields that fail validation.

    Args:
        data (bytes): The input bytes to decode.
        model (type): The target type to decode into.

    Returns:
        tuple[any, list[MsgspecError]]: (result, errors) validated result and a list of collected errors
            If validate failed and model (or any deeply nested field) can't be default constructed,
            (e.g. model has a required field) result would be NODEFAULT
            Note that it's function caller's duty to check if result is NODEFAULT,
                and decide whether to generate a default or raise error.

    Examples:
        class SimpleStruct(Struct):
            a: int
            b: str
        class NestedStruct(Struct):
            s: SimpleStruct
            c: int
        data = b'{"s": {"a": "bad-int", "b": "good"}, "c": 123}'
        result, errors = load_json_with_default(data, NestedStruct, guess_default=True)
        print(result)
        # NestedStruct(s=SimpleStruct(a=0, b='good'), c=123)
        print(errors)
        # [MsgspecError(msg='Expected `int`, got `str` - at `$.s.a`',
        # type=<ErrorType.TYPE_MISMATCH>, loc=('s', 'a'), ctx=ErrorCtx())]
    """
    try:
        return decode_json(data, type=model), []
    except ValidationError as e:
        try:
            raw_obj = decode_json(data)
        except (DecodeError, UnicodeDecodeError) as error:
            return _handle_root_error(model, error)
        return _handle_obj_repair(raw_obj, model, e)
    except (DecodeError, UnicodeDecodeError) as error:
        return _handle_root_error(model, error)
