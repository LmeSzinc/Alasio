from typing import Any

import trio

from alasio.backend.reactive.base_msgbus import on_msgbus_config_event
from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.reactive.rx_trio import async_reactive_nocache
from alasio.backend.topic.state import ConnState
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.entry.loader import MOD_LOADER
from alasio.config.entry.mod import ConfigSetEvent
from alasio.ext.deep import deep_iter_depth2, deep_set, deep_values_depth1


def get_first_card(gui_config: "dict[str, dict[str, Any]]") -> "dict[str, Any] | None":
    for card in deep_values_depth1(gui_config):
        return card
    return None


class Dashboard(BaseTopic):
    FULL_EVENT_ONLY = True
    # dict that convert config path to topic data path
    # key: (task, group, arg), value: str
    dict_config_to_topic = {}

    @async_reactive_nocache
    async def data(self):
        """
        Returns:
            dict[str, dict[str, dict]]:
                key: {index}.{arg_name}
                    index=0 is shown by default
                    index>0 only show if expanded
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

        # get first card and remote info
        data = get_first_card(data)
        if data is None:
            return {}
        data.pop('_info', None)

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
    async def on_config_event(self, event: "ConfigSetEvent | dict"):
        """
        Handle config event from msgbus
        """
        # we may receive dict from worker, because it's decoded from bytes
        if type(event) is dict:
            event = ConfigSetEvent(**event)
        key = self.dict_config_to_topic.get((event.task, event.group, event.arg))
        if key is None:
            # not displaying this key
            return None
        key = (key, event.group, 'value', event.arg, 'value')
        data = await self.data
        # set to topic data
        deep_set(data, keys=key, value=event.value)
        # send to frontend
        event = ResponseEvent(t=self.topic_name(), o='set', k=key, v=event.value)
        await self.server.send(event)
