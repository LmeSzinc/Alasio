import functools
import types
from typing import Any, TYPE_CHECKING

import msgspec
from msgspec._core import Factory
from msgspecerror import get_class_annotation_dict


class _StateMeta(type):
    if TYPE_CHECKING:
        struct_model: "type[msgspec.Struct]"
        dict_defaults: "dict[str, Any]"

    def __init__(cls, name, bases, dct):
        super().__init__(name, bases, dct)

        # gather all fields
        dict_annotations = get_class_annotation_dict(cls)
        struct_fields = []
        for attr, anno in dict_annotations.items():
            try:
                default = getattr(cls, attr)
            except AttributeError:
                raise TypeError(f'State field {cls.__name__}.{attr} must have a default')
            struct_fields.append((attr, anno, default))

        # create struct model
        struct_model: "type[msgspec.Struct]" = msgspec.defstruct(
            f'{cls.__name__}Struct', struct_fields, omit_defaults=True, forbid_unknown_fields=True,
        )
        if len(struct_model.__struct_fields__) != len(struct_model.__struct_defaults__):
            raise ValueError(f'All fields in state class {cls.__name__} must have a default value')

        # gather default values
        dict_defaults = {}
        for name, value in zip(struct_model.__struct_fields__, struct_model.__struct_defaults__):
            if value is msgspec.UNSET:
                continue
            if value is msgspec.NODEFAULT:
                raise ValueError(f'State field {cls.__name__}.{name} must have a default value')
            if isinstance(value, Factory):
                raise TypeError(f'State field {cls.__name__}.{name} must be static default value, not {value}')
            dict_defaults[name] = value

        # Use super().__setattr__ to bypass the custom __setattr__ hook
        # since struct_model and dict_defaults are internal, not state fields
        super().__setattr__('struct_model', struct_model)
        super().__setattr__('dict_defaults', dict_defaults)

    def __call__(cls, *args, **kwargs):
        raise TypeError(
            f"State class '{cls.__name__}' cannot be instantiated. "
            f"Please use class attributes directly (e.g., {cls.__name__}.field_name)."
        )

    def update(cls, **kwargs):
        """
        Update state with kwargs

        Args:
            kwargs: key-value pairs to update
        """
        # may raise ValidationError
        obj = msgspec.convert(kwargs, cls.struct_model)
        for name in kwargs:
            if name not in cls.dict_defaults:
                continue
            # if validate success, set to class attribute
            # value type may change after validation
            value = getattr(obj, name)
            super().__setattr__(name, value)

    def __setattr__(cls, name, value):
        data = {name: value}
        cls.update(**data)

    def is_modified(cls, name):
        """
        Args:
            name (str):

        Returns:
            bool: true if attr is modified
        """
        try:
            default = cls.dict_defaults[name]
            value = getattr(cls, name)
        except (KeyError, AttributeError):
            raise ValueError(f'State field {cls.__name__}.{name} does not exist')
        return value != default

    def get_default(cls, name):
        """
        Args:
            name (str):

        Returns:
            Any: default value of attr
        """
        try:
            return cls.dict_defaults[name]
        except KeyError:
            raise ValueError(f'State field {cls.__name__}.{name} does not exist')

    def reset_field(cls, name):
        """
        Reset an attr to default value

        Args:
            name (str):
        """
        try:
            default = cls.dict_defaults[name]
        except KeyError:
            raise ValueError(f'State field {cls.__name__}.{name} does not exist')
        super().__setattr__(name, default)

    def reset_all_fields(cls):
        """
        Reset all attrs to default values
        """
        for name, value in cls.dict_defaults.items():
            super().__setattr__(name, value)

    def _iter_all_subclasses(cls):
        """
        Iterate all subclasses

        Yields:
            Type[StateBase]: subclass
        """
        stack = set(cls.__subclasses__())
        while True:
            new_stack = set()
            for subclass in stack:
                yield subclass
                new_stack.update(subclass.__subclasses__())
            if not new_stack:
                break
            stack = new_stack

    def reset_all_subclasses(cls):
        """
        Reset all subclasses' fields to default values
        """
        subclasses = list(cls._iter_all_subclasses())
        for subclass in subclasses:
            subclass.reset_all_fields()

    def match(cls, **kwargs):
        """
        Args:
            **kwargs:

        Returns:
            bool: True if state matches given condition
        """
        matched = True
        for key, value in kwargs.items():
            try:
                if getattr(cls, key) != value:
                    matched = False
                    break
            except AttributeError:
                matched = False
                break
        return matched

    def when(cls, **kwargs):
        """
        Return a decorator that runs the function only if the state class matches the condition

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
                # If no fallback method is defined and state doesn't match the condition above,
                # calling commission_parse() will do nothing and return None

            # the correct method will be called, depending on state
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


class GlobalState(metaclass=_StateMeta):
    """
    A global state class
    Note that every field must have typehint and static default value.

    Examples:
        class GlobalState(TaskState):
            server: Literal['cn', 'en'] = 'cn'
            lang: Literal['zh-CN', 'en'] = 'zh-CN'

        if not GlobalState.server == 'cn':
            GlobalState.server = 'cn'
    """
    pass


class TaskState(metaclass=_StateMeta):
    """
    A state class that resets on task switch
    Note that every field must have typehint and static default value.

    Examples:
        class CampaignState(TaskState):
            is_clear_mode: bool = False
            is_fleet_lock: bool = False

        if not CampaignState.is_clear_mode:
            CampaignState.is_fleet_lock = False
    """
    pass


class _StateDispatcher:
    # key: (module name, qualname), value: callable function
    FUNC_REGISTRY = {}

    def __init__(self, first_func, state_cls: "_StateMeta"):
        self.state_cls = state_cls
        # [(conditions_dict, target_function)]
        self.cases = []
        functools.update_wrapper(self, first_func)

    def __call__(self, *args, **call_kwargs):
        fallback_func = None

        for cond, func in self.cases:
            if not cond:
                fallback_func = func
                continue
            if self.state_cls.match(**cond):
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
    def match_server(cls, *server):
        """
        Args:
            *server: server names

        Returns:
            bool: True if any server matches the current state
        """
        for item in server:
            if cls.match(server=item):
                return True
        return False

    @classmethod
    def match_lang(cls, *lang):
        """
        Args:
            *lang: language names

        Returns:
            bool: True if any language matches the current state
        """
        for item in lang:
            if cls.match(lang=item):
                return True
        return False
