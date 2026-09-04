"""
Tests for WebsocketTopicServer (alasio/backend/ws/ws_server.py).

Drives the server with a FakeWebSocket (tests/backend/ws/helpers.py), so no
real network is involved. Asserts exact protocol messages.
"""

import msgspec
import pytest
import trio
from starlette.websockets import WebSocketDisconnect, WebSocketState

from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.ws.ws_server import WebsocketTopicServer
from alasio.logger import logger
from tests.backend.ws.helpers import (
    EmptyTopic, ErrorTopic, FakeWebSocket, FullOnlyTopic, HarnessWebsocketServer, MismatchTopic, SampleTopic,
    ServerHarness
)


class TestConnectionLifecycle:
    @pytest.mark.parametrize('error', [
        WebSocketDisconnect(4001),
        RuntimeError('accept failed'),
    ])
    @pytest.mark.trio
    async def test_accept_failure_closes(self, error):
        """accept() raising closes the websocket and serve() returns"""
        fake_ws = FakeWebSocket(accept_error=error)
        server = WebsocketTopicServer(fake_ws)
        async with trio.open_nursery() as nursery:
            nursery.start_soon(server.serve)
        assert fake_ws.application_state == WebSocketState.DISCONNECTED
        assert fake_ws.closed == [(1000, None)]

    @pytest.mark.trio
    async def test_serve_accepts_and_subscribes_default(self, ws_server_harness):
        """serve() accepts the connection and init() subscribes default topics"""
        harness = ws_server_harness
        assert harness.fake_ws.application_state == WebSocketState.CONNECTED
        # default topic is subscribed without any client message
        assert 'sample' in harness.server.subscribed
        assert isinstance(harness.server.subscribed['sample'], SampleTopic)
        # named singleton is keyed by conn_id
        assert SampleTopic.singleton_instances().get(harness.server.id) is harness.server.subscribed['sample']

    @pytest.mark.trio
    async def test_message_flows_through_all_tasks(self, ws_server_harness):
        """
        A client message reaches the websocket output, proving the
        task_recv -> task_job -> task_send pipeline is running
        """
        harness = ws_server_harness
        harness.fake_ws.send_message(b'{"t":"full_only"}')
        await trio.testing.wait_all_tasks_blocked()
        assert harness.sent_events() == [{'t': 'full_only', 'o': 'full', 'v': {'a': 1, 'b': 2}}]

    @pytest.mark.trio
    async def test_client_disconnect_ends_serve(self):
        """A client disconnect message makes all tasks exit and serve() return"""
        fake_ws = FakeWebSocket()
        server = HarnessWebsocketServer(fake_ws)
        with trio.fail_after(5):
            async with trio.open_nursery() as nursery:
                nursery.start_soon(server.serve)
                await trio.testing.wait_all_tasks_blocked()
                assert fake_ws.application_state == WebSocketState.CONNECTED
                fake_ws.disconnect(1001)
                # nursery exits when serve() returns
        assert fake_ws.closed == [(1000, None)]

    @pytest.mark.trio
    async def test_endpoint_runs_cleanup(self):
        """endpoint() serves the connection and unsubscribes all topics on exit"""
        fake_ws = FakeWebSocket()
        async with trio.open_nursery() as nursery:
            nursery.start_soon(HarnessWebsocketServer.endpoint, fake_ws)
            # wait until accepted and the default topic is subscribed
            with trio.fail_after(5):
                while not SampleTopic.singleton_instances():
                    await trio.sleep(0)
            # subscribe a non-default topic
            fake_ws.send_message(b'{"t":"full_only"}')
            with trio.fail_after(5):
                while not FullOnlyTopic.singleton_instances():
                    await trio.sleep(0)
            # client disconnects, endpoint() returns after cleanup
            fake_ws.disconnect(1001)
            # nursery exits when endpoint() returns
        # all topics were unsubscribed by endpoint's cleanup
        assert SampleTopic.singleton_instances() == {}
        assert FullOnlyTopic.singleton_instances() == {}

    @pytest.mark.parametrize('error', [
        RuntimeError('close failed'),
        WebSocketDisconnect(1000),
    ])
    @pytest.mark.trio
    async def test_close_swallows_websocket_errors(self, error):
        """close() swallows errors raised by ws.close()"""
        fake_ws = FakeWebSocket(close_error=error)
        fake_ws.application_state = WebSocketState.CONNECTED
        server = WebsocketTopicServer(fake_ws)
        await server.close()
        assert fake_ws.closed == []

    @pytest.mark.trio
    async def test_close_skips_when_disconnected(self):
        """close() does not call ws.close() on an already-disconnected websocket"""
        fake_ws = FakeWebSocket()
        fake_ws.application_state = WebSocketState.DISCONNECTED
        server = WebsocketTopicServer(fake_ws)
        await server.close()
        assert fake_ws.closed == []


class TestSub:
    @pytest.mark.trio
    async def test_sub_valid_topic_sends_full(self, ws_server_harness):
        """sub on a valid topic creates the topic and sends a full event"""
        harness = ws_server_harness
        harness.fake_ws.send_message(b'{"t":"full_only"}')
        await trio.testing.wait_all_tasks_blocked()
        assert harness.sent_events() == [{'t': 'full_only', 'o': 'full', 'v': {'a': 1, 'b': 2}}]
        assert 'full_only' in harness.server.subscribed
        assert FullOnlyTopic.singleton_instances().get(harness.server.id) is harness.server.subscribed['full_only']

    @pytest.mark.trio
    async def test_sub_via_text_message(self, ws_server_harness):
        """text messages are accepted the same as bytes"""
        harness = ws_server_harness
        harness.fake_ws.send_message('{"t":"full_only"}')
        await trio.testing.wait_all_tasks_blocked()
        assert harness.sent_events() == [{'t': 'full_only', 'o': 'full', 'v': {'a': 1, 'b': 2}}]

    @pytest.mark.trio
    async def test_sub_unknown_topic_denied(self, ws_server_harness):
        """sub on an unknown topic sends an error, connection stays alive"""
        harness = ws_server_harness
        harness.fake_ws.send_message(b'{"t":"unknown"}')
        await trio.testing.wait_all_tasks_blocked()
        assert harness.sent_events() == [{
            't': 'error',
            'o': 'full',
            'v': 'AccessDenied: No such topic: "unknown"',
        }]
        # connection still alive, next message works
        harness.fake_ws.send_message(b'{"t":"full_only"}')
        await trio.testing.wait_all_tasks_blocked()
        assert harness.sent_events()[-1] == {'t': 'full_only', 'o': 'full', 'v': {'a': 1, 'b': 2}}

    @pytest.mark.trio
    async def test_sub_default_topic_ignored(self, ws_server_harness):
        """sub on a default-subscribed topic is ignored"""
        harness = ws_server_harness
        harness.fake_ws.send_message(b'{"t":"sample"}')
        await trio.testing.wait_all_tasks_blocked()
        assert harness.fake_ws.sent == []

    @pytest.mark.trio
    async def test_sub_duplicate_ignored(self, ws_server_harness):
        """sub on an already-subscribed topic is ignored"""
        harness = ws_server_harness
        harness.fake_ws.send_message(b'{"t":"full_only"}')
        await trio.testing.wait_all_tasks_blocked()
        harness.fake_ws.send_message(b'{"t":"full_only"}')
        await trio.testing.wait_all_tasks_blocked()
        assert harness.sent_events() == [{'t': 'full_only', 'o': 'full', 'v': {'a': 1, 'b': 2}}]
        # only one topic instance was created
        assert len(FullOnlyTopic.singleton_instances()) == 1

    @pytest.mark.trio
    async def test_sub_empty_data_sends_nothing(self, ws_server_harness):
        """sub on a topic with falsy data subscribes without sending anything"""
        harness = ws_server_harness
        harness.fake_ws.send_message(b'{"t":"empty"}')
        await trio.testing.wait_all_tasks_blocked()
        assert harness.fake_ws.sent == []
        assert 'empty' in harness.server.subscribed
        assert isinstance(harness.server.subscribed['empty'], EmptyTopic)


class TestUnsub:
    @pytest.mark.trio
    async def test_unsub_subscribed_removes_topic(self, ws_server_harness):
        """unsub on a subscribed topic removes it and releases the singleton"""
        harness = ws_server_harness
        harness.fake_ws.send_message(b'{"t":"full_only"}')
        await trio.testing.wait_all_tasks_blocked()
        conn_id = harness.server.id
        assert FullOnlyTopic.singleton_instances().get(conn_id) is not None

        harness.fake_ws.send_message(b'{"t":"full_only","o":"unsub"}')
        await trio.testing.wait_all_tasks_blocked()
        assert 'full_only' not in harness.server.subscribed
        assert FullOnlyTopic.singleton_instances().get(conn_id) is None
        # only the full event was sent
        assert harness.sent_events() == [{'t': 'full_only', 'o': 'full', 'v': {'a': 1, 'b': 2}}]

    @pytest.mark.trio
    async def test_unsub_never_subscribed_ignored(self, ws_server_harness):
        """unsub on a topic that was never subscribed is ignored"""
        harness = ws_server_harness
        harness.fake_ws.send_message(b'{"t":"full_only","o":"unsub"}')
        await trio.testing.wait_all_tasks_blocked()
        assert harness.fake_ws.sent == []
        # can still subscribe afterwards
        harness.fake_ws.send_message(b'{"t":"full_only"}')
        await trio.testing.wait_all_tasks_blocked()
        assert len(harness.sent_events()) == 1

    @pytest.mark.trio
    async def test_unsub_default_topic_ignored(self, ws_server_harness):
        """unsub on a default-subscribed topic is ignored"""
        harness = ws_server_harness
        harness.fake_ws.send_message(b'{"t":"sample","o":"unsub"}')
        await trio.testing.wait_all_tasks_blocked()
        assert 'sample' in harness.server.subscribed
        assert harness.fake_ws.sent == []


class TestRpc:
    @pytest.mark.trio
    async def test_rpc_missing_id(self, ws_server_harness):
        """rpc without id sends an error and does not execute the method"""
        harness = ws_server_harness
        harness.fake_ws.send_message(b'{"t":"sample","o":"rpc","f":"echo","v":{"x":1}}')
        await trio.testing.wait_all_tasks_blocked()
        assert harness.fake_ws.sent == [b'{"t":"error","v":"Missing RPC ID in event"}']
        topic = SampleTopic.singleton_instances().get(harness.server.id)
        assert topic.calls == []

    @pytest.mark.trio
    async def test_rpc_unknown_topic(self, ws_server_harness):
        harness = ws_server_harness
        harness.fake_ws.send_message(b'{"t":"unknown","o":"rpc","f":"f","v":{},"i":"r1"}')
        await trio.testing.wait_all_tasks_blocked()
        assert harness.sent_events() == [{
            't': 'unknown',
            'v': 'No such topic: "unknown"',
            'i': 'r1',
        }]

    @pytest.mark.trio
    async def test_rpc_not_subscribed(self, ws_server_harness):
        """rpc on a valid but not-subscribed topic sends an error"""
        harness = ws_server_harness
        harness.fake_ws.send_message(b'{"t":"full_only","o":"rpc","f":"f","v":{},"i":"r1"}')
        await trio.testing.wait_all_tasks_blocked()
        assert harness.sent_events() == [{
            't': 'full_only',
            'v': 'Cannot do RPC before subscribing topic: "full_only"',
            'i': 'r1',
        }]

    @pytest.mark.trio
    async def test_rpc_success_omits_value(self, ws_server_harness):
        """successful rpc responds with the same id and no value"""
        harness = ws_server_harness
        # 'sample' is default-subscribed, no explicit sub needed
        harness.fake_ws.send_message(b'{"t":"sample","o":"rpc","f":"echo","v":{"x":7},"i":"r1"}')
        await trio.testing.wait_all_tasks_blocked()
        assert harness.fake_ws.sent == [b'{"t":"sample","i":"r1"}']
        # the method was called with converted args
        topic = SampleTopic.singleton_instances().get(harness.server.id)
        assert topic.calls == [('echo', 7, 'hi')]

    @pytest.mark.trio
    async def test_rpc_method_not_found(self, ws_server_harness):
        harness = ws_server_harness
        harness.fake_ws.send_message(b'{"t":"sample","o":"rpc","f":"nope","v":{},"i":"r1"}')
        await trio.testing.wait_all_tasks_blocked()
        assert harness.sent_events() == [{
            't': 'sample',
            'v': 'RPC method not found "nope"',
            'i': 'r1',
        }]

    @pytest.mark.trio
    async def test_rpc_validation_error(self, ws_server_harness):
        """invalid argument types send an error with the same id"""
        harness = ws_server_harness
        harness.fake_ws.send_message(b'{"t":"sample","o":"rpc","f":"echo","v":{"x":"bad"},"i":"r1"}')
        await trio.testing.wait_all_tasks_blocked()
        assert harness.sent_events() == [{
            't': 'sample',
            'v': 'ValidationError: Invalid type for arg "x": Expected `int`, got `str`',
            'i': 'r1',
        }]

    @pytest.mark.trio
    async def test_rpc_rpc_value_error(self, ws_server_harness):
        harness = ws_server_harness
        harness.fake_ws.send_message(b'{"t":"sample","o":"rpc","f":"bad_input","v":{},"i":"r1"}')
        await trio.testing.wait_all_tasks_blocked()
        assert harness.sent_events() == [{
            't': 'sample',
            'v': 'RpcValueError: bad input',
            'i': 'r1',
        }]


class TestMessageErrors:
    @pytest.mark.trio
    async def test_unknown_op_denied(self, ws_server_harness):
        """an operation outside sub/unsub/rpc is rejected at decode time, connection stays alive"""
        harness = ws_server_harness
        harness.fake_ws.send_message(b'{"t":"sample","o":"foo"}')
        await trio.testing.wait_all_tasks_blocked()
        assert harness.sent_events() == [{
            't': 'error',
            'o': 'full',
            'v': "ValidationError: Invalid enum value 'foo' - at `$.o`",
        }]
        # connection still alive
        harness.fake_ws.send_message(b'{"t":"full_only"}')
        await trio.testing.wait_all_tasks_blocked()
        assert len(harness.sent_events()) == 2

    @pytest.mark.trio
    async def test_invalid_json_message(self, ws_server_harness):
        """invalid JSON sends a decode error, connection stays alive"""
        harness = ws_server_harness
        harness.fake_ws.send_message(b'not json')
        await trio.testing.wait_all_tasks_blocked()
        assert harness.sent_events() == [{
            't': 'error',
            'o': 'full',
            'v': 'DecodeError: JSON is malformed: invalid character (byte 4)',
        }]
        # connection still alive
        harness.fake_ws.send_message(b'{"t":"full_only"}')
        await trio.testing.wait_all_tasks_blocked()
        assert harness.sent_events()[-1] == {'t': 'full_only', 'o': 'full', 'v': {'a': 1, 'b': 2}}

    @pytest.mark.parametrize('message', [
        {'foo': 1},
        {'bytes': None},
        {'text': None},
    ])
    @pytest.mark.trio
    async def test_message_without_bytes_or_text(self, message, ws_server_harness):
        """messages without usable bytes/text send a validation error"""
        harness = ws_server_harness
        harness.fake_ws.send_message(message)
        await trio.testing.wait_all_tasks_blocked()
        assert harness.sent_events() == [{
            't': 'error',
            'o': 'full',
            'v': 'ValidationError: Websocket event does not contain bytes nor text',
        }]


class TestHeartbeat:
    @pytest.mark.parametrize('pong', ['pong', b'pong'])
    @pytest.mark.trio
    async def test_ping_pong_keeps_alive(self, pong, ws_server_harness, autojump_clock):
        """ping is sent after PING_INTERVAL, pong resets activity and keeps the connection"""
        harness = ws_server_harness
        # no activity for PING_INTERVAL
        await trio.sleep(30)
        await trio.testing.wait_all_tasks_blocked()
        assert harness.fake_ws.sent == [b'ping']
        assert harness.fake_ws.closed == []

        # pong keeps the connection alive, next ping comes after another interval
        harness.fake_ws.send_message(pong)
        await trio.testing.wait_all_tasks_blocked()
        await trio.sleep(30)
        await trio.testing.wait_all_tasks_blocked()
        assert harness.fake_ws.sent == [b'ping', b'ping']
        assert harness.fake_ws.closed == []

    @pytest.mark.trio
    async def test_pong_timeout_closes(self, ws_server_harness, autojump_clock):
        """no pong within PONG_TIMEOUT closes the connection"""
        harness = ws_server_harness
        await trio.sleep(30)
        await trio.testing.wait_all_tasks_blocked()
        assert harness.fake_ws.sent == [b'ping']
        # no pong, close after PONG_TIMEOUT
        await trio.sleep(15)
        await trio.testing.wait_all_tasks_blocked()
        assert harness.fake_ws.closed == [(1000, 'Pong timeout')]

    @pytest.mark.trio
    async def test_activity_delays_ping(self, ws_server_harness, autojump_clock):
        """incoming messages reset last_active, so no ping is sent early"""
        harness = ws_server_harness
        # continuous activity below the interval
        for _ in range(3):
            await trio.sleep(9)
            harness.fake_ws.send_message(b'{"t":"full_only","o":"unsub"}')
            await trio.testing.wait_all_tasks_blocked()
        # total 27s of activity, still no ping
        assert harness.fake_ws.sent == []
        # last activity was at 27s, ping fires at 57s
        await trio.sleep(30)
        await trio.testing.wait_all_tasks_blocked()
        assert harness.fake_ws.sent == [b'ping']

    @pytest.mark.trio
    async def test_proactive_pong_keeps_alive(self, ws_server_harness, autojump_clock):
        """proactive pongs (not triggered by ping) never cause a timeout close"""
        harness = ws_server_harness
        # the client sends a proactive pong before the first ping
        harness.fake_ws.send_message('pong')
        await trio.testing.wait_all_tasks_blocked()
        # the first ping is still sent at PING_INTERVAL
        await trio.sleep(30)
        await trio.testing.wait_all_tasks_blocked()
        assert harness.fake_ws.sent == [b'ping']
        # the client keeps sending proactive pongs every 10s,
        # covering every PONG_TIMEOUT window across several ping cycles
        for _ in range(5):
            await trio.sleep(10)
            harness.fake_ws.send_message('pong')
            await trio.testing.wait_all_tasks_blocked()
        # total 80s, several ping cycles, no timeout close
        assert harness.fake_ws.closed == []


class TestSendBuffer:
    @pytest.mark.trio
    async def test_send_backpressure(self, autojump_clock):
        """send() blocks when the send buffer is full, send_nowait raises WouldBlock"""
        server = WebsocketTopicServer(FakeWebSocket())
        server.send_buffer, recv = trio.open_memory_channel(2)
        event = ResponseEvent(t='sample')
        assert await server.send(event) is True
        assert await server.send(event) is True
        # buffer is full now, send() blocks
        with pytest.raises(trio.TooSlowError):
            with trio.fail_after(0.05):
                await server.send(event)
        # drain one message, send succeeds again
        recv.receive_nowait()
        assert await server.send(event) is True
        # send_nowait raises WouldBlock on a full buffer
        with pytest.raises(trio.WouldBlock):
            server.send_nowait(event)
        recv.close()
        # send on a closed buffer returns False instead of raising
        assert await server.send(event) is False
        assert server.send_nowait(event) is False

    @pytest.mark.trio
    async def test_send_batch_events(self):
        """send() encodes a list of events as a JSON array"""
        server = WebsocketTopicServer(FakeWebSocket())
        server.send_buffer, recv = trio.open_memory_channel(8)
        events = [ResponseEvent(t='a'), ResponseEvent(t='b')]
        assert await server.send(events) is True
        assert recv.receive_nowait() == b'[{"t":"a"},{"t":"b"}]'

    @pytest.mark.trio
    async def test_send_nowait(self):
        """send_nowait puts encoded bytes into the buffer"""
        server = WebsocketTopicServer(FakeWebSocket())
        server.send_buffer, recv = trio.open_memory_channel(8)
        event = ResponseEvent(t='sample', o='full', v={'a': 1})
        assert server.send_nowait(event) is True
        assert recv.receive_nowait() == b'{"t":"sample","o":"full","v":{"a":1}}'

    @pytest.mark.trio
    async def test_send_encode_failure_returns_false(self):
        """unencodable data returns False and logs the error"""
        server = WebsocketTopicServer(FakeWebSocket())
        server.send_buffer, recv = trio.open_memory_channel(8)
        with logger.mock_capture_writer() as capture:
            assert await server.send({'bad': object()}) is False
            assert capture.fd.any_contains('Failed to encode data')
        # buffer is untouched
        with pytest.raises(trio.WouldBlock):
            recv.receive_nowait()


class TestLossyBuffer:
    @pytest.mark.trio
    async def test_send_lossy_drops_oldest(self):
        """send_lossy keeps at most LOSSY_BUFFER_LENGTH messages, dropping the oldest"""
        class SmallLossyServer(WebsocketTopicServer):
            LOSSY_BUFFER_LENGTH = 2

        server = SmallLossyServer(FakeWebSocket())
        assert server.lossy_buffer.maxlen == 2
        for n in range(3):
            assert server.send_lossy(ResponseEvent(t='sample', o='full', v={'n': n})) is True
        assert len(server.lossy_buffer) == 2
        assert msgspec.json.decode(server.lossy_buffer[0]) == {'t': 'sample', 'o': 'full', 'v': {'n': 1}}
        assert msgspec.json.decode(server.lossy_buffer[1]) == {'t': 'sample', 'o': 'full', 'v': {'n': 2}}

    @pytest.mark.trio
    async def test_send_lossy_encode_failure_returns_false(self):
        """unencodable data returns False and leaves the lossy buffer untouched"""
        server = WebsocketTopicServer(FakeWebSocket())
        with logger.mock_capture_writer() as capture:
            assert server.send_lossy({'bad': object()}) is False
            assert capture.fd.any_contains('Failed to encode data')
        assert len(server.lossy_buffer) == 0

    @pytest.mark.trio
    async def test_send_lossy_reaches_websocket(self, ws_server_harness):
        """lossy-buffered messages are sent to the websocket when the send buffer is empty"""
        harness = ws_server_harness
        assert harness.server.send_lossy(ResponseEvent(t='sample', o='full', v={'n': 1})) is True
        await trio.testing.wait_all_tasks_blocked()
        assert harness.sent_events() == [{'t': 'sample', 'o': 'full', 'v': {'n': 1}}]


class TestSendError:
    @pytest.mark.trio
    async def test_send_error_conversion(self):
        """send_error converts Exception/str and passes bytes/events through"""
        server = WebsocketTopicServer(FakeWebSocket())
        server.send_buffer, recv = trio.open_memory_channel(8)
        # Exception -> "ClassName: message"
        await server.send_error(RuntimeError('boom'))
        assert recv.receive_nowait() == b'{"t":"error","o":"full","v":"RuntimeError: boom"}'
        # str -> value directly
        await server.send_error('plain message')
        assert recv.receive_nowait() == b'{"t":"error","o":"full","v":"plain message"}'
        # bytes pass through
        await server.send_error(b'\x00raw')
        assert recv.receive_nowait() == b'\x00raw'
        # ResponseEvent pass through
        event = ResponseEvent(t='x', o='del', k=('a',))
        await server.send_error(event)
        assert recv.receive_nowait() == b'{"t":"x","o":"del","k":["a"]}'


class TestTopicNameMismatch:
    """subscriptions are keyed by the requested topic name, not by TOPIC_NAME"""

    class MismatchServer(WebsocketTopicServer):
        ALL_TOPIC_CLASS = {'mismatch': MismatchTopic}
        DEFAULT_TOPIC_CLASS = {}

    @pytest.mark.trio
    async def test_sub_unsub_rpc_use_requested_name(self):
        """sub/unsub/rpc all work by the requested name even if NAME differs"""
        harness = ServerHarness(self.MismatchServer)
        async with trio.open_nursery() as nursery:
            nursery.start_soon(harness.run_serve)
            await harness.wait_connected()
            # sub by the registered key, events still carry the TOPIC_NAME
            harness.fake_ws.send_message(b'{"t":"mismatch"}')
            await trio.testing.wait_all_tasks_blocked()
            assert 'mismatch' in harness.server.subscribed
            assert harness.sent_events() == [{'t': 'mismatch_actual', 'o': 'full', 'v': {'x': 1}}]
            # rpc finds the topic by the requested name
            harness.fake_ws.send_message(b'{"t":"mismatch","o":"rpc","f":"echo","v":{},"i":"r1"}')
            await trio.testing.wait_all_tasks_blocked()
            assert harness.sent_events()[-1] == {'t': 'mismatch_actual', 'i': 'r1'}
            # unsub removes the topic by the requested name
            harness.fake_ws.send_message(b'{"t":"mismatch","o":"unsub"}')
            await trio.testing.wait_all_tasks_blocked()
            assert 'mismatch' not in harness.server.subscribed
            # graceful shutdown
            await harness.stop()
            await harness.server.cleanup()


class TestCloseAll:
    @pytest.mark.trio
    async def test_close_all_connections(self, ws_server_harness):
        """close_all_connections closes the websocket with code 1001"""
        harness = ws_server_harness
        await WebsocketTopicServer.close_all_connections()
        await trio.testing.wait_all_tasks_blocked()
        assert harness.fake_ws.closed == [(1001, 'Server shutdown')]


class TestCleanup:
    @pytest.mark.trio
    async def test_cleanup_unsubscribes_all(self, ws_server_harness):
        """cleanup unsubscribes every topic and releases singletons"""
        harness = ws_server_harness
        harness.fake_ws.send_message(b'{"t":"full_only"}')
        harness.fake_ws.send_message(b'{"t":"empty"}')
        await trio.testing.wait_all_tasks_blocked()
        assert set(harness.server.subscribed) == {'sample', 'full_only', 'empty'}

        await harness.server.cleanup()
        assert harness.server.subscribed == {}
        assert SampleTopic.singleton_instances() == {}
        assert FullOnlyTopic.singleton_instances() == {}
        assert EmptyTopic.singleton_instances() == {}

    @pytest.mark.trio
    async def test_cleanup_survives_topic_error(self, ws_server_harness):
        """cleanup keeps going when a topic's op_unsub raises"""
        harness = ws_server_harness
        harness.fake_ws.send_message(b'{"t":"error_topic"}')
        await trio.testing.wait_all_tasks_blocked()
        assert 'error_topic' in harness.server.subscribed
        assert isinstance(harness.server.subscribed['error_topic'], ErrorTopic)

        with logger.mock_capture_writer() as capture:
            await harness.server.cleanup()
            assert capture.fd.any_contains('unsub failed')
        # subscribed is cleared even though one topic failed
        assert harness.server.subscribed == {}
