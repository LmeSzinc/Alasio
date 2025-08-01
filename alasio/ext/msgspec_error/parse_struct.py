import sys
from typing import ForwardRef, Generic, Type, TypeVar

from msgspec import NODEFAULT, Struct, UNSET, field
from msgspec._core import Factory
from msgspec._utils import _apply_params, _get_class_mro_and_typevar_mappings

from alasio.ext.msgspec_error.parse_type import _eval_type, _forward_ref


def get_field_name(model: "Type[Struct]", encode_name):
    """
    Convert encode name, where you defined with `Field(name=...)`
    to field name (the python attribute name)

    Args:
        model: Subclass of msgspec.Struct
        encode_name (str):

    Returns:
        str: field_name

    Raises:
        AttributeError: if failed
    """
    # 1. Get name lists from magic attributes.
    try:
        field_names = model.__struct_fields__
        encode_names = model.__struct_encode_fields__
    except AttributeError:
        raise AttributeError(f'Type {model} is not a valid msgspec.Struct')

    # 2. Find the index of the target field.
    try:
        idx = encode_names.index(encode_name)
    except ValueError:
        raise AttributeError(f'Type {model} has no field with encode_name="{encode_name}"')

    # 3. Get the Python attribute name (field_name).
    try:
        return field_names[idx]
    except IndexError:
        # this shouldn't happen, __struct_fields__ should have the same length as __struct_encode_fields__
        raise AttributeError(f'Type {model} field_index={idx} is out of __struct_fields__={field_names}')


def get_field_default(model: "Type[Struct]", field_name):
    """
    Get default and default_factory of model.field_name

    Args:
        model: Subclass of msgspec.Struct
        field_name (str):

    Returns:
        tuple[Any | NODEFAULT, Callable | NODEFAULT]:
            default and default_factory, either can be NODEFAULT if field does not have a default

    Raises:
        AttributeError: if failed
    """
    # 1. Get name lists from magic attributes.
    try:
        field_names = model.__struct_fields__
        defaults = model.__struct_defaults__
    except AttributeError:
        raise AttributeError(f'Type {model} is not a valid msgspec.Struct')

    # 2. Find the index of the target field.
    try:
        idx = field_names.index(field_name)
    except ValueError:
        raise AttributeError(f'Type {model} has no field with field_name="{field_name}"')

    num_required = len(field_names) - len(defaults)
    if idx >= num_required:
        # 3. Find field default
        default_idx = idx - num_required
        try:
            default_obj = defaults[default_idx]
        except IndexError:
            # this shouldn't happen, __struct_defaults__ should have corresponding length
            raise AttributeError(f'Type {model} default_idx={default_idx} is out of __struct_defaults__={defaults}')

        if default_obj is NODEFAULT or default_obj is UNSET:
            return NODEFAULT, NODEFAULT
        if isinstance(default_obj, Factory):
            return NODEFAULT, default_obj.factory
        return default_obj, NODEFAULT

    return NODEFAULT, NODEFAULT


def _contains_typevar(tp):
    """
    Recursively check if a type annotation contains a TypeVar.
    """
    if isinstance(tp, TypeVar):
        return True
    args = getattr(tp, '__args__', None)
    if args:
        for arg in args:
            if _contains_typevar(arg):
                return True
    return False


def get_field_typehint(model: "Type[Struct]", field_name):
    """
    Get typehint or annotation of model.field_name

    Args:
        model: Subclass of msgspec.Struct
        field_name (str):

    Returns:
        Any: typehint of model.field_name

    Raises:
        AttributeError: if failed
    """
    mro = model.__mro__

    for cls in mro:
        if cls in (Generic, object):
            continue
        # A classic MRO of msgspec model would be like
        # (<class '__main__.Team'>, <class 'msgspec.Struct'>, <class 'msgspec._core._StructMixin'>, <class 'object'>)
        # Digging into msgspec.Struct won't get more, we just stop
        if cls is Struct:
            break

        try:
            anno = cls.__annotations__.get(field_name, NODEFAULT)
        except AttributeError:
            raise AttributeError(f'Type {model} is not a valid msgspec.Struct')
        if anno is NODEFAULT:
            continue

        # convert forward ref
        if type(anno) is str:
            anno = _forward_ref(anno)
            cls_globals = getattr(sys.modules.get(cls.__module__, None), '__dict__', {})
            anno = _eval_type(anno, cls_globals, {})
        elif isinstance(anno, ForwardRef):
            cls_globals = getattr(sys.modules.get(cls.__module__, None), '__dict__', {})
            anno = _eval_type(anno, cls_globals, {})

        # return directly if it doesn't contain TypeVar
        if not _contains_typevar(anno):
            return anno

        # convert TypeVar
        _, typevar_mappings = _get_class_mro_and_typevar_mappings(model)
        merged_mapping = {
            k: v for d in typevar_mappings.values() for k, v in d.items()
        }
        return _apply_params(anno, merged_mapping)

    raise AttributeError(f'Type {model} has no field with field_name="{field_name}"')
