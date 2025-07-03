from typing import Any, List, Literal, Tuple, Union

import msgspec

from alasio.ext.deep import deep_iter_patch
from alasio.ext.reactive.rx_trio import AsyncReactiveCallback, async_reactive
from alasio.ext.singleton import SingletonNamed

NO_VALUE = object()


class RequestEvent(msgspec.Struct):
    # topic
    t: str
    # operation
    o: Literal['sub', 'unsub', 'add', 'set', 'del']
    # keys
    k: Tuple[Union[str, int]] = ()
    # value
    v: Any = NO_VALUE


class ResponseEvent(msgspec.Struct):
    # topic
    t: str
    # operation
    o: Literal['full', 'add', 'set', 'del']
    # keys
    k: List[Union[str, int]]
    # value
    v: Any


class BaseTopic(AsyncReactiveCallback, metaclass=SingletonNamed):
    # subclasses should override `topic` and topic name should be unique
    # If name is empty, class name will be used
    topic = ''

    def __init__(self, conn_id, server):
        """
        Create a data topic, that supports subscribe/unsubscribe
        and sends data changes once subscribed

        Args:
            conn_id (str):
            server (WebsocketServer):
        """
        self.conn_id = conn_id
        self.server = server

        cls = self.__class__
        if cls.topic:
            self.topic = cls.topic
        else:
            self.topic = cls.__name__

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
        raise NotImplementedError

    async def getdata(self):
        """
        A wrapper function to get data.
        So you can do some pre-process and post-process
        """
        return await self.data

    async def subscribe(self):
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
        event = ResponseEvent(t=self.topic, o='full', k=[], v=data)

        # send event
        await self.server.send(event)

    async def reactive_callback(self, name, old, new):
        """
        Callback function to send diff when `self.data` is re-computed
        """
        topic = self.topic
        for op, keys, value in deep_iter_patch(old, new):
            event = ResponseEvent(t=topic, o=op, k=keys, v=value)
            await self.server.send(event)

    async def unsubscribe(self):
        """
        Release current data topic
        """
        cls = self.__class__
        cls.singleton_remove(self.conn_id)
