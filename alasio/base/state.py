import functools
import types
from typing import Type

from alasio.ext.cache import CacheOperation, cached_property
from alasio.ext.singleton import Singleton


class StateBase(metaclass=Singleton):
    @cached_property
    def dict_defaults(self):
        """
        Get all attrs of state and their default values

        Returns:
            dict[str, Any]:
        """
        res = {}
        # Iterate over all attributes of the class including inherited ones
        for name in dir(self.__class__):
            # Skip private/protected attributes
            if name.startswith('_'):
                continue
            # Skip methods and other callables
            attr = getattr(self.__class__, name)
            if callable(attr):
                continue
            if isinstance(attr, CacheOperation):
                continue

            res[name] = attr
        return res

    def is_set(self, attr):
        """
        Args:
            attr (str):

        Returns:
            bool: True if attr is set on state instance
        """
        return attr in self.dict_defaults

    def unset(self, attr):
        """
        Unset an attr
        After unset, you can still access obj.attr and get default value

        Args:
            attr (str):

        Returns:
            Any: The value of attr
        """
        if attr not in self.dict_defaults:
            return None
        try:
            return self.__dict__.pop(attr)
        except KeyError:
            return getattr(self, attr, None)

    def unset_defaults(self):
        """
        Unset all attrs that equals default
        """
        defaults = self.dict_defaults
        for key, value in list(self.__dict__.items()):
            if key in defaults and value == defaults[key]:
                self.__dict__.pop(key, None)
        return self

    def clear(self):
        """
        Clear all instance attributes, all attrs will be default values
        """
        defaults = self.dict_defaults
        for key, value in list(self.__dict__.items()):
            if key in defaults:
                self.__dict__.pop(key, None)

    def get_attrs(self):
        """
        Get all attrs and their values
        attrs that haven't been set will be included with default value

        Returns:
            dict[str, Any]:
        """
        res = self.dict_defaults.copy()
        for k, v in self.__dict__.items():
            if k in res:
                res[k] = v
        return res

    def get_attrs_set(self):
        """
        Get all attrs which is set on current obj and their values
        attrs that haven't been set are not included

        Returns:
            dict[str, Any]:
        """
        defaults = self.dict_defaults
        return {k: v for k, v in self.__dict__.items() if k in defaults}

    def merge(self, other):
        """
        Merge another state into self.
        attrs that is set on another state and exists in self, will be set to self

        Args:
            other (StateBase):
        """
        defaults = self.dict_defaults
        for key, value in other.get_attrs_set().items():
            if key in defaults:
                setattr(self, key, value)
        return self


class GlobalState(StateBase):
    """
    A global state class
    """
    _subclasses: "dict[int, StateBase]" = {}

    def __init__(self):
        GlobalState._subclasses[id(self)] = self

    @classmethod
    def subclasses_clear_all(cls):
        subclasses = GlobalState._subclasses
        GlobalState._subclasses = {}
        for subclass in subclasses.values():
            subclass.clear()
            subclass.__class__.singleton_clear()


class TaskState(StateBase):
    """
    A state class that resets on task switch

    Examples:
        class CampaignState(TaskState):
            is_clear_mode = False
            is_fleet_lock = False

        state = CampaignState()
        if not state.is_clear_mode:
            state.is_fleet_lock = False
    """
    _subclasses: "dict[int, StateBase]" = {}

    def __init__(self):
        TaskState._subclasses[id(self)] = self

    @classmethod
    def subclasses_clear_all(cls):
        subclasses = TaskState._subclasses
        TaskState._subclasses = {}
        for subclass in subclasses.values():
            subclass.clear()
            subclass.__class__.singleton_clear()


class _StateDispatcher:
    # key: (module name, qualname), value: callable function
    FUNC_REGISTRY = {}

    def __init__(self, first_func, state_cls: "Type[GameStateBase]"):
        self.state_cls = state_cls
        # [(conditions_dict, target_function)]
        self.cases = []
        functools.update_wrapper(self, first_func)

    def __call__(self, *args, **call_kwargs):
        state = self.state_cls()
        fallback_func = None

        for cond, func in self.cases:
            if not cond:
                fallback_func = func
                continue
            if state.match(**cond):
                return func(*args, **call_kwargs)

        if fallback_func is not None:
            return fallback_func(*args, **call_kwargs)

        return None

    def __get__(self, instance, owner):
        if instance is None:
            return self
        return types.MethodType(self, instance)


class GameStateBase(GlobalState):
    """
    Examples:
        # Define your GameState
        # All fields should have default value
        class GameState(GameStateBase):
            server: t.Literal['cn', 'en', 'jp', 'tw'] = 'cn'
    """
    server: str = 'cn'
    lang: str = 'zh-CN'

    @classmethod
    def set_server(cls, server):
        self = cls()
        self.server = server

    @classmethod
    def set_lang(cls, lang):
        self = cls()
        self.lang = lang

    @classmethod
    def match(cls, **kwargs):
        """
        Args:
            **kwargs:

        Returns:
            bool: True if GameState matches given condition
        """
        self = cls()
        return self._match(**kwargs)

    def _match(self, **kwargs):
        """
        Args:
            **kwargs:

        Returns:
            bool: True if GameState matches given condition
        """
        matched = True
        for key, value in kwargs.items():
            try:
                if getattr(self, key) != value:
                    matched = False
                    break
            except AttributeError:
                # condition does not exist, no match
                matched = False
                break
        return matched

    @classmethod
    def when(cls, **kwargs):
        """
        Return a decorator that runs the function only if GameState matches the condition

        Examples:
            @GameState.when(server='cn')
            def commission_parse(...):
                # this only runs when server='cn'

            @GameState.when(server='en')
            @GameState.when(server='jp')
            def commission_parse(...):
                # this only runs when server='en' or server='jp'

            @GameState.when()
            def commission_parse(...):
                # fallback method if no conditions matched
                # If no fallback method is defined and GameState doesn't match the condition above,
                # calling commission_parse() will do nothing and return None

            # the correct method will be called, depending on GameState
            result = commission_parse(...)

        Note that when using with @classmethod or @staticmethod, when() should at inner
        Examples:
            @classmethod
            @GameStateBase.when(server='cn')
            def class_method(cls):
                return f'class_method_on_{cls.__name__}'
        """

        def decorator(func):
            # unique key
            key = (func.__module__, func.__qualname__)

            if key not in _StateDispatcher.FUNC_REGISTRY:
                dispatcher = _StateDispatcher(func, cls)
                _StateDispatcher.FUNC_REGISTRY[key] = dispatcher
            else:
                dispatcher = _StateDispatcher.FUNC_REGISTRY[key]

            # if decorator is chained, extract the function from last case
            if isinstance(func, _StateDispatcher):
                try:
                    func = dispatcher.cases[-1][1]
                except IndexError:
                    # this shouldn't happen
                    pass

            # add cases
            dispatcher.cases.append((kwargs, func))
            return dispatcher

        return decorator
