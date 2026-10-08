"""
Tests for the Update topic (alasio/backend/topic/update.py): the source
events (one entry per mod, set / delete) and the rpc layer of the update
flow (the manager is faked, its logic is covered by
tests/backend/app/test_update_manager.py).
"""
import pytest
import trio

from alasio.backend.app import update as update_module
from alasio.backend.app.update import UpdateError
from alasio.backend.reactive.event import RpcValueError
from alasio.backend.topic.update import Update, UpdateInfo, UpdateSource
from tests.backend.reactive.test_source_base import MockTopic, decode


@pytest.fixture(autouse=True)
def cleanup_sources():
    """Clear the source and topic singletons after each test."""
    yield
    UpdateSource.singleton_clear()
    Update.singleton_clear()


class FakeUpdateManager:
    """UpdateManager stand-in recording the rpc calls."""

    def __init__(self):
        self.calls = []
        self.error = None

    async def check(self, name=''):
        self.calls.append(('check', name))
        if self.error is not None:
            raise self.error

    async def apply(self, name):
        self.calls.append(('apply', name))
        if self.error is not None:
            raise self.error

    async def cancel(self):
        self.calls.append(('cancel',))
        if self.error is not None:
            raise self.error


@pytest.fixture
def update_topic(monkeypatch):
    """An Update topic of a fake connection, with a fake manager."""
    manager = FakeUpdateManager()
    monkeypatch.setattr(update_module, 'UPDATE_MANAGER', manager)
    yield Update('conn_test', None), manager


class TestUpdateSource:
    """The data = dict[mod_name, UpdateInfo] of the topic."""

    def test_set_and_delete(self):
        source = UpdateSource()
        info = UpdateInfo(state='available', current_version='c1', latest_version='c2')

        source.on_event(('m', info))
        assert source.data == {'m': info}

        # a new instance replaces the entry, a delete removes it
        other = UpdateInfo(state='downloading', current_version='c1', latest_version='c2')
        source.on_event(('m', other))
        assert source.data == {'m': other}
        source.on_event(('m', None))
        assert source.data == {}
        # deleting a missing entry changes nothing
        source.on_event(('m', None))
        assert source.data == {}

    def test_global_singleton(self):
        assert UpdateSource() is UpdateSource()

    @pytest.mark.trio
    async def test_delivery_shape(self):
        source = UpdateSource()
        topic = MockTopic(topic_name='Update')
        source.on_event(('a', UpdateInfo(state='checking')))

        # the snapshot on subscribe
        event = decode(await source.subscribe(topic))
        assert (event.t, event.o) == ('Update', 'full')
        assert event.v['a']['state'] == 'checking'

        # an incremental set and a delete
        source.on_event(('a', UpdateInfo(state='uptodate', current_version='c1')))
        await trio.testing.wait_all_tasks_blocked()
        event = decode(topic.sent[0])
        assert (event.t, event.o, event.k) == ('Update', 'set', ('a',))
        assert event.v['state'] == 'uptodate'

        source.on_event(('a', None))
        await trio.testing.wait_all_tasks_blocked()
        event = decode(topic.sent[1])
        assert (event.t, event.o, event.k) == ('Update', 'del', ('a',))


class TestUpdateRpc:
    """The rpc layer of the update flow."""

    @pytest.mark.trio
    async def test_check_calls_the_manager(self, update_topic):
        topic, manager = update_topic

        await topic.update_check()
        await topic.update_check('m')

        assert manager.calls == [('check', ''), ('check', 'm')]

    @pytest.mark.trio
    async def test_apply_calls_the_manager(self, update_topic):
        topic, manager = update_topic

        await topic.update_apply('m')

        assert manager.calls == [('apply', 'm')]

    @pytest.mark.trio
    async def test_cancel_calls_the_manager(self, update_topic):
        topic, manager = update_topic

        await topic.update_cancel()

        assert manager.calls == [('cancel',)]

    @pytest.mark.trio
    async def test_refusals_become_rpc_value_errors(self, update_topic):
        """The user-facing refusals of the manager carry to the frontend."""
        topic, manager = update_topic
        manager.error = UpdateError('The mod has no update available: "m"')

        with pytest.raises(RpcValueError, match='no update available'):
            await topic.update_apply('m')
        with pytest.raises(RpcValueError, match='no update available'):
            await topic.update_check('m')
        with pytest.raises(RpcValueError, match='no update available'):
            await topic.update_cancel()


class TestTopicRegistration:
    """The topic is served by the global websocket server."""

    def test_update_is_registered(self):
        from alasio.backend.ws.topic import WebsocketServer

        assert WebsocketServer.ALL_TOPIC_CLASS['Update'] is Update
