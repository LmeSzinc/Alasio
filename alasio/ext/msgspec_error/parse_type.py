import sys
import typing

from msgspec._utils import _CONCRETE_TYPES

# Backport typing alias in msgspec._utils, so we can keep supporting py3.8
# <-- Start of the copied code -->
try:
    from types import UnionType as _types_UnionType  # type: ignore
except Exception:
    _types_UnionType = type("UnionType", (), {})  # type: ignore

try:
    from typing import Final, TypeAliasType as _TypeAliasType, Union  # type: ignore
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
