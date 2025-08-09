from functools import wraps
from threading import Lock
from typing import Any, Callable, TypeVar

# cached_property in python is kind of a mess, it has 3 different cached_property function.
# - cached_property package, the original cached_property implementation before py3.8
# - functools.cached_property on py3.8+, the adopted cached_property
# - functools.cached_property on py3.12+, global lock is removed starting from 3.12
# So we deside to maintain our own cached_property to provide consistent behaviour among all python versions,
# and also keeping it fast, typed, thread-safe.
# 1. **Fast**
# our cached_property work as the original cached_property, the calculated value replace the
# cached_property function in __dict__. It's fast because at the second and later access it just a normal "obj.attr"
# access, while on py3.8+ and py3.12+ every access will go through __get__ method.
# 2. **Thread Safe**
# We separate another function threaded_cached_property, use it if you do care thread safety.
# On py3.8 every decorated function withs the same name share the lock resulting in bad performance.
# On py3.12 cached_property may be calculated twice on race condition.
# 3. **Drop async support**
# It would be unclear if you decorate an async function and the decorator somehow awaited the future which you are
# completely unaware. It's also weird to do `await obj.attr` while obj.attr is already a static value.
# So we just drop it, you shouldn't use cached_property on async function, if you do need to cache an async result, do:
# @cached_property
# def result(self):
#     return await self.calculate_result()

T = TypeVar("T")


class cached_property:
    """
    A high-performance, non-thread-safe cached property
    """

    def __init__(self, func: "Callable[[Any], T]"):
        self.func = func
        wraps(func)(self)

    def __get__(self, instance, cls) -> T:
        if instance is None:
            return self

        attrname = self.func.__name__
        try:
            cache = instance.__dict__
        except AttributeError:
            # not all objects have __dict__ (e.g. class defines slots)
            msg = (
                f"No '__dict__' attribute on {type(instance).__name__!r} "
                f"instance to cache {attrname!r} property."
            )
            raise TypeError(msg) from None

        # calculate
        value = self.func(instance)
        try:
            cache[attrname] = value
        except TypeError:
            msg = (
                f"The '__dict__' attribute on {type(instance).__name__!r} instance "
                f"does not support item assignment for caching {attrname!r} property."
            )
            raise TypeError(msg) from None
        return value


class threaded_cached_property:
    """
    A thread-safe cached property
    """

    def __init__(self, func: "Callable[[Any], T]"):
        self.func = func
        # per-property, cross-instance lock, shares among all instances
        self.create_lock = Lock()
        wraps(func)(self)

    def __get__(self, instance, cls) -> T:
        if instance is None:
            return self

        attrname = self.func.__name__
        try:
            cache = instance.__dict__
        except AttributeError:
            # not all objects have __dict__ (e.g. class defines slots)
            msg = (
                f"No '__dict__' attribute on {type(instance).__name__!r} "
                f"instance to cache {attrname!r} property."
            )
            raise TypeError(msg) from None

        # create pre-instance-property lock
        lock_name = f"_cached_property_lock_for_{attrname}"
        try:
            lock = cache[lock_name]
        except KeyError:
            with self.create_lock:
                # double-checked locking
                # check if in cache first to bypass except KeyError for faster because it's not in cache at happy path
                if lock_name in cache:
                    try:
                        lock = cache[attrname]
                    except KeyError:
                        # race condition
                        lock = Lock()
                        try:
                            cache[lock_name] = lock
                        except TypeError:
                            msg = (
                                f"The '__dict__' attribute on {type(instance).__name__!r} instance "
                                f"does not support item assignment for caching {lock_name!r} property."
                            )
                            raise TypeError(msg) from None
                else:
                    lock = Lock()
                    try:
                        cache[lock_name] = lock
                    except TypeError:
                        msg = (
                            f"The '__dict__' attribute on {type(instance).__name__!r} instance "
                            f"does not support item assignment for caching {lock_name!r} property."
                        )
                        raise TypeError(msg) from None

        # calculate value with lock
        with lock:
            # double-checked locking
            # check if the value was computed before the lock was acquired
            # check if in cache first to bypass except KeyError for faster because it's not in cache at happy path
            if attrname in cache:
                try:
                    return cache[attrname]
                except KeyError:
                    # race condition
                    pass

            # calculate
            value = self.func(instance)
            try:
                cache[attrname] = value
            except TypeError:
                msg = (
                    f"The '__dict__' attribute on {type(instance).__name__!r} instance "
                    f"does not support item assignment for caching {attrname!r} property."
                )
                raise TypeError(msg) from None

            # cleanup, remove the lock from the instance dict
            # as it will never be used again for this property on this instance
            try:
                del cache[lock_name]
            except KeyError:
                # this shouldn't happen
                pass
            return value


def del_cached_property(instance, attrname):
    """
    Delete a cached property safely.

    Args:
        instance:
        attrname (str):
    """
    try:
        del instance.__dict__[attrname]
    except KeyError:
        # never cached or already deleted
        pass
    except AttributeError:
        # No __dict__, just no need to delete
        pass


def has_cached_property(instance, attrname):
    """
    Check if a property is cached.

    Args:
        instance:
        attrname (str):
    """
    try:
        return attrname in instance.__dict__
    except AttributeError:
        # No __dict__, just not exists
        return False


def set_cached_property(instance, attrname, value):
    """
    Set a cached property.

    Args:
        instance:
        attrname (str):
        value:
    """
    try:
        instance.__dict__[attrname] = value
    except AttributeError:
        # not all objects have __dict__ (e.g. class defines slots)
        msg = (
            f"No '__dict__' attribute on {type(instance).__name__!r} "
            f"instance to cache {attrname!r} property."
        )
        raise TypeError(msg) from None
    except TypeError:
        msg = (
            f"The '__dict__' attribute on {type(instance).__name__!r} instance "
            f"does not support item assignment for caching {attrname!r} property."
        )
        raise TypeError(msg) from None


def warm_cached_property(instance, attrname):
    """
    Warmup a cached property.
    Return None to avoid passing big cached objects across threads.

    Args:
        instance:
        attrname (str):
    """
    try:
        getattr(instance, attrname)
    except AttributeError:
        pass
