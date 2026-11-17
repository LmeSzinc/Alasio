"""
Tests of the mod warmup gate of the source-model subscription scaffold
(alasio/backend/ws/ws_topic.py, BaseTopic.wait_mod_ready).

The gate waits for the models and the json of a mod before a topic builds a
view out of that mod. It is called by the topics that read a mod, with the mod
they resolved, so the dependency is explicit and a topic that does not read a
mod is neither delayed by the selected one nor resubscribed when it changes.
"""
import gc

import pytest

from alasio.backend.reactive.source import EventSource
from alasio.backend.topic.state import ConnState
from alasio.backend.ws.ws_topic import BaseTopic

CONN_ID = 'conn_mod_gate'


class PreloadLog:
    """Records the mod names the gate waited for, and never blocks."""

    def __init__(self):
        self.waits = []

    async def wait(self, mod_name):
        self.waits.append(mod_name)


class FakeServer:
    """Records the payloads the scaffold sends."""

    def __init__(self):
        self.sent = []

    def send_nowait(self, data):
        self.sent.append(data)

    async def send(self, data):
        self.sent.append(data)


class StaticSource(EventSource):
    """A cache source whose data has nothing to do with the mod."""

    TOPIC_NAME = 'mod_gate_static'

    def __init__(self):
        super().__init__()
        self.data = {'v': 1}


class StaticTopic(BaseTopic):
    """A topic bound to one fixed source, like ConfigScan / ModList / Update."""

    TOPIC_NAME = 'mod_gate_static'

    def __init__(self, conn_id, server):
        super().__init__(conn_id, server)
        self.source = StaticSource()
        # number of resubscribe rounds, counted at the get_source entry
        self.rounds = 0

    async def get_source(self):
        self.rounds += 1
        return self.source


class ModTopic(StaticTopic):
    """A topic that reads the mod and gates on it, like ConfigNav."""

    TOPIC_NAME = 'mod_gate_mod'

    async def get_source(self):
        self.rounds += 1
        state = ConnState(self.conn_id, self.server)
        mod_name = await state.mod_name
        if not mod_name:
            return None
        await self.wait_mod_ready(mod_name)
        return self.source


async def open_config(mod_name, conn_id=CONN_ID):
    """
    Point the connection at a config of the given mod, the way set_config does.

    Args:
        mod_name (str): Mod of the opened config
        conn_id (str): Connection of the caller

    Returns:
        ConnState: The state of the connection
    """
    state = ConnState(conn_id, None)
    nav = await state.nav_state
    nav.config_name = 'config_a'
    nav.mod_name = mod_name
    await state.nav_state.mutate()
    return state


@pytest.fixture(autouse=True)
def cleanup_topics():
    """Drop the per-connection topic instances after each test"""
    yield
    ConnState.singleton_clear()
    StaticTopic.singleton_clear()
    ModTopic.singleton_clear()
    # A subscribed topic and its source reference each other (the source
    # keeps the topic in its subscriber set), so the pair of a finished test
    # only dies with a gc pass. The observer registries of the reactive
    # descriptors are global and weakly keyed: a topic that is still alive
    # would be resubscribed by the next test's mod switch.
    gc.collect()


@pytest.fixture
def preload(monkeypatch):
    """A gate that records its waits instead of waiting for a warmup"""
    log = PreloadLog()
    monkeypatch.setattr('alasio.config.entry.preload.MOD_PRELOAD', log)
    return log


class TestWaitModReady:
    """The gate waits for the mod it is given, and for nothing else."""

    @pytest.mark.trio
    async def test_waits_for_the_given_mod(self, preload):
        """The name passed by the caller is the one waited for."""
        topic = StaticTopic(CONN_ID, FakeServer())

        await topic.wait_mod_ready('alas')

        assert preload.waits == ['alas']

    @pytest.mark.trio
    async def test_an_empty_name_waits_for_nothing(self, preload):
        """No config selected: the gate returns without a warmup to wait for."""
        topic = StaticTopic(CONN_ID, FakeServer())

        await topic.wait_mod_ready('')

        assert preload.waits == ['']

    @pytest.mark.trio
    async def test_the_gate_reads_no_connection_state(self, preload):
        """The gate is fed by its caller: it must not resolve anything itself,
        or the wait would become a dependency of every topic again."""
        topic = StaticTopic(CONN_ID, FakeServer())
        await open_config('alas')

        await topic.wait_mod_ready('example_mod')

        # the caller's name wins over the selected mod
        assert preload.waits == ['example_mod']


class TestGateAndResubscription:
    """Only the topics that read a mod follow the mod switch."""

    @pytest.mark.trio
    async def test_a_mod_independent_topic_is_not_resubscribed(self, preload):
        """The data of the topic does not change with the mod: no round."""
        server = FakeServer()
        static = StaticTopic(CONN_ID, server)
        await static._resubscribe
        assert static.rounds == 1

        await open_config('alas')
        await open_config('example_mod')

        assert static.rounds == 1
        assert static._src is static.source
        assert preload.waits == []

    @pytest.mark.trio
    async def test_a_mod_dependent_topic_rebinds_and_waits(self, preload):
        """The mod moved: the topic resolves its source again and gates."""
        server = FakeServer()
        modded = ModTopic(CONN_ID, server)
        await modded._resubscribe
        # no config selected yet: no source, and nothing to wait for
        assert modded._src is None
        assert preload.waits == []

        await open_config('alas')
        assert modded.rounds == 2
        assert modded._src is modded.source
        assert preload.waits == ['alas']

        await open_config('example_mod')
        assert modded.rounds == 3
        assert preload.waits == ['alas', 'example_mod']

    @pytest.mark.trio
    async def test_one_connection_feeds_both_topics(self, preload):
        """The same mod switch moves only the topic that reads the mod."""
        server = FakeServer()
        static = StaticTopic(CONN_ID, server)
        modded = ModTopic(CONN_ID, server)
        await static._resubscribe
        await modded._resubscribe

        await open_config('alas')

        assert static.rounds == 1
        assert modded.rounds == 2
        assert preload.waits == ['alas']
