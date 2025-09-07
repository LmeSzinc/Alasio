from msgspec import Struct

from alasio.backend.topic.scan import ConfigScan
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.const import Const
from alasio.config.table.scan import validate_config_name
from alasio.ext.deep import deep_get
from alasio.ext.locale.accept_language import negotiate_accept_language
from alasio.ext.reactive.base_rpc import rpc
from alasio.ext.reactive.event import RpcValueError
from alasio.ext.reactive.rx_trio import async_reactive_source


class NavState(Struct):
    config_name: str = ''
    mod_name: str = ''
    nav_name: str = ''


class ConnState(BaseTopic):
    @async_reactive_source
    async def lang(self) -> str:
        return 'en-US'

    @rpc
    async def set_lang(self, lang: str):
        use = negotiate_accept_language(lang, Const.GUI_LANGUAGE)
        if not use:
            raise RpcValueError(f'Language "{lang}" does not match any available languages')
        # set
        await self.__class__.lang.mutate(self, lang)

    @async_reactive_source
    async def nav_state(self):
        return NavState()

    @rpc
    async def set_config(self, name: str):
        """
        Set config name, and change nav_state accordingly
        """
        # check if name is a validate filename
        error = validate_config_name(name)
        if error:
            raise RpcValueError(error)

        state: NavState = await self.nav_state
        if state.config_name == name:
            # same config, no need to change it
            return

        # get current configs
        data = await ConfigScan(self.conn_id).data
        if name not in data:
            raise RpcValueError(f'No such config: "{name}"')

        # set
        # note that mod_name is calculated in backend to ensure consistency of mod_name and config_name
        mod_name = deep_get(data, keys=[name, 'mod'], default='')
        state.config_name = name
        state.mod_name = mod_name
        # reset nav when switching to new config
        state.nav_name = ''

        # broadcast
        await self.__class__.nav_state.mutate(self, state)

    @rpc
    async def set_nav(self, name: str):
        """
        Set config name, and change nav_state accordingly
        """
        state: NavState = await self.nav_state
        if state.nav_name == name:
            # same nav, no need to change it
            return

        # maybe we don't need to validate nav_name, as ConfigArg can handle
        state.nav_name = name

        # broadcast
        await self.__class__.nav_state.mutate(self, state)
