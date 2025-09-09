import datetime
import decimal
import sys
import typing
import uuid
from typing import Any, TypeVar, Union

import msgspec
from msgspec import NODEFAULT, UnsetType
from msgspec._utils import _CONCRETE_TYPES
from msgspec.inspect import _is_attrs, _is_dataclass, _is_enum, _is_namedtuple, _is_struct, _is_typeddict
from typing_extensions import Literal

# Backport typing alias in msgspec._utils, so we can keep supporting py3.8
# <-- Start of the copied code -->
try:
    from types import UnionType as _types_UnionType  # type: ignore
except Exception:
    _types_UnionType = type("UnionType", (), {})  # type: ignore

try:
    from typing import Final, TypeAliasType as _TypeAliasType  # type: ignore
except Exception:
    _TypeAliasType = type("TypeAliasType", (), {})  # type: ignore

try:
    from typing_extensions import _AnnotatedAlias
except Exception:
    try:
        from typing import _AnnotatedAlias
    except Exception:
        _AnnotatedAlias = None

# The `is_class` argument was new in 3.11, but was backported to 3.9 and 3.10.
# It's _likely_ to be available for 3.9/3.10, but may not be. Easiest way to
# check is to try it and see.
try:
    typing.ForwardRef("Foo", is_class=True)
except TypeError:

    def _forward_ref(value):
        return typing.ForwardRef(value, is_argument=False)

else:

    def _forward_ref(value):
        return typing.ForwardRef(value, is_argument=False, is_class=True)

# Python 3.13 adds a new mandatory type_params kwarg to _eval_type
if sys.version_info >= (3, 13):

    def _eval_type(t, globalns, localns):
        return typing._eval_type(t, globalns, localns, ())
elif sys.version_info < (3, 10):

    def _eval_type(t, globalns, localns):
        try:
            return typing._eval_type(t, globalns, localns)
        except TypeError as e:
            try:
                from eval_type_backport import eval_type_backport
            except ImportError:
                raise TypeError(
                    f"Unable to evaluate type annotation {t.__forward_arg__!r}. If you are making use "
                    "of the new typing syntax (unions using `|` since Python 3.10 or builtins subscripting "
                    "since Python 3.9), you should either replace the use of new syntax with the existing "
                    "`typing` constructs or install the `eval_type_backport` package."
                ) from e

            return eval_type_backport(
                t,
                globalns,
                localns,
                try_default=False,
            )
else:
    _eval_type = typing._eval_type


# <-- End of the copied code -->

def origin_args(t):
    """
    A simplified version of msgspec.inspect._origin_args_metadata
    Get origin and args of given typehint.
    """
    # Strip wrappers (Annotated, NewType, Final) until we hit a concrete type
    while True:
        try:
            origin = _CONCRETE_TYPES.get(t)
            if origin is not None:
                args = None
                break
        except TypeError:
            # t is not hashable
            pass

        origin = getattr(t, "__origin__", None)
        if origin is not None:
            if type(t) is _AnnotatedAlias:
                t = origin
            elif origin == Final:
                t = t.__args__[0]
            elif type(origin) is _TypeAliasType:
                t = origin.__value__[t.__args__]
            else:
                args = getattr(t, "__args__", None)
                origin = _CONCRETE_TYPES.get(origin, origin)
                break
        else:
            supertype = getattr(t, "__supertype__", None)
            if supertype is not None:
                t = supertype
            elif type(t) is _TypeAliasType:
                t = t.__value__
            else:
                origin = t
                args = None
                break

    origin_type = type(origin)
    if origin_type is _types_UnionType:
        args = origin.__args__
        origin = Union
    if origin_type is tuple:
        # Handle an annoying compatibility issue:
        # - Tuple[()] has args == ((),)
        # - tuple[()] has args == ()
        if args == ((),):
            args = ()
    return origin, args


def is_struct_like(t):
    """
    Equivalent to the followings, functions are from msgspec.inspect

    _is_struct(t)
    or _is_typeddict(t)
    or _is_dataclass(t)
    or _is_attrs(t)
    or _is_namedtuple(t)
    """
    # _is_struct()
    if type(t) is type(msgspec.Struct):
        return True
    # _is_dataclass()
    if hasattr(t, "__dataclass_fields__"):
        return True
    try:
        # _is_typeddict()
        if issubclass(t, dict) and hasattr(t, "__total__"):
            return True
        # _is_namedtuple(t)
        if issubclass(t, tuple) and hasattr(t, "_fields"):
            return True
    except TypeError:
        return False
    # _is_attrs()
    if hasattr(t, "__attrs_attrs__"):
        return True
    return False


def get_default(t, guess_default=False, return_struct=False):
    """
    Calculates a default value for a given type hint

    Args:
        t: Any typehint
        guess_default (bool):
            False by default for safety.
            If True, invalid primitive values (like int, str, bytes) will be replaced by their "zero"
                or "empty" values (0, "", b"").
            If False, a validation error for such a type will cause the repair to fail,
                unless an explicit default is set on the model field.
            This does not affect:
            - container types (e.g., a list field with a non-list value will still default to `[]`),
            - Optional fields (which default to `None`)
            - Enums/Literals (which are never guessed)
        return_struct (bool):
            True to return msgspec.Struct instead of {}

    Returns:
        Any: If `t` can be default constructed, return value in type `t`
            Otherwise return NODEFAULT
    """
    origin, args = origin_args(t)

    # --- Most common types first for performance ---

    # Handle `None` (very common in Optional[T])
    if origin is None or origin is type(None):
        return None

    # Handle `str`
    if origin is str:
        return "" if guess_default else NODEFAULT

    # Handle `int`
    if origin is int:
        return 0 if guess_default else NODEFAULT

    # Handle `list` (the most common collection)
    if origin is list:
        return []

    # Handle `dict`
    if origin is dict:
        return {}

    # Handle `bool`
    if origin is bool:
        return False if guess_default else NODEFAULT

    # --- Structured object types ---

    if return_struct and type(t) is type(msgspec.Struct):
        # try if `t` can be default constructed
        try:
            return t()
        except Exception:
            return NODEFAULT

    # All struct-like types default to an empty object (`{}`), representing a
    # valid but empty structure, which is useful for error correction.
    if is_struct_like(origin):
        return {}

    # --- Union types ---

    if origin is Union:
        # If None is a member of the union (i.e., Optional[T]), it's the best default.
        for arg in args:
            if arg is UnsetType:
                continue
            if arg is None or arg is type(None):
                return None

        # Otherwise, recursively find the first type in the union that can provide a default.
        for arg in args:
            if arg is UnsetType:
                continue
            default = get_default(arg, guess_default, return_struct)
            if default is not NODEFAULT:
                return default

        # If no type in the union can be defaulted, the union itself cannot.
        return NODEFAULT

    # --- Other container types ---

    if origin is set:
        return set()

    if origin is frozenset:
        return frozenset()

    if origin is tuple:
        # An empty tuple is a safe default for both fixed and variable-length tuples.
        return ()

    # --- Types that cannot be safely guessed ---

    # We cannot safely pick a default value from an Enum or a set of Literals.
    if _is_enum(origin):
        return NODEFAULT

    if origin is Literal:
        return NODEFAULT

    # --- Less common scalar and special types ---

    if origin is float:
        return 0.0 if guess_default else NODEFAULT

    if origin is bytes:
        return b"" if guess_default else NODEFAULT

    if origin is bytearray:
        return bytearray()

    if origin is memoryview:
        # A memoryview of an empty bytes object is a safe default.
        return memoryview(b"")

    # For complex objects, we avoid guessing a default (e.g., datetime.now())
    # as it can have side effects or be misleading.
    if origin in (
            datetime.datetime,
            datetime.time,
            datetime.date,
            datetime.timedelta,
            uuid.UUID,
            decimal.Decimal,
            msgspec.Raw,
            msgspec.msgpack.Ext,
    ):
        return NODEFAULT

    # --- Abstract and generic types ---

    if origin is Any:
        return NODEFAULT

    if isinstance(origin, TypeVar):
        # If the TypeVar is bound to another type, try to get the default of that bound type.
        if origin.__bound__ is not None:
            return get_default(origin.__bound__, guess_default)
        return NODEFAULT

    # --- Fallback ---

    # If the type is not recognized by any of the guards above, it's treated as a
    # custom or unknown type for which we cannot provide a default.
    return NODEFAULT
