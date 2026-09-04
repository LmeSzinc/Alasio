"""
Tests for BaseTopic (alasio/backend/ws/ws_topic.py): the source-model
subscription scaffold (op_sub binds the topic to the source resolved by
get_source and sends the snapshot), unsubscription and RPC handling.

The old topic-side data()/reactive_callback diff machinery was removed
together with the last non-source topic: every topic is dynamically bound
to a source and the full snapshot is sent directly after subscribe.
"""

from unittest.mock import AsyncMock, MagicMock

import msgspec
import pytest

from alasio.backend.reactive.event import ResponseEvent
from alasio.logger import logger
from tests.backend.ws.helpers import SampleTopic

DECODER = msgspec.json.Decoder(ResponseEvent)


def mock_server():
    """
    A mock server whose send_nowait records the scaffold's direct snapshot
    sends and whose send() records awaited sends
    """
    server = MagicMock()
    server.sent = []
    server.send_nowait = server.sent.append
    server.send = AsyncMock(side_effect=lambda data: server.sent.append(data))
    return server


def decode_full(server):
    """
    Decode the single full snapshot recorded by the mock server
    (subscribe snapshots are pre-encoded bytes)

    Args:
        server (MagicMock):

    Returns:
        ResponseEvent:
    """
    assert len(server.sent) == 1
    return DECODER.decode(server.sent[0])


@pytest.fixture(autouse=True)
def cleanup_local_singletons():
    """
    Clear singletons of locally defined topics, plus shared test topics
    """
    yield
    SampleTopic.singleton_clear()


class TestOpSub:
    @pytest.mark.trio
    async def test_op_sub_binds_source_and_sends_snapshot(self):
        """
        op_sub binds the topic to the source resolved by get_source and
        sends the subscribe snapshot directly (no topic-side data())
        """
        server = mock_server()
        topic = SampleTopic('conn-1', server)
        await topic.op_sub()
        # dynamically bound to its source
        assert topic._src is topic.source
        assert topic in topic.source._subscribers
        event = decode_full(server)
        assert event.t == 'sample'
        assert event.o == 'full'
        assert event.v == {'a': 1, 'b': 2}

    @pytest.mark.parametrize('data', [{}, [], '', 0, None])
    @pytest.mark.trio
    async def test_op_sub_empty_data_sends_nothing(self, data):
        """
        op_sub with falsy source data registers but sends nothing
        (empty snapshot sends no full, matching the cache-source semantics)
        """
        server = mock_server()
        topic = SampleTopic('conn-1', server, initial=data)
        await topic.op_sub()
        assert topic._src is topic.source
        assert topic in topic.source._subscribers
        assert server.sent == []

    @pytest.mark.trio
    async def test_op_sub_non_dict_data(self):
        """any truthy snapshot value is sent as the full event value"""
        server = mock_server()
        topic = SampleTopic('conn-1', server, initial='hello')
        await topic.op_sub()
        event = decode_full(server)
        assert event.t == 'sample'
        assert event.o == 'full'
        assert event.v == 'hello'


class TestOpRpc:
    @pytest.mark.trio
    async def test_rpc_success(self):
        """successful rpc calls the method with converted args and responds without value"""
        server = mock_server()
        topic = SampleTopic('conn-1', server)
        await topic.op_rpc(func='echo', value={'x': 5}, rpc_id='r1')
        assert topic.calls == [('echo', 5, 'hi')]
        assert server.sent == [ResponseEvent(t='sample', i='r1')]

    @pytest.mark.trio
    async def test_rpc_unknown_method(self):
        server = mock_server()
        topic = SampleTopic('conn-1', server)
        await topic.op_rpc(func='nope', value={}, rpc_id='r1')
        assert server.sent == [ResponseEvent(t='sample', v='RPC method not found "nope"', i='r1')]

    @pytest.mark.trio
    async def test_rpc_validation_error(self):
        """invalid argument types respond with a ValidationError message"""
        server = mock_server()
        topic = SampleTopic('conn-1', server)
        await topic.op_rpc(func='echo', value={'x': 'bad'}, rpc_id='r1')
        assert server.sent == [ResponseEvent(
            t='sample', v='ValidationError: Invalid type for arg "x": Expected `int`, got `str`', i='r1')]

    @pytest.mark.trio
    async def test_rpc_missing_required_arg(self):
        server = mock_server()
        topic = SampleTopic('conn-1', server)
        await topic.op_rpc(func='echo', value={}, rpc_id='r1')
        assert server.sent == [ResponseEvent(t='sample', v='ValidationError: Missing arg: "x"', i='r1')]

    @pytest.mark.trio
    async def test_rpc_input_not_dict(self):
        server = mock_server()
        topic = SampleTopic('conn-1', server)
        await topic.op_rpc(func='echo', value=['not', 'dict'], rpc_id='r1')
        assert server.sent == [ResponseEvent(t='sample', v='ValidationError: Input is not a dict', i='r1')]

    @pytest.mark.trio
    async def test_rpc_input_none(self):
        """value=None is treated as a non-dict input"""
        server = mock_server()
        topic = SampleTopic('conn-1', server)
        await topic.op_rpc(func='echo', value=None, rpc_id='r1')
        assert server.sent == [ResponseEvent(t='sample', v='ValidationError: Input is not a dict', i='r1')]

    @pytest.mark.trio
    async def test_rpc_access_denied(self):
        """methods raising AccessDenied respond with an AccessDenied message"""
        server = mock_server()
        topic = SampleTopic('conn-1', server)
        await topic.op_rpc(func='deny', value={}, rpc_id='r1')
        assert server.sent == [ResponseEvent(t='sample', v='AccessDenied: denied', i='r1')]

    @pytest.mark.trio
    async def test_rpc_rpc_value_error(self):
        server = mock_server()
        topic = SampleTopic('conn-1', server)
        await topic.op_rpc(func='bad_input', value={}, rpc_id='r1')
        assert server.sent == [ResponseEvent(t='sample', v='RpcValueError: bad input', i='r1')]

    @pytest.mark.trio
    async def test_rpc_internal_error(self):
        """unexpected exceptions respond with an error and are logged"""
        server = mock_server()
        topic = SampleTopic('conn-1', server)
        with logger.mock_capture_writer() as capture:
            await topic.op_rpc(func='explode', value={}, rpc_id='r1')
            assert capture.fd.any_contains('boom')
        assert server.sent == [ResponseEvent(t='sample', v='ValueError: boom', i='r1')]


class TestOpUnsub:
    @pytest.mark.trio
    async def test_op_unsub_removes_singleton_and_unsubscribes_source(self):
        """
        op_unsub unsubscribes the bound source and releases the named
        singleton instance
        """
        server = mock_server()
        topic = SampleTopic('conn-1', server)
        await topic.op_sub()
        assert SampleTopic.singleton_instances().get('conn-1') is topic
        assert topic in topic.source._subscribers
        await topic.op_unsub()
        assert topic not in topic.source._subscribers
        assert topic._src is None
        assert 'conn-1' not in SampleTopic.singleton_instances()

    @pytest.mark.trio
    async def test_op_unsub_twice_no_error(self):
        """calling op_unsub twice does not raise"""
        server = mock_server()
        topic = SampleTopic('conn-1', server)
        await topic.op_unsub()
        await topic.op_unsub()
