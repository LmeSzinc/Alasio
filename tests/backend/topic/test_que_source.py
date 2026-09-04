"""
Tests for TaskQueueSource (alasio/backend/topic/que.py): key merge apply,
keyed patch increments (referencing the event payload), running
preservation and the running-trust / TTL fetch semantics.
"""

import pytest
import trio

from alasio.backend.topic.que import TaskQueueSource
from alasio.backend.worker.event import ConfigEvent
from tests.backend.reactive.test_source_base import MockTopic, decode


def make_event(**kwargs):
    """
    A TaskQueue ConfigEvent carrying the given data subset

    Args:
        **kwargs: any subset of running / pending / waiting

    Returns:
        ConfigEvent:
    """
    return ConfigEvent(t='TaskQueue', c='alas', v=kwargs)


def full_data(running=None, pending=None, waiting=None):
    """
    The canonical 3-key task queue data shape (what on_init produces)

    Args:
        running (str | None):
        pending (list | None):
        waiting (list | None):

    Returns:
        dict:
    """
    return {'running': running, 'pending': [] if pending is None else pending,
            'waiting': [] if waiting is None else waiting}


@pytest.fixture(autouse=True)
def cleanup_task_queue():
    """Clear TaskQueueSource singletons after each test"""
    yield
    TaskQueueSource.singleton_clear()


class TestMergeApply:
    def test_merge_subset_updates_only_present_keys(self):
        """an event with a key subset merges: present keys replaced, others kept"""
        source = TaskQueueSource('alas')
        source.data = full_data(running='A', pending=[1], waiting=[2])
        assert source._apply(make_event(running='B')) is True
        assert source.data == full_data(running='B', pending=[1], waiting=[2])
        assert source._apply(make_event(pending=[3])) is True
        assert source.data == full_data(running='B', pending=[3], waiting=[2])
        assert source._apply(make_event(waiting=[])) is True
        assert source.data == full_data(running='B', pending=[3], waiting=[])

    def test_merge_no_change_returns_false(self):
        """an event whose values equal the current data does not modify"""
        source = TaskQueueSource('alas')
        source.data = full_data(running='A', pending=[1], waiting=[2])
        assert source._apply(make_event(running='A')) is False
        assert source._apply(make_event()) is False

    def test_merge_ignores_unknown_keys(self):
        """unknown keys in the payload are ignored"""
        source = TaskQueueSource('alas')
        source.data = full_data()
        event = ConfigEvent(t='TaskQueue', c='alas', v={'running': 'A', 'unknown': 1})
        assert source._apply(event) is True
        assert 'unknown' not in source.data


class TestDelivery:
    @pytest.mark.trio
    async def test_increment_is_keyed_patch(self):
        """
        increments deliver keyed set patches of the changed keys: the
        client merges the patch into the task table instead of replacing
        the whole data at the root.
        """
        source = TaskQueueSource('alas')
        topic = MockTopic(topic_name='TaskQueue')
        await source.subscribe(topic)
        source.on_event(make_event(running='B'))
        await trio.testing.wait_all_tasks_blocked()
        event = decode(topic.sent[0])
        assert event.t == 'TaskQueue'
        assert event.o == 'set'
        assert event.k == ('running',)
        assert event.v == 'B'

    @pytest.mark.trio
    async def test_multi_key_event_forwards_one_patch_per_key(self):
        """
        a {pending, waiting} event forwards one keyed set per changed key
        (the responses are queued inside one critical section and arrive
        as one batch).
        """
        source = TaskQueueSource('alas')
        topic = MockTopic(topic_name='TaskQueue')
        await source.subscribe(topic)
        source.on_event(make_event(pending=['P'], waiting=['W']))
        await trio.testing.wait_all_tasks_blocked()
        events = decode(topic.sent[0])
        if not isinstance(events, list):
            events = [events]
        assert [(e.o, e.k, e.v) for e in events] == [
            ('set', ('pending',), ['P']),
            ('set', ('waiting',), ['W']),
        ]

    @pytest.mark.trio
    async def test_patch_values_frozen_by_reference(self):
        """
        Each keyed patch references the applied event payload; a later
        replacement of the key in data does not change the queued patch
        (the payload object becomes an orphan).
        """
        source = TaskQueueSource('alas')
        topic = MockTopic(topic_name='TaskQueue')
        await source.subscribe(topic)
        # two changes queued before the trio thread drains them
        source.on_event(make_event(pending=[1]))
        source.on_event(make_event(pending=[2]))
        await trio.testing.wait_all_tasks_blocked()
        events = decode(topic.sent[0])
        if not isinstance(events, list):
            events = [events]
        assert [e.v for e in events] == [[1], [2]]

    @pytest.mark.trio
    async def test_subscribe_snapshot(self):
        """the subscribe snapshot is the whole current data"""
        source = TaskQueueSource('alas')
        source.data = full_data(running='A')
        topic = MockTopic(topic_name='TaskQueue')
        event = decode(await source.subscribe(topic))
        assert event.o == 'full'
        assert event.v == full_data(running='A')


class TestRunningPreserve:
    @pytest.mark.trio
    async def test_reinit_preserves_running_from_data(self):
        """
        reinit preserves the running state: fetch reads it under the lock
        and the new data carries it (the task table rebuild never clears a
        running worker).
        """
        source = TaskQueueSource('alas')
        source.on_event(make_event(running='MyTask'))
        # the running trust only applies to live event producers: clear it so
        # the fetch path (which preserves the running value) is exercised
        source._running = False
        source._lastrun = 0.

        def fake_on_init(running):
            assert running == 'MyTask'
            return full_data(running=running)

        source.on_init = fake_on_init
        source._lastrun = 0.
        new = await source.fetch_init()
        assert new == full_data(running='MyTask')


class TestFetchInit:
    @pytest.mark.trio
    async def test_running_trust_skips_read(self):
        """
        running trust: once the worker events are alive (_running), fetch
        skips the task table read entirely.
        """
        source = TaskQueueSource('alas')
        calls = []

        def fake_on_init(running):
            calls.append(running)
            return full_data(running=running)

        source.on_init = fake_on_init
        # simulate a received worker event
        source.on_event(make_event(running='A'))
        source._lastrun = 0.
        assert await source.fetch_init() is None
        assert calls == []

    @pytest.mark.trio
    async def test_ttl_fresh_skips_read(self):
        """without running trust, a fresh TTL skips the read"""
        source = TaskQueueSource('alas')
        calls = []

        def fake_on_init(running):
            calls.append(running)
            return full_data(running=running)

        source.on_init = fake_on_init
        source._lastrun = 999999999999.  # far future: fresh
        assert await source.fetch_init() is None
        assert calls == []

    @pytest.mark.trio
    async def test_stale_reads_task_table(self):
        """a stale cache reads the task table"""
        source = TaskQueueSource('alas')
        calls = []

        def fake_on_init(running):
            calls.append(running)
            return full_data(running=running, pending=['task'])

        source.on_init = fake_on_init
        source._lastrun = 0.
        new = await source.fetch_init()
        assert calls == [None]
        assert new == full_data(pending=['task'])

    @pytest.mark.trio
    async def test_force_skips_freshness(self):
        """force=True always reads"""
        source = TaskQueueSource('alas')
        calls = []

        def fake_on_init(running):
            calls.append(running)
            return full_data(running=running)

        source.on_init = fake_on_init
        source._lastrun = 999999999999.
        new = await source.fetch_init(force=True)
        assert len(calls) == 1
        assert new['running'] is None


class TestReinitPending:
    @pytest.mark.trio
    async def test_reinit_mark_debounce(self):
        """_reinit_mark merges consecutive requests"""
        source = TaskQueueSource('alas')
        assert source._reinit_mark() is True
        # second request while pending: merged
        assert source._reinit_mark() is False
        source._reinit_clear()
        assert source._reinit_mark() is True
        source._reinit_clear()
