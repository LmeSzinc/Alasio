from copy import deepcopy

from msgspec import Struct


class ModEntryInfo(Struct):
    """
    ModEntryInfo
    """
    # mod name
    name: str
    # absolute path to mod root folder, or relative path from running folder to mod root folder
    # default to ""
    root: str = ''
    # relative path from mod root to config folder,
    # where you have config.index.json, nav.index.json, model.index.json, and all config definitions
    # default to "module/config"
    path_config: str = 'module/config'

    def copy(self):
        """
        Returns:
            ModEntryInfo:
        """
        return deepcopy(self)


# default mod entry
# key: mod name, value: ModEntryInfo
DICT_MOD_ENTRY = {m.name: m for m in [
    ModEntryInfo(name='example_mod', root='ExampleMod'),
    ModEntryInfo(name='alas', root='AzurlaneAutoScript'),
    ModEntryInfo(name='src', root='StarRailCopilot'),
]}
