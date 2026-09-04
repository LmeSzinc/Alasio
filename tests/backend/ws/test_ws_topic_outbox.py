"""
Tests for the per-topic reliable send outbox of BaseTopic
(alasio/backend/ws/ws_topic.py): the topic-level backpressure buffer that
takes over when the connection's send buffer is full.

- fast path: deliver() send_nowait'ed straight into the send buffer;
- slow path: WouldBlock queues the payload into the outbox and spawns a
  single-flight drain task that sends with await send() -- it suspends on
  the channel's backpressure and refills automatically when the send
  buffer frees a slot (no new events required);
- ordering: while a drain task runs every payload is queued; one sender
  owns the FIFO, so backlogged messages are never overtaken;
- lifecycle: op_unsub / source rebinding drop the backlog and cancel the
  drain task (a canceled await send never emits its popped payload);
- the outbox is bounded (OUTBOX_MAXLEN): overflow drops the oldest with
  an error log.

Driven by a real trio memory channel standing in for the connection's
send buffer, through a real EventSource (the full source->topic->channel
integration path). Every test keeps the subscription and its outbox
activity inside ONE nursery: the topic captures that nursery on op_sub
and spawns its drain tasks into it.
"""

import msgspec
import pytest
import trio

from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.reactive.rx_trio import async_reactive, async_reactive_source
from alasio.backend.reactive.source import EventSource
from alasio.backend.ws.ws_topic import BaseTopic

DECODER = msgspec.json.Decoder(ResponseEvent)


def decode(payload):
    """
    Decode one recorded payload into a ResponseEvent

    Args:
        payload (bytes):

    Returns:
        ResponseEvent:
    """
    return DECODER.decode(payload)


class FakeSource(EventSource):
    """
    A minimal cache source: data = {'x': value}
    """
    TOPIC_NAME = 'fake_outbox_src'

    def __init__(self, value=0):
        super().__init__()
        self.data = {'x': value}

    def _apply(self, event):
        key, value = event
        if self.data.get(key) == value:
            return False
        self.data[key] = value
        return True

    def _convert(self, event):
        key, value = event
        return ResponseEvent(t=self.TOPIC_NAME, o='set', k=(key,), v=value)


class ChannelServer:
    """
    A real trio memory channel standing in for the connection's send
    buffer: send_nowait raises WouldBlock when full, send() blocks until
    a slot frees up (true backpressure, like ws_server.task_send draining
    the channel). Closing the channel wakes a suspended send with False,
    like ws_server sending into a closed buffer.
    """

    def __init__(self, capacity=2):
        self._send, self._recv = trio.open_memory_channel(capacity)

    def send_nowait(self, data):
        self._send.send_nowait(data)
        return True

    async def send(self, data):
        try:
            await self._send.send(data)
        except (trio.BrokenResourceError, trio.ClosedResourceError):
            return False
        return True

    def receive_nowait(self):
        """
        Receive one message; raises WouldBlock when the channel is empty
        """
        return self._recv.receive_nowait()

    async def close(self):
        """
        Close the channel (like task_job closing the send buffer when the
        connection tears down): wakes a suspended drain task with False
        """
        await self._send.aclose()

    def drain(self):
        """
        Receive all currently available messages in order

        Returns:
            list[bytes]:
        """
        out = []
        while True:
            try:
                out.append(self._recv.receive_nowait())
            except trio.WouldBlock:
                return out


class CacheSourceTopic(BaseTopic):
    """
    A source topic whose data comes from a cache source snapshot
    """
    TOPIC_NAME = 'outbox_cache_topic'

    def __init__(self, conn_id, server, source):
        super().__init__(conn_id, server)
        self.source = source

    async def get_source(self):
        return self.source


class ReactiveTopic(BaseTopic):
    """
    A topic whose source selection depends on a mutable raw value:
    switching configs re-runs _resubscribe through the reactive chain.
    """
    TOPIC_NAME = 'outbox_reactive_topic'

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


@pytest.fixture(autouse=True)
def cleanup():
    """Clear local topic singletons after each test"""
    yield
    for cls in (CacheSourceTopic, ReactiveTopic):
        cls.singleton_clear()


class TestDeliverFastPath:
    @pytest.mark.trio
    async def test_deliver_send_nowait_when_room(self):
        """
        With room in the send buffer deliver() sends immediately: no
        outbox backlog, no drain task.
        """
        server = ChannelServer(capacity=8)
        source = FakeSource(0)
        topic = CacheSourceTopic('conn-1', server, source)
        async with trio.open_nursery() as nursery:
            nursery.start_soon(topic.op_sub)
            await trio.testing.wait_all_tasks_blocked()
            assert topic._nursery is nursery

            source.on_event(('x', 1))
            await trio.testing.wait_all_tasks_blocked()

            assert not topic._outbox
            assert topic._sending is False
            events = [decode(p) for p in server.drain()]
            assert events[0].o == 'full'
            assert events[0].v == {'x': 0}
            assert events[1].o == 'set'
            assert events[1].v == 1


class TestOutboxBacklog:
    @pytest.mark.trio
    async def test_would_block_queues_and_task_suspends(self):
        """
        A full send buffer queues the payload and spawns the drain task,
        which suspends on await send (no polling). Payloads arriving
        while it is suspended queue behind it.
        """
        server = ChannelServer(capacity=1)
        source = FakeSource(0)
        topic = CacheSourceTopic('conn-1', server, source)
        async with trio.open_nursery() as nursery:
            nursery.start_soon(topic.op_sub)
            await trio.testing.wait_all_tasks_blocked()
            # the full event occupies the single slot: every increment blocks

            source.on_event(('x', 1))
            await trio.testing.wait_all_tasks_blocked()
            # the drain task popped the payload and suspended on the full
            # channel (the send buffer still holds the full event)
            assert topic._sending is True
            assert not topic._outbox

            # while the task is suspended, new payloads queue behind it
            source.on_event(('x', 2))
            await trio.testing.wait_all_tasks_blocked()
            assert len(topic._outbox) == 1

            # draining the channel lets the task send everything in order
            assert decode(server.receive_nowait()).o == 'full'
            await trio.testing.wait_all_tasks_blocked()
            assert decode(server.receive_nowait()).v == 1
            await trio.testing.wait_all_tasks_blocked()
            assert decode(server.receive_nowait()).v == 2
            await trio.testing.wait_all_tasks_blocked()
            assert not topic._outbox
            assert topic._sending is False

    @pytest.mark.trio
    async def test_backlog_flushes_without_new_events(self):
        """
        The drain task refills the send buffer as soon as a slot frees
        up: no new event is required (the core improvement over the old
        event-driven retry).
        """
        server = ChannelServer(capacity=1)
        source = FakeSource(0)
        topic = CacheSourceTopic('conn-1', server, source)
        async with trio.open_nursery() as nursery:
            nursery.start_soon(topic.op_sub)
            await trio.testing.wait_all_tasks_blocked()
            source.on_event(('x', 1))
            await trio.testing.wait_all_tasks_blocked()
            # the drain task holds the payload, suspended on the full channel
            assert topic._sending is True

            # drain the full event: the task sends the backlog by itself
            assert decode(server.receive_nowait()).o == 'full'
            await trio.testing.wait_all_tasks_blocked()
            assert decode(server.receive_nowait()).v == 1

            # task self-terminated after the backlog drained
            await trio.testing.wait_all_tasks_blocked()
            assert not topic._outbox
            assert topic._sending is False

    @pytest.mark.trio
    async def test_backlog_order_preserved(self):
        """
        While a drain task runs every new payload is queued behind the
        backlog: messages leave strictly in FIFO order (a newer payload
        never overtakes an older one).
        """
        server = ChannelServer(capacity=1)
        source = FakeSource(0)
        topic = CacheSourceTopic('conn-1', server, source)
        async with trio.open_nursery() as nursery:
            nursery.start_soon(topic.op_sub)
            await trio.testing.wait_all_tasks_blocked()

            for n in range(1, 4):
                source.on_event(('x', n))
                await trio.testing.wait_all_tasks_blocked()
            # the first increment is in the hands of the suspended task,
            # the other two are queued
            assert len(topic._outbox) == 2

            received = []
            for _ in range(4):  # full + 3 increments
                received.append(decode(server.receive_nowait()))
                await trio.testing.wait_all_tasks_blocked()

            assert received[0].o == 'full'
            assert [e.v for e in received[1:]] == [1, 2, 3]
            assert not topic._outbox

    @pytest.mark.trio
    async def test_single_flight_one_task_per_episode(self):
        """
        One drain task per backlog episode: messages arriving while the
        task is suspended never spawn a second task; the next episode
        (after a full drain) starts a fresh one.
        """
        server = ChannelServer(capacity=1)
        source = FakeSource(0)
        topic = CacheSourceTopic('conn-1', server, source)
        async with trio.open_nursery() as nursery:
            nursery.start_soon(topic.op_sub)
            await trio.testing.wait_all_tasks_blocked()

            spawns = []
            original = topic._outbox_send

            async def counting_send():
                spawns.append(1)
                await original()

            topic._outbox_send = counting_send

            # episode 1: three messages while blocked -> one task
            for n in range(1, 4):
                source.on_event(('x', n))
                await trio.testing.wait_all_tasks_blocked()
            assert len(spawns) == 1
            assert topic._sending is True

            # drain everything
            for _ in range(4):
                server.receive_nowait()
                await trio.testing.wait_all_tasks_blocked()
            assert not topic._outbox
            assert topic._sending is False
            assert len(spawns) == 1

            # episode 2: block the channel again -> a second, fresh task
            server.send_nowait(b'occupy')  # fill the single slot
            source.on_event(('x', 4))
            await trio.testing.wait_all_tasks_blocked()
            assert topic._sending is True
            assert len(spawns) == 2

            # drain episode 2 so the task ends before the nursery exits
            server.receive_nowait()  # the occupy payload
            await trio.testing.wait_all_tasks_blocked()
            server.receive_nowait()  # the increment
            await trio.testing.wait_all_tasks_blocked()
            assert topic._sending is False

    @pytest.mark.trio
    async def test_outbox_full_drops_oldest(self, monkeypatch):
        """
        A full outbox silently drops its oldest payload (deque maxlen);
        the newest payloads are retained in FIFO order. The deque maxlen
        is fixed at construction, so the limit is set on the class first.
        """
        monkeypatch.setattr(CacheSourceTopic, 'OUTBOX_MAXLEN', 2)
        server = ChannelServer(capacity=1)
        source = FakeSource(0)
        topic = CacheSourceTopic('conn-1', server, source)
        async with trio.open_nursery() as nursery:
            nursery.start_soon(topic.op_sub)
            await trio.testing.wait_all_tasks_blocked()

            source.on_event(('x', 1))
            await trio.testing.wait_all_tasks_blocked()
            source.on_event(('x', 2))
            await trio.testing.wait_all_tasks_blocked()
            source.on_event(('x', 3))
            await trio.testing.wait_all_tasks_blocked()
            # the first increment is in the hands of the suspended task
            assert len(topic._outbox) == 2
            source.on_event(('x', 4))
            await trio.testing.wait_all_tasks_blocked()
            # oldest queued (x=2) dropped, newest retained in FIFO order
            assert len(topic._outbox) == 2
            assert [decode(p).v for p in topic._outbox] == [3, 4]
            # let the suspended drain task out before the nursery exits
            await server.close()
            await trio.testing.wait_all_tasks_blocked()


class TestOutboxLifecycle:
    @pytest.mark.trio
    async def test_op_unsub_cancels_suspended_task(self):
        """
        op_unsub cancels the drain task: a payload popped but not yet
        sent is never emitted after the topic left its source.
        """
        server = ChannelServer(capacity=1)
        source = FakeSource(0)
        topic = CacheSourceTopic('conn-1', server, source)
        async with trio.open_nursery() as nursery:
            nursery.start_soon(topic.op_sub)
            await trio.testing.wait_all_tasks_blocked()
            source.on_event(('x', 1))
            await trio.testing.wait_all_tasks_blocked()
            # the task popped the payload and suspended on the full channel
            assert not topic._outbox
            assert topic._sending is True
            assert topic._send_scope is not None

            await topic.op_unsub()
            await trio.testing.wait_all_tasks_blocked()

            assert topic not in source._subscribers
            # only the full event ever reached the channel: the canceled
            # task never emitted its popped payload (the channel is never
            # drained again)
            assert decode(server.receive_nowait()).o == 'full'
            with pytest.raises(trio.WouldBlock):
                server.receive_nowait()

            # the task is suspended on the full channel and cannot be
            # canceled there (trio backpressure): closing the channel
            # wakes it and the cleanup runs
            await server.close()
            await trio.testing.wait_all_tasks_blocked()
            assert topic._sending is False
            assert topic._send_scope is None

    @pytest.mark.trio
    async def test_op_unsub_drops_queued_backlog(self):
        """
        op_unsub drops the queued backlog: no stale increment is sent
        after unsubscription.
        """
        server = ChannelServer(capacity=1)
        source = FakeSource(0)
        topic = CacheSourceTopic('conn-1', server, source)
        async with trio.open_nursery() as nursery:
            nursery.start_soon(topic.op_sub)
            await trio.testing.wait_all_tasks_blocked()
            for n in range(1, 4):
                source.on_event(('x', n))
                await trio.testing.wait_all_tasks_blocked()
            # the first increment is in the hands of the suspended task,
            # the other two are queued
            assert len(topic._outbox) == 2

            await topic.op_unsub()
            await trio.testing.wait_all_tasks_blocked()

            assert not topic._outbox
            assert decode(server.receive_nowait()).o == 'full'
            with pytest.raises(trio.WouldBlock):
                server.receive_nowait()
            # wake the suspended task (it holds the first increment)
            await server.close()
            await trio.testing.wait_all_tasks_blocked()
            assert topic._sending is False

    @pytest.mark.trio
    async def test_resubscribe_drops_old_source_backlog(self):
        """
        Rebinding to a new source drops the backlog of the old source:
        stale increments are never emitted after the full event of the
        new source.
        """
        server = ChannelServer(capacity=1)
        topic = ReactiveTopic('conn-1', server)
        async with trio.open_nursery() as nursery:
            nursery.start_soon(topic.op_sub)
            await trio.testing.wait_all_tasks_blocked()
            assert topic._src is topic.sources['A']

            # backlog of source A while its full event still occupies the
            # channel: the drain task holds the increment, suspended
            topic.sources['A'].on_event(('x', 9))
            await trio.testing.wait_all_tasks_blocked()
            assert topic._sending is True
            assert not topic._outbox

            # switch to B: the resubscribe round unsubscribes A, drops
            # the backlog and blocks sending full B (channel still full)
            topic._raw['config'] = 'B'
            nursery.start_soon(topic.raw.mutate)
            await trio.testing.wait_all_tasks_blocked()

            # free the channel: full A goes out first; the suspended
            # drain task holds the stale A increment -- if the channel
            # frees a slot for it, it goes out BEFORE full B (it can
            # never follow the full event)
            assert decode(server.receive_nowait()).v == {'x': 1}
            await trio.testing.wait_all_tasks_blocked()
            events = [decode(p) for p in server.drain()]
            await trio.testing.wait_all_tasks_blocked()

            # the last event is the full event of B: the client is
            # eventually consistent (full replaces the whole view), the
            # stale A increment never follows it
            assert events[-1].o == 'full'
            assert events[-1].v == {'x': 2}
            assert not topic._outbox
            assert topic._src is topic.sources['B']
            assert topic not in topic.sources['A']._subscribers
            assert topic in topic.sources['B']._subscribers

    @pytest.mark.trio
    async def test_connection_shutdown_cancels_task(self):
        """
        The drain task dies with the connection nursery: leaving the
        nursery while the task is suspended cleans it up without error.
        """
        server = ChannelServer(capacity=1)
        source = FakeSource(0)
        topic = CacheSourceTopic('conn-1', server, source)

        async with trio.open_nursery() as nursery:
            nursery.start_soon(topic.op_sub)
            await trio.testing.wait_all_tasks_blocked()
            source.on_event(('x', 1))
            await trio.testing.wait_all_tasks_blocked()
            # the task popped the payload and suspended on the full channel
            assert topic._sending is True
            assert not topic._outbox
            # like task_job closing the send buffer on teardown: this is
            # what actually wakes a task suspended on a full channel
            await server.close()

        # nursery exited: the suspended task was woken, cleaned up
        assert topic._sending is False
        assert topic._send_scope is None


class TestSubscriberIsolation:
    @pytest.mark.trio
    async def test_slow_subscriber_does_not_block_fast_one(self):
        """
        Per-connection outbox isolation: a slow connection queues its own
        backlog while a fast one keeps receiving everything in order.
        """
        source = FakeSource(0)
        slow_server = ChannelServer(capacity=1)
        fast_server = ChannelServer(capacity=8)
        slow_topic = CacheSourceTopic('conn-slow', slow_server, source)
        fast_topic = CacheSourceTopic('conn-fast', fast_server, source)

        async with trio.open_nursery() as nursery:
            nursery.start_soon(slow_topic.op_sub)
            nursery.start_soon(fast_topic.op_sub)
            await trio.testing.wait_all_tasks_blocked()
            # slow channel is full (its full event); fast channel has room
            for n in range(1, 4):
                source.on_event(('x', n))
                await trio.testing.wait_all_tasks_blocked()

            # the fast subscriber received everything, in order
            fast_events = [decode(p) for p in fast_server.drain()]
            assert fast_events[0].o == 'full'
            assert [e.v for e in fast_events[1:]] == [1, 2, 3]
            # the slow subscriber queues its own backlog, nothing sent yet
            assert slow_topic._sending is True
            assert len(slow_topic._outbox) == 2  # e1 is in the task's hands

            # draining the slow channel delivers full, then the backlog
            # refills automatically and in order
            received = []
            for _ in range(4):
                received.append(decode(slow_server.receive_nowait()))
                await trio.testing.wait_all_tasks_blocked()
            assert received[0].o == 'full'
            assert [e.v for e in received[1:]] == [1, 2, 3]
            await trio.testing.wait_all_tasks_blocked()
            assert not slow_topic._outbox
            assert slow_topic._sending is False
