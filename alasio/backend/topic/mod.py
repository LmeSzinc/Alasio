from msgspec import Struct

from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.entry.loader import MOD_LOADER


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
