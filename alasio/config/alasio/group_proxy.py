import functools
import types
from typing import TYPE_CHECKING

from msgspec import Struct

if TYPE_CHECKING:
    from alasio.base.servertime import ServerTime
    from alasio.config.base import AlasioConfigBase


class GroupProxy(Struct):
    """
    A proxy object upon group model to capture property set event
    So you can set property directly and trigger auto save:
        config.Campaign.Name = "12-4"
    """
    _obj: Struct
    _config: "AlasioConfigBase"
    _task: str
    _group: str

    def __getattr__(self, item):
        # proxy attribute access
        obj = self._obj
        obj_cls = type(obj)
        descriptor = getattr(obj_cls, item, None)

        if descriptor:
            fget = None
            if isinstance(descriptor, property):
                fget = descriptor.fget
            elif hasattr(descriptor, 'func'):  # cached_property
                # Both functools and alasio cached_property have .func
                fget = descriptor.func

            if fget and getattr(fget, '_alasio_batch_set', False):
                with self._config.batch_set():
                    return descriptor.__get__(obj, obj_cls)

        attr = getattr(obj, item)

        # wrap group function with batch_set to avoid multiple save() calls.
        # class Dashboard(m.Struct):
        #     @batch_set
        #     def set(self, value: int):
        #         self.Value = value
        #         self.update()
        # when you call:
        #     config.dashboard.set(123)
        # it magically becomes:
        #     with config.batch_set():
        #         config.dashboard.set(123)
        if isinstance(attr, types.MethodType) and attr.__self__ is obj:
            underlying_func = attr.__func__

            # Check if method is marked by @batch_set
            is_batch = getattr(underlying_func, '_alasio_batch_set', False)

            # If user forgot to wrap by @functools.wrap(), give a warning
            if underlying_func.__name__ != item and not hasattr(underlying_func, "__wrapped__"):
                from alasio.logger import logger
                logger.warning(
                    f"Un-wrapped decorator detected on '{self._group}.{item}'. "
                    f"This may hide @batch_set marker and break self-redirection."
                )

            def wrapped_method(*args, **kwargs):
                if is_batch:
                    with self._config.batch_set():
                        return underlying_func(self, *args, **kwargs)
                else:
                    return underlying_func(self, *args, **kwargs)

            # wraps wrapped_method
            return functools.update_wrapper(wrapped_method, underlying_func)

        return attr

    @property
    def servertime(self) -> "ServerTime":
        # see DashboardBase.get_servertime()
        return self._config.servertime

    def __setattr__(self, key, value):
        obj = self._obj
        setattr(obj, key, value)
        # register modify
        self._config.register_modify(task=self._task, group=self._group, arg=key, value=value)

    def __repr__(self):
        return repr(self._obj)

    def __str__(self):
        return str(self._obj)


def batch_set(func):
    """
    Mark a group method to run within config.batch_set() context.
    This only works for group validation classes. Example:
    ```
    class Dashboard(m.Struct):
        Value: e.Annotated[int, m.Meta(ge=0)] = 0
        Time: e.Annotated[d.datetime, m.Meta(tz=True)] = d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)

        @batch_set
        def set(self, value: int):
            self.Value = value
            self.Time = getnow()

    # when you call:
    #     config.dashboard.set(123)
    # it magically becomes:
    #     with config.batch_set():
    #         config.dashboard.set(123)
    ```

    Note that decorator that wraps @batch_set must have @functools.wraps(),
    otherwise one value set will trigger on write call.
    ```
    def other_deco(function):
        @wraps(function)  # must have this
        def wrapper(*args, **kwargs):
        ...

    class Dashboard(m.Struct):
        @other_deco
        @batch_set
        def my_method(self):
        ...
    ```
    """
    func._alasio_batch_set = True
    return func
