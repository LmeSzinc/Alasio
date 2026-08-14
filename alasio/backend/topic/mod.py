import trio
from msgspec import Struct
from msgspec.structs import asdict

from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.entry.loader import MOD_LOADER
from alasio.deploy.history.decode_history import decode_history
from alasio.ext.cache.resource import ResourceCacheTTL
from alasio.ext.path.atomic import atomic_read_bytes
from alasio.logger import logger


class ModOption(Struct):
    value: str
    label: str


class ModList(BaseTopic):
    # one-time-usage
    async def getdata(self):
        """
        Returns:
            list[ModOption]: List of mod names
        """
        dic_mod = MOD_LOADER.dict_mod
        return [ModOption(value=name, label=name) for name in dic_mod if name]


class HistoryCache(ResourceCacheTTL):
    def load_resource(self, file):
        """
        Load the packed release history of a mod

        Args:
            file (str): Path to .pack/history.pack

        Returns:
            list[HistoryObj]: Decoded history objects
        """
        return decode_history(atomic_read_bytes(file))


HISTORY_CACHE = HistoryCache()


class ModHistory(BaseTopic):
    # one-time-usage
    @classmethod
    def load_history(cls):
        """
        Traverse all mods and decode their packed release history.
        This is a synchronous function, call it in a thread to avoid blocking the event loop.

        Returns:
            dict[str, dict[str, Union[list[dict], str]]]:
                key: mod name
                value: {"data": list[dict], "error": str}
                    "data" is the release history of the mod, empty on error
                    "error" is the error message, only present on error
        """
        dic_mod = MOD_LOADER.dict_mod
        out = {}
        for name, mod in dic_mod.items():
            if not name:
                continue
            file = mod.root.joinpath('.pack/history.pack')
            try:
                history = HISTORY_CACHE.get(file)
            except Exception as e:
                logger.warning(f'Failed to load mod history "{file}": {e}')
                out[name] = {'data': [], 'error': f'{e.__class__.__name__}: {e}'}
                continue
            # HistoryObj is array_like, convert to dict to avoid encoding as a list
            out[name] = {'data': [asdict(obj) for obj in history]}
        return out

    async def getdata(self):
        """
        Returns:
            dict[str, dict[str, Union[list[dict], str]]]:
                key: mod name
                value: {"data": list[dict], "error": str}
                    "data" is the release history of the mod, empty on error
                    "error" is the error message, only present on error
        """
        return await trio.to_thread.run_sync(self.load_history)
