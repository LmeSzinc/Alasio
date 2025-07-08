from typing import TYPE_CHECKING

from .event import AccessDenied, ResponseEvent
from .rx_trio import AsyncReactiveCallback, async_reactive
from ..deep import deep_iter_patch
from ..singleton import SingletonNamed

if TYPE_CHECKING:
    # For IDE typehint, avoid recursive import
    from .ws_server import WebsocketTopicServer


class BaseTopic(AsyncReactiveCallback, metaclass=SingletonNamed):
    # subclasses should override `topic` and topic name should be unique
    # If topic name is empty, class name will be used
    # The following names are preserved:
    # - "error", the builtin topic to give response to invalid input
    name = ''

    @classmethod
    def topic_name(cls):
        if cls.name:
            return cls.name
        else:
            return cls.__name__

    def __init__(self, conn_id, server):
        """
        Create a data topic, that supports subscribe/unsubscribe
        and sends data changes once subscribed

        Args:
            conn_id (str):
            server (WebsocketTopicServer):
        """
        self.conn_id = conn_id
        self.server = server

    @async_reactive
    async def data(self):
        """
        Subclasses should implement how to get data.

        Examples:
            @reactive
            async def data(self):
                # Do simple data filtering
                return Shared().data.get('lang', 'cn')
                # Put the real data fetching on thread to avoid blocking event loop
                return await run_sync(ConfigScanSource.scan)
        """
        raise AccessDenied('Topic did not implement "data" method')

    async def getdata(self):
        """
        A wrapper function to get data.
        So you can do some pre-process and post-process
        """
        return await self.data

    async def op_sub(self):
        """
        Subscribe to this topic, once subscribe the data will flow

        When receiving a "sub" event from client, the data flows
        --> Topic.subscribe()
        --> Topic.getdata()
        --> Topic.data
            data is returned and observer chain is built

        Changes may come from:
        - backend background task that updates data
        - external database changes
        - another topic changes the dependency ot current topic
        - another client changes the data of current topic

        When receiving a dependency change, the data flows:
        --> DataSource.data.mutate(self, data)
        --> @async_reactive
            changes will broadcast to callback function
            --> reactive_callback
            --> sender.send()
            and also broadcast to each observer
            --> Observer1.data
            --> Observer2.data
        """
        data = await self.getdata()

        # prepare event
        topic = self.topic_name()
        event = ResponseEvent(t=topic, o='full', v=data)

        # send event
        await self.server.send(event)

    async def reactive_callback(self, name, old, new):
        """
        Callback function to send diff when `self.data` is re-computed
        """
        topic = self.topic_name()
        for op, keys, value in deep_iter_patch(old, new):
            event = ResponseEvent(t=topic, o=op, k=keys, v=value)
            await self.server.send(event)

    async def op_unsub(self):
        """
        Release current data topic
        """
        cls = self.__class__
        cls.singleton_remove(self.conn_id)

    async def op_add(self, keys, value):
        """
        Override this method to handle operation "add"

        Args:
            keys (tuple[str]):
            value (Any):
        """
        raise AccessDenied('Operation "add" is not allowed')

    async def op_set(self, keys, value):
        """
        Override this method to handle operation "set"

        Args:
            keys (tuple[str]):
            value (Any):
        """
        raise AccessDenied('Operation "set" is not allowed')

    async def op_del(self, keys):
        """
        Override this method to handle operation "del"

        Args:
            keys (tuple[str]):
        """
        raise AccessDenied('Operation "del" is not allowed')
