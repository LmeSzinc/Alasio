from typing import Type

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
    ALL_TOPIC_CLASS = create_topic_dict([
        ConfigScan,
        ConnState,
    ])
    DEFAULT_TOPIC_CLASS = create_topic_dict([
        ConnState,
    ])
