from typing import List, Optional

import trio
from msgspec import Struct

from alasio.assets.manager import AssetsManager
from alasio.assets.model.folder import AssetFolder
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.entry.loader import MOD_LOADER
from alasio.ext.reactive.base_rpc import rpc
from alasio.ext.reactive.event import RpcValueError
from alasio.ext.reactive.rx_trio import async_reactive, async_reactive_nocache, async_reactive_source


class ManagerState(Struct):
    mod_name: str = ''
    path: str = ''


class DevAssetsManager(BaseTopic):
    @async_reactive_source
    async def assets_state(self):
        state = ManagerState()
        # select first mod
        if not state.mod_name:
            for mod in MOD_LOADER.dict_mod.values():
                state.mod_name = mod.name
                # init path to assets
                path_assets = mod.entry.path_assets
                if not state.path.startswith(path_assets):
                    state.path = path_assets
                break

        return state

    @async_reactive
    async def asset_folder(self) -> Optional[AssetFolder]:
        state: ManagerState = await self.assets_state
        if not state.mod_name:
            return None

        try:
            folder = AssetsManager.get_folder_manager(state.mod_name, state.path)
        except ValueError:
            # path invalid
            return None
        return folder

    @async_reactive_nocache
    async def data(self):
        folder = await self.asset_folder
        if folder is None:
            return {}

        data = await trio.to_thread.run_sync(folder.getdata)
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

    @rpc
    async def resource_add_base64(self, source: str, data: str):
        """
        Add a resource from a base64 encoded string.
        """
        folder: "AssetFolder | None" = await self.asset_folder
        if folder is None:
            raise RpcValueError('Folder not initialized')

        try:
            await trio.to_thread.run_sync(folder.resource_add_base64, source, data)
        except ValueError as e:
            raise RpcValueError(str(e))
        await self.__class__.data.mutate(self)

    @rpc
    async def resource_del(self, names: List[str]):
        """
        Delete resources without tracking their usage.
        """
        folder: "AssetFolder | None" = await self.asset_folder
        if folder is None:
            raise RpcValueError('Folder not initialized')

        try:
            await trio.to_thread.run_sync(folder.resource_del_force, names)
        except ValueError as e:
            raise RpcValueError(str(e))
        await self.__class__.data.mutate(self)

    @rpc
    async def resource_track(self, names: List[str]):
        folder: "AssetFolder | None" = await self.asset_folder
        if folder is None:
            raise RpcValueError('Folder not initialized')

        try:
            await trio.to_thread.run_sync(folder.resource_track, names)
        except ValueError as e:
            raise RpcValueError(str(e))
        await self.__class__.data.mutate(self)

    @rpc
    async def resource_untrack(self, names: List[str]):
        folder: "AssetFolder | None" = await self.asset_folder
        if folder is None:
            raise RpcValueError('Folder not initialized')

        try:
            await trio.to_thread.run_sync(folder.resource_untrack_force, names)
        except ValueError as e:
            raise RpcValueError(str(e))
        await self.__class__.data.mutate(self)

    @rpc
    async def resource_to_asset(self, source: List[str], override: bool = False):
        """
        Convert a resource file to a new asset.
        """
        folder: "AssetFolder | None" = await self.asset_folder
        if folder is None:
            raise RpcValueError('Folder not initialized')

        try:
            await trio.to_thread.run_sync(folder.resource_to_asset, source, override)
        except ValueError as e:
            raise RpcValueError(str(e))

        await self.__class__.data.mutate(self)

    @rpc
    async def asset_add(self, asset: str):
        """
        Create a new empty asset.
        """
        folder: "AssetFolder | None" = await self.asset_folder
        if folder is None:
            raise RpcValueError('Folder not initialized')

        try:
            await trio.to_thread.run_sync(folder.asset_add, asset)
        except ValueError as e:
            raise RpcValueError(str(e))

        await self.__class__.data.mutate(self)

    @rpc
    async def asset_del(self, asset: List[str]):
        """
        Delete an asset and its associated template files.
        """
        folder: "AssetFolder | None" = await self.asset_folder
        if folder is None:
            raise RpcValueError('Folder not initialized')

        try:
            await trio.to_thread.run_sync(folder.asset_del, asset)
        except ValueError as e:
            raise RpcValueError(str(e))

        await self.__class__.data.mutate(self)
