import trio.to_thread

from alasio.backend.topic.state import ConnState, NavState
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.entry.loader import MOD_LOADER
from alasio.ext.reactive.rx_trio import async_reactive


class ConfigNav(BaseTopic):
    @async_reactive
    async def data(self):
        """
        Returns:
            dict[str, dict[str, str]]:
                key: {nav_name}.{card_name}
                value: translation
        """
        state = ConnState(self.conn_id, self.server)
        nav_state: NavState = await state.nav_state
        mod_name = nav_state.mod_name
        lang: str = await state.lang
        if not mod_name:
            return {}

        # for dev now
        mod_name = 'alasio'
        data = await trio.to_thread.run_sync(MOD_LOADER.get_gui_nav, mod_name, lang)
        return data
