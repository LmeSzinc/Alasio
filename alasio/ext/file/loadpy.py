import os.path
import threading
from collections import defaultdict
from typing import Any

from alasio.ext.path.calc import get_stem


def loadpy(file):
    """
    Dynamically load a python file.
    Note that you should use this function very carefully, remember:
        1. Target python file cannot have relative import syntax like "from . import xxx"
            because it has no parent package.
        2. You should always use the module in the minimum scope.
            If you do:
                obj.data = module.data
            the entire module won't get garbage collected, because it's referenced. You will have memory leak.
        3. Each loadpy() call will create a new module, even if they load the same file.
            To avoid duplicate module and to be threadsafe, use class LoadpyCache.

    Args:
        file (str): Absolute filepath to python file

    Returns:
        a python module object

    Raises:
        ImportError: if encounter any error
    """
    name = get_stem(file)
    # file can't be subclasses of str
    file = str(file)

    import importlib.util

    # create spec
    spec = importlib.util.spec_from_file_location(name, file)
    if spec is None:
        # path invalid or not endswith .py
        raise ImportError(f'Could not create import spec for file "{file}"')
    if spec.loader is None:
        raise ImportError(f'Could not create spec.loader for file "{file}"')

    # create module object
    module = importlib.util.module_from_spec(spec)

    # import
    try:
        spec.loader.exec_module(module)
    except FileNotFoundError as e:
        # no such file
        raise ImportError(str(e))
    except PermissionError as e:
        if os.path.isdir(file):
            # target is a directory
            raise ImportError(f'Filepath to load is not a file {file}')
        else:
            # other permission errors
            raise ImportError(str(e))
    except ImportError as e:
        msg = str(e)
        if 'no known parent package' in msg:
            # ImportError: attempted relative import with no known parent package
            raise ImportError(
                'loadpy() cannot load files that has relative import syntax like "from . import xxx"') from e
        else:
            raise
    except Exception as e:
        raise ImportError(f'Could not load file {file}') from e

    return module


class LoadpyCache:
    def __init__(self):
        # all cache
        # key: filepath, value: cache
        self.cache: "dict[str, Any]" = {}
        # global lock to create per-file lock
        self.create_lock = threading.Lock()
        # per-file lock
        # key: filepath, value: lock
        self.dict_lock: "dict[str, threading.Lock]" = defaultdict(threading.Lock)

    def loadpy(self, file):
        """
        Dynamically load a python file.
        see loadpy()

        Args:
            file (str): Absolute filepath to python file

        Returns:
            a python module object

        Raises:
            ImportError: if encounter any error
        """
        # Quick access, no global lock
        try:
            return self.cache[file]
        except KeyError:
            pass

        # get file lock, defaultdict.__getitem__ might not be threadsafe
        with self.create_lock:
            lock = self.dict_lock[file]

        # read file
        with lock:
            # Another thread may have read file when current thread was waiting for lock
            try:
                data = self.cache[file]
                # if we success to get from cache, remove file lock
                try:
                    del self.dict_lock[file]
                except KeyError:
                    # already deleted by another thread
                    pass
                return data
            except KeyError:
                pass

            # Read file
            data = loadpy(file)
            self.cache[file] = data

        # remove file lock
        # no global lock because dict set/get/del are threadsafe
        try:
            del self.dict_lock[file]
        except KeyError:
            # already deleted by another thread
            pass

        return data

    def gc(self):
        """
        clear all cache
        """
        with self.create_lock:
            self.dict_lock.clear()
            self.cache.clear()
        import gc
        gc.collect()


LOADPY_CACHE = LoadpyCache()
