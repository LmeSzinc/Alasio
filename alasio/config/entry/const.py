import os.path
from copy import deepcopy
from typing import Dict, Union

from msgspec import Struct, field

from alasio.ext import env
from alasio.ext.cache import cached_property
from alasio.ext.path.calc import joinnormpath

# default mirrors of the PyPI simple index (PEP 503): the source of the
# distributions a mod installs with pip, when they do not update by pack.
# Two groups of the public mirrors: 'cn' (the mirrors of the mainland
# network) and 'global' (the official PyPI). The groups run in parallel, the
# members of a group are tried in the declared order, see
# alasio.deploy.httpclient.probe_mirrors. A mod whose distributions come from
# another source (e.g. a private index) overrides it on its entry
# (ModEntryInfo.pypi_mirrors); the mods that keep the default share one
# mirror record, see ModEntryInfo.pypi_record_name
DEFAULT_PYPI_MIRRORS = {
    'cn': {
        'tsinghua': 'https://pypi.tuna.tsinghua.edu.cn/simple',
        'aliyun': 'https://mirrors.aliyun.com/pypi/simple',
        'tencent': 'https://mirrors.cloud.tencent.com/pypi/simple',
    },
    'global': {
        'pypi': 'https://pypi.org/simple',
    },
}


class ModEntryInfo(Struct, dict=True):
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
    # relative path from mod root to mod scheduler
    path_main: str = 'module/main.py'
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
    # folders to scan for task entry functions, relative to mod root
    # default to scan "module" and "tasks" folders
    task_entry_folders: Dict[str, None] = field(default_factory=lambda: dict.fromkeys(['module', 'tasks']))
    # update source of the mod: the mirror structure of its update server,
    # see alasio.deploy.httpclient.probe_mirrors.Mirrors.from_input() for the
    # accepted forms (a single url, name to url, group to mirrors, and the
    # flat/nested mix). The mods with an empty value declare no update
    # source and are never checked. Defaults to ''
    mirrors: Union[str, Dict[str, str], Dict[str, Dict[str, str]]] = ''
    # mirrors of the PyPI simple index (PEP 503) the distributions of the mod
    # are downloaded from, when they do not update by pack: the same input
    # forms as mirrors, see
    # alasio.deploy.httpclient.probe_mirrors.Mirrors.from_input(). Defaults
    # to DEFAULT_PYPI_MIRRORS; a mod whose distributions come from another
    # source (e.g. a private index) overrides it. The mods that keep the
    # default share one mirror record, see pypi_record_name
    pypi_mirrors: Union[str, Dict[str, str], Dict[str, Dict[str, str]]] = field(
        default_factory=lambda: deepcopy(DEFAULT_PYPI_MIRRORS))
    # relative path from mod root to the requirements file of the mod: the
    # distributions it runs with, every one pinned to an exact version, see
    # alasio.deploy.simple_pip.install_base. Defaults to 'requirements.txt'
    requirements_file: str = 'requirements.txt'
    # distributions updated from the pack channel of the mod instead of pip:
    # the names of the requirements file, any case and separator style (the
    # compare is on the PEP 503 normalized name). A distribution not
    # declared here installs with pip. Defaults to empty, see
    # alasio.deploy.simple_pip.install_base
    pack_update_deps: Dict[str, None] = field(default_factory=dict)

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
        file = joinnormpath(file, '_index/config.index.json')
        return os.path.exists(file)

    def iter_asset_lang(self):
        """
        Yields:
            str: share first, then asset_lang
        """
        yield ''
        yield from self.asset_lang

    @cached_property
    def pypi_record_name(self):
        """
        The name of this mod in the record of the pypi mirror probes
        (alasio.deploy.httpclient.probe_db): the mods whose pypi mirrors are
        the default share one record (''), whichever of them probes first
        serves the others, the mirrors are probed once per machine instead of
        once per mod; a mod with mirrors of its own keeps a record of its own
        (the mod name), its selection never overwrites the shared one.

        The compare is on the digest of the mirror structure (the group
        boundaries and the mirror names in their order; the urls and the
        group names are not part of it, see Mirrors.digest): two entries
        share the record exactly when the probe engine would reuse it for
        both. Cached: the mirrors of an entry are fixed at creation.

        Returns:
            str: '' when the mirrors are the default, the mod name otherwise
        """
        # The import is local on purpose: the config layer is imported by
        # everything, it must not pull the deploy layer at import time
        from alasio.deploy.httpclient.probe_mirrors import Mirrors

        mirrors = Mirrors.from_input(self.pypi_mirrors)
        default = Mirrors.from_input(DEFAULT_PYPI_MIRRORS)
        if mirrors.digest == default.digest:
            return ''
        return self.name

    @classmethod
    def alasio(cls):
        return cls(
            name='alasio',
            root=env.ALASIO_ROOT,
            path_config='alasio/config',
            gui_language=dict.fromkeys(['zh-CN', 'en-US', 'ja-JP', 'zh-TW', 'en-ES']),
        )


class ConfigConst:
    SCHEDULER_PRIORITY = """
    RestartDevice > RestartGame
    """


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
