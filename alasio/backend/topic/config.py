from collections import deque
from typing import Any

import trio.to_thread
from msgspec import ValidationError
from msgspecerror import ErrorInfo

from alasio.backend.reactive.base_rpc import rpc
from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.reactive.source import KeyedEventSource, ViewportEventSource
from alasio.backend.topic import config_event
from alasio.backend.topic.scan import ConfigScanSource
from alasio.backend.topic.state import ConnState, NavState
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.entry.loader import MOD_LOADER
from alasio.config.entry.model import ConfigSetEvent
from alasio.ext.deep import deep_iter


class ConfigNavSource(KeyedEventSource):
    """
    One-shot keyed cache source of ConfigNav: key (mod_name, lang).
    """
    TOPIC_NAME = 'ConfigNav'
    TTL = 8
    IDLE_TTL = 8

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


class ConfigArgSource(ViewportEventSource):
    """
    Viewport source of the ConfigArg topic: key (mod_name, config_name,
    nav_name, lang). The full view (values + i18n) is built on subscribe
    (MOD_LOADER.get_gui_config); the mapping
    (task, group, arg) -> (card_name, group_name, arg_name) is built from
    the GUI structure only and is this source's private business.
    """
    TOPIC_NAME = 'ConfigArg'

    def __init__(self, config_name, mod_name, nav_name, lang):
        super().__init__(config_name)
        self.mod_name = mod_name
        self.nav_name = nav_name
        self.lang = lang
        # Build the structure mapping (nav JSON cache, no config values).
        # Raises KeyError when the config / mod / nav has been deleted:
        # get() converts that into None (no source).
        self.dict_config_to_topic = self._build_mapping()

    @classmethod
    def get(cls, mod_name, config_name, nav_name, lang):
        """
        [Trio] Get or create the viewport source of (mod_name, config_name,
        nav_name, lang).

        Returns:
            ConfigArgSource | None: None when the source cannot be built
                (config / mod / nav deleted).
        """
        return super().get(config_name, cls, mod_name, nav_name, lang)

    async def _build_full(self):
        """
        [Trio] Build the full view (values + i18n) in a thread. Runs in the
        single-flight path of subscribe: concurrent subscribers of the same
        key share one build. An empty view returns None (registered, no
        full sent).
        """
        return await trio.to_thread.run_sync(
            MOD_LOADER.get_gui_config,
            self.mod_name, self.config_name, self.nav_name, self.lang
        )

    def _build_mapping(self):
        """
        Build the mapping from the GUI structure only (nav JSON cache), no
        config values. Replicates the mapping logic of the old
        ConfigArg.data(): iterate the nav tree at depth 3, skip the '_info'
        pseudo groups and entries without task / group / arg references.

        Raises:
            KeyError: When the config / mod / nav no longer exists.
        """
        configs = ConfigScanSource().data
        try:
            info = configs[self.config_name]
        except KeyError:
            raise KeyError(f'No such config: "{self.config_name}"') from None
        if info.mod != self.mod_name:
            # the config was re-bound to another mod (external edit):
            # treat it as nonexistent under this key
            raise KeyError(f'Config "{self.config_name}" is not under mod "{self.mod_name}"') from None
        try:
            mod = MOD_LOADER.dict_mod[info.mod]
        except KeyError:
            raise KeyError(f'No such mod: "{info.mod}"') from None
        try:
            nav_ref = mod.config_index_data()[self.nav_name]
        except KeyError:
            raise KeyError(f'No such nav: "{self.nav_name}"') from None
        tree = mod.nav_config_json(nav_ref.file)

        dict_config_to_topic = {}
        for keys, arg_data in deep_iter(tree, depth=3):
            card_name, group_name, arg_name = keys
            if group_name == '_info':
                continue
            try:
                task = arg_data['task']
                group = arg_data['group']
                arg = arg_data['arg']
            except KeyError:
                # this shouldn't happen
                continue
            dict_config_to_topic[(task, group, arg)] = (card_name, group_name, arg_name)
        return dict_config_to_topic

    def _convert(self, event):
        """
        [锁内] One event -> a set response of the view key, or None when the
        arg is not displayed by this view (dropped).
        """
        # we may receive dict from worker, because it's decoded from bytes
        if type(event) is dict:
            event = ConfigSetEvent(**event)

        key = self.dict_config_to_topic.get((event.task, event.group, event.arg))
        if key is None:
            # not displaying this key
            return None
        topic_key = (*key, 'value')
        return ResponseEvent(t=self.TOPIC_NAME, o='set', k=topic_key, v=event.value)


class ConfigArg(BaseTopic):
    TOPIC_NAME = 'ConfigArg'

    async def get_source(self):
        """
        Resolve the viewport source of the current (mod, config, nav,
        lang); the full view is built by source.subscribe() on
        registration.
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
