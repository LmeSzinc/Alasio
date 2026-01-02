import time
from typing import List

import trio

from alasio.backend.reactive.base_msgbus import on_msgbus_global_event
from alasio.backend.reactive.base_rpc import rpc
from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.table.scan import ConfigInfo, DndRequest, ScanTable
from alasio.ext.singleton import Singleton


class ConfigScanSource(metaclass=Singleton):
    def __init__(self):
        self.lastrun = 0
        self.data: "dict[str, ConfigInfo]" = {}
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
            # broadcast
            topic = 'ConfigScan'
            if self.data and data != self.data:
                # send on updates only, because full event is already send on topic subscription
                # send full event to avoid data bouncing after drag and drop
                event = ResponseEvent(t=topic, o='full', v=data)
                await BaseTopic.msgbus_global_asend(topic, event)
            # record time after all awaits
            self.data = data
            self.lastrun = time.time()
            return data


class ConfigScan(BaseTopic):
    async def getdata(self):
        # re-scan before getting data
        return await ConfigScanSource().scan()

    @on_msgbus_global_event('ConfigScan')
    async def on_scan_diff(self, value: ResponseEvent):
        await self.server.send(value)

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
