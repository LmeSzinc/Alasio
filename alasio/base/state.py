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
