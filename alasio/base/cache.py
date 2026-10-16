"""
Cache computed values until the device takes a new screenshot or dumps a new hierarchy

Examples:
    class EnemySearching(ModuleBase):
        @cached_on_screenshot
        def is_in_map(self):
            # template matching on self.device.image
            return self.appear(IN_MAP)

        if self.is_in_map:      # computed once per screenshot
            ...
"""
import weakref
from functools import wraps
from typing import Any, Callable, Generic, TypeVar

from alasio.ext.cache import InstanceCacheOperation
from alasio.logger import logger

T = TypeVar("T")


class _CachedOnEvent(Generic[T], InstanceCacheOperation):
    """
    Base of `cached_on_screenshot` and `cached_on_hierarchy`

    A cached property that is dropped when the device event it depends on happens.
    The value is computed on the first access after the event and reused until the
    next event. It is stored as a plain attribute in the instance `__dict__`,
    exactly like cached_property does, so a cache hit is a normal attribute access.

    The values are dropped by the device, see `DeviceBase._on_new_screenshot()`.
    The device keeps a registry of `{weakref.ref(instance): {attrname}}` for that,
    the registry references neither the instance nor the value, so nothing is kept
    alive by the cache.
    """

    # The event name, used in the misuse warning
    event_name = ''
    # Name of the device attribute holding the event epoch
    epoch_attr = ''
    # Name of the device attribute holding the registry
    registry_attr = ''

    def __init__(self, func: Callable[[Any], T]):
        """
        Args:
            func (callable): The function computing the cached value
        """
        self.func = func
        wraps(func)(self)

    def __get__(self, instance, cls) -> T:
        """
        Compute the value on a cache miss

        This is a non-data descriptor: without a `__set__` method, Python
        returns the instance `__dict__` entry on a cache hit, so this function
        is only called when there is no cached value.

        Args:
            instance:
            cls:

        Returns:
            T: The cached value
        """
        if instance is None:
            return self

        attrname = self.func.__name__
        try:
            cache = instance.__dict__
        except AttributeError:
            raise TypeError(
                f"No '__dict__' attribute on {type(instance).__name__!r} "
                f"instance to cache {attrname!r} property."
            ) from None

        # double check, CacheOperation.warm() calls __get__ even when cached
        try:
            return cache[attrname]
        except KeyError:
            pass

        device = self._device_of(instance)
        # snapshot the epoch before computing, the value is only cached when
        # no new event happened while computing
        epoch = getattr(device, self.epoch_attr)
        value = self.func(instance)

        with device._cache_lock:
            # register first, a registration failure means "do not cache"
            self._register(device, instance, attrname)
            cache[attrname] = value
            crossed = getattr(device, self.epoch_attr) != epoch

        if crossed:
            # Non-standard usage: a new screenshot landed while the value was
            # being computed. The cache behavior is undefined in that case.
            logger.warning(
                f'{type(instance).__name__}.{attrname} was computed across a new {self.event_name}, '
                f'do not trigger a new {self.event_name} inside a cached function'
            )

        return value

    @classmethod
    def set(cls, instance, attrname, value):
        """
        Write a value to the cache, managed like a computed value

        A plain `instance.attrname = value` also works, but it is not registered,
        so the device would never drop it. Use this instead.

        Args:
            instance: The object having the cached property
            attrname (str): Name of the cached property
            value: Value to cache
        """
        device = cls._device_of(instance)
        with device._cache_lock:
            cls._register(device, instance, attrname)
            instance.__dict__[attrname] = value

    @classmethod
    def _device_of(cls, instance):
        """
        The device providing the epoch and the registry

        Args:
            instance: The object having the cached property

        Returns:
            DeviceBase:
        """
        try:
            return instance.device
        except AttributeError:
            raise AttributeError(
                f'{type(instance).__name__} must have a "device" attribute '
                f'to use @{cls.__name__}'
            ) from None

    @classmethod
    def _register(cls, device, instance, attrname):
        """
        Register the instance on the device registry, so its values are dropped
        on the next event

        The caller must hold `device._cache_lock`.

        Args:
            device (DeviceBase):
            instance: The object having the cached property
            attrname (str): Name of the cached property
        """
        registry = getattr(device, cls.registry_attr)
        try:
            ref = weakref.ref(instance)
        except TypeError:
            raise TypeError(
                f'{type(instance).__name__} does not support weak references, '
                f'cannot cache with @{cls.__name__}'
            ) from None

        attrs = registry.get(ref)
        if attrs is None:
            registry[ref] = {attrname}
        else:
            attrs.add(attrname)


class cached_on_screenshot(_CachedOnEvent):
    """
    A cached property dropped when a new screenshot is taken

    The value is computed on the first access in a screenshot and reused until
    the next screenshot. Values are dropped by `DeviceBase._on_new_screenshot()`,
    which is called by `DeviceBase.screenshot()` implementations after a new image
    lands, by `DeviceBase.set_image()` and by `DeviceBase.on_idle()`. Dropping is
    active: the value is released right at the new screenshot, not on the next access.

    Examples:
        class EnemySearching(ModuleBase):
            @cached_on_screenshot
            def is_in_map(self):
                return self.appear(IN_MAP)

            if self.is_in_map:      # a normal attribute access, computed once per screenshot
                ...

    Note:
        The cached function must depend on the current screenshot only.
        Do not take a screenshot inside it, that is not supported.

    Note:
        `cached_on_screenshot.pop()` drops the value manually, see
        `InstanceCacheOperation`. Assign values with `cached_on_screenshot.set()`.
    """
    event_name = 'screenshot'
    epoch_attr = '_screenshot_epoch'
    registry_attr = '_screenshot_caches'


class cached_on_hierarchy(_CachedOnEvent):
    """
    A cached property dropped when a new hierarchy is dumped

    The mirror of `cached_on_screenshot`. Values are dropped by
    `DeviceBase._on_new_hierarchy()`, which is called by `DeviceBase.dump_hierarchy()`
    implementations after `self.hierarchy` is updated, and by `DeviceBase.set_hierarchy()`.

    Examples:
        class EnemySearching(ModuleBase):
            @cached_on_hierarchy
            def hierarchy_index(self):
                # parse self.device.hierarchy, the caller dumps the hierarchy
                return build_index(self.device.hierarchy)
    """
    event_name = 'hierarchy'
    epoch_attr = '_hierarchy_epoch'
    registry_attr = '_hierarchy_caches'


__all__ = ('cached_on_screenshot', 'cached_on_hierarchy')
