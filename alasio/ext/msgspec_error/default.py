from msgspec import DecodeError, NODEFAULT, ValidationError, convert
from msgspec.inspect import DictType, FrozenSetType, ListType, SetType, StructType, TupleType, VarTupleType, type_info
from msgspec.json import decode as decode_json
from msgspec.msgpack import decode as decode_msgpack

from alasio.ext.msgspec_error.const import ErrorType
from alasio.ext.msgspec_error.error import ErrorCtx, MsgspecError, parse_msgspec_error


def load_json_with_default(data, model):
    """
    Decodes bytes, substituting defaults for fields that fail validation.

    This function prioritizes performance for valid data. If a ValidationError
    occurs, it enters an iterative repair loop. If a DecodeError or
    UnicodeDecodeError occurs, it treats this as a root-level failure and
    attempts to substitute a default for the entire model.

    Args:
        data (bytes): The input bytes to decode.
        model (type): The target type to decode into.

    Returns:
        tuple[any, list[MsgspecError]]: A tuple containing the validated result
            and a list of errors that were corrected.

    Raises:
        msgspec.ValidationError: If a validation error occurs on an item
            that has no default value.
        msgspec.DecodeError: If the input data is malformed and the target
            model cannot be default-constructed.
        UnicodeDecodeError: If the input data has a unicode error and the
            target model cannot be default-constructed.
    """
    try:
        return decode_json(data, type=model), []
    except ValidationError as e:
        # A field is invalid, but the overall structure is valid JSON.
        # Enter the iterative repair process.
        try:
            raw_obj = decode_json(data)
        except (DecodeError, UnicodeDecodeError):
            # This can happen if the initial validation found a structural
            # error that the generic decoder also chokes on. Treat it as
            # a root-level decode error.
            return _handle_root_decode_error(model, e)
        return _repair_and_convert(raw_obj, model, e)
    except (DecodeError, UnicodeDecodeError) as e:
        # The data is not valid JSON/MsgPack or has a unicode error.
        # Attempt to substitute the entire object.
        return _handle_root_decode_error(model, e)


def load_msgpack_with_default(data, model):
    """
    Decodes bytes, substituting defaults for fields that fail validation.
    See load_json_with_default for more info

    Args:
        data (bytes): The input bytes to decode.
        model (type): The target type to decode into.

    Returns:
        tuple[any, list[MsgspecError]]: A tuple containing the validated result
            and a list of errors that were corrected.

        Raises:
        msgspec.ValidationError: If a validation error occurs on an item
            that has no default value.
        msgspec.DecodeError: If the input data is malformed and the target
            model cannot be default-constructed.
        UnicodeDecodeError: If the input data has a unicode error and the
            target model cannot be default-constructed.
    """
    try:
        return decode_msgpack(data, type=model), []
    except ValidationError as e:
        # A field is invalid, but the overall structure is valid JSON.
        # Enter the iterative repair process.
        try:
            raw_obj = decode_msgpack(data)
        except (DecodeError, UnicodeDecodeError):
            # This can happen if the initial validation found a structural
            # error that the generic decoder also chokes on. Treat it as
            # a root-level decode error.
            return _handle_root_decode_error(model, e)
        return _repair_and_convert(raw_obj, model, e)
    except (DecodeError, UnicodeDecodeError) as e:
        # The data is not valid JSON/MsgPack or has a unicode error.
        # Attempt to substitute the entire object.
        return _handle_root_decode_error(model, e)


def _handle_root_decode_error(model, error):
    """Handles a root-level decode error by trying to use a model default.

    Args:
        model (type): The target type to construct.
        error (Exception): The original DecodeError or UnicodeDecodeError.

    Returns:
        tuple[any, list[MsgspecError]]: The default-constructed model and the
            error object.

    Raises:
        Exception: Re-raises the original error if the model cannot be
            default-constructed.
    """
    default_value = _find_field_default(model, ())
    if default_value is NODEFAULT:
        raise error  # Re-raise original error if unrecoverable

    # Create an error object representing this root-level failure.
    err_obj = MsgspecError(
        type=ErrorType.WRAPPED_ERROR, loc=(), msg=str(error), ctx=ErrorCtx()
    )
    return default_value, [err_obj]


def _repair_and_convert(raw_obj, model, original_error):
    """Helper to perform the iterative repair loop for ValidationErrors.

    Args:
        raw_obj (any): The raw, decoded Python object.
        model (type): The target type to convert to.
        original_error (msgspec.ValidationError): The first error encountered.

    Returns:
        tuple[any, list[MsgspecError]]: The repaired result and a list of
            corrected errors.
    """
    collected_errors = []
    last_error_msg = None

    while True:
        try:
            result = convert(raw_obj, type=model)
            return result, collected_errors
        except ValidationError as e:
            current_error_msg = str(e)
            if current_error_msg == last_error_msg:
                raise original_error

            last_error_msg = current_error_msg
            parsed_error = parse_msgspec_error(current_error_msg)
            loc = parsed_error.loc

            default_value = _find_field_default(model, loc)

            if default_value is NODEFAULT:
                raise original_error

            default_as_raw = convert(default_value, object)

            if not loc:
                raw_obj = default_as_raw
            else:
                _set_value_at_path(raw_obj, loc, default_as_raw)

            collected_errors.append(parsed_error)


def _find_field_default(model_type, loc):
    """Inspects a type to find the default value for an item at a given path.

    Args:
        model_type (type): The top-level type to inspect.
        loc (tuple[str | int, ...]): The path to the item with an error.

    Returns:
        any: The default value for the item, or `msgspec.NODEFAULT` if none exists.
    """
    current_info = type_info(model_type)

    if not loc:
        # Find a default for the type itself.
        if isinstance(current_info, StructType):
            try:
                return current_info.cls()
            except TypeError:
                return NODEFAULT
        elif isinstance(current_info, ListType):
            return list()
        elif isinstance(current_info, SetType):
            return set()
        elif isinstance(current_info, FrozenSetType):
            return frozenset()
        elif isinstance(current_info, (TupleType, VarTupleType)):
            return tuple()
        elif isinstance(current_info, DictType):
            return dict()
        return NODEFAULT

    # Traverse the info objects according to the path.
    for part in loc[:-1]:
        if isinstance(current_info, (ListType, VarTupleType, SetType, FrozenSetType)):
            current_info = current_info.item_type
        elif isinstance(current_info, TupleType):
            if isinstance(part, int) and part < len(current_info.item_types):
                current_info = current_info.item_types[part]
            else:
                return NODEFAULT
        elif isinstance(current_info, DictType):
            current_info = current_info.value_type
        elif isinstance(current_info, StructType):
            if not isinstance(part, str):
                return NODEFAULT
            field_found = False
            for field in current_info.fields:
                if field.name == part or field.encode_name == part:
                    current_info = field.type
                    field_found = True
                    break
            if not field_found:
                return NODEFAULT
        else:
            return NODEFAULT

    # At the parent, find the default for the final part of the path.
    last_part = loc[-1]
    if isinstance(current_info, StructType) and isinstance(last_part, str):
        for field in current_info.fields:
            if field.name == last_part or field.encode_name == last_part:
                if field.default_factory is not NODEFAULT:
                    return field.default_factory()
                return field.default

    return NODEFAULT


def _set_value_at_path(obj, loc, value):
    """Sets a value deep within a nested object at the specified path.

    Args:
        obj (any): A mutable object (dict or list) to modify.
        loc (tuple[str | int, ...]): The path to the item to set.
        value (any): The value to set at the specified location.
    """
    current = obj
    for i, part in enumerate(loc[:-1]):
        if isinstance(part, int):
            while len(current) <= part:
                current.append(None)
            if current[part] is None and i + 1 < len(loc) and isinstance(loc[i + 1], str):
                current[part] = {}
            current = current[part]
        else:
            current = current.setdefault(part, {})

    last_part = loc[-1]
    if isinstance(last_part, int):
        while len(current) <= last_part:
            current.append(None)
        current[last_part] = value
    else:
        current[last_part] = value
