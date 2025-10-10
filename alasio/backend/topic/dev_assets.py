import trio
from msgspec import Struct

from alasio.assets.manager import AssetsManager
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.entry.loader import MOD_LOADER
from alasio.ext.reactive.base_rpc import rpc
from alasio.ext.reactive.event import RpcValueError
from alasio.ext.reactive.rx_trio import async_reactive_nocache, async_reactive_source


class ManagerState(Struct):
    mod_name: str = ''
    path: str = ''


class DevAssetsManager(BaseTopic):
    @async_reactive_source
    async def assets_state(self):
        state = ManagerState()
        # select first mod
        if not state.mod_name:
            for mod_name in MOD_LOADER.dict_mod:
                state.mod_name = mod_name
                break
        # init path to assets
        try:
            mod = MOD_LOADER.dict_mod[state.mod_name]
        except KeyError:
            # no mods
            pass
        else:
            path_assets = mod.entry.path_assets
            if not state.path.startswith(path_assets):
                state.path = path_assets

        return state

    @async_reactive_nocache
    async def data(self):
        state: ManagerState = await self.assets_state
        if not state.mod_name:
            return {}

        data = await trio.to_thread.run_sync(
            AssetsManager.get_data,
            state.mod_name, state.path
        )
        return data

    @rpc
    async def set_mod(self, mod_name: str):
        """
        Set mod_name
        """
        try:
            mod = MOD_LOADER.dict_mod[mod_name]
        except KeyError:
            raise RpcValueError(f'No such mod: "{mod_name}"')

        state: ManagerState = await self.assets_state
        # set mod
        state.mod_name = mod_name
        state.path = mod.entry.path_assets
        await self.__class__.assets_state.mutate(self, state)

    @rpc
    async def set_path(self, path: str):
        """
        Set mod_name
        """
        state: ManagerState = await self.assets_state
        state.path = path

        # validate path
        try:
            _ = AssetsManager.get_folder_manager(state.mod_name, state.path)
        except ValueError as e:
            # path invalid
            raise RpcValueError(e)

        await self.__class__.assets_state.mutate(self, state)
