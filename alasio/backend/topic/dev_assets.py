import trio
from msgspec import Struct

from alasio.assets.manager import AssetsManager
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.ext.reactive.rx_trio import async_reactive, async_reactive_source


class ManagerState(Struct):
    mod_name: str = 'example_mod'
    path: str = 'assets/combat'


class DevAssetsManager(BaseTopic):
    @async_reactive_source
    async def assets_state(self):
        return ManagerState()

    @async_reactive
    async def data(self):
        state: ManagerState = await self.assets_state
        data = await trio.to_thread.run_sync(
            AssetsManager.get_data,
            state.mod_name, state.path
        )
        return data
