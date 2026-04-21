import threading
from hashlib import sha256
from typing import Union

from yarl import URL


class CrawlerDeduplicator:
    def __init__(self):
        self._lock = threading.Lock()
        self._history = {}

    def clear_history(self):
        with self._lock:
            self._history.clear()

    def get_key(self, func: "callable", url: Union[URL, str]) -> str:
        """
        Args:
            func:
            url:

        Returns:
            str: function digest key like
                __main__:CrawlerDeduplicator:2471302636352:CrawlerDeduplicator.get_key:url
                __main__::0:CrawlerDeduplicator.get_key:url
        """
        module_name = func.__module__
        func_name = func.__qualname__
        obj = getattr(func, '__self__', None)
        if obj is None:
            obj_name = ''
            obj_id = 0
        else:
            obj_name = obj.__class__.__name__
            obj_id = id(obj)

        key = f'{module_name}:{obj_name}:{obj_id}:{func_name}:{url}'
        return key

    def check_and_add(self, func: "callable", url: Union[URL, str], key: str = None):
        """
        Args:
            func:
            url:
            key:

        Returns:
            bool: False if duplicate
        """
        if key is None:
            key = self.get_key(func, url)
        digest = sha256(key.encode()).digest()
        with self._lock:
            if digest in self._history:
                return False
            self._history[digest] = None
            return True


CRAWLER_DEDUPLICATOR = CrawlerDeduplicator()
