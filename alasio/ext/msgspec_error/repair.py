from collections import deque
from typing import Any, Dict, Literal, Type

from msgspec import DecodeError, NODEFAULT, ValidationError, convert
from msgspec.inspect import _is_struct
from msgspec.json import Decoder as JsonDecoder, decode as decode_json
from msgspec.msgpack import Decoder as MsgpackDecoder, decode as decode_msgpack

from alasio.ext.msgspec_error.const import ErrorType
from alasio.ext.msgspec_error.error import MsgspecError, parse_msgspec_error
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
    error = MsgspecError(type=ErrorType.WRAPPED_ERROR, loc=(), msg=str(error))
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


def _find_unicode_errors(obj: Any) -> "list[MsgspecError]":
    """
    Iteratively and with maximum efficiency, finds all locations (paths)
    within a Python object that contain a Unicode replacement character.
    This version uses guard clauses and lazy path construction to optimize performance.

    Args:
        obj: The Python object (decoded from JSON) to scan.

    Returns:
        A list of tuples, where each tuple represents the path to an error.
    """
    obj_type = type(obj)

    # --- Guard Clauses for Non-Container Root Objects ---
    # Handle the case where the root object itself is a string.
    if obj_type is str:
        if '\ufffd' in obj:
            error = MsgspecError(
                'Invalid UTF-8 sequence in root',
                type=ErrorType.UNICODE_DECODE_ERROR, loc=())
            return [error]
        else:
            return []

    # If the root object is not a container, there's nothing to traverse.
    if obj_type is not dict and obj_type is not list:
        return []

    # --- Main Iterative Traversal for Containers ---
    # From this point, `obj` is guaranteed to be a dict or a list.
    errors = []
    stack = deque()
    stack.append((obj, ()))
    replacement_char = '\ufffd'

    while 1:
        new_stack = deque()
        for container, path in stack:
            if type(container) is dict:
                for key, value in container.items():
                    value_type = type(value)

                    # Check the dict key
                    if type(key) is str and replacement_char in key:
                        error = MsgspecError(
                            'Invalid UTF-8 sequence in dict key',
                            type=ErrorType.UNICODE_DECODE_ERROR, loc=path + (key,))
                        errors.append(error)

                    # Check the dict value.
                    if value_type is str:
                        if replacement_char in value:
                            error = MsgspecError(
                                'Invalid UTF-8 sequence in dict value',
                                type=ErrorType.UNICODE_DECODE_ERROR, loc=path + (key,))
                            errors.append(error)
                    elif value_type is dict or value_type is list:
                        new_stack.append((value, path + (key,)))
            else:
                # It must be a list, as the stack only contains dicts or lists.
                for i, item in enumerate(container):
                    item_type = type(item)

                    # Check list item
                    if item_type is str:
                        if replacement_char in item:
                            error = MsgspecError(
                                'Invalid UTF-8 sequence in list item',
                                type=ErrorType.UNICODE_DECODE_ERROR, loc=path + (i,))
                            errors.append(error)
                    elif item_type is dict or item_type is list:
                        new_stack.append((item, path + (i,)))

        if new_stack:
            stack = new_stack
        else:
            break

    return errors


def _handle_json_unicode_repair(
        data: bytes,
        utf8_error: "Literal['replace', 'ignore']"
) -> "tuple[bytes | str, list[MsgspecError]]":
    """
    Helper function to repair json data that has unicode error
    """
    if utf8_error == 'replace':
        data = data.decode('utf-8', errors=utf8_error)
        # msgspec allow decoding json in str, so we just keep in str
        # data = data_str.encode('utf-8')
        try:
            raw_obj = decode_json(data)
        except DecodeError:
            return data, []
        return data, _find_unicode_errors(raw_obj)
    elif utf8_error == 'ignore':
        data = data.decode('utf-8', errors=utf8_error)
        return data, []
    else:
        # this shouldn't happen
        return data, []


def load_json_with_default(
        data: bytes,
        model: Any,
        utf8_error: "Literal['strict', 'replace', 'ignore']" = 'replace',
        decoder: JsonDecoder = None,
) -> "tuple[Any, list[MsgspecError]]":
    """
    Decodes bytes, substituting defaults for fields that fail validation or have invalid unicode.

    Args:
        data (bytes): The input bytes to decode.
        model (type): The target type to decode into.
        utf8_error: The error handling scheme to use for the handling of decoding errors.
            - "strict", any UnicodeDecodeError will be treated as root level error and generate a root level default.
                You may lose tons of useful data because of one unicode error.
            - "replace", replace error bytes with � (U+FFFD, '\uFFFD', \xef\xbf\xbd).
                Most data will be preserved, but you may have a U+FFFD in string.
            - "ignore", remove error bytes.
                Most data will be preserved, but you may lose the error data
        decoder: Custom decoder to use

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
    collected_errors = []
    for _ in range(2):
        try:
            # happy path, return directly
            if decoder is None:
                return decode_json(data, type=model), collected_errors
            else:
                return decoder.decode(data), collected_errors
        except ValidationError as e:
            try:
                raw_obj = decode_json(data)
            except DecodeError as error:
                return _handle_root_error(model, error)
            except UnicodeDecodeError as error:
                if utf8_error in ['replace', 'ignore']:
                    data, new_errors = _handle_json_unicode_repair(data, utf8_error)
                    collected_errors.extend(new_errors)
                    continue
                else:
                    return _handle_root_error(model, error)
            # Most errors will enter here
            raw_obj, new_errors = _handle_obj_repair(raw_obj, model, e)
            collected_errors.extend(new_errors)
            return raw_obj, collected_errors
        except DecodeError as error:
            return _handle_root_error(model, error)
        except UnicodeDecodeError as error:
            if utf8_error in ['replace', 'ignore']:
                data, new_errors = _handle_json_unicode_repair(data, utf8_error)
                collected_errors.extend(new_errors)
                continue
            else:
                return _handle_root_error(model, error)

    # this shouldn't happen
    error = RuntimeError(f'Failed to solve UnicodeDecodeError')
    return _handle_root_error(model, error)


def load_msgpack_with_default(
        data: bytes,
        model: Any,
        decoder: MsgpackDecoder = None,
) -> "tuple[Any, list[MsgspecError]]":
    """
    Decodes bytes, substituting defaults for fields that fail validation.
    Note that load_msgpack_with_default can't handle UnicodeDecodeError, will act like utf8_error='strict'

    See load_json_with_default for more info.
    """
    collected_errors = []
    try:
        # happy path, return directly
        if decoder is None:
            return decode_msgpack(data, type=model), collected_errors
        else:
            return decoder.decode(data), collected_errors
    except ValidationError as e:
        try:
            raw_obj = decode_json(data)
        except DecodeError as error:
            return _handle_root_error(model, error)
        except UnicodeDecodeError as error:
            return _handle_root_error(model, error)
        # Most errors will enter here
        raw_obj, new_errors = _handle_obj_repair(raw_obj, model, e)
        collected_errors.extend(new_errors)
        return raw_obj, collected_errors
    except DecodeError as error:
        return _handle_root_error(model, error)
    except UnicodeDecodeError as error:
        return _handle_root_error(model, error)
