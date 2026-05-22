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

    def singleton_clear(cls):
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


class SingletonOptionalNamed(SingletonNamed):
    """
    A metaclass combining global singleton and named singleton behavior.

    When a name is provided, it acts as a named singleton (like SingletonNamed).
    When no name is provided (or None), it acts as a global singleton (like Singleton).
    This implementation is thread-safe.

    Usage:
        class MyService(metaclass=SingletonOptionalNamed):
            def __init__(self, name=None, value=0):
                self.name = name
                self.value = value

        # unnamed: global singleton
        s1 = MyService()
        s2 = MyService()
        assert s1 is s2

        # named: named singleton
        s3 = MyService("a")
        s4 = MyService("a")
        assert s3 is s4
        s5 = MyService("b")
        assert s3 is not s5
    """

    def __init__(cls, name, bases, dct):
        super().__init__(name, bases, dct)
        cls.__unnamed_instance = None
        cls.__unnamed_lock = threading.Lock()

    def __call__(cls, name=None, *args, **kwargs):
        """
        Return a singleton instance.

        Args:
            name: Optional name for named singleton behavior.
                When None, behaves as a global singleton.

        Returns:
            The singleton instance.
        """
        if name is not None:
            return super().__call__(name, *args, **kwargs)

        # unnamed: global singleton behavior
        instance = cls.__unnamed_instance
        if instance is not None:
            return instance

        with cls.__unnamed_lock:
            instance = cls.__unnamed_instance
            if instance is not None:
                return instance

            instance = super().__call__(name, *args, **kwargs)
            cls.__unnamed_instance = instance
            return instance

    def singleton_clear(cls):
        """
        Remove all instances, both named and unnamed.
        """
        SingletonNamed.singleton_clear(cls)
        with cls.__unnamed_lock:
            cls.__unnamed_instance = None

    def singleton_remove(cls, name):
        """
        Remove a specific instance, handling unnamed (None) case.

        Args:
            name: Name of the instance to remove, or None for unnamed.

        Returns:
            bool: If removed
        """
        result = SingletonNamed.singleton_remove(cls, name)
        if name is None and result:
            cls.__unnamed_instance = None
        return result
