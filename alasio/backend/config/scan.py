import time

import trio

from alasio.config.table.scan import ScanTable
from alasio.ext.file.msgspecfile import deepcopy_msgpack
from alasio.ext.reactive.rx_trio import async_reactive, async_reactive_source
from alasio.ext.reactive.ws_topic import BaseTopic
from alasio.ext.singleton import Singleton


class ConfigScanSource(metaclass=Singleton):
    def __init__(self):
        self.lastrun = 0
        self.lock = trio.Lock()

    async def scan(self):
        now = time.time()
        if now - self.lastrun < 5:
            return self.data

        async with self.lock:
            # Double lock check
            now = time.time()
            if now - self.lastrun < 5:
                return self.data

            # call
            data = ScanTable().scan()
            # run_sync
            await ConfigScanSource.data.mutate(self, data)
            return data

    @async_reactive_source
    async def data(self):
        return {}


class ConfigScan(BaseTopic):
    @async_reactive
    async def data(self):
        data = await ConfigScanSource().data
        return deepcopy_msgpack(data)

    async def getdata(self):
        # re-scan before getting data
        await ConfigScanSource().scan()
        return await self.data
