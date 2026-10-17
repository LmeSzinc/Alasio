from functools import wraps
from typing import Any, Awaitable, Callable, Generic, TypeVar

from .cache import InstanceCacheOperation

# Async cached property for coroutine functions, see cached_property_async.
# 1. **No event loop primitive**
# The calculated value is cached as an AwaitableResult, a fake awaitable that returns the value
# without yielding to the event loop. So it doesn't bind to asyncio or trio, the cached value can
# be awaited on any coroutine framework, on any event loop, or without any event loop at all, and
# unlike a coroutine or an asyncio.Future, it can be awaited multiple times.
# 2. **Not deduplicated**
# Like the plain cached_property under threads, concurrent first awaits may calculate the value
# more than once, because waiting for a calculation in progress requires an event loop primitive.
# 3. **No threadsafe version**
# A thread lock can't be held across an await, so there is no cached_property_async_threadsafe.

T = TypeVar('T')


class AwaitableResult(Generic[T]):
    """
    A fake awaitable that carries an already calculated value.

    Awaiting it never yields to the event loop, so no event loop is required to use it,
    and it works on any coroutine framework (asyncio, trio, ...). Unlike a coroutine or
    an asyncio.Future, it can be awaited multiple times.
    """

    __slots__ = ('_value',)

    def __init__(self, value: T):
        """
        Args:
            value (T): The value to return when awaited
        """
        self._value = value

    def __await__(self):
        """
        Returns:
            T: The value, returned without yielding to the event loop
        """
        # An unreachable yield, it makes this function a generator, so the await
        # protocol takes the value from its StopIteration and the caller is never
        # suspended. It is faster than making the result its own iterator.
        return self._value
        yield

    def __repr__(self):
        return f'<{type(self).__name__}: {self._value!r}>'


class AsyncCacheOperation(InstanceCacheOperation):
    """
    Helper functions to manage INSTANCE cached property of coroutine functions.

    Unlike InstanceCacheOperation, set() wraps the value into an AwaitableResult,
    and warm() is a coroutine, so that the value can be calculated on warmup.
    """

    # Tell the sync InstanceCacheOperation.warm() that the value can't be calculated synchronously
    calculated_on_await = True

    @staticmethod
    def set(instance, attrname, value):
        """
        Set a cached property on an INSTANCE.

        The value is wrapped into an AwaitableResult, so `await instance.attrname`
        returns it, the same as a calculated value.

        Args:
            instance:
            attrname (str):
            value:
        """
        InstanceCacheOperation.set(instance, attrname, AwaitableResult(value))

    @staticmethod
    async def warm(instance, attrname):
        """
        Warmup a cached property on an INSTANCE.

        Args:
            instance:
            attrname (str):

        Returns:
            bool: True if warmup or already warmup
                False if failed
        """
        try:
            if attrname in instance.__dict__:
                # already warmup
                return True
        except AttributeError:
            # No '__dict__' attribute
            return False

        # find descriptor
        cls = type(instance)
        descriptor = None
        for base in cls.__mro__:
            try:
                base_dict = base.__dict__
            except AttributeError:
                # No '__dict__' attribute
                continue
            descriptor = base_dict.get(attrname, None)
            if descriptor is not None:
                break
        if descriptor is None:
            return False

        # call __get__ function
        try:
            descriptor_get = descriptor.__get__
        except AttributeError:
            return False
        # calculate the value, it is cached when the await completes
        await descriptor_get(instance, cls)
        return attrname in instance.__dict__


class _PendingResult(Generic[T]):
    """
    The awaitable returned by cached_property_async when the value was never calculated.
    It calculates the value on await, then caches it into the instance.
    """

    __slots__ = ('_func', '_instance', '_attrname')

    def __init__(self, func, instance, attrname):
        """
        Args:
            func (Callable[[Any], Awaitable[T]]): Coroutine function of the cached property
            instance (object): Instance to calculate the value on
            attrname (str): Attribute name to cache the calculated value
        """
        self._func = func
        self._instance = instance
        self._attrname = attrname

    def __await__(self):
        """
        Returns:
            T: The calculated value
        """
        return self._calculate().__await__()

    async def _calculate(self):
        """
        Calculate the value, or reuse the value calculated by another await.

        Returns:
            T: The calculated value
        """
        instance = self._instance
        attrname = self._attrname
        cache = instance.__dict__
        # Another await of this property may have calculated the value
        # after this awaitable was created
        try:
            return await cache[attrname]
        except KeyError:
            pass

        # calculate
        value = await self._func(instance)
        try:
            cache[attrname] = AwaitableResult(value)
        except TypeError:
            msg = (
                f"The '__dict__' attribute on {type(instance).__name__!r} instance "
                f"does not support item assignment for caching {attrname!r} property."
            )
            raise TypeError(msg) from None
        return value


class cached_property_async(Generic[T], AsyncCacheOperation):
    """
    A high-performance, non-thread-safe cached property for coroutine functions.

    The coroutine function is called on the first `await instance.attr`, the calculated
    value is cached as an AwaitableResult, so later awaits return the cached value
    without calculating it again and without yielding to the event loop.

    It doesn't use any event loop primitive like Future or Task, so it works on any
    coroutine framework (asyncio, trio, ...). Meanwhile, like the plain cached_property
    under threads, concurrent first awaits may calculate the value more than once.

    Use the coroutine AsyncCacheOperation.warm() to calculate the value in advance,
    and AsyncCacheOperation.set() to set a value manually.
    """

    def __init__(self, func: Callable[[Any], "Awaitable[T]"]):
        self.func = func
        wraps(func)(self)

    def __get__(self, instance, cls):
        """
        Args:
            instance (object): Instance that the property is accessed from,
                can be None if accessed from class.
            cls (type): The class that the property is accessed from.

        Returns:
            AwaitableResult[T]: The cached value, if the property was calculated before
            _PendingResult[T]: The awaitable that calculates the value, otherwise
        """
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

        # check cache
        try:
            return cache[attrname]
        except KeyError:
            pass

        # calculate on await
        return _PendingResult(self.func, instance, attrname)
