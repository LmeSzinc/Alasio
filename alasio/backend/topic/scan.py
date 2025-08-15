import time
from typing import List

import trio

from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.table.scan import ConfigInfo, DndRequest, ScanTable
from alasio.ext.file.msgspecfile import deepcopy_msgpack
from alasio.ext.reactive.base_rpc import rpc
from alasio.ext.reactive.rx_trio import async_reactive, async_reactive_source
from alasio.ext.singleton import Singleton


class ConfigScanSource(metaclass=Singleton):
    def __init__(self):
        self.lastrun = 0
        self.lock = trio.Lock()

    async def scan(self, force=False):
        """
        Args:
            force:

        Returns:
            dict[str, ConfigInfo]:
        """
        now = time.time()
        if not force and now - self.lastrun < 5:
            return self.data

        async with self.lock:
            # Double lock check
            if not force:
                now = time.time()
                if now - self.lastrun < 5:
                    return self.data

            # call
            table = ScanTable()
            data = await trio.to_thread.run_sync(table.scan)
            # run_sync
            await ConfigScanSource.data.mutate(self, data)
            # record time after all awaits
            self.lastrun = time.time()
            return data

    @async_reactive_source
    async def data(self):
        return {}


class ConfigScan(BaseTopic):
    @async_reactive
    async def data(self):
        """
        Returns:
            dict[str, dict[str, Any]]:
                key: config_name
                value: ConfigInfo in dict
        """
        data = await ConfigScanSource().data
        return deepcopy_msgpack(data)

    async def getdata(self):
        # re-scan before getting data
        await ConfigScanSource().scan()
        return await self.data

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
        await ConfigScanSource().scan(force=True)

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
        await ConfigScanSource().scan(force=True)

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
        await ConfigScanSource().scan(force=True)

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
        await ConfigScanSource().scan(force=True)
