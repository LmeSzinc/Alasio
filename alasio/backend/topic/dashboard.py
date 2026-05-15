from typing import Any

import trio

from alasio.backend.reactive.base_msgbus import on_msgbus_config_event
from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.reactive.rx_trio import async_reactive_nocache
from alasio.backend.topic.state import ConnState
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.entry.loader import MOD_LOADER
from alasio.config.entry.model import ConfigSetEvent
from alasio.ext.deep import deep_iter_depth2, deep_set, deep_values_depth1


class Dashboard(BaseTopic):
    FULL_EVENT_ONLY = True
    # dict that convert config path to topic data path
    # key: (task, group, arg), value: str
    dict_config_to_topic = {}

    @async_reactive_nocache
    async def data(self):
        """
        Returns:
            dict[str, dict[str, dict[str, Any]]]:
                key: {card_name}.{group_name}.{arg_name}
                    first card is shown by default
                    rest of the cards only show if expanded
                {group_name}._info.dashboard is the dashboard type
        """
        state = ConnState(self.conn_id, self.server)
        mod_name = await state.mod_name
        config_name = await state.config_name
        if not mod_name or not config_name:
            return {}

        lang: str = await state.lang
        # call
        data: "dict[str, dict[str, Any]]" = await trio.to_thread.run_sync(
            MOD_LOADER.get_gui_config,
            mod_name, config_name, 'dashboard', lang
        )

        # convert config path to topic data path
        dict_config_to_topic = {}
        for group_name, arg_name, info in deep_iter_depth2(data):
            try:
                task = info['task']
                group = info['group']
                arg_value = info['value']
            except KeyError:
                # this shouldn't happen
                continue
            for dashboard_arg_data in deep_values_depth1(arg_value):
                try:
                    arg = dashboard_arg_data['arg']
                except KeyError:
                    # this shouldn't happen
                    continue
                dict_config_to_topic[(task, group, arg)] = group_name
        self.dict_config_to_topic = dict_config_to_topic

        return data

    @on_msgbus_config_event('ConfigArg')
    async def on_config_event(self, event: "ConfigSetEvent | list[ConfigSetEvent] | dict | list[dict]"):
        """
        Handle config event from msgbus
        """
        if isinstance(event, list):
            events = event
        else:
            events = [event]

        resps = []
        data = await self.data
        for e in events:
            # we may receive dict from worker, because it's decoded from bytes
            if type(e) is dict:
                e = ConfigSetEvent(**e)

            key = self.dict_config_to_topic.get((e.task, e.group, e.arg))
            if key is None:
                # not displaying this key
                continue

            topic_key = (key, e.group, 'value', e.arg, 'value')
            # set to topic data
            deep_set(data, keys=topic_key, value=e.value)
            # collect response
            resps.append(ResponseEvent(t=self.topic_name(), o='set', k=topic_key, v=e.value))

        if resps:
            if len(resps) == 1:
                await self.server.send(resps[0])
            else:
                await self.server.send(resps)
