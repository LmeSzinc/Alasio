"""
Tests for the source-model subscription scaffold of BaseTopic
(alasio/backend/ws/ws_topic.py): _resubscribe over get_source, snapshot /
viewport full semantics, dependency-driven resubscribe and the latest-wins
merging of concurrent triggers (busy/dirty catch-up loop).
"""

import msgspec
import pytest
import trio

from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.reactive.rx_trio import async_reactive, async_reactive_source
from alasio.backend.reactive.source import EventSource, ViewportEventSource
from alasio.backend.ws.ws_topic import BaseTopic

DECODER = msgspec.json.Decoder(ResponseEvent)


def decode(payload):
    """
    Decode a recorded payload: bytes are decoded, ResponseEvent objects
    are returned as is.

    Args:
        payload (bytes | ResponseEvent):

    Returns:
        ResponseEvent:
    """
    if isinstance(payload, bytes):
        return DECODER.decode(payload)
    return payload


class FakeSource(EventSource):
    """
    A minimal cache source: data = {'x': value}
    """
    TOPIC_NAME = 'fake_src'

    def __init__(self, value=0):
        super().__init__()
        self.data = {'x': value}

    def _apply(self, event):
        key, value = event
        if self.data.get(key) == value:
            return False
        self.data[key] = value
        return True

    def _make_response(self, event):
        key, value = event
        return ResponseEvent(t=self.TOPIC_NAME, o='set', k=(key,), v=value)


class FakeViewportSource(ViewportEventSource):
    """
    A viewport source with a fixed mapping and a fixed full view. The full
    view is built on subscribe (single-flight) and returned as the encoded
    payload, mirroring ConfigArgSource / DashboardSource.
    """
    # Bound to ViewportTopic below: the full-event topic name must match
    # the NAME of the topic it serves.
    TOPIC_NAME = 'viewport_topic'

    def __init__(self, config_name, view):
        super().__init__(config_name)
        self.view = view
        self.builds = 0  # number of full view builds (single-flight probe)
        self.dict_config_to_topic = {('t1', 'g1', 'a1'): ('card1', 'g1', 'a1')}

    async def _build_full(self):
        self.builds += 1
        return self.view

    def _convert(self, event):
        if type(event) is dict:
            task, group, arg, value = event['task'], event['group'], event['arg'], event['value']
        else:
            task, group, arg, value = event.task, event.group, event.arg, event.value
        key = self.dict_config_to_topic.get((task, group, arg))
        if key is None:
            return None
        return ResponseEvent(t=self.TOPIC_NAME, o='set', k=(*key, 'value'), v=value)


class Server:
    """
    Records send_nowait / send calls; the scaffold's ordering contract is
    that the full event is send_nowait'ed right after registration.
    """

    def __init__(self):
        self.sent = []

    def send_nowait(self, data):
        self.sent.append(data)

    async def send(self, data):
        self.sent.append(data)


class CacheSourceTopic(BaseTopic):
    """
    A source topic whose data comes from a cache source snapshot
    """
    TOPIC_NAME = 'cache_src_topic'

    def __init__(self, conn_id, server, source):
        super().__init__(conn_id, server)
        self.source = source

    async def get_source(self):
        if self.source is None:
            return None
        return self.source


class ViewportTopic(BaseTopic):
    """
    A source topic bound to a viewport source whose full view is built on
    subscribe (source.subscribe returns the encoded full payload).
    """
    TOPIC_NAME = 'viewport_topic'

    def __init__(self, conn_id, server, view=None):
        super().__init__(conn_id, server)
        if view is None:
            view = {'view': 1}
        self.source = FakeViewportSource('config_a', view)

    async def get_source(self):
        return self.source


class ReactiveTopic(BaseTopic):
    """
    A topic whose source selection depends on a mutable raw value:
    switching configs re-runs _resubscribe through the reactive chain.
    """
    TOPIC_NAME = 'reactive_src_topic'

    def __init__(self, conn_id, server):
        super().__init__(conn_id, server)
        self._raw = {'config': 'A'}
        self.sources = {'A': FakeSource(1), 'B': FakeSource(2)}

    @async_reactive_source
    async def raw(self):
        return self._raw

    @async_reactive
    async def selected(self):
        raw = await self.raw
        return raw['config']

    async def get_source(self):
        config = await self.selected
        return self.sources[config]


class GatedTopic(ReactiveTopic):
    """
    A topic whose first get_source round blocks on a gate, to interleave
    a second trigger into the running round deterministically.
    """
    TOPIC_NAME = 'gated_topic'

    def __init__(self, conn_id, server):
        super().__init__(conn_id, server)
        self.gate = trio.Event()
        self.calls = 0

    async def get_source(self):
        self.calls += 1
        if self.calls == 1:
            await self.gate.wait()
        config = await self.selected
        return self.sources[config]


@pytest.fixture(autouse=True)
def cleanup():
    """Clear local topic singletons and viewport registry after each test"""
    yield
    for cls in (CacheSourceTopic, ViewportTopic, ReactiveTopic, GatedTopic):
        cls.singleton_clear()
    ViewportEventSource._by_config.clear()


class TestResubscribe:
    @pytest.mark.trio
    async def test_cache_source_op_sub_sends_snapshot(self):
        """op_sub registers into the cache source and sends its snapshot full"""
        server = Server()
        source = FakeSource(42)
        topic = CacheSourceTopic('conn-1', server, source)
        await topic.op_sub()
        assert topic in source._subscribers
        assert topic._src is source
        assert len(server.sent) == 1
        event = decode(server.sent[0])
        assert event.t == 'fake_src'
        assert event.o == 'full'
        assert event.v == {'x': 42}

    @pytest.mark.trio
    async def test_cache_source_snapshot_precedes_increments(self):
        """
        Events applied before registration are included in the snapshot;
        increments after registration queue behind the snapshot send
        (ordering contract: no await between subscribe and send_nowait).
        """
        server = Server()
        source = FakeSource(0)
        topic = CacheSourceTopic('conn-1', server, source)
        # an event lands before registration: applied into data
        source.on_event(('x', 1))
        await topic.op_sub()
        assert len(server.sent) == 1
        assert decode(server.sent[0]).o == 'full'
        assert decode(server.sent[0]).v == {'x': 1}
        # an event lands after registration: delivered as an increment,
        # strictly after the snapshot
        source.on_event(('x', 2))
        await trio.testing.wait_all_tasks_blocked()
        events = [decode(p) for p in server.sent]
        assert events[0].o == 'full'
        assert events[1].o == 'set'
        assert events[1].v == 2

    @pytest.mark.trio
    async def test_viewport_topic_subscribe_builds_and_sends_full(self):
        """
        Viewport topics build the full view inside source.subscribe
        (single-flight) and send the returned encoded payload.
        """
        server = Server()
        topic = ViewportTopic('conn-1', server)
        await topic.op_sub()
        assert topic._src is topic.source
        assert topic.source.builds == 1
        assert len(server.sent) == 1
        event = decode(server.sent[0])
        assert event.t == 'viewport_topic'
        assert event.o == 'full'
        assert event.v == {'view': 1}

    @pytest.mark.trio
    async def test_viewport_topic_empty_view_no_full(self):
        """
        An empty view (falsy _build_full result): the topic is registered
        but no full is sent (empty-view semantics).
        """
        server = Server()
        topic = ViewportTopic('conn-1', server, view={})
        await topic.op_sub()
        assert topic._src is topic.source
        assert topic in topic.source._subscribers
        assert server.sent == []

    @pytest.mark.trio
    async def test_no_source_silent(self):
        """get_source returning None subscribes nothing and sends nothing"""
        server = Server()
        topic = CacheSourceTopic('conn-1', server, None)
        await topic.op_sub()
        assert topic._src is None
        assert server.sent == []

    @pytest.mark.trio
    async def test_dependency_change_resubscribes(self):
        """
        Mutating the reactive dependency re-runs _resubscribe: the old
        source is unsubscribed, the new source subscribed, a new full sent.
        """
        server = Server()
        topic = ReactiveTopic('conn-1', server)
        await topic.op_sub()
        assert topic._src is topic.sources['A']
        assert topic in topic.sources['A']._subscribers

        # switch config
        topic._raw['config'] = 'B'
        await topic.raw.mutate()
        await trio.testing.wait_all_tasks_blocked()

        assert topic._src is topic.sources['B']
        assert topic not in topic.sources['A']._subscribers
        assert topic in topic.sources['B']._subscribers
        # full of the new source
        assert decode(server.sent[-1]).v == {'x': 2}

    @pytest.mark.trio
    async def test_op_unsub_unsubscribes_source(self):
        """op_unsub unsubscribes the source and removes the singleton"""
        server = Server()
        topic = ReactiveTopic('conn-1', server)
        await topic.op_sub()
        assert topic in topic.sources['A']._subscribers
        await topic.op_unsub()
        assert topic not in topic.sources['A']._subscribers
        assert topic._src is None
        assert 'conn-1' not in ReactiveTopic.singleton_instances()

    @pytest.mark.trio
    async def test_viewport_increments_after_subscribe(self):
        """viewport increments reach the topic after op_sub"""
        server = Server()
        topic = ViewportTopic('conn-1', server)
        await topic.op_sub()
        topic.source.on_event({'task': 't1', 'group': 'g1', 'arg': 'a1', 'value': 9})
        await trio.testing.wait_all_tasks_blocked()
        assert decode(server.sent[-1]).o == 'set'
        assert decode(server.sent[-1]).v == 9


class TestLatestWinsMerging:
    @pytest.mark.trio
    async def test_trigger_during_round_merges_and_catches_up(self):
        """
        A trigger arriving while a round is blocked inside get_source does
        NOT start a second round: it only marks the running round dirty.
        The running round drops its stale result and catches up with one
        more round reading the newest state (latest wins).
        """
        server = Server()
        topic = GatedTopic('conn-1', server)

        async with trio.open_nursery() as nursery:
            nursery.start_soon(topic.op_sub)
            # let round 1 block at the gate
            await trio.testing.wait_all_tasks_blocked()
            assert topic.calls == 1
            # round 2 (observer-driven) arrives while round 1 is blocked:
            # merged into the running round, no second get_source yet
            nursery.start_soon(type(topic)._resubscribe.broadcast, topic)
            await trio.testing.wait_all_tasks_blocked()
            assert topic.calls == 1
            assert topic._src is None  # round 2 did not register
            # release round 1: it sees the dirty flag, drops its result and
            # catches up with a fresh round
            topic.gate.set()
            await trio.testing.wait_all_tasks_blocked()

        # exactly one registration and one full, from the catch-up round
        assert topic.calls == 2
        assert topic._src is topic.sources['A']
        assert topic in topic.sources['A']._subscribers
        assert len(server.sent) == 1

    @pytest.mark.trio
    async def test_merging_skips_intermediate_builds(self):
        """
        Rapid triggers while a viewport build is running are merged: only
        the running build and one catch-up build happen (the intermediate
        full view builds are skipped).
        """
        server = Server()
        topic = ViewportTopic('conn-1', server)
        source = topic.source
        # hold the single-flight build open so triggers pile up
        started = trio.Event()
        release = trio.Event()

        async def slow_build():
            started.set()
            await release.wait()
            return {'view': 1}

        source._build_full = slow_build
        async with trio.open_nursery() as nursery:
            nursery.start_soon(topic.op_sub)
            await started.wait()
            # three triggers while the first build is still running
            for _ in range(3):
                nursery.start_soon(type(topic)._resubscribe.broadcast, topic)
            await trio.testing.wait_all_tasks_blocked()
            release.set()
            await trio.testing.wait_all_tasks_blocked()

        # the running round dropped its stale full, the catch-up round
        # rebuilt and sent: two builds total, one full sent
        assert topic._src is source
        assert topic in source._subscribers
        assert len(server.sent) == 1
        assert decode(server.sent[0]).o == 'full'

    @pytest.mark.trio
    async def test_op_unsub_aborts_inflight_round(self):
        """
        op_unsub closes the topic: an in-flight round that resumes after
        the unsubscribe must abort and never re-register (no leak).
        """
        server = Server()
        topic = GatedTopic('conn-1', server)

        async with trio.open_nursery() as nursery:
            nursery.start_soon(topic.op_sub)
            await trio.testing.wait_all_tasks_blocked()
            assert topic.calls == 1  # round 1 blocked at the gate
            # unsubscribe while the round is in flight
            await topic.op_unsub()
            topic.gate.set()
            await trio.testing.wait_all_tasks_blocked()

        # the aborted round did not register
        assert topic._src is None
        assert server.sent == []
        assert topic.sources['A']._subscribers == set()

    @pytest.mark.trio
    async def test_op_unsub_during_build_unregisters(self):
        """
        op_unsub while the viewport build is in flight: the round undoes
        its registration at the closed checkpoint (no leak into the
        source).
        """
        server = Server()
        topic = ViewportTopic('conn-1', server)
        source = topic.source
        started = trio.Event()
        release = trio.Event()

        async def slow_build():
            started.set()
            await release.wait()
            return {'view': 1}

        source._build_full = slow_build
        async with trio.open_nursery() as nursery:
            nursery.start_soon(topic.op_sub)
            await started.wait()
            # unsubscribe while the build is running
            await topic.op_unsub()
            release.set()
            await trio.testing.wait_all_tasks_blocked()

        # the registration performed after the build was undone
        assert topic._src is None
        assert server.sent == []
        assert source._subscribers == set()
