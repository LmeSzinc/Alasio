from threading import Lock
from time import time
from typing import Generic, TypeVar

T = TypeVar('T')


class ResourceCacheTTL(Generic[T]):
    def __init__(self):
        self._create_lock = Lock()
        self._cache: "dict[str, T]" = {}
        self._lock: "dict[str, Lock]" = {}
        self._last_use: "dict[str, float]" = {}

    def load_resource(self, file: str, **kwargs) -> T:
        """
        Load resource directly.
        Subclasses must implement this
        """
        raise NotImplementedError

    def get(self, file: str, **kwargs) -> T:
        """
        Get resource from cache
        If not in cache, load and cache it.
        """
        # fast path, return directly
        try:
            value = self._cache[file]
            self._last_use[file] = time()
            return value
        except KeyError:
            pass

        # create pre-resource lock
        with self._create_lock:
            if file in self._lock:
                try:
                    lock = self._lock[file]
                except KeyError:
                    # race condition
                    lock = Lock()
                    self._lock[file] = lock
            else:
                lock = Lock()
                self._lock[file] = lock

        with lock:
            # double-checked locking
            # check if the value was computed before the lock was acquired
            # check if in cache first to bypass except KeyError for faster because it's not in cache at happy path
            if file in self._cache:
                try:
                    value = self._cache[file]
                except KeyError:
                    # race condition
                    pass
                else:
                    self._last_use[file] = time()
                    # remove pre-resource lock to reduce memory
                    try:
                        del self._lock[file]
                    except KeyError:
                        pass
                    return value

            # load
            value = self.load_resource(file, **kwargs)
            self._cache[file] = value
            self._last_use[file] = time()
            # remove pre-resource lock to reduce memory
            try:
                del self._lock[file]
            except KeyError:
                pass
            return value

    def gc(self, idle=60):
        """
        Release resources that have not been used for more than 60s
        """
        # iter `_cache` instead of `_last_use` to avoid orphan cache
        try:
            files = list(self._cache)
        except RuntimeError:
            # race condition that another thread is inserting _cache
            # let the next gc call to do the job
            return False

        outdated = time() - idle
        last_use = self._last_use
        for file in files:
            try:
                if last_use[file] > outdated:
                    # still using
                    continue
            except KeyError:
                # orphan cache, consider as outdated
                pass

            # clear cache
            try:
                del self._cache[file]
            except KeyError:
                pass
            try:
                del last_use[file]
            except KeyError:
                pass


class ResourceCache(Generic[T]):
    def __init__(self):
        self._create_lock = Lock()
        self._cache: "dict[str, T]" = {}
        self._lock: "dict[str, Lock]" = {}

    def load_resource(self, file: str, **kwargs) -> T:
        """
        Load resource directly.
        Subclasses must implement this
        """
        raise NotImplementedError

    def get(self, file: str, **kwargs) -> T:
        """
        Get resource from cache
        If not in cache, load and cache it.
        """
        # fast path, return directly
        try:
            return self._cache[file]
        except KeyError:
            pass

        # create pre-resource lock
        with self._create_lock:
            if file in self._lock:
                try:
                    lock = self._lock[file]
                except KeyError:
                    # race condition
                    lock = Lock()
                    self._lock[file] = lock
            else:
                lock = Lock()
                self._lock[file] = lock

        with lock:
            # double-checked locking
            # check if the value was computed before the lock was acquired
            # check if in cache first to bypass except KeyError for faster because it's not in cache at happy path
            if file in self._cache:
                try:
                    value = self._cache[file]
                except KeyError:
                    # race condition
                    pass
                else:
                    # remove pre-resource lock to reduce memory
                    try:
                        del self._lock[file]
                    except KeyError:
                        pass
                    return value

            # load
            value = self.load_resource(file, **kwargs)
            self._cache[file] = value
            # remove pre-resource lock to reduce memory
            try:
                del self._lock[file]
            except KeyError:
                pass
            return value

    def gc(self):
        """
        Clear all resources from cache.
        """
        # .clear() is an atomic operation in CPython and thus thread-safe.
        self._cache.clear()
