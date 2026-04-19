from collections import deque
from typing import Any

import trio.to_thread
from msgspec import ValidationError

from alasio.backend.reactive.base_msgbus import on_msgbus_config_event
from alasio.backend.reactive.base_rpc import rpc
from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.reactive.rx_trio import async_reactive_nocache
from alasio.backend.topic.state import ConnState, NavState
from alasio.backend.worker.event import ConfigEvent
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.entry.loader import MOD_LOADER
from alasio.config.entry.mod import ConfigSetEvent
from alasio.ext.deep import deep_get, deep_iter, deep_set


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
    # key: (task, group, arg), value: (card_name, group_name, arg_name)
    dict_config_to_topic = {}

    @async_reactive_nocache
    async def data(self):
        """
        Returns:
            dict[str, dict[str, dict[str, dict]]]:
                key: {card_name}.{group_name}.{arg_name}
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
        for keys, info in deep_iter(data, depth=3):
            card_name, group_name, arg_name = keys
            if group_name == '_info':
                continue
            try:
                task = info['task']
                group = info['group']
                arg = info['arg']
            except KeyError:
                # this shouldn't happen
                continue
            dict_config_to_topic[(task, group, arg)] = (card_name, group_name, arg_name)
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

            topic_key = (*key, 'value')
            # set to topic data
            deep_set(data, keys=topic_key, value=e.value)
            # collect response
            resps.append(ResponseEvent(t=self.topic_name(), o='set', k=topic_key, v=e.value))

        if resps:
            if len(resps) == 1:
                await self.server.send(resps[0])
            else:
                await self.server.send(resps)

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
            event = ConfigEvent(t=self.topic_name(), c=config_name, v=resp)
            await self.msgbus_config_asend(event)
            await self.msgbus_global_asend(self.topic_name(), event)
        else:
            # rollback self
            key = self.dict_config_to_topic.get((resp.task, resp.group, resp.arg))
            if key is None:
                # not displaying this key
                return
            key = (*key, 'value')
            data = await self.data
            prev = deep_get(data, key, default=resp.value)
            resp_event = ResponseEvent(t=self.topic_name(), o='set', k=key, v=prev)
            await self.server.send(resp_event)
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
        event = ConfigEvent(t=self.topic_name(), c=config_name, v=resp)
        await self.msgbus_config_asend(event)
        await self.msgbus_global_asend(self.topic_name(), event)

    @rpc
    async def group_reset(self, card: str):
        if not card:
            return
        # get config_name
        state = ConnState(self.conn_id, self.server)
        nav: NavState = await state.nav_state
        mod_name = nav.mod_name
        config_name = nav.config_name
        nav_name = nav.nav_name
        if not config_name or not nav_name:
            return

        # get all task-group within card
        # copy to avoid modification during iterating, group reset is rarely used so copy is acceptable
        list_task_group = deque()
        dict_config_to_topic = self.dict_config_to_topic.copy()
        for key, value in dict_config_to_topic.items():
            # dict_config_to_topic[(task, group, arg)] = (card_name, group_name, arg_name)
            try:
                task = key[0]
                group = key[1]
                card_name = value[0]
            except (IndexError, TypeError):
                # this shouldn't happen
                continue
            if card_name == card:
                list_task_group.append((task, group))
        # config_group_batch_reset will do de-redundancy, so no need to do here

        # call
        resp = await trio.to_thread.run_sync(
            MOD_LOADER.gui_config_group_batch_reset,
            mod_name, config_name, list_task_group
        )
        # resp: list[ConfigSetEvent]
        if not resp:
            return

        # broadcast to all connections
        event = ConfigEvent(t=self.topic_name(), c=config_name, v=resp)
        await self.msgbus_config_asend(event)
        await self.msgbus_global_asend(self.topic_name(), event)
