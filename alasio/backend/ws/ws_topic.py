from typing import TYPE_CHECKING

from msgspec import DecodeError, ValidationError

from alasio.ext.reactive.base_topic import BaseTopic as BaseMixin
from alasio.ext.reactive.event import AccessDenied, ResponseEvent, RpcValueError
from alasio.ext.reactive.rx_trio import AsyncReactiveCallback, async_reactive
from alasio.ext.deep import deep_iter_patch
from alasio.ext.singleton import SingletonNamed
from alasio.logger import logger

if TYPE_CHECKING:
    # For IDE typehint, avoid recursive import
    from .ws_server import WebsocketTopicServer


class BaseTopic(AsyncReactiveCallback, BaseMixin, metaclass=SingletonNamed):
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
        if name != 'data':
            return
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

    async def op_rpc(self, func, value, rpc_id):
        """
        Do RPC call on current topic

        Args:
            func (str): RPC method name
            value (Any): RPC method args
            rpc_id (str):
        """
        if not rpc_id:
            msg = 'Missing RPC ID in event'
            event = ResponseEvent(t=self.topic_name(), v=msg, i=rpc_id)
            await self.server.send(event)
            return
        try:
            method = self.rpc_methods[func]
        except KeyError:
            msg = f'RPC method not found "{func}"'
            event = ResponseEvent(t=self.topic_name(), v=msg, i=rpc_id)
            await self.server.send(event)
            return

        # RPC call
        try:
            await method.call_async(self, value)
        except (ValidationError, DecodeError, UnicodeDecodeError, AccessDenied, RpcValueError) as e:
            # input errors
            msg = f'{e.__class__.__name__}: {e}'
            event = ResponseEvent(t=self.topic_name(), v=msg, i=rpc_id)
            await self.server.send(event)
            return
        except Exception as e:
            # unexpected internal errors
            logger.exception(e)
            msg = f'{e.__class__.__name__}: {e}'
            event = ResponseEvent(t=self.topic_name(), v=msg, i=rpc_id)
            await self.server.send(event)
            return

        # success
        # RPC success has no return value sent, omitting "v" means success, having "v" means error
        # The real RPC response will go through existing topic subscription
        event = ResponseEvent(t=self.topic_name(), i=rpc_id)
        await self.server.send(event)
        return
