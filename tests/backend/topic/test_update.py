"""
Tests for the Update topic (alasio/backend/topic/update.py): the source
events (one entry per mod, set / delete) and the rpc layer of the update
flow (the check module and the restart manager are faked, their logic is
covered by tests/backend/app/restart/).
"""
import pytest
import trio
from msgspec import ValidationError

from alasio.backend.app.restart import update_check as update_check_module
from alasio.backend.app.restart.restart_base import FlowRefused
from alasio.backend.reactive.event import RpcValueError
from alasio.backend.topic.update import Update, UpdateInfo, UpdateSource
from tests.backend.reactive.test_source_base import MockTopic, decode


@pytest.fixture(autouse=True)
def cleanup_sources():
    """Clear the source and topic singletons after each test."""
    yield
    UpdateSource.singleton_clear()
    Update.singleton_clear()


class FakeUpdateCheck:
    """UpdateCheck stand-in recording the rpc calls."""

    def __init__(self):
        self.calls = []
        self.available_names = []
        self.cancelled = True
        self.message = ''

    def names(self, managed=True):
        return ['m', 'u']

    def available(self):
        return list(self.available_names)

    async def update_check(self, name):
        self.calls.append(('update_check', name))
        if name not in ('m', 'u'):
            raise ValueError(f'No such mod: "{name}"')
        return self.message

    def cancel_check(self, name):
        self.calls.append(('cancel_check', name))
        return self.cancelled


class FakeRestartManager:
    """RestartManager stand-in recording the rpc calls."""

    def __init__(self):
        self.calls = []
        self.error = None

    def update_apply(self):
        self.calls.append(('update_apply',))
        if self.error is not None:
            raise self.error

    async def cancel_apply(self):
        self.calls.append(('cancel_apply',))
        if self.error is not None:
            raise self.error


@pytest.fixture
def update_topic(monkeypatch):
    """An Update topic of a fake connection, with a fake check module."""
    update_check = FakeUpdateCheck()
    manager = FakeRestartManager()
    monkeypatch.setattr(update_check_module, 'UPDATE_CHECK', update_check)
    # the rpc imports the manager lazily; patching the attribute of the module
    # it imports from is enough for the fakes above
    import alasio.backend.app.restart.restart_manager as manager_module
    monkeypatch.setattr(manager_module, 'RESTART_MANAGER', manager)
    yield Update('conn_test', None), update_check, manager


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
    async def test_check_calls_the_module(self, update_topic):
        topic, update_check, manager = update_topic

        # one mod per call: there is no "check all" entry
        await topic.update_check('m')
        await topic.update_check('u')

        assert update_check.calls == [('update_check', 'm'), ('update_check', 'u')]

    @pytest.mark.trio
    async def test_check_returns_the_message_of_the_module(self, update_topic):
        topic, update_check, manager = update_topic

        assert await topic.update_check('m') == ''
        update_check.message = 'Just checked a moment ago'
        assert await topic.update_check('m') == 'Just checked a moment ago'

    @pytest.mark.trio
    async def test_check_of_an_unknown_mod_is_refused(self, update_topic):
        topic, update_check, manager = update_topic

        with pytest.raises(RpcValueError, match='No such mod'):
            await topic.update_check('nope')

    @pytest.mark.trio
    async def test_apply_requires_an_available_update(self, update_topic):
        topic, update_check, manager = update_topic

        with pytest.raises(RpcValueError, match='No update available'):
            await topic.update_apply()
        assert manager.calls == []

        update_check.available_names = ['m']
        await topic.update_apply()
        assert manager.calls == [('update_apply',)]

    @pytest.mark.trio
    async def test_apply_refusal_carries_to_the_frontend(self, update_topic):
        topic, update_check, manager = update_topic
        update_check.available_names = ['m']
        manager.error = FlowRefused('An update is already in progress')

        with pytest.raises(RpcValueError, match='already in progress'):
            await topic.update_apply()

    @pytest.mark.trio
    async def test_cancel_check_cancels_the_check_of_one_mod(self, update_topic):
        topic, update_check, manager = update_topic

        await topic.cancel_check('m')
        assert update_check.calls == [('cancel_check', 'm')]
        assert manager.calls == []

        update_check.cancelled = False
        with pytest.raises(RpcValueError, match='No check is in flight'):
            await topic.cancel_check('m')

    @pytest.mark.trio
    async def test_cancel_apply_cancels_the_update_flow(self, update_topic):
        topic, update_check, manager = update_topic

        await topic.cancel_apply()
        assert manager.calls == [('cancel_apply',)]
        assert update_check.calls == []

        manager.error = FlowRefused('No update is in progress')
        with pytest.raises(RpcValueError, match='No update is in progress'):
            await topic.cancel_apply()

    def test_the_rpc_surface_is_symmetric(self):
        """The update rpcs are two pairs: update_* starts, cancel_* interrupts."""
        assert set(Update.rpc_methods) == {
            'update_check', 'cancel_check', 'update_apply', 'cancel_apply'
        }

    def test_the_mod_name_is_required(self):
        """The two check rpcs address one mod: a payload without a name is refused."""
        for rpc_name in ('update_check', 'cancel_check'):
            with pytest.raises(ValidationError, match='Missing arg: "name"'):
                Update.rpc_methods[rpc_name].convert({})


class TestTopicRegistration:
    """The topic is served by the global websocket server."""

    def test_update_is_registered(self):
        from alasio.backend.ws.topic import WebsocketServer

        assert WebsocketServer.ALL_TOPIC_CLASS['Update'] is Update
