import threading
import weakref
from typing import Callable, TypeVar

T = TypeVar('T')
_NOT_FOUND = object()


class ReactiveContext:
    """
    Global context for tracking dependencies during computation.
    """
    _local = threading.local()

    @classmethod
    def get_current_observer(cls):
        """
        Returns:
            tuple[Any, str] | None: (weak_ref_to_object, property_name)
        """
        return getattr(cls._local, 'current_observer', None)

    @classmethod
    def set_current_observer(cls, observer):
        """
        Args:
            observer (tuple[Any, str] | None): (weak_ref_to_object, property_name)
        """
        cls._local.current_observer = observer


class reactive:
    def __init__(self, func: Callable[..., T]):
        self.func = func
        self.name = func.__name__
        self.cache_attr = f'_reactive_cache_{self.name}'

        # Track observers: Set of (weak_ref_to_object, property_name)
        self.observers: "set[tuple[weakref.ref, str]]" = set()
        # Lock computation and observer changes
        self.lock = threading.RLock()

    def __get__(self, obj, cls=None) -> T:
        if obj is None:
            return self

        # return cache if available
        cache = getattr(obj, self.cache_attr, _NOT_FOUND)
        if cache is not _NOT_FOUND:
            # register observer
            parent = ReactiveContext.get_current_observer()
            if parent is not None and parent != (obj, self.name):
                parent_obj, parent_name = parent
                self._observer_add(parent_obj, parent_name)
            return cache

        with self.lock:
            # another thread may have computed while we are waiting for lock
            cache = getattr(obj, self.cache_attr, _NOT_FOUND)
            if cache is not _NOT_FOUND:
                # register observer
                parent = ReactiveContext.get_current_observer()
                if parent is not None and parent != (obj, self.name):
                    parent_obj, parent_name = parent
                    self._observer_add(parent_obj, parent_name)
                return cache
            # compute the value
            return self.compute(obj)

    def __set__(self, obj, value):
        raise ValueError('You should not set value to a reactive object, that would break the observation chain. '
                         'Set value to reactive_source object instead')

    def compute(self, obj):
        """
        Compute value and set it to cache, assumes lock is held
        """
        parent = ReactiveContext.get_current_observer()
        # enter new context, so dependencies can know who's observing
        current = (obj, self.name)
        ReactiveContext.set_current_observer(current)
        try:
            # call
            value = self.func(obj)
            setattr(obj, self.cache_attr, value)

            # register observer
            if parent is not None and parent != (obj, self.name):
                parent_obj, parent_name = parent
                self._observer_add_unsafe(parent_obj, parent_name)

            return value

        finally:
            # rollback context
            ReactiveContext.set_current_observer(parent)

    def _observer_add(self, obj, name):
        """
        Add an observer to property

        Args:
            obj (Any):
            name: property_name
        """
        with self.lock:
            self._observer_add_unsafe(obj, name)

    def _observer_add_unsafe(self, obj, name):
        """
        Add an observer to property, assumes lock is held

        Args:
            obj (Any):
            name: property_name
        """
        ref = weakref.ref(obj, self._observer_remove)
        self.observers.add((ref, name))

    def _observer_remove(self, ref):
        """
        Clean up dead observer references

        Args:
            ref (weakref.ref):
        """
        with self.lock:
            self.observers = {(r, n) for r, n in self.observers if r != ref}

    def broadcast(self, obj, compute=True):
        """
        Broadcast changes to all observers

        Args:
            obj:
            compute: True to re-compute cache
        """
        # Get observers copy under lock, then notify outside lock
        with self.lock:
            if compute:
                self.compute(obj)
            observers = list(self.observers)

        # Broadcast changes to external function
        if isinstance(obj, ReactiveBroadcast):
            cache = getattr(obj, self.cache_attr, _NOT_FOUND)
            if cache is not _NOT_FOUND:
                obj.broadcast(self.name, cache)

        # Notify observers outside the lock to prevent deadlocks
        for ref, name in observers:
            observer_obj = ref()
            if observer_obj is not None:
                observer_cls = observer_obj.__class__
                observer_reactive = getattr(observer_cls, name, _NOT_FOUND)
                if observer_reactive is not _NOT_FOUND:
                    try:
                        broadcast = observer_reactive.broadcast
                    except AttributeError:
                        # this shouldn't happen
                        continue
                    broadcast(observer_obj)


class reactive_source(reactive):
    def compute(self, obj):
        """
        Compute value and set it to cache, assumes lock is held
        """
        value = self.func(obj)
        setattr(obj, self.cache_attr, value)
        # reactive_source should not observe anything
        return value

    def __set__(self, obj, value: T):
        with self.lock:
            cache = getattr(obj, self.cache_attr, _NOT_FOUND)
            if cache is _NOT_FOUND:
                # new value
                setattr(obj, self.cache_attr, value)
                self.broadcast(obj, compute=False)
            elif cache == value:
                # value unchanged
                pass
            else:
                # value changed
                setattr(obj, self.cache_attr, value)
                self.broadcast(obj, compute=False)


class ReactiveBroadcast:
    def broadcast(self, name, value):
        """
        If any reactive value from this object is changed, this method will be called
        """
        pass


if __name__ == '__main__':
    """
    reactive example
    """


    class Counter(ReactiveBroadcast):
        @reactive_source
        def value(self):
            return 1

        @reactive
        def doubled(self):
            # `doubled` will be immediately re-computed once `value` changed
            return self.value * 2

        def broadcast(self, name, value):
            # `broadcast` will be called once `value` or `doubled` changed
            print(f'property "{name}" changed to: {value}')


    counter = Counter()
    print(counter.value)
    # 1
    print(counter.doubled)
    # 2
    counter.value = 11
    # property "value" changed to: 11
    # property "doubled" changed to: 22
    counter.doubled = 2