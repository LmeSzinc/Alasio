import trio

from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.reactive.source import ViewportEventSource
from alasio.backend.topic.scan import ConfigScanSource
from alasio.backend.topic.state import ConnState
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.entry.loader import MOD_LOADER
from alasio.config.entry.model import ConfigSetEvent
from alasio.ext.deep import deep_iter


class DashboardSource(ViewportEventSource):
    """
    Viewport source of the Dashboard topic: key (mod_name, config_name,
    lang), view fixed to 'dashboard'. The full view (values + i18n) is
    built on subscribe (MOD_LOADER.get_gui_config); the mapping
    (task, group, arg) -> card_name is this source's private business.
    """
    TOPIC_NAME = 'Dashboard'

    def __init__(self, config_name, mod_name, lang):
        super().__init__(config_name)
        self.mod_name = mod_name
        self.lang = lang
        # Build the structure mapping (nav JSON cache, no config values).
        # Raises KeyError when the config / mod / dashboard nav has been
        # deleted: get() converts that into None (no source).
        self.dict_config_to_topic = self._build_mapping()

    @classmethod
    def get(cls, mod_name, config_name, lang):
        """
        [Trio] Get or create the dashboard source of (mod_name,
        config_name, lang).

        Returns:
            DashboardSource | None: None when the source cannot be built
                (config / mod deleted).
        """
        return super().get(config_name, cls, mod_name, lang)

    async def _build_full(self):
        """
        [Trio] Build the full dashboard view (values + i18n) in a thread.
        Runs in the single-flight path of subscribe.
        """
        return await trio.to_thread.run_sync(
            MOD_LOADER.get_gui_config,
            self.mod_name, self.config_name, 'dashboard', self.lang
        )

    def _build_mapping(self):
        """
        Build the mapping from the GUI structure only (nav JSON cache), no
        config values. Replicates the mapping logic of the old
        Dashboard.data().

        Raises:
            KeyError: When the config / mod / dashboard nav no longer exists.
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
            nav_ref = mod.config_index_data()['dashboard']
        except KeyError:
            raise KeyError('No such nav: "dashboard"') from None
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
            dict_config_to_topic[(task, group, arg)] = card_name
        return dict_config_to_topic

    def _convert(self, event):
        """
        [锁内] One event -> a set response of the view key, or None when the
        arg is not displayed by the dashboard (dropped).
        """
        # we may receive dict from worker, because it's decoded from bytes
        if type(event) is dict:
            event = ConfigSetEvent(**event)

        card_name = self.dict_config_to_topic.get((event.task, event.group, event.arg))
        if card_name is None:
            # not displaying this key
            return None
        topic_key = (card_name, event.group, event.arg, 'value')
        return ResponseEvent(t=self.TOPIC_NAME, o='set', k=topic_key, v=event.value)


class Dashboard(BaseTopic):
    TOPIC_NAME = 'Dashboard'

    async def get_source(self):
        """
        Resolve the dashboard source of the current (mod, config, lang);
        the full view is built by source.subscribe() on registration.
        """
        state = ConnState(self.conn_id, self.server)
        mod_name = await state.mod_name
        config_name = await state.config_name
        lang = await state.lang
        if not mod_name or not config_name or not lang:
            return None
        source = DashboardSource.get(mod_name, config_name, lang)
        if source is None:
            # config / mod deleted: silent
            return None
        return source
