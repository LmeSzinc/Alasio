from typing import Type

from alasio.backend.msgbus.share import ConfigEvent
from alasio.backend.topic.config import ConfigArg, ConfigNav
from alasio.backend.topic.mod import ModList
from alasio.backend.topic.scan import ConfigScan
from alasio.backend.topic.state import ConnState, DICT_CONFIG_TO_CONN, NavState
from alasio.backend.ws.ws_server import WebsocketTopicServer
from alasio.backend.ws.ws_topic import BaseTopic


def create_topic_dict(topic_classes: "list[Type[BaseTopic]]") -> "dict[str, Type[BaseTopic]]":
    """
    Convert a list of topic classes to a dict of them
    """
    return {topic.topic_name(): topic for topic in topic_classes}


class WebsocketServer(WebsocketTopicServer):
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

    async def init(self):
        await super().init()
        # set language
        topic = self.subscribed.get(ConnState.topic_name(), None)
        if topic is not None:
            lang = self._negotiate_lang()
            await ConnState.lang.mutate(topic, lang)

    @classmethod
    async def handle_config_event(cls, event: ConfigEvent):
        """
        Broadcast config events to all connections that subscribed this config
        """
        connections = DICT_CONFIG_TO_CONN[event.c]
        if not connections:
            # nobody subscribing given config_name
            return

        try:
            topic_cls = cls.ALL_TOPIC_CLASS[event.t]
        except KeyError:
            # this shouldn't happen
            # no such topic
            return

        # make a copy so we can safely iterate in async
        connections = list(connections)
        # access with cls.singleton_instances()[conn_id] to make sure we don't create new instances
        topic_instance = topic_cls.singleton_instances()
        state_instance = ConnState.singleton_instances()
        # Do we need asynchronous concurrency here?
        for conn_id in connections:
            try:
                topic: BaseTopic = topic_instance[conn_id]
                state: ConnState = state_instance[conn_id]
            except KeyError:
                # race condition that topic just unsubscribed
                # or DICT_CONFIG_TO_CONN is inconsistent with topic_cls(conn_id)
                continue
            # check if topic is subscribing current topic
            nav_state: NavState = await state.nav_state
            if nav_state.config_name != event.c:
                continue
            # broadcast
            await topic.on_config_event(event)
