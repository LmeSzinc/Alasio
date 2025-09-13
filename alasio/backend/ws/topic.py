from typing import Type

from alasio.backend.topic.config import ConfigArg, ConfigNav
from alasio.backend.topic.mod import ModList
from alasio.backend.topic.scan import ConfigScan
from alasio.backend.topic.state import ConnState
from alasio.backend.ws.ws_server import WebsocketTopicServer
from alasio.backend.ws.ws_topic import BaseTopic


def create_topic_dict(topic_classes: "list[Type[BaseTopic]]") -> "dict[str, Type[BaseTopic]]":
    """
    Convert a list of topic classes to a dict of them
    """
    return {topic.topic_name(): topic for topic in topic_classes}


class WebsocketServer(WebsocketTopicServer):
    async def init(self):
        await super().init()
        # set language
        topic = self.subscribed.get(ConnState.topic_name(), None)
        if topic is not None:
            lang = self._negotiate_lang()
            await ConnState.lang.mutate(topic, lang)

    DEFAULT_TOPIC_CLASS = create_topic_dict([
        # must contain ConnState
        ConnState,
    ])

    ALL_TOPIC_CLASS = create_topic_dict([
        # must contain ConnState
        ConnState,
        ModList,
        ConfigScan,
        ConfigNav,
        ConfigArg,
    ])
