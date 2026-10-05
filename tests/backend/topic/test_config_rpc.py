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

from alasio.backend.topic import _config_event
from alasio.backend.topic.config import ConfigArg
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
