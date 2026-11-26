"""
Tests for the config RPC entry points of ConfigArg
(alasio/backend/topic/config.py).

The rpc methods resolve the config from the connection state (ConnState),
call the loader in a worker thread and hand the returned events to the
unified config event entry (_config_event.on_config_event), which is the
only path that pushes updates to the views. The loader is stubbed here, so
the tests pin that delegation (argument order included) and the broadcast;
the reset logic itself is covered by tests/config/entry/test_mod_config.py
and the view push by test_config_event.py / test_viewport_subclasses.py.
"""

import pytest
from msgspec import ValidationError
from msgspecerror import ErrorInfo, ErrorType

from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.topic import _config_event
from alasio.backend.topic.config import ConfigArg, ConfigArgSource
from alasio.backend.topic.state import ConnState
from alasio.config.entry.loader import MOD_LOADER
from alasio.config.entry.model import ConfigSetEvent

MOD_NAME = 'test_mod'
CONFIG = 'alas'
CONN_ID = 'conn_test'


class FakeGroupReset:
    """Stand-in of MOD_LOADER.gui_config_group_reset: records its calls"""

    def __init__(self, responses=None):
        """
        Args:
            responses (list[ConfigSetEvent]): Events the reset returns, one
                per arg. Defaults to a single event.
        """
        if responses is None:
            responses = [ConfigSetEvent(task='TaskA', group='group_a', arg='arg_a', value=0)]
        self.responses = responses
        self.calls = []

    def __call__(self, mod_name, config_name, task_name, group_name):
        self.calls.append((mod_name, config_name, task_name, group_name))
        return self.responses


@pytest.fixture
def loader(monkeypatch):
    """A fake loader in place of MOD_LOADER.gui_config_group_reset"""
    fake = FakeGroupReset()
    monkeypatch.setattr(MOD_LOADER, 'gui_config_group_reset', fake)
    return fake


@pytest.fixture
def broadcast(monkeypatch):
    """Capture the calls of the unified config event entry"""
    sent = []
    monkeypatch.setattr(_config_event, 'on_config_event', lambda config_name, event: sent.append((config_name, event)))
    return sent


@pytest.fixture(autouse=True)
def cleanup_topics():
    """Drop the per-connection topic instances after each test"""
    yield
    ConfigArg.singleton_clear()
    ConnState.singleton_clear()


async def set_nav(config_name=CONFIG, mod_name=MOD_NAME):
    """
    Point the connection state at a config, the way set_config rpc does.

    Args:
        config_name (str): Config name to open, empty to close it
        mod_name (str): Mod of the config
    """
    state = ConnState(CONN_ID, None)
    nav = await state.nav_state
    nav.mod_name = mod_name
    nav.config_name = config_name
    await state.nav_state.mutate()


async def call_group_reset(payload):
    """
    Call the rpc the way the ws server does: by name, with the decoded payload.

    Args:
        payload (dict): Rpc input
    """
    topic = ConfigArg(CONN_ID, None)
    await ConfigArg.rpc_methods['group_reset'].call_async(topic, payload)


class TestGroupResetRpc:
    """group_reset(task, group): reset an entire group of the opened config"""

    @pytest.mark.trio
    async def test_group_reset_delegates_and_broadcasts(self, loader, broadcast):
        """The loader gets (mod, config, task, group), its events are broadcast"""
        await set_nav()

        await call_group_reset({'task': 'TaskA', 'group': 'group_a'})

        assert loader.calls == [(MOD_NAME, CONFIG, 'TaskA', 'group_a')]
        assert len(broadcast) == 1
        config_name, events = broadcast[0]
        assert config_name == CONFIG
        # the events of the loader are handed over as is, no copy / re-wrap
        assert events is loader.responses

    @pytest.mark.trio
    async def test_group_reset_requires_task_and_group(self, loader, broadcast):
        """An empty task / group is a silent no-op"""
        await set_nav()

        await call_group_reset({'task': '', 'group': 'group_a'})
        await call_group_reset({'task': 'TaskA', 'group': ''})

        assert loader.calls == []
        assert broadcast == []

    @pytest.mark.trio
    async def test_group_reset_requires_config(self, loader, broadcast):
        """A connection without an opened config is a silent no-op"""
        await set_nav(config_name='')

        await call_group_reset({'task': 'TaskA', 'group': 'group_a'})

        assert loader.calls == []
        assert broadcast == []

    @pytest.mark.trio
    async def test_group_reset_without_events_is_silent(self, broadcast, monkeypatch):
        """An unknown group yields no events: nothing is broadcast"""
        loader = FakeGroupReset(responses=[])
        monkeypatch.setattr(MOD_LOADER, 'gui_config_group_reset', loader)
        await set_nav()

        await call_group_reset({'task': 'NoTask', 'group': 'NoGroup'})

        assert loader.calls == [(MOD_NAME, CONFIG, 'NoTask', 'NoGroup')]
        assert broadcast == []


class FakeBatchSet:
    """Stand-in of MOD_LOADER.gui_config_batch_set: records its calls"""

    def __init__(self, success=True, responses=None):
        """
        Args:
            success (bool): Result of the batch set
            responses (list[ConfigSetEvent]): Events the batch returns
        """
        if responses is None:
            responses = [ConfigSetEvent(task='TaskA', group='group_a', arg='arg_a', value=0)]
        self.success = success
        self.responses = responses
        self.calls = []

    def __call__(self, mod_name, config_name, events):
        self.calls.append((mod_name, config_name, events))
        return self.success, self.responses


class FakeServer:
    """Stand-in of the websocket server of one connection"""

    def __init__(self):
        self.sent = []

    async def send(self, event):
        self.sent.append(event)


class FakeArgSource:
    """Stand-in of ConfigArgSource: only the event mapping is read"""

    def __init__(self, mapping):
        self.dict_config_to_topic = mapping


async def call_batch_set(payload, server=None):
    """
    Call the rpc the way the ws server does: by name, with the decoded payload.

    Args:
        payload (dict): Rpc input
        server (FakeServer): Server of the calling connection
    """
    topic = ConfigArg(CONN_ID, server)
    await ConfigArg.rpc_methods['batch_set'].call_async(topic, payload)


class TestBatchSetRpc:
    """batch_set(events): save several args of the opened config atomically"""

    @pytest.mark.trio
    async def test_batch_set_delegates_and_broadcasts(self, broadcast, monkeypatch):
        """The loader gets (mod, config, events), its events are broadcast"""
        loader = FakeBatchSet()
        monkeypatch.setattr(MOD_LOADER, 'gui_config_batch_set', loader)
        await set_nav()

        await call_batch_set({'events': [
            {'task': 'TaskA', 'group': 'group_a', 'arg': 'Value', 'value': 119},
            {'task': 'TaskA', 'group': 'group_a', 'arg': 'Time', 'value': '2026-11-26T12:00:00Z'},
        ]})

        assert len(loader.calls) == 1
        mod_name, config_name, events = loader.calls[0]
        assert (mod_name, config_name) == (MOD_NAME, CONFIG)
        assert events == [
            ConfigSetEvent(task='TaskA', group='group_a', arg='Value', value=119),
            ConfigSetEvent(task='TaskA', group='group_a', arg='Time', value='2026-11-26T12:00:00Z'),
        ]
        assert len(broadcast) == 1
        broadcast_config, broadcast_events = broadcast[0]
        assert broadcast_config == CONFIG
        assert broadcast_events is loader.responses

    @pytest.mark.trio
    async def test_batch_set_ignores_an_empty_batch(self, broadcast, monkeypatch):
        """An empty event list is a silent no-op"""
        loader = FakeBatchSet()
        monkeypatch.setattr(MOD_LOADER, 'gui_config_batch_set', loader)
        await set_nav()

        await call_batch_set({'events': []})

        assert loader.calls == []
        assert broadcast == []

    @pytest.mark.trio
    async def test_batch_set_ignores_an_incomplete_event(self, broadcast, monkeypatch):
        """A batch with an incomplete event is dropped as a whole"""
        loader = FakeBatchSet()
        monkeypatch.setattr(MOD_LOADER, 'gui_config_batch_set', loader)
        await set_nav()

        await call_batch_set({'events': [
            {'task': 'TaskA', 'group': 'group_a', 'arg': 'Value', 'value': 119},
            {'task': 'TaskA', 'group': 'group_a', 'arg': '', 'value': 0},
        ]})

        assert loader.calls == []
        assert broadcast == []

    @pytest.mark.trio
    async def test_batch_set_requires_config(self, broadcast, monkeypatch):
        """A connection without an opened config is a silent no-op"""
        loader = FakeBatchSet()
        monkeypatch.setattr(MOD_LOADER, 'gui_config_batch_set', loader)
        await set_nav(config_name='')

        await call_batch_set({'events': [
            {'task': 'TaskA', 'group': 'group_a', 'arg': 'Value', 'value': 119},
        ]})

        assert loader.calls == []
        assert broadcast == []

    @pytest.mark.trio
    async def test_batch_set_failure_sends_every_rollback_to_the_caller(self, broadcast, monkeypatch):
        """A failed batch restores the optimistic values of the caller only"""
        error = ErrorInfo(msg='Value must be at most 150', type=ErrorType.INPUT_REJECTED, loc=('Value',))
        loader = FakeBatchSet(success=False, responses=[
            ConfigSetEvent(task='TaskA', group='group_a', arg='Value', value=119, error=error),
            ConfigSetEvent(task='TaskA', group='group_a', arg='Time', value='2020-01-01T00:00:00Z'),
        ])
        monkeypatch.setattr(MOD_LOADER, 'gui_config_batch_set', loader)
        monkeypatch.setattr(ConfigArgSource, 'get', staticmethod(lambda *args: FakeArgSource({
            ('TaskA', 'group_a', 'Value'): ('card_a', 'group_a', 'Value'),
            ('TaskA', 'group_a', 'Time'): ('card_a', 'group_a', 'Time'),
        })))
        await set_nav()
        server = FakeServer()

        with pytest.raises(ValidationError, match='Value must be at most 150'):
            await call_batch_set({'events': [
                {'task': 'TaskA', 'group': 'group_a', 'arg': 'Value', 'value': 999},
                {'task': 'TaskA', 'group': 'group_a', 'arg': 'Time', 'value': '2026-11-26T12:00:00Z'},
            ]}, server=server)

        # Nothing is broadcast: the store was not modified
        assert broadcast == []
        assert server.sent == [
            ResponseEvent(t='ConfigArg', o='set', k=('card_a', 'group_a', 'Value', 'value'), v=119),
            ResponseEvent(t='ConfigArg', o='set', k=('card_a', 'group_a', 'Time', 'value'),
                          v='2020-01-01T00:00:00Z'),
        ]
