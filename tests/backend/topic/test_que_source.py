"""
Tests for TaskQueueSource (alasio/backend/topic/que.py): two-key merge
apply (pending / waiting), keyed patch increments (referencing the event
payload), the framework fetch semantics (TTL / dirty, no running trust),
the config-event linkage (subscriber force refresh / dirty mark, no
debounce) and the scheduler-settings hit detection.
"""

from unittest.mock import AsyncMock, patch

import pytest
import trio

from alasio.backend.topic.que import TaskQueueSource
from alasio.backend.worker.event import ConfigEvent
from alasio.backend.ws.context import GLOBAL_CONTEXT
from alasio.config.entry.model import ConfigSetEvent
from tests.backend.reactive.test_source_base import MockTopic, decode


def make_event(**kwargs):
    """
    A TaskQueue ConfigEvent carrying the given data subset

    Args:
        **kwargs: any subset of pending / waiting

    Returns:
        ConfigEvent:
    """
    return ConfigEvent(t='TaskQueue', c='alas', v=kwargs)


def full_data(pending=None, waiting=None):
    """
    The canonical 2-key task queue data shape (what on_init produces)

    Args:
        pending (list | None):
        waiting (list | None):

    Returns:
        dict:
    """
    return {'pending': [] if pending is None else pending,
            'waiting': [] if waiting is None else waiting}


def scheduler_event(value=True):
    """
    A Scheduler.Enable ConfigSetEvent

    Args:
        value (bool):

    Returns:
        ConfigSetEvent:
    """
    return ConfigSetEvent(task='Scheduler', group='Scheduler', arg='Enable', value=value)


def next_run_event():
    """
    A Scheduler.NextRun ConfigSetEvent

    Returns:
        ConfigSetEvent:
    """
    return ConfigSetEvent(task='Scheduler', group='Scheduler', arg='NextRun', value='2026-09-03T00:00:00Z')


def other_event():
    """
    An unrelated ConfigSetEvent

    Returns:
        ConfigSetEvent:
    """
    return ConfigSetEvent(task='General', group='General', arg='Foo', value=1)


@pytest.fixture(autouse=True)
def cleanup_task_queue():
    """Clear TaskQueueSource singletons after each test"""
    yield
    TaskQueueSource.singleton_clear()


class TestMergeApply:
    def test_merge_subset_updates_only_present_keys(self):
        """an event with a key subset merges: present keys replaced, others kept"""
        source = TaskQueueSource('alas')
        source.data = full_data(pending=[1], waiting=[2])
        assert source._apply(make_event(pending=[3])) is True
        assert source.data == full_data(pending=[3], waiting=[2])
        assert source._apply(make_event(waiting=[])) is True
        assert source.data == full_data(pending=[3], waiting=[])

    def test_merge_no_change_returns_false(self):
        """an event whose values equal the current data does not modify"""
        source = TaskQueueSource('alas')
        source.data = full_data(pending=[1], waiting=[2])
        assert source._apply(make_event(pending=[1])) is False
        assert source._apply(make_event()) is False

    def test_merge_ignores_unknown_keys(self):
        """
        unknown keys in the payload are ignored. running is gone from the
        event protocol (it moved to TaskRunning): a stale event carrying
        it must not resurrect the key in data.
        """
        source = TaskQueueSource('alas')
        source.data = full_data()
        event = ConfigEvent(
            t='TaskQueue', c='alas',
            v={'running': 'A', 'unknown': 1, 'pending': ['P']},
        )
        assert source._apply(event) is True
        assert source.data == full_data(pending=['P'])
        assert 'running' not in source.data
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
        source.on_event(make_event(pending=['B']))
        await trio.testing.wait_all_tasks_blocked()
        event = decode(topic.sent[0])
        assert event.t == 'TaskQueue'
        assert event.o == 'set'
        assert event.k == ('pending',)
        assert event.v == ['B']

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
        """the subscribe snapshot is the whole current data (two keys)"""
        source = TaskQueueSource('alas')
        source.data = full_data(pending=['P'], waiting=['W'])
        topic = MockTopic(topic_name='TaskQueue')
        event = decode(await source.subscribe(topic))
        assert event.o == 'full'
        assert event.v == full_data(pending=['P'], waiting=['W'])


class TestReinitTaskTable:
    @pytest.mark.trio
    async def test_reinit_rebuilds_the_two_key_table(self):
        """
        reinit rebuilds only the task table (pending / waiting): running
        left the topic (it moved to TaskRunning), so on_init takes no
        argument and the rebuild never touches a running state.
        """
        source = TaskQueueSource('alas')
        # seed data through the event stream
        source.on_event(make_event(pending=['P1'], waiting=['W1']))
        assert source.data == full_data(pending=['P1'], waiting=['W1'])
        calls = []

        def fake_on_init():
            calls.append(1)
            return {'pending': ['P2'], 'waiting': ['W2']}

        source.on_init = fake_on_init
        await source.reinit(force=True)
        assert calls == [1]
        assert source.data == full_data(pending=['P2'], waiting=['W2'])


class TestFetchInit:
    @pytest.mark.trio
    async def test_stale_reads_task_table(self):
        """
        a stale cache reads the task table; on_init is called without
        arguments (fetch_init is the framework version: TTL + dirty, no
        running trust, no running preservation).
        """
        source = TaskQueueSource('alas')
        calls = []

        def fake_on_init():
            calls.append(1)
            return {'pending': ['task'], 'waiting': []}

        source.on_init = fake_on_init
        source._lastrun = 0.
        new = await source.fetch_init()
        assert calls == [1]
        assert new == full_data(pending=['task'])

    @pytest.mark.trio
    async def test_ttl_fresh_skips_read(self):
        """a fresh TTL skips the read"""
        source = TaskQueueSource('alas')
        calls = []

        def fake_on_init():
            calls.append(1)
            return full_data()

        source.on_init = fake_on_init
        # loaded data far in the future: fresh, the read is skipped
        source._lastrun = 999999999999.
        source._loaded = True
        assert await source.fetch_init() is None
        assert calls == []

    @pytest.mark.trio
    async def test_force_skips_freshness(self):
        """force=True always reads"""
        source = TaskQueueSource('alas')
        calls = []

        def fake_on_init():
            calls.append(1)
            return full_data()

        source.on_init = fake_on_init
        source._lastrun = 999999999999.
        new = await source.fetch_init(force=True)
        assert calls == [1]
        assert new == full_data()


class TestNeedReinit:
    """TaskQueueSource._need_reinit: scheduler-settings hit detection."""

    @pytest.mark.parametrize('responses', [
        scheduler_event(),
        [scheduler_event()],
        next_run_event(),
        [next_run_event(), other_event()],
        {'task': 'Scheduler', 'group': 'Scheduler', 'arg': 'Enable', 'value': True},
        [{'task': 'Scheduler', 'group': 'Scheduler', 'arg': 'NextRun', 'value': ''}],
        [other_event(), scheduler_event()],
    ])
    def test_hit(self, responses):
        """Scheduler.Enable / NextRun payloads hit (struct and dict, single and list)"""
        assert TaskQueueSource._need_reinit(responses) is True

    @pytest.mark.parametrize('responses', [
        other_event(),
        [other_event()],
        [],
        {'task': 'Scheduler', 'group': 'Scheduler', 'arg': 'Other', 'value': 1},
        {'task': 'Scheduler', 'group': 'Other', 'arg': 'Enable', 'value': True},
        {'task': 'Scheduler', 'group': 'Scheduler', 'arg': 'enable', 'value': True},
        {},
        None,
    ])
    def test_miss(self, responses):
        """everything else misses"""
        assert TaskQueueSource._need_reinit(responses) is False

    @pytest.mark.parametrize('responses', [
        # the historical check only inspects group / arg, not task
        {'task': 'General', 'group': 'Scheduler', 'arg': 'Enable', 'value': True},
        {'group': 'Scheduler', 'arg': 'Enable', 'value': True},
    ])
    def test_hit_ignores_task_field(self, responses):
        """hits are decided on (group, arg) only, replicating the old check"""
        assert TaskQueueSource._need_reinit(responses) is True


class TestOnConfigEventLinkage:
    """
    TaskQueueSource.on_config_event: scheduler-settings hits trigger a
    forced refresh (subscribers) or mark the data dirty (no subscriber);
    misses do nothing; consecutive hits are not debounced (duplicates are
    absorbed by the reinit machinery itself).
    """

    @pytest.fixture
    async def trio_context(self):
        """
        Provide a live nursery + trio token through GLOBAL_CONTEXT
        """
        async with trio.open_nursery() as nursery:
            with patch.object(GLOBAL_CONTEXT, 'global_nursery', nursery), \
                    patch.object(GLOBAL_CONTEXT, 'trio_token', trio.lowlevel.current_trio_token()):
                yield nursery

    @pytest.mark.trio
    async def test_subscriber_triggers_forced_reinit(self, trio_context):
        """a hit with subscribers queues exactly one forced reinit"""
        source = TaskQueueSource('alas')
        topic = MockTopic(topic_name='TaskQueue')
        await source.subscribe(topic)
        done = trio.Event()
        calls = []

        async def fake_reinit(self, force=False):
            calls.append(force)
            done.set()

        with patch.object(TaskQueueSource, 'reinit', fake_reinit):
            source.on_config_event(scheduler_event())
            with trio.fail_after(2):
                await done.wait()
            await trio.testing.wait_all_tasks_blocked()
        assert calls == [True]

    @pytest.mark.trio
    async def test_no_subscriber_marks_dirty(self, trio_context):
        """
        a hit without subscribers only marks the data dirty: nothing to
        broadcast, the refresh happens on the next fetch (subscribe /
        reinit bypass the TTL while dirty).
        """
        source = TaskQueueSource('alas')
        with patch.object(TaskQueueSource, 'reinit', AsyncMock()) as reinit:
            source.on_config_event(scheduler_event())
            await trio.testing.wait_all_tasks_blocked()
            await trio.sleep(0.01)
            reinit.assert_not_called()
        assert source._dirty == 1

    @pytest.mark.trio
    async def test_unrelated_event_no_action(self, trio_context):
        """an unrelated config change never refreshes nor marks dirty"""
        source = TaskQueueSource('alas')
        topic = MockTopic(topic_name='TaskQueue')
        await source.subscribe(topic)
        with patch.object(TaskQueueSource, 'reinit', AsyncMock()) as reinit:
            source.on_config_event(other_event())
            await trio.testing.wait_all_tasks_blocked()
            await trio.sleep(0.01)
            reinit.assert_not_called()
        assert source._dirty == 0

    @pytest.mark.trio
    async def test_consecutive_hits_both_trigger(self, trio_context):
        """
        no debounce: a hit arriving while a refresh is still running
        triggers a second refresh. Duplicates are absorbed downstream
        (_fetch_lock serialization + identical reads not broadcasting),
        never by dropping the hit.
        """
        source = TaskQueueSource('alas')
        topic = MockTopic(topic_name='TaskQueue')
        await source.subscribe(topic)
        started = trio.Event()
        release = trio.Event()
        calls = []

        async def fake_reinit(self, force=False):
            calls.append(force)
            started.set()
            await release.wait()

        with patch.object(TaskQueueSource, 'reinit', fake_reinit):
            source.on_config_event(scheduler_event())
            with trio.fail_after(2):
                await started.wait()
            # second hit while the first refresh is still running
            source.on_config_event(next_run_event())
            release.set()
            await trio.testing.wait_all_tasks_blocked()
            await trio.sleep(0.05)
        # both hits ran their refresh (no debounce window)
        assert calls == [True, True]

    @pytest.mark.trio
    async def test_dirty_consumed_by_next_fetch(self):
        """
        the dirty mark of a no-subscriber save is consumed by the next
        fetch: reinit re-reads (bypassing the TTL) and broadcasts nothing
        (no subscribers); a second reinit inside the TTL skips the read.
        """
        source = TaskQueueSource('alas')
        calls = []

        def fake_on_init():
            calls.append(1)
            return {'pending': ['p'], 'waiting': []}

        source.on_init = fake_on_init
        source.on_config_event(scheduler_event())  # no subscriber: dirty
        assert source._dirty == 1
        await source.reinit()
        assert source._dirty == 0
        assert calls == [1]
        assert source.data == full_data(pending=['p'])
        # the mark was consumed: a fresh reinit skips the read
        await source.reinit()
        assert calls == [1]

    @pytest.mark.trio
    async def test_linkage_from_thread(self, trio_context):
        """
        on_config_event works from a worker thread: the refresh runs on
        the Trio thread, the calling thread never blocks.
        """
        source = TaskQueueSource('alas')
        topic = MockTopic(topic_name='TaskQueue')
        await source.subscribe(topic)
        done = trio.Event()
        calls = []

        async def fake_reinit(self, force=False):
            calls.append(force)
            done.set()

        with patch.object(TaskQueueSource, 'reinit', fake_reinit):
            # call from a plain thread, like the worker recv thread
            await trio.to_thread.run_sync(source.on_config_event, scheduler_event())
            with trio.fail_after(2):
                await done.wait()
        assert calls == [True]
