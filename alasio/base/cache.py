from typing import Callable, Generic, TypeVar

T = TypeVar("T")


class cached_property(Generic[T]):
    """
    cached-property from https://github.com/pydanny/cached-property
    Add typing support, non-thread-safe.

    A property that is only computed once per instance and then replaces itself
    with an ordinary attribute. Deleting the attribute resets the property.
    Source: https://github.com/bottlepy/bottle/commit/fa7733e075da0d790d809aa3d2f53071897e6f76
    """

    def __init__(self, func: Callable[..., T]):
        self.func = func

    def __get__(self, obj, cls) -> T:
        if obj is None:
            return self

        value = obj.__dict__[self.func.__name__] = self.func(obj)
        return value


_NOT_FOUND = object()


class cached_property_py312(Generic[T]):
    """
    Backport functools.cached_property on python >= 3.12
    """

    def __init__(self, func: Callable[..., T]):
        self.func = func
        self.attrname = None
        self.__doc__ = func.__doc__
        self.__module__ = func.__module__

    def __set_name__(self, owner, name):
        if self.attrname is None:
            self.attrname = name
        elif name != self.attrname:
            raise TypeError(
                "Cannot assign the same cached_property to two different names "
                f"({self.attrname!r} and {name!r})."
            )

    # @overload
    # def __get__(self, instance: None, owner: Any) -> "cached_property_py312[T]":
    #     ...
    #
    # @overload
    # def __get__(self, instance: Any, owner: Any) -> T:
    #     ...

    def __get__(self, instance, owner=None) -> T:
        if instance is None:
            return self
        if self.attrname is None:
            raise TypeError(
                "Cannot use cached_property instance without calling __set_name__ on it.")
        try:
            cache = instance.__dict__
        except AttributeError:  # not all objects have __dict__ (e.g. class defines slots)
            msg = (
                f"No '__dict__' attribute on {type(instance).__name__!r} "
                f"instance to cache {self.attrname!r} property."
            )
            raise TypeError(msg) from None
        val = cache.get(self.attrname, _NOT_FOUND)
        if val is _NOT_FOUND:
            val = self.func(instance)
            try:
                cache[self.attrname] = val
            except TypeError:
                msg = (
                    f"The '__dict__' attribute on {type(instance).__name__!r} instance "
                    f"does not support item assignment for caching {self.attrname!r} property."
                )
                raise TypeError(msg) from None
        return val

    # __class_getitem__ = classmethod(GenericAlias)


def del_cached_property(obj, name):
    """
    Delete a cached property safely.

    Args:
        obj:
        name (str):
    """
    try:
        del obj.__dict__[name]
    except KeyError:
        pass


def has_cached_property(obj, name):
    """
    Check if a property is cached.

    Args:
        obj:
        name (str):
    """
    return name in obj.__dict__


def set_cached_property(obj, name, value):
    """
    Set a cached property.

    Args:
        obj:
        name (str):
        value:
    """
    obj.__dict__[name] = value
