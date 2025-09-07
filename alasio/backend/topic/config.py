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

        data = await trio.to_thread.run_sync(
            MOD_LOADER.get_gui_nav,
            mod_name, lang
        )
        return data


class ConfigArg(BaseTopic):
    @async_reactive
    async def data(self):
        """
        Returns:
            dict[str, dict[str, dict]]:
                key: {card_name}.{arg_name}
                value: {
                    'task': task_name,
                    'group': group_name,
                    'arg': arg_name,
                    'dt': data_type, # see TYPE_DT_TO_PYTHON
                    'value': Any,
                    ...  # any others
                }
        """
        state = ConnState(self.conn_id, self.server)
        nav: NavState = await state.nav_state
        mod_name = nav.mod_name
        config_name = nav.config_name
        nav_name = nav.nav_name
        lang: str = await state.lang
        if not mod_name or not config_name or not nav_name:
            return {}

        data = await trio.to_thread.run_sync(
            MOD_LOADER.get_gui_config,
            mod_name, config_name, nav_name, lang
        )
        return data
