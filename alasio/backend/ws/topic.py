from typing import Type

from alasio.backend.topic.config import ConfigArg, ConfigNav
from alasio.backend.topic.mod import ModList
from alasio.backend.topic.scan import ConfigScan
from alasio.backend.topic.state import ConnState, DICT_CONFIG_TO_CONN
from alasio.backend.topic.worker import Worker
from alasio.backend.ws.ws_server import WebsocketTopicServer
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.ext.reactive.base_topic import MSGBUS_CONFIG_HANDLERS, MSGBUS_CONFIG_RECV, MSGBUS_GLOBAL_HANDLERS, \
    MSGBUS_GLOBAL_RECV
from alasio.logger import logger


def create_topic_dict(topic_classes: "list[Type[BaseTopic]]") -> "dict[str, Type[BaseTopic]]":
    """
    Convert a list of topic classes to a dict of them
    """
    return {topic.topic_name(): topic for topic in topic_classes}


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
        ConfigScan,
        ConfigNav,
        ConfigArg,
        Worker,
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
        topic: "ConnState | None" = self.subscribed.get(ConnState.topic_name(), None)
        if topic is not None:
            lang = self._negotiate_lang()
            state = await topic.nav_state
            state.lang = lang
            await ConnState.nav_state.mutate(topic, state)

    @classmethod
    async def handle_global_event(cls, topic: str, value):
        """
        Broadcast global events to all connections that subscribed this config
        """
        try:
            handlers = MSGBUS_GLOBAL_HANDLERS[topic]
        except KeyError:
            # nobody listening given topic
            return

        for handler in handlers:
            topic_cls, func = handler
            # make a copy so we can safely iterate in async
            topic_instances = list(topic_cls.singleton_instances().values())
            for topic_obj in topic_instances:
                # broadcast
                await func(topic_obj, value)

    @classmethod
    async def handle_config_event(cls, topic: str, config: str, value):
        """
        Broadcast config events to all connections that subscribed this config
        """
        connections = DICT_CONFIG_TO_CONN[config]
        if not connections:
            # nobody subscribing given config
            return

        try:
            handlers = MSGBUS_CONFIG_HANDLERS[topic]
        except KeyError:
            # nobody listening given topic
            return

        # make a copy so we can safely iterate in async
        connections = list(connections)
        for handler in handlers:
            topic_cls, func = handler
            # access with cls.singleton_instances()[conn_id] to make sure we don't create new instances
            topic_instances = topic_cls.singleton_instances()
            # Do we need asynchronous concurrency here?
            for conn_id in connections:
                topic_obj = topic_instances.get(conn_id, None)
                if topic_obj is None:
                    # race condition that topic just unsubscribed
                    # or DICT_CONFIG_TO_CONN is inconsistent with topic_cls(conn_id)
                    continue
                # broadcast
                await func(topic_obj, value)

    @classmethod
    async def task_msgbus_global(cls):
        """
        Coroutine task that handles global events on msgbus
        """
        while 1:
            # receive then do, msg order matters
            event = await MSGBUS_GLOBAL_RECV.receive()
            try:
                await cls.handle_global_event(*event)
            except Exception as e:
                logger.exception(e)

    @classmethod
    async def task_msgbus_config(cls):
        """
        Coroutine task that handles config events on msgbus
        """
        while 1:
            # receive then do, msg order matters
            event = await MSGBUS_CONFIG_RECV.receive()
            try:
                await cls.handle_config_event(*event)
            except Exception as e:
                logger.exception(e)
