from alasio.backend.reactive.source import KeyedSource
from alasio.backend.topic._gui_config import GuiConfigSource
from alasio.backend.topic.state import ConnState
from alasio.backend.ws.ws_topic import BaseTopic


class DashboardSource(KeyedSource, GuiConfigSource):
    """
    No-cache view source of the Dashboard topic: key (mod_name,
    config_name, lang), the view is the fixed 'dashboard' nav. The full
    view and the config-save event mapping come from GuiConfigSource;
    this class only fixes its key shape (nav name constant).
    """
    TOPIC_NAME = 'Dashboard'

    def __init__(self, mod_name, config_name, lang):
        super().__init__(mod_name, config_name, 'dashboard', lang)


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
