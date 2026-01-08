from typing import Any

import trio.to_thread
from msgspec import ValidationError

from alasio.backend.reactive.base_msgbus import on_msgbus_config_event
from alasio.backend.reactive.base_rpc import rpc
from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.reactive.rx_trio import async_reactive_nocache
from alasio.backend.topic.state import ConnState, NavState
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.entry.loader import MOD_LOADER
from alasio.config.entry.mod import ConfigSetEvent
from alasio.ext.deep import deep_iter_depth2, deep_set


class ConfigNav(BaseTopic):
    FULL_EVENT_ONLY = True

    @async_reactive_nocache
    async def data(self):
        """
        Returns:
            dict[str, dict[str, str]]:
                key: {nav_name}.{card_name}
                value: translation
        """
        state = ConnState(self.conn_id, self.server)
        mod_name = await state.mod_name
        lang = await state.lang
        if not mod_name:
            return {}

        data = await trio.to_thread.run_sync(
            MOD_LOADER.get_gui_nav,
            mod_name, lang
        )
        return data


class ConfigArg(BaseTopic):
    FULL_EVENT_ONLY = True
    # dict that convert config path to topic data path
    # key: (task, group, arg), value: (card_name, arg_name)
    dict_config_to_topic = {}

    @async_reactive_nocache
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
        mod_name = await state.mod_name
        config_name = await state.config_name
        nav_name = await state.nav_name
        if not mod_name or not config_name or not nav_name:
            return {}

        lang: str = await state.lang
        # call
        data = await trio.to_thread.run_sync(
            MOD_LOADER.get_gui_config,
            mod_name, config_name, nav_name, lang
        )

        # convert config path to topic data path
        dict_config_to_topic = {}
        for card_name, arg_name, info in deep_iter_depth2(data):
            try:
                task = info['task']
                group = info['group']
                arg = info['arg']
            except KeyError:
                # this shouldn't happen
                continue
            dict_config_to_topic[(task, group, arg)] = (card_name, arg_name)
        self.dict_config_to_topic = dict_config_to_topic

        return data

    @on_msgbus_config_event('ConfigArg')
    async def on_config_event(self, event: ConfigSetEvent):
        """
        Handle config event from msgbus
        """
        key = self.dict_config_to_topic.get((event.task, event.group, event.arg))
        if key is None:
            # not displaying this key
            return None
        key = (*key, 'value')
        data = await self.data
        # set to topic data
        deep_set(data, keys=key, value=event.value)
        # send to frontend
        event = ResponseEvent(t=self.topic_name(), o='set', k=key, v=event.value)
        await self.server.send(event)

    @rpc
    async def set(self, task: str, group: str, arg: str, value: Any):
        if not task or not group or not arg:
            return
        # get config_name
        state = ConnState(self.conn_id, self.server)
        nav: NavState = await state.nav_state
        mod_name = nav.mod_name
        config_name = nav.config_name
        if not config_name:
            return

        # call
        success, resp = await trio.to_thread.run_sync(
            MOD_LOADER.gui_config_set,
            mod_name, config_name, task, group, arg, value
        )
        resp: ConfigSetEvent
        # logger.info([success, resp])
        if success:
            # broadcast to all connections
            await self.msgbus_config_asend(self.topic_name(), config_name, resp)
        else:
            # rollback self
            key = self.dict_config_to_topic.get((resp.task, resp.group, resp.arg))
            if key is None:
                # not displaying this key
                return
            key = (*key, 'value')
            event = ResponseEvent(t=self.topic_name(), o='set', k=key, v=resp.value)
            await self.server.send(event)
            # re-raise error, so server will treat as RPC call failed
            if resp.error is not None:
                msg = resp.error.msg
            else:
                msg = 'Unknown validation error'
            raise ValidationError(msg)

    @rpc
    async def reset(self, task: str, group: str, arg: str):
        if not task or not group or not arg:
            return
        # get config_name
        state = ConnState(self.conn_id, self.server)
        nav: NavState = await state.nav_state
        mod_name = nav.mod_name
        config_name = nav.config_name
        if not config_name:
            return

        # call
        resp = await trio.to_thread.run_sync(
            MOD_LOADER.gui_config_reset,
            mod_name, config_name, task, group, arg
        )
        # resp: ConfigSetEvent | None
        if resp is None:
            # reset failed, do nothing
            return

        # broadcast to all connections
        await self.msgbus_config_asend(self.topic_name(), config_name, resp)
