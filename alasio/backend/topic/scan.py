from typing import List

import trio

from alasio.backend.reactive.base_rpc import rpc
from alasio.backend.reactive.event_cache import GlobalEventCache
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.entry.loader import MOD_LOADER
from alasio.config.table.scan import ConfigInfo, DndRequest, ScanTable
from alasio.logger import logger


class ConfigScanSource(GlobalEventCache):
    TOPIC = 'ConfigScan'
    data: "dict[str, ConfigInfo]"

    def on_init(self):
        table = ScanTable()
        return table.scan()

    def _create_default_config(self):
        # keep using dict to keep mod sorting
        all_mod = MOD_LOADER.dict_mod.copy()
        # access self.data with thread safe
        data = self.data.copy()
        for config in data.values():
            all_mod.pop(config.name, None)
        if not all_mod:
            return

        logger.info(f'Create default config: {list(all_mod)}')
        table = ScanTable()
        # create self mod first
        self_mod = MOD_LOADER.self_mod
        if self_mod:
            mod_name = all_mod.pop(self_mod.name, None)
            if mod_name:
                table.config_add(mod_name, mod_name)
        # create sub mods
        for mod_name in all_mod:
            table.config_add(mod_name, mod_name)

    @classmethod
    async def create_default_config(cls):
        """
        Create default config for each mod, named with mod name.
        So user can easily find an entry when having a new mod.
        """
        self = cls()
        await self._fetch_init()
        await trio.to_thread.run_sync(self._create_default_config)
        await self.reinit()


class ConfigScan(BaseTopic):
    async def op_sub(self):
        cache = ConfigScanSource()
        await cache.subscribe(self)

    async def op_unsub(self):
        cache = ConfigScanSource()
        cache.unsubscribe(self)

    @rpc
    async def config_add(self, name: str, mod: str):
        """
        Create a new config file.

        Args:
            name (str): Config name
            mod (str): Module name
        """
        # Run the synchronous ScanTable.config_add in a thread
        scan_table = ScanTable()
        await trio.to_thread.run_sync(scan_table.config_add, name, mod)

        # Force rescan to update the data and notify observers
        await ConfigScanSource().reinit()

    @rpc
    async def config_copy(self, old_name: str, new_name: str):
        """
        Copy an existing config as a new config.

        Args:
            old_name (str): Source config name
            new_name (str): Target config name
        """
        # Run the synchronous ScanTable.config_copy in a thread
        scan_table = ScanTable()
        await trio.to_thread.run_sync(scan_table.config_copy, old_name, new_name)

        # Force rescan to update the data and notify observers
        await ConfigScanSource().reinit()

    @rpc
    async def config_rename(self, old_name: str, new_name: str):
        """
        Rename an existing config.

        Args:
            old_name (str): Source config name
            new_name (str): Target config name
        """
        # Run the synchronous ScanTable.config_copy in a thread
        scan_table = ScanTable()
        await trio.to_thread.run_sync(scan_table.config_rename, old_name, new_name)

        # Force rescan to update the data and notify observers
        await ConfigScanSource().reinit()

    @rpc
    async def config_del(self, name: str):
        """
        Delete a config file.

        Args:
            name (str): Config name to delete
        """
        # Run the synchronous ScanTable.config_del in a thread
        scan_table = ScanTable()
        await trio.to_thread.run_sync(scan_table.config_del, name)

        # Force rescan to update the data and notify observers
        await ConfigScanSource().reinit()

    @rpc
    async def config_dnd(self, configs: List[DndRequest]):
        """
        Drag and drop configs.
        Requests will be treated as instructions that where would the user like to insert this config onto.
        ScanTable will re-sort all configs based on user's request.

        Args:
            configs (List[DndRequest]): List of drag and drop requests
        """
        # Run the synchronous ScanTable.config_dnd in a thread
        scan_table = ScanTable()
        await trio.to_thread.run_sync(scan_table.config_dnd, configs)

        # Force rescan to update the data and notify observers
        await ConfigScanSource().reinit()
