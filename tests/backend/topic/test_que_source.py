"""
Tests for TaskQueueSource (alasio/backend/topic/que.py): two-key merge
apply (pending / waiting) over the worker payload shape, keyed patch
increments, the framework fetch semantics (TTL / dirty, no running
trust), the time-driven refresh registration (subscribe / unsubscribe /
event / reinit -> TaskQueueUpdateManager) and the config-save linkage
(refresh request / dirty mark, no debounce).
"""

import time
from datetime import datetime, timedelta

import pytest
import trio

from alasio.backend.topic.que import TaskQueueSource, TaskQueueUpdateManager
from alasio.backend.worker.event import ConfigEvent
from alasio.config.entry.model import ConfigSetEvent, TaskItem
from alasio.logger import logger
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


def task_item(task_name, offset=0):
    """
    A TaskItem whose NextRun is ``offset`` seconds from now (aware)

    Args:
        task_name (str):
        offset (float):

    Returns:
        TaskItem:
    """
    return TaskItem(TaskName=task_name, NextRun=datetime.now().astimezone() + timedelta(seconds=offset))


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
    """
    Clear the source singleton and reset the manager state after each test

    The manager is reset IN PLACE (not singleton_clear): the sources hold
    the module-level handle TASK_QUEUE_UPDATE_MANAGER, a fresh instance
    would leave them talking to the abandoned one.
    """
    yield
    TaskQueueSource.singleton_clear()
    manager = TaskQueueUpdateManager()
    with manager._lock:
        manager._watched.clear()
        manager._requested.clear()
    manager._wake = None
    manager._trio_token = None
    manager._last_wake = None


class TestMergeApply:
    def test_merge_subset_updates_only_present_keys(self):
        """an event with a key subset merges: present keys replaced, others kept"""
        source = TaskQueueSource('alas')
        first = task_item('P1', -10)
        second = task_item('W1', 60)
        third = task_item('P2', -20)
        source.data = full_data(pending=[first], waiting=[second])
        assert source._apply(make_event(pending=[third])) is True
        assert source.data == full_data(pending=[third], waiting=[second])
        assert source._apply(make_event(waiting=[])) is True
        assert source.data == full_data(pending=[third], waiting=[])

    def test_merge_no_change_returns_false(self):
        """an event whose values equal the current data does not modify"""
        source = TaskQueueSource('alas')
        task = task_item('P1', -10)
        source.data = full_data(pending=[task])
        assert source._apply(make_event(pending=[task])) is False
        assert source._apply(make_event()) is False

    def test_dict_payload_decodes_to_the_same_items(self):
        """
        a worker push decodes into the very TaskItem shape a disk read
        produces, so an identical payload is not a change (no broadcast)
        """
        source = TaskQueueSource('alas')
        task = task_item('P1', -10)
        source.data = full_data(pending=[task])
        # the push shape on the wire: plain dicts (v: Any on decode)
        payload = {'TaskName': task.TaskName, 'NextRun': task.NextRun}
        assert source._apply(make_event(pending=[payload])) is False
        assert source.data == full_data(pending=[task])

    def test_merge_ignores_unknown_keys(self):
        """
        unknown keys in the payload are ignored. running is gone from the
        event protocol (it moved to TaskRunning): a stale event carrying
        it must not resurrect the key in data.
        """
        source = TaskQueueSource('alas')
        source.data = full_data()
        task = task_item('P', -10)
        event = ConfigEvent(
            t='TaskQueue', c='alas',
            v={
                'running': 'A',
                'unknown': 1,
                'pending': [{'TaskName': task.TaskName, 'NextRun': task.NextRun}],
            },
        )
        assert source._apply(event) is True
        assert source.data == full_data(pending=[task])
        assert 'running' not in source.data
        assert 'unknown' not in source.data

    def test_malformed_payload_key_is_dropped(self):
        """
        a payload that does not decode is dropped with a warning: data
        only ever holds TaskItem items, the previous value stays
        """
        source = TaskQueueSource('alas')
        task = task_item('P1', -10)
        source.data = full_data(pending=[task])
        before = dict(source.data)
        with logger.mock_capture_writer() as capture:
            assert source._apply(make_event(pending=['P'], waiting=[{'TaskName': 'W'}])) is False
        assert source.data == before
        assert capture.fd.any_contains('Malformed task table payload ignored')


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
        source.on_event(make_event(pending=[task_item('B', 30)]))
        await trio.testing.wait_all_tasks_blocked()
        event = decode(topic.sent[0])
        assert event.t == 'TaskQueue'
        assert event.o == 'set'
        assert event.k == ('pending',)
        assert [t['TaskName'] for t in event.v] == ['B']

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
        source.on_event(make_event(pending=[task_item('P', -10)], waiting=[task_item('W', 60)]))
        await trio.testing.wait_all_tasks_blocked()
        events = decode(topic.sent[0])
        if not isinstance(events, list):
            events = [events]
        assert [(e.o, e.k, [t['TaskName'] for t in e.v]) for e in events] == [
            ('set', ('pending',), ['P']),
            ('set', ('waiting',), ['W']),
        ]

    @pytest.mark.trio
    async def test_patch_values_frozen_by_reference(self):
        """
        Each keyed patch references the value bound into data at its own
        apply; a later replacement of the key does not change the queued
        patch (the data list object becomes an orphan).
        """
        source = TaskQueueSource('alas')
        topic = MockTopic(topic_name='TaskQueue')
        await source.subscribe(topic)
        # two changes queued before the trio thread drains them
        source.on_event(make_event(pending=[task_item('B1', -10)]))
        source.on_event(make_event(pending=[task_item('B2', -20)]))
        await trio.testing.wait_all_tasks_blocked()
        events = decode(topic.sent[0])
        if not isinstance(events, list):
            events = [events]
        assert [[t['TaskName'] for t in e.v] for e in events] == [['B1'], ['B2']]

    @pytest.mark.trio
    async def test_subscribe_snapshot(self):
        """the subscribe snapshot is the whole current data (two keys)"""
        source = TaskQueueSource('alas')
        source.data = full_data(pending=[task_item('P', -10)], waiting=[task_item('W', 60)])
        topic = MockTopic(topic_name='TaskQueue')
        event = decode(await source.subscribe(topic))
        assert event.o == 'full'
        assert event.v['pending'][0]['TaskName'] == 'P'
        assert event.v['waiting'][0]['TaskName'] == 'W'


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
        first = task_item('P1', -10)
        second = task_item('W1', 60)
        source.on_event(make_event(pending=[first], waiting=[second]))
        assert source.data == full_data(pending=[first], waiting=[second])
        calls = []
        rebuilt_pending = [task_item('P2', -20)]
        rebuilt_waiting = [task_item('W2', 120)]

        def fake_on_init():
            calls.append(1)
            return full_data(pending=rebuilt_pending, waiting=rebuilt_waiting)

        source.on_init = fake_on_init
        await source.reinit(force=True)
        assert calls == [1]
        assert source.data == full_data(pending=rebuilt_pending, waiting=rebuilt_waiting)


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
        task = task_item('task', -10)

        def fake_on_init():
            calls.append(1)
            return full_data(pending=[task])

        source.on_init = fake_on_init
        source._lastrun = 0.
        new = await source.fetch_init()
        assert calls == [1]
        assert new == full_data(pending=[task])

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


class TestTimeDrivenRefresh:
    """
    Source + manager, end to end: the reported bug was that a waiting task
    stayed waiting after its NextRun passed as long as no worker event
    arrived (no worker running, nothing else touched the config).
    """

    @pytest.mark.trio
    async def test_waiting_becomes_pending_without_any_trigger(self, monkeypatch):
        monkeypatch.setattr(TaskQueueUpdateManager, 'MIN_LEAD', 0.05)
        monkeypatch.setattr(TaskQueueUpdateManager, 'MAX_WAIT', 0.05)
        next_run = datetime.now().astimezone() + timedelta(seconds=0.15)

        def fake_on_init():
            # the real split rule in miniature: NextRun <= now is pending
            task = TaskItem(TaskName='A', NextRun=next_run)
            if task.NextRun > datetime.now().astimezone():
                return full_data(waiting=[task])
            return full_data(pending=[task])

        source = TaskQueueSource('alas')
        source.on_init = fake_on_init
        topic = MockTopic(topic_name='TaskQueue')
        manager = TaskQueueUpdateManager()
        async with trio.open_nursery() as nursery:
            nursery.start_soon(manager.run)
            # the real subscription flow: get_source() reinits, then subscribes
            await source.reinit()
            topic.deliver(await source.subscribe(topic))
            # no further trigger from here on: no worker event, no config
            # save, no new subscription -- the manager alone must move A
            with trio.fail_after(2):
                while len(topic.sent) < 2:
                    await trio.sleep(0.01)
            # nothing waits any more (the deadline is None): no extra refresh
            await trio.sleep(0.2)
            nursery.cancel_scope.cancel()
        assert len(topic.sent) == 2
        first = decode(topic.sent[0])
        second = decode(topic.sent[1])
        assert first.o == 'full'
        assert [t['TaskName'] for t in first.v['waiting']] == ['A']
        assert first.v['pending'] == []
        assert second.o == 'full'
        assert [t['TaskName'] for t in second.v['pending']] == ['A']
        assert second.v['waiting'] == []


class TestUpdateTimeRegistration:
    """
    TaskQueueSource -> manager: the next update time is derived state of
    the task table (the earliest waiting NextRun). It is republished on
    every data change and on every subscriber attach / detach; without a
    watcher nothing is registered (nothing displays the table, and the
    forced disk read is the expensive part).
    """

    @pytest.mark.trio
    async def test_subscribe_registers_the_deadline(self):
        """subscribing publishes the earliest waiting NextRun"""
        source = TaskQueueSource('alas')
        source.data = full_data(waiting=[task_item('A', 30)])
        await source.subscribe(MockTopic(topic_name='TaskQueue'))
        deadline = TaskQueueUpdateManager()._watched['alas']
        assert deadline == pytest.approx(time.time() + 30, abs=1)

    @pytest.mark.trio
    async def test_near_deadline_is_floored(self):
        """a deadline closer than MIN_LEAD accumulates instead of firing"""
        source = TaskQueueSource('alas')
        source.data = full_data(waiting=[task_item('A', 1)])
        await source.subscribe(MockTopic(topic_name='TaskQueue'))
        deadline = TaskQueueUpdateManager()._watched['alas']
        assert deadline == pytest.approx(time.time() + TaskQueueUpdateManager.MIN_LEAD, abs=1)

    @pytest.mark.trio
    async def test_nothing_waiting_registers_none(self):
        """a watched config with no waiting task is tracked without a time"""
        source = TaskQueueSource('alas')
        source.data = full_data(pending=[task_item('A', -10)])
        await source.subscribe(MockTopic(topic_name='TaskQueue'))
        assert TaskQueueUpdateManager()._watched['alas'] is None

    @pytest.mark.trio
    async def test_last_unsubscribe_clears_the_entry(self):
        """the entry lives exactly as long as a watcher does"""
        source = TaskQueueSource('alas')
        source.data = full_data(waiting=[task_item('A', 30)])
        first = MockTopic(topic_name='TaskQueue')
        second = MockTopic(topic_name='TaskQueue')
        await source.subscribe(first)
        await source.subscribe(second)
        source.unsubscribe(first)
        assert 'alas' in TaskQueueUpdateManager()._watched
        source.unsubscribe(second)
        assert 'alas' not in TaskQueueUpdateManager()._watched

    @pytest.mark.trio
    async def test_full_push_re_derives_the_deadline(self):
        """a worker push moves the next update time together with the table"""
        source = TaskQueueSource('alas')
        source.data = full_data(waiting=[task_item('A', 600)])
        await source.subscribe(MockTopic(topic_name='TaskQueue'))
        source.on_event(make_event(pending=[], waiting=[task_item('A', 20)]))
        deadline = TaskQueueUpdateManager()._watched['alas']
        assert deadline == pytest.approx(time.time() + 20, abs=1)

    @pytest.mark.trio
    async def test_reinit_re_registers(self):
        """a full read republishes the deadline of the new table"""
        source = TaskQueueSource('alas')
        source.data = full_data(waiting=[task_item('A', 600)])
        await source.subscribe(MockTopic(topic_name='TaskQueue'))
        calls = []

        def fake_on_init():
            calls.append(1)
            return full_data(waiting=[task_item('A', 15)])

        source.on_init = fake_on_init
        await source.reinit(force=True)
        assert calls == [1]
        deadline = TaskQueueUpdateManager()._watched['alas']
        assert deadline == pytest.approx(time.time() + 15, abs=1)

    def test_event_without_subscriber_registers_nothing(self):
        """no watcher: the manager must not wait for this config"""
        source = TaskQueueSource('alas')
        source.on_event(make_event(pending=[], waiting=[task_item('A', 20)]))
        assert TaskQueueUpdateManager()._watched == {}


class TestLinkage:
    """
    TaskQueueSource.on_config_event: a scheduler-settings hit asks the
    manager for an immediate refresh when subscribers are present, marks
    the data dirty otherwise; misses do nothing. A full worker push
    satisfies the request (the worker computed the very table the save
    asked for), so the backend read is skipped.
    """

    @pytest.mark.trio
    async def test_subscriber_requests_a_refresh(self):
        """the linkage no longer spawns a refresh of its own"""
        source = TaskQueueSource('alas')
        await source.subscribe(MockTopic(topic_name='TaskQueue'))
        source.on_config_event(scheduler_event())
        assert TaskQueueUpdateManager()._requested == {'alas'}

    @pytest.mark.trio
    async def test_no_subscriber_marks_dirty(self):
        """
        a hit without subscribers only marks the data dirty: nothing to
        broadcast, the refresh happens on the next fetch (subscribe /
        reinit bypass the TTL while dirty), and the manager keeps nothing.
        """
        source = TaskQueueSource('alas')
        source.on_config_event(scheduler_event())
        assert source._dirty == 1
        assert TaskQueueUpdateManager()._requested == set()
        assert TaskQueueUpdateManager()._watched == {}

    @pytest.mark.trio
    async def test_unrelated_event_no_action(self):
        """an unrelated config change never requests nor marks dirty"""
        source = TaskQueueSource('alas')
        await source.subscribe(MockTopic(topic_name='TaskQueue'))
        source.on_config_event(other_event())
        assert source._dirty == 0
        assert TaskQueueUpdateManager()._requested == set()

    @pytest.mark.trio
    async def test_repeated_hits_collapse_into_one_request(self):
        """no debounce: repeated hits collapse in the manager, none is lost"""
        source = TaskQueueSource('alas')
        await source.subscribe(MockTopic(topic_name='TaskQueue'))
        source.on_config_event(scheduler_event())
        source.on_config_event(next_run_event())
        assert TaskQueueUpdateManager()._requested == {'alas'}

    @pytest.mark.trio
    async def test_full_push_satisfies_the_request(self):
        """
        task_delay: the worker pushes the table it computed for the very
        same save, so the requested read is not needed
        """
        source = TaskQueueSource('alas')
        await source.subscribe(MockTopic(topic_name='TaskQueue'))
        source.on_config_event(next_run_event())
        assert TaskQueueUpdateManager()._requested == {'alas'}
        source.on_event(make_event(pending=[], waiting=[task_item('A', 20)]))
        assert TaskQueueUpdateManager()._requested == set()

    @pytest.mark.trio
    async def test_partial_push_keeps_the_deadline_and_the_request(self):
        """
        a push without the waiting key proves nothing: it neither moves the
        deadline nor satisfies the request
        """
        source = TaskQueueSource('alas')
        source.data = full_data(waiting=[task_item('A', 600)])
        await source.subscribe(MockTopic(topic_name='TaskQueue'))
        manager = TaskQueueUpdateManager()
        before = manager._watched['alas']
        manager.request_refresh('alas')
        source.on_event(make_event(pending=[task_item('A', -1)]))
        assert manager._watched['alas'] == before
        assert manager._requested == {'alas'}

    @pytest.mark.trio
    async def test_worker_push_does_not_read_the_disk(self):
        """the event path merges and republishes, it never re-reads"""
        source = TaskQueueSource('alas')
        await source.subscribe(MockTopic(topic_name='TaskQueue'))
        calls = []

        def fake_on_init():
            calls.append(1)
            return full_data()

        source.on_init = fake_on_init
        source.on_event(make_event(pending=[], waiting=[task_item('A', 20)]))
        await trio.testing.wait_all_tasks_blocked()
        assert calls == []

    @pytest.mark.trio
    async def test_request_is_executed_by_the_manager_loop(self, monkeypatch):
        """
        no worker push (task_call / a direct scheduler write): the manager
        executes the request and the table is read again
        """
        monkeypatch.setattr(TaskQueueUpdateManager, 'MIN_LEAD', 0.05)
        monkeypatch.setattr(TaskQueueUpdateManager, 'MAX_WAIT', 0.05)
        source = TaskQueueSource('alas')
        await source.subscribe(MockTopic(topic_name='TaskQueue'))
        calls = []
        task = task_item('A', 20)

        def fake_on_init():
            calls.append(1)
            return full_data(waiting=[task])

        source.on_init = fake_on_init
        manager = TaskQueueUpdateManager()
        async with trio.open_nursery() as nursery:
            nursery.start_soon(manager.run)
            source.on_config_event(next_run_event())
            with trio.fail_after(2):
                while not calls:
                    await trio.sleep(0.005)
            nursery.cancel_scope.cancel()
        assert calls == [1]
        assert source.data == full_data(waiting=[task])
        assert manager._requested == set()

    @pytest.mark.trio
    async def test_dirty_consumed_by_next_fetch(self):
        """
        the dirty mark of a no-subscriber save is consumed by the next
        fetch: reinit re-reads (bypassing the TTL) and broadcasts nothing
        (no subscribers); a second reinit inside the TTL skips the read.
        """
        source = TaskQueueSource('alas')
        calls = []
        task = task_item('P', -10)

        def fake_on_init():
            calls.append(1)
            return full_data(pending=[task])

        source.on_init = fake_on_init
        source.on_config_event(scheduler_event())  # no subscriber: dirty
        assert source._dirty == 1
        await source.reinit()
        assert source._dirty == 0
        assert calls == [1]
        assert source.data == full_data(pending=[task])
        # the mark was consumed: a fresh reinit skips the read
        await source.reinit()
        assert calls == [1]

    @pytest.mark.trio
    async def test_linkage_from_thread(self):
        """
        on_config_event works from a worker thread: the request lands in the
        manager without the calling thread ever blocking
        """
        source = TaskQueueSource('alas')
        await source.subscribe(MockTopic(topic_name='TaskQueue'))
        await trio.to_thread.run_sync(source.on_config_event, scheduler_event())
        assert TaskQueueUpdateManager()._requested == {'alas'}
