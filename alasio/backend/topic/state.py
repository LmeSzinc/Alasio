from collections import defaultdict

from msgspec import Struct

from alasio.backend.topic.scan import ConfigScan
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.const import Const
from alasio.config.table.scan import validate_config_name
from alasio.ext.deep import deep_get
from alasio.ext.locale.accept_language import negotiate_accept_language
from alasio.ext.reactive.base_rpc import rpc
from alasio.ext.reactive.event import RpcValueError
from alasio.ext.reactive.rx_trio import async_reactive, async_reactive_source

# dict to speedup message backwards from worker to connection
# key: config_name, value: set of conn_id
DICT_CONFIG_TO_CONN: "dict[str, set[str]]" = defaultdict(set)


class NavState(Struct):
    lang: str = 'en-US'
    config_name: str = ''
    mod_name: str = ''
    nav_name: str = ''


class ConnState(BaseTopic):
    @async_reactive_source
    async def nav_state(self):
        return NavState()

    # Proxy reactive nav_state,
    # so one modification to nav_state won't trigger mutation to all topics listening to nav_state
    @async_reactive
    async def lang(self) -> str:
        state = await self.nav_state
        return state.lang

    @async_reactive
    async def config_name(self) -> str:
        state = await self.nav_state
        return state.config_name

    @async_reactive
    async def mod_name(self) -> str:
        state = await self.nav_state
        return state.mod_name

    @async_reactive
    async def nav_name(self) -> str:
        state = await self.nav_state
        return state.nav_name

    @rpc
    async def set_lang(self, lang: str):
        use = negotiate_accept_language(lang, Const.GUI_LANGUAGE)
        if not use:
            raise RpcValueError(f'Language "{lang}" does not match any available languages')
        # set
        state: NavState = await self.nav_state
        state.lang = lang
        await self.__class__.nav_state.mutate(self, state)

    async def op_unsub(self):
        await super().op_unsub()
        # maintain DICT_CONFIG_TO_CONN
        for connections in DICT_CONFIG_TO_CONN.values():
            if self.conn_id in connections:
                connections.remove(self.conn_id)

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

        # maintain DICT_CONFIG_TO_CONN
        if state.config_name:
            DICT_CONFIG_TO_CONN[state.config_name].remove(self.conn_id)
        if name:
            DICT_CONFIG_TO_CONN[name].add(self.conn_id)

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
