import trio

from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.reactive.source import NoCachePush
from alasio.backend.topic.scan import ConfigScanSource
from alasio.config.entry.loader import MOD_LOADER
from alasio.config.entry.model import ConfigSetEvent
from alasio.ext.deep import deep_iter

# Concrete GUI view source classes that take part in config-save routing.
# Collected at class definition time (only real topics -- with a
# TOPIC_NAME -- register, see GuiConfigSource.__init_subclass__).
_CONFIG_GUI_CLASSES: "list[type]" = []


class GuiConfigSource(NoCachePush):
    """
    Application-level shared base of the config GUI view sources
    (ConfigArgSource / DashboardSource). It is NOT a framework dimension:
    it is the one place where the "config view over ConfigSetEvent" shape
    lives, so the framework base (NoCachePush) stays generic.

    Both views are built from the same blocks:
    - the full view is MOD_LOADER.get_gui_config(mod, config, nav, lang),
      differing only in the nav name;
    - the event -> keyed-set mapping is built from the nav structure JSON:
      (task, group, arg) rows -> (card, group, arg) location;
    - both consume ConfigSetEvent (or its dict form, as decoded from
      worker bytes) and convert it into a keyed 'set' patch of the
      displayed value.

    Subclasses only fix their key shape / constructor arguments and the
    nav name: ConfigArgSource(mod, config, nav, lang) for any nav, or
    DashboardSource(mod, config, lang) with nav fixed to 'dashboard'.
    The registry (KeyedSource.get) converts a KeyError of the constructor
    (config / mod / nav deleted) into None.
    """

    def __init__(self, mod_name, config_name, nav_name, lang):
        super().__init__()
        self.mod_name = mod_name
        self.config_name = config_name
        self.nav_name = nav_name
        self.lang = lang
        # Build the structure mapping (nav JSON cache, no config values).
        # Raises KeyError when the config / mod / nav has been deleted:
        # KeyedSource.get() converts that into None (no source).
        self.dict_config_to_topic = self._build_mapping()

    def _build_mapping(self):
        """
        Build the mapping from the GUI structure only (nav JSON cache), no
        config values. Rows of the nav tree at depth 3 are keyed by their
        (task, group, arg) reference and mapped to their (card, group,
        arg) display location; '_info' pseudo groups and rows without
        task / group / arg references are skipped.

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

    def _convert(self, event):
        """
        [锁内] One config-save event -> a set response of the view key, or
        None when the arg is not displayed by this view (dropped).
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

    # ---------------- config-save routing (application layer) ----------------

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # a concrete view source (real topic) joins the config-save route
        # list once at class definition time
        if cls.TOPIC_NAME:
            _CONFIG_GUI_CLASSES.append(cls)

    @classmethod
    def dispatch_config(cls, config_name, event):
        """
        [任意线程] Route a config-save event to every GUI view source of
        the config (every nav instance of ConfigArg + the Dashboard
        source). Each source filters the event by its own mapping (inbox +
        doorbell under its lock). Thread safe: instance enumeration goes
        through a locked registry snapshot; sources removed in between
        drop the event (no subscriber).
        """
        for src_cls in _CONFIG_GUI_CLASSES:
            for _, source in src_cls.singleton_items():
                if source.config_name == config_name:
                    source.on_event(event)
