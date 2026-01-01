import threading
from typing import Any, Dict, Optional, Type, TypeVar

T = TypeVar('T')


class Singleton(type):
    """
    A metaclass for creating a global singleton.

    Any class using this metaclass will have only one instance.
    Subclasses will have their own unique singleton instance.
    This implementation is thread-safe.

    Usage:
        class ConfigScanSource(metaclass=Singleton):
            pass
        # first call creates new instance
        source = ConfigScanSource()
        # second and later calls return the same instance
        source = ConfigScanSource()
    """

    def __init__(cls, name, bases, dct):
        super().__init__(name, bases, dct)
        cls.__instance = None
        cls.__lock = threading.Lock()

    def __call__(cls: Type[T], *args, **kwargs) -> T:
        # return cached instance directly
        instance = cls.__instance
        if instance is not None:
            return instance

        # create new instance
        with cls.__lock:
            # another thread may have created while we are waiting
            instance = cls.__instance
            if instance is not None:
                return instance

            # create
            instance = super().__call__(*args, **kwargs)
            cls.__instance = instance
            return instance

    def singleton_clear(cls):
        """
        Remove all instances
        """
        with cls.__lock:
            cls.__instance = None

    def singleton_instance(cls) -> Optional[T]:
        """
        Access instance directly
        """
        return cls.__instance


class SingletonNamed(type):
    """
    A metaclass for creating a named singleton.

    Instances are created based on the first argument provided to the constructor.
    Each class will have its own separate cache of named instances.
    This implementation is thread-safe.

    Usage:
        class AlasioConfigDB(metaclass=SingletonNamed):
            # __init__ must have at least one argument
            def __init__(self, config_name):
                self.file = config_name

        # first call creates new instance
        db = AlasioConfigDB("alas")
        # second and later calls return the same instance
        db = AlasioConfigDB("alas")
        # different input different instance
        db2 = AlasioConfigDB("alas2")
    """

    def __init__(cls, name, bases, dct):
        super().__init__(name, bases, dct)
        cls.__instances = {}
        cls.__lock = threading.Lock()

    def __call__(cls: Type[T], name, *args, **kwargs) -> T:
        # return cached instance directly
        try:
            return cls.__instances[name]
        except KeyError:
            pass

        # create new instance
        with cls.__lock:
            # another thread may have created while we are waiting
            try:
                return cls.__instances[name]
            except KeyError:
                pass

            # create
            instance = super().__call__(name, *args, **kwargs)
            cls.__instances[name] = instance
            return instance

    def singleton_remove(cls, name):
        """
        Remove a specific instance.
        Instance will be re-created, the nest time it is requested.

        Returns:
            bool: If removed
        """
        # delete from dict is threadsafe
        try:
            del cls.__instances[name]
            return True
        except KeyError:
            return False

    def singleton_clear_all(cls):
        """
        Remove all instances
        """
        with cls.__lock:
            cls.__instances.clear()

    def singleton_instances(cls) -> Dict[Any, T]:
        """
        Access all instances directly
        """
        return cls.__instances
