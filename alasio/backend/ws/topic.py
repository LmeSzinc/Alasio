from typing import Type

from alasio.backend.locale.accept_language import negotiate_accept_language
from alasio.backend.topic.config import ConfigArg, ConfigNav
from alasio.backend.topic.dashboard import Dashboard
from alasio.backend.topic.log import Log
from alasio.backend.topic.mod import ModHistory, ModList
from alasio.backend.topic.preview import Preview
from alasio.backend.topic.que import TaskQueue, TaskQueueI18n
from alasio.backend.topic.scan import ConfigScan
from alasio.backend.topic.state import ConnState
from alasio.backend.topic.worker import Worker
from alasio.backend.ws.ws_server import WebsocketTopicServer
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.const import Const


def create_topic_dict(topic_classes: "list[Type[BaseTopic]]") -> "dict[str, Type[BaseTopic]]":
    """
    Convert a list of topic classes to a dict of them
    """
    return {topic.TOPIC_NAME: topic for topic in topic_classes}


class WebsocketServer(WebsocketTopicServer):
    # Alasio transfer data via topics, not one-time request-response
    #
    # Frontend can subscribe to these topics, backend will send a "full" event immediately,
    # and push data events if any data changes. Once frontend unsubscribed, data push ends.
    #
    # After subscribing, frontend can call RPC function under that topic.
    # RPC functions won't give response data, it will trigger internal data changes and push topic data updates.
    ALL_TOPIC_CLASS = create_topic_dict([
        # must contain ConnState
        ConnState,
        ModList,
        ModHistory,
        ConfigScan,
        ConfigNav,
        ConfigArg,
        Worker,
        Log,
        TaskQueue,
        TaskQueueI18n,
        Dashboard,
        # DevAssetsManager,
    ])
    # List of default subscribed topics.
    # Without actively subscribing:
    # - Frontend can send and backend should accept RPC calls.
    # - Backend will send and frontend should accept data updates.
    DEFAULT_TOPIC_CLASS = create_topic_dict([
        # must contain ConnState
        ConnState,
    ])

    async def init(self):
        await super().init()
        # set language
        topic: "ConnState | None" = self.subscribed.get(ConnState.TOPIC_NAME, None)
        if topic is not None:
            lang = self._negotiate_lang()
            state = await topic.nav_state
            state.lang = lang
            await topic.nav_state.mutate()

    def _negotiate_lang(self, default='en-US'):
        """
        Parse request into one available language

        Returns:
            str:
        """
        available = Const.GUI_LANGUAGE
        ws = self.ws
        # try to match the lang in cookie
        prefer = ws.cookies.get('alasio_lang', '')
        if prefer:
            use = negotiate_accept_language(prefer, available)
            if use:
                return use
        # try to match Accept-Language header
        try:
            prefer = ws.headers['Accept-Language']
        except KeyError:
            # this shouldn't happen, most browsers would post Accept-Language header
            prefer = ''
        if prefer:
            use = negotiate_accept_language(prefer, available)
            if use:
                return use
        # no luck, try to use default
        if default in available:
            return default
        # ohno, default language is not available, use the first language
        try:
            return available[0]
        except IndexError:
            # empty available languages, there's nothing we can do
            # return default anyway
            return default


class PreviewServer(WebsocketTopicServer):
    ALL_TOPIC_CLASS = create_topic_dict([
        Preview,
    ])
    # buffer 1 preview only
    LOSSY_BUFFER_LENGTH = 1
