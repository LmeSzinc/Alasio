import time

import msgspec
import trio
from msgspec import DecodeError, EncodeError, ValidationError
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from alasio.logger import logger
from .event import RequestEvent, ResponseEvent
from ..area.rng import random_id

TRIO_CHANNEL_ERRORS = (trio.BrokenResourceError, trio.BusyResourceError, trio.ClosedResourceError, trio.EndOfChannel)
WEBSOCKET_ERRORS = (WebSocketDisconnect, RuntimeError)
DECODE_ERRORS = (ValidationError, DecodeError, UnicodeDecodeError)
ENCODE_ERRORS = (EncodeError, UnicodeEncodeError)
REQUEST_EVENT_DECODER = msgspec.json.Decoder(RequestEvent)
RESPONSE_EVENT_ENCODER = msgspec.json.Encoder()


class WebsocketServer:
    @classmethod
    async def endpoint(cls, ws: WebSocket):
        """
        Websocket endpoint
        """
        server = WebsocketServer(ws)
        print(server)

        await server.serve()

        # cleanup
        pass

    # If no activity for X seconds,
    # we will send a "ping" to client
    PING_INTERVAL = 30
    # When client received a "ping", client should respond with a "pong" within X seconds,
    # otherwise we will close the connection
    PONG_TIMEOUT = 15

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.id = f'{int(time.time())}_{random_id(6)}'

        # timestamp of last activity, used to calculate the next "ping"
        self.last_active = 0.
        # track if "pong" is received
        self.pong_received = trio.Event()
        # buffer the message to be sent
        self.send_buffer: "trio.MemorySendChannel[ResponseEvent]" = None

    def __str__(self):
        return f'Websocket({self.id})'

    async def serve(self):
        """
        Serve a websocket connection, start tasks for receive, send, heartbeat
        """
        try:
            await self.ws.accept()
            # accept success, update activity
            self.last_active = trio.current_time()
        except WEBSOCKET_ERRORS:
            await self.close()
            return

        # handle messages
        async with trio.open_nursery() as nursery:
            # open 2 buffers, send buffer and recv buffer
            # start 4 async tasks, sender, receiver, job handler, heartbeat handler

            # send buffer, set send buffer first
            self.send_buffer, recv = trio.open_memory_channel(32)
            nursery.start_soon(self.task_send, recv)

            # recv buffer
            send, recv = trio.open_memory_channel(8)
            nursery.start_soon(self.task_recv, send)
            nursery.start_soon(self.task_job, recv)

            # heartbeat
            nursery.start_soon(self.task_heartbeat)

    async def close(self, code=1000, reason=None):
        """
        Close websocket connection with error handling

        Args:
            code (int):
            reason (str | None):
        """
        if self.ws.application_state != WebSocketState.DISCONNECTED:
            try:
                await self.ws.close(code=code, reason=reason)
            except WEBSOCKET_ERRORS:
                pass

    async def send(self, data: ResponseEvent):
        """
        Send an event to send buffer
        """
        try:
            await self.send_buffer.send(data)
        except TRIO_CHANNEL_ERRORS:
            # buffer closed
            pass

    async def send_error(self, data: "ResponseEvent | Exception | str | bytes"):
        """
        Send data as error
        """
        # convert errors
        if isinstance(data, Exception):
            data = f'{data.__class__.__name__}: {data}'
        # convert to event
        if not isinstance(data, ResponseEvent):
            data = ResponseEvent(t='error', o='full', v=data)

        await self.send(data)

    async def _ws_receive(self, ws: WebSocket):
        """
        Similar to WebSocket.receive_bytes but accept both text and bytes and return bytes

        Returns:
            bytes | str:

        Raises:
            WebSocketDisconnect:
            RuntimeError:
            ValidationError:
        """
        if ws.application_state != WebSocketState.CONNECTED:
            raise RuntimeError('WebSocket is not connected. Need to call "accept" first.')
        message = await ws.receive()
        ws._raise_on_disconnect(message)

        # receive success, update activity
        self.last_active = trio.current_time()

        try:
            data = message['bytes']
            if data is not None:
                return data
        except KeyError:
            pass
        try:
            data = message['text']
            if data is not None:
                return data
        except KeyError:
            # This shouldn't happen
            pass

        # We re-raise as ValidationError to ignore this message
        raise ValidationError('Websocket event does not contain bytes nor text') from None

    async def task_recv(self, send_buffer: "trio.MemorySendChannel[RequestEvent]"):
        """
        Coroutine task that receive from websocket and send to send_buffer
        """
        while True:
            try:
                message = await self._ws_receive(self.ws)
            except WEBSOCKET_ERRORS:
                # websocket disconnected
                # we capture and exit silently, so trio will wait other task to finish current job
                break
            except DECODE_ERRORS as e:
                # invalid message, we ignore this and hope the next message is good
                await self.send_error(e)
                continue

            # heartbeat message
            if message == b'pong' or message == 'pong':
                self.pong_received.set()
                continue

            # normal message
            try:
                data = REQUEST_EVENT_DECODER.decode(message)
            except DECODE_ERRORS as e:
                # parse error is acceptable, just drop to message
                await self.send_error(e)
                continue
            except Exception as e:
                logger.error(f'Failed to decode message {message}')
                logger.exception(e)
                continue
            try:
                await send_buffer.send(data)
            except TRIO_CHANNEL_ERRORS:
                # channel closed, skip sending
                continue

        # close from upstream to downstream, so downstream can still finish curren job
        await send_buffer.aclose()

    async def task_job(
            self,
            recv_buffer: "trio.MemoryReceiveChannel[RequestEvent]",
    ):
        """
        Coroutine task that receive data from recv_buffer and do the actual job
        """
        while True:
            try:
                data = await recv_buffer.receive()
            except TRIO_CHANNEL_ERRORS:
                # websocket closed -> recv_buffer closed
                break

            # do jobs,
            # jobs will send data to send_buffer
            try:
                print(data)
            except Exception as e:
                logger.exception(e)
                continue

        # close from upstream to downstream, so downstream can still finish curren job
        if self.send_buffer is not None:
            await self.send_buffer.aclose()

    async def task_send(
            self,
            send_buffer: "trio.MemoryReceiveChannel[ResponseEvent]"
    ):
        """
        Coroutine task that receive data from send_buffer and send to websocket
        """
        while True:
            # receive from buffer
            try:
                data = await send_buffer.receive()
            except TRIO_CHANNEL_ERRORS:
                # websocket closed -> recv_buffer closed -> send_buffer closed
                break

            # encode message
            try:
                message = RESPONSE_EVENT_ENCODER.encode(data)
            except ENCODE_ERRORS as e:
                # invalid data, this shouldn't happen
                logger.error(f'Failed to encode data {data}')
                logger.exception(e)
                continue
            except Exception as e:
                # this shouldn't happen
                logger.error(f'Failed to encode data {data}')
                logger.exception(e)
                continue

            # send message
            try:
                await self.ws.send_bytes(message)
                # send success, update activity
                self.last_active = trio.current_time()
            except WEBSOCKET_ERRORS:
                # websocket disconnected
                # we capture and exit silently, so trio will wait other task to finish current job
                break

        # confirm websocket is closed
        await self.close()

    async def task_heartbeat(self):
        """
        Coroutine task that send "ping" to keep websocket alive if it idled for 30s
        """
        while True:
            now = trio.current_time()
            next_ping = self.last_active + 30
            wait_time = next_ping - now

            # wait until next ping time
            if wait_time > 0:
                await trio.sleep(wait_time)

            # woke up, check activity
            now = trio.current_time()
            next_ping = self.last_active + 30
            if now < next_ping:
                # we have activity during sleep, no need to ping for now
                continue

            # no activity, do ping
            self.pong_received = trio.Event()
            try:
                await self.ws.send_bytes(b'ping')
                # send success, update activity
                self.last_active = trio.current_time()
            except WEBSOCKET_ERRORS:
                # websocket disconnected
                # we capture and exit silently, so trio will wait other task to finish current job
                break

            # wait pong
            with trio.move_on_after(self.PONG_TIMEOUT) as cancel_scope:
                await self.pong_received.wait()

            # check if pong timeout
            if cancel_scope.cancelled_caught:
                await self.close(reason='Pong timeout')
                break

        # confirm websocket is closed
        await self.close()
