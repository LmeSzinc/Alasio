import os.path
from copy import deepcopy
from typing import Dict

from msgspec import Struct, field

from alasio.ext.path.calc import joinnormpath


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
    path_config: str = 'module/config'
    # relative path from mod root to assets folder
    path_assets: str = 'assets'
    # valid file extension of assets
    # png is commonly used because jpg is a lossy compression
    asset_ext: Dict[str, None] = field(default_factory=lambda: dict.fromkeys(['.png', '.gif']))
    # valid lang (or server) of assets
    # ASSETS_LANG must not in ASSETS_EXT, must not be empty string, must not be a digit
    asset_lang: Dict[str, None] = field(default_factory=lambda: dict.fromkeys(['cn', 'en']))
    # valid languages on GUI
    gui_language: Dict[str, None] = field(default_factory=lambda: dict.fromkeys(['zh-CN', 'en-US']))

    def copy(self):
        """
        Returns:
            ModEntryInfo:
        """
        return deepcopy(self)

    def exist(self):
        """
        Returns:
            bool:
        """
        # Mod must have config.index.json
        file = joinnormpath(self.root, self.path_config)
        file = joinnormpath(file, 'config.index.json')
        return os.path.exists(file)

    def iter_asset_lang(self):
        """
        Yields:
            str: share first, then asset_lang
        """
        yield ''
        yield from self.asset_lang


# default mod entry
# key: mod name, value: ModEntryInfo
DICT_MOD_ENTRY = {m.name: m for m in [
    ModEntryInfo(name='example_mod', root='ExampleMod'),
    ModEntryInfo(
        name='alas',
        root='AzurlaneAutoScript',
        asset_lang=dict.fromkeys(['cn', 'en', 'jp', 'tw']),
        gui_language=dict.fromkeys(['zh-CN', 'en-US', 'ja-JP', 'zh-TW']),
    ),
    ModEntryInfo(
        name='src',
        root='StarRailCopilot',
        asset_lang=dict.fromkeys(['cn', 'en']),
        gui_language=dict.fromkeys(['zh-CN', 'en-US', 'ja-JP', 'zh-TW', 'es-ES']),
    ),
]}
