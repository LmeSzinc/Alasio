from collections import deque
from typing import Any

import trio.to_thread
from msgspec import ValidationError
from msgspecerror import ErrorInfo

from alasio.backend.reactive.base_rpc import rpc
from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.reactive.source import DiskCache, KeyedSource
from alasio.backend.topic import config_event
from alasio.backend.topic._gui_config import GuiConfigSource
from alasio.backend.topic.state import ConnState, NavState
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.entry.loader import MOD_LOADER
from alasio.config.entry.model import ConfigSetEvent


class ConfigNavSource(KeyedSource, DiskCache):
    """
    One-shot disk-cache source of ConfigNav: key (mod_name, lang).

    Static nav content read from disk; no push after the full snapshot --
    content changes come from subscription-condition changes (mod / lang),
    which rebuild the data through get_source -> reinit. Recycled by the
    data-expiry GC when the data TTL expired, regardless of subscribers
    (DiskCache semantics).
    """
    TOPIC_NAME = 'ConfigNav'
    TTL = 8

    def __init__(self, mod_name, lang):
        super().__init__()
        self.mod_name = mod_name
        self.lang = lang

    def on_init(self):
        """
        Returns:
            dict[str, dict[str, str]]:
                key: {nav_name}.{card_name}
                value: translation
        """
        return MOD_LOADER.get_gui_nav(self.mod_name, self.lang)


class ConfigNav(BaseTopic):
    TOPIC_NAME = 'ConfigNav'

    async def get_source(self):
        state = ConnState(self.conn_id, self.server)
        mod_name = await state.mod_name
        lang = await state.lang
        if not mod_name or not lang:
            return None
        source = ConfigNavSource.get(mod_name, lang)
        await source.reinit()
        return source


class ConfigArgSource(KeyedSource, GuiConfigSource):
    """
    No-cache view source of the ConfigArg topic: key (mod_name,
    config_name, nav_name, lang). The full view (values + i18n) is built
    on subscribe and config-save events are forwarded to the displayed
    args; the shared implementation lives in GuiConfigSource (the
    structure mapping and the ConfigSetEvent conversion are the same for
    every nav), this class only fixes its key shape.
    """
    TOPIC_NAME = 'ConfigArg'
    # Constructor signature (mod_name, config_name, nav_name, lang) is
    # inherited from GuiConfigSource: the whole argument tuple is the
    # KeyedSource registry key.


class ConfigArg(BaseTopic):
    TOPIC_NAME = 'ConfigArg'

    async def get_source(self):
        """
        Resolve the view source of the current (mod, config, nav, lang);
        the full view is built by source.subscribe() on registration.
        """
        state = ConnState(self.conn_id, self.server)
        mod_name = await state.mod_name
        config_name = await state.config_name
        nav_name = await state.nav_name
        lang = await state.lang
        if not mod_name or not config_name or not nav_name or not lang:
            return None
        source = ConfigArgSource.get(mod_name, config_name, nav_name, lang)
        if source is None:
            # config / mod / nav deleted: silent, next dependency change
            # re-runs this flow
            return None
        return source

    @rpc
    async def set(self, task: str, group: str, arg: str, value: Any):
        if not task or not group or not arg:
            return
        # get config_name
        state = ConnState(self.conn_id, self.server)
        nav: NavState = await state.nav_state
        mod_name = nav.mod_name
        config_name = nav.config_name
        nav_name = nav.nav_name
        lang = await state.lang
        if not config_name:
            return

        # call
        success, responses = await trio.to_thread.run_sync(
            MOD_LOADER.gui_config_set,
            mod_name, config_name, task, group, arg, value
        )
        responses: "list[ConfigSetEvent]"
        # logger.info([success, responses])
        if success:
            # unified event entry: viewport sources of every nav + Dashboard
            # + TaskQueue linkage (sync, thread safe)
            config_event.on_config_event(config_name, responses)
        else:
            # there always be one rollback_event; convert it directly into a
            # set response for this connection (values are not re-read from
            # the config store), the error message comes from resp.error
            resp = responses[0]
            source = ConfigArgSource.get(mod_name, config_name, nav_name, lang)
            if source is None:
                # config deleted: nothing to roll back on screen
                return
            key = source.dict_config_to_topic.get((resp.task, resp.group, resp.arg))
            if key is None:
                # not displaying this key
                return
            key = (*key, 'value')
            resp_event = ResponseEvent(t=self.TOPIC_NAME, o='set', k=key, v=resp.value)
            await self.server.send(resp_event)
            # re-raise error, so server will treat as RPC call failed
            if resp.error is not None:
                if isinstance(resp.error, ErrorInfo):
                    msg = resp.error.msg
                else:
                    msg = str(resp.error)
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

        # unified event entry
        config_event.on_config_event(config_name, [resp])

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
        lang = await state.lang
        if not config_name or not nav_name:
            return

        # get all task-group within card
        # copy to avoid modification during iterating, group reset is rarely used so copy is acceptable
        source = ConfigArgSource.get(mod_name, config_name, nav_name, lang)
        if source is None:
            return
        list_task_group = deque()
        for key, value in source.dict_config_to_topic.items():
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

        # unified event entry
        config_event.on_config_event(config_name, resp)
