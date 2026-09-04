"""
Shared test helpers for the ws framework tests.

FakeWebSocket mimics the starlette WebSocket surface used by ws_server.py,
so WebsocketTopicServer can be driven without real network.
"""

import msgspec
import trio
from starlette.websockets import WebSocketDisconnect, WebSocketState

from alasio.backend.reactive.base_rpc import rpc
from alasio.backend.reactive.event import AccessDenied, RpcValueError
from alasio.backend.reactive.source import EventSource
from alasio.backend.ws.ws_server import WebsocketTopicServer
from alasio.backend.ws.ws_topic import BaseTopic

# Sentinel distinguishing "no initial data given" from an explicit None
_MISSING = object()


class FakeWebSocket:
    """
    Mimics the starlette WebSocket surface used by ws_server.py:
    accept / receive / send_bytes / close / headers / cookies /
    application_state / _raise_on_disconnect
    """

    def __init__(self, accept_error=None, close_error=None):
        """
        Args:
            accept_error (Exception | None): If set, accept() raises it.
            close_error (Exception | None): If set, close() raises it.
        """
        self.headers = {}
        self.cookies = {}
        self.application_state = WebSocketState.CONNECTING
        # All data passed to send_bytes
        self.sent = []
        # All close calls, as (code, reason)
        self.closed = []
        self.accept_error = accept_error
        self.close_error = close_error
        # Test-injected receive messages
        self._inbox_send, self._inbox_recv = trio.open_memory_channel(64)

    async def accept(self, subprotocol=None, headers=None):
        if self.accept_error is not None:
            raise self.accept_error
        self.application_state = WebSocketState.CONNECTED

    async def receive(self):
        try:
            return await self._inbox_recv.receive()
        except trio.EndOfChannel:
            # inbox closed, treat as connection dropped
            self.application_state = WebSocketState.DISCONNECTED
            raise WebSocketDisconnect(1000)

    async def send_bytes(self, data):
        self.sent.append(data)

    async def close(self, code=1000, reason=None):
        if self.close_error is not None:
            raise self.close_error
        if self.application_state != WebSocketState.DISCONNECTED:
            self.application_state = WebSocketState.DISCONNECTED
        self.closed.append((code, reason))

    def _raise_on_disconnect(self, message):
        if message.get('type') == 'websocket.disconnect':
            raise WebSocketDisconnect(message.get('code', 1000), message.get('reason'))

    # Test helpers

    def send_message(self, data):
        """
        Inject a client message.
        bytes -> {'bytes': ...}, str -> {'text': ...}, dict -> pass through
        """
        if isinstance(data, bytes):
            self._inbox_send.send_nowait({'bytes': data})
        elif isinstance(data, str):
            self._inbox_send.send_nowait({'text': data})
        else:
            self._inbox_send.send_nowait(data)

    def disconnect(self, code=1000, reason=None):
        """
        Inject a client disconnect message
        """
        self._inbox_send.send_nowait({'type': 'websocket.disconnect', 'code': code, 'reason': reason})

    def close_inbox(self):
        """
        Close the receive channel, making task_recv exit and cascade shutdown
        """
        self._inbox_send.close()


class FakeTopicSource(EventSource):
    """
    A minimal cache source for the topic helpers: the full event topic name
    is set per instance, the snapshot value is the data dict. The scaffold
    sends the subscribe snapshot directly (no topic-side data() anymore).
    """

    def __init__(self, topic_name, data):
        self.TOPIC = topic_name
        super().__init__()
        self.data = data


class SampleTopic(BaseTopic):
    """
    A test topic bound to a static cache source, with RPC methods covering
    every response path
    """
    NAME = 'sample'

    def __init__(self, conn_id, server, initial=_MISSING):
        super().__init__(conn_id, server)
        if initial is _MISSING:
            initial = {'a': 1, 'b': 2}
        self.source = FakeTopicSource(self.topic_name(), initial)
        # Record of RPC calls, as (func_name, *args)
        self.calls = []

    async def get_source(self):
        return self.source

    @rpc
    async def echo(self, x: int, y: str = 'hi'):
        """
        Successful RPC, return value should be ignored by the framework
        """
        self.calls.append(('echo', x, y))
        return 'ignored'

    @rpc
    async def deny(self):
        raise AccessDenied('denied')

    @rpc
    async def bad_input(self):
        raise RpcValueError('bad input')

    @rpc
    async def explode(self):
        raise ValueError('boom')


class FullOnlyTopic(BaseTopic):
    """
    A test topic bound to a static cache source
    """
    NAME = 'full_only'

    def __init__(self, conn_id, server):
        super().__init__(conn_id, server)
        self.source = FakeTopicSource(self.topic_name(), {'a': 1, 'b': 2})

    async def get_source(self):
        return self.source


class EmptyTopic(BaseTopic):
    """
    A test topic whose source data is falsy: subscribing registers but
    sends nothing (empty data sends no full)
    """
    NAME = 'empty'

    def __init__(self, conn_id, server):
        super().__init__(conn_id, server)
        self.source = FakeTopicSource(self.topic_name(), {})

    async def get_source(self):
        return self.source


class ErrorTopic(BaseTopic):
    """
    A test topic whose op_unsub raises, cleanup should survive it
    """
    NAME = 'error_topic'

    def __init__(self, conn_id, server):
        super().__init__(conn_id, server)
        self.source = FakeTopicSource(self.topic_name(), {'x': 1})

    async def get_source(self):
        return self.source

    async def op_unsub(self):
        raise RuntimeError('unsub failed')


class MismatchTopic(BaseTopic):
    """
    A test topic whose NAME differs from its registered key, so the server
    must key subscriptions by the requested name
    """
    NAME = 'mismatch_actual'

    def __init__(self, conn_id, server):
        super().__init__(conn_id, server)
        self.source = FakeTopicSource(self.topic_name(), {'x': 1})

    async def get_source(self):
        return self.source

    @rpc
    async def echo(self):
        return 'ok'


class HarnessWebsocketServer(WebsocketTopicServer):
    """
    A WebsocketTopicServer with the test topics registered
    """
    ALL_TOPIC_CLASS = {
        SampleTopic.topic_name(): SampleTopic,
        FullOnlyTopic.topic_name(): FullOnlyTopic,
        EmptyTopic.topic_name(): EmptyTopic,
        ErrorTopic.topic_name(): ErrorTopic,
    }
    DEFAULT_TOPIC_CLASS = {
        SampleTopic.topic_name(): SampleTopic,
    }


class ServerHarness:
    """
    Drive a WebsocketTopicServer with a FakeWebSocket inside a trio nursery
    """

    def __init__(self, server_cls=HarnessWebsocketServer):
        self.fake_ws = FakeWebSocket()
        self.server = server_cls(self.fake_ws)
        self.serve_finished = trio.Event()

    async def run_serve(self):
        try:
            await self.server.serve()
        finally:
            self.serve_finished.set()

    async def wait_connected(self):
        # wait until the connection is accepted and init() subscribed the default topics
        default_names = set(self.server.DEFAULT_TOPIC_CLASS)
        with trio.fail_after(5):
            while True:
                if self.fake_ws.application_state != WebSocketState.CONNECTED:
                    await trio.sleep(0)
                    continue
                if not default_names.issubset(self.server.subscribed):
                    await trio.sleep(0)
                    continue
                return

    async def stop(self):
        """
        Stop the server gracefully: closing the inbox makes task_recv exit,
        which cascades to task_job / task_send / task_heartbeat, then serve()
        returns.
        """
        self.fake_ws.close_inbox()
        with trio.move_on_after(5):
            await self.serve_finished.wait()

    def sent_events(self):
        """
        Decode all JSON events sent to the websocket, ignoring raw bytes (b'ping')

        Returns:
            list[dict]:
        """
        events = []
        for data in self.fake_ws.sent:
            try:
                events.append(msgspec.json.decode(data))
            except msgspec.DecodeError:
                continue
        return events
