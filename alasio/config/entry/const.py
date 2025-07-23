from copy import deepcopy

from msgspec import Struct


class ModEntryInfo(Struct):
    """
    ModEntryInfo
    """
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
# key: mod name, value: path to mod root folder
DICT_MOD_ENTRY = {
    'alasio': ModEntryInfo(path_config='config_alasio'),
}
