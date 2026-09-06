"""
Tests for TaskRunningSource / TaskRunning (alasio/backend/topic/que.py):
apply / convert of the running-task events (task name or None), subscribe
full from the resident in-memory data, per-config singleton and the pure
event-stream semantics (no full-data read, no reinit).
"""

import pytest
import trio

from alasio.backend.reactive.source import BaseSource
from alasio.backend.topic.que import TaskRunningSource
from alasio.backend.worker.event import ConfigEvent
from tests.backend.reactive.test_source_base import MockTopic, decode


def running_event(value):
    """
    A TaskRunning ConfigEvent

    Args:
        value (str | None): task name, or None (no task running)

    Returns:
        ConfigEvent:
    """
    return ConfigEvent(t='TaskRunning', c='alas', v=value)


@pytest.fixture(autouse=True)
def cleanup_task_running():
    """Clear TaskRunningSource singletons after each test"""
    yield
    TaskRunningSource.singleton_clear()


class TestApply:
    def test_apply_sets_task(self):
        """an event sets the running task (data starts at None)"""
        source = TaskRunningSource('alas')
        assert source.data is None
        assert source._apply(running_event('Fight')) is True
        assert source.data == 'Fight'

    def test_apply_stop_sets_none(self):
        """a stop event (v=None) clears the running task"""
        source = TaskRunningSource('alas')
        source._apply(running_event('Fight'))
        assert source._apply(running_event(None)) is True
        assert source.data is None

    def test_apply_same_value_no_change(self):
        """an event with the current value does not modify"""
        source = TaskRunningSource('alas')
        assert source._apply(running_event('Fight')) is True
        assert source._apply(running_event('Fight')) is False
        assert source._apply(running_event(None)) is True
        assert source._apply(running_event(None)) is False


class TestConvert:
    def test_convert_is_root_set(self):
        """
        the incremental is a root set of the running task: data is a
        scalar (str | None), the client replaces its whole value.
        """
        source = TaskRunningSource('alas')
        source._apply(running_event('Fight'))
        event = source._convert(running_event('Fight'))
        assert event.t == 'TaskRunning'
        assert event.o == 'set'
        assert event.k == ()  # root set (no key)
        assert event.v == 'Fight'


class TestOnEvent:
    @pytest.mark.trio
    async def test_event_stream_broadcasts_set(self):
        """
        subscribers receive a root set on every task switch; before the
        first event the data is None: the subscriber registers without a
        full event (the client keeps undefined = no task running).
        """
        source = TaskRunningSource('alas')
        topic = MockTopic(topic_name='TaskRunning')
        assert await source.subscribe(topic) is None
        source.on_event(running_event('Fight'))
        await trio.testing.wait_all_tasks_blocked()
        event = decode(topic.sent[0])
        assert event.t == 'TaskRunning'
        assert event.o == 'set'
        assert event.v == 'Fight'

    @pytest.mark.trio
    async def test_stop_event_broadcasts_none(self):
        """a stop event broadcasts a root set of None"""
        source = TaskRunningSource('alas')
        topic = MockTopic(topic_name='TaskRunning')
        await source.subscribe(topic)
        source.on_event(running_event('Fight'))
        await trio.testing.wait_all_tasks_blocked()
        topic.sent.clear()
        source.on_event(running_event(None))
        await trio.testing.wait_all_tasks_blocked()
        event = decode(topic.sent[0])
        assert event.o == 'set'
        assert event.v is None

    @pytest.mark.trio
    async def test_same_task_switch_not_broadcast(self):
        """an unchanged running task is not broadcast"""
        source = TaskRunningSource('alas')
        topic = MockTopic(topic_name='TaskRunning')
        await source.subscribe(topic)
        source.on_event(running_event('Fight'))
        await trio.testing.wait_all_tasks_blocked()
        topic.sent.clear()
        source.on_event(running_event('Fight'))
        await trio.testing.wait_all_tasks_blocked()
        assert topic.sent == []

    @pytest.mark.trio
    async def test_no_subscriber_applies_only(self):
        """
        without subscribers events only update the data: the in-memory
        value is what a page refresh later reads as full (resident).
        """
        source = TaskRunningSource('alas')
        source.on_event(running_event('Fight'))
        assert source.data == 'Fight'

    @pytest.mark.trio
    async def test_subscribe_full_after_events(self):
        """
        a subscriber arriving after events receives the in-memory running
        task as the full snapshot (no full-data read: TaskRunningSource
        is a pure event-stream source).
        """
        source = TaskRunningSource('alas')
        source.on_event(running_event('Fight'))
        topic = MockTopic(topic_name='TaskRunning')
        event = decode(await source.subscribe(topic))
        assert event.o == 'full'
        assert event.v == 'Fight'


class TestInstance:
    def test_per_config_singleton(self):
        """instances are named singletons keyed by config name"""
        a = TaskRunningSource('config_a')
        b = TaskRunningSource('config_a')
        c = TaskRunningSource('config_b')
        assert a is b
        assert a is not c

    @pytest.mark.trio
    async def test_resident_not_idle_collected(self):
        """
        TaskRunningSource is resident (IDLE_TTL=None): the in-memory
        running task survives subscriber round trips (page refresh /
        navigation), so it is never idle-collected.
        """
        source = TaskRunningSource('alas')
        topic = MockTopic(topic_name='TaskRunning')
        await source.subscribe(topic)
        source.unsubscribe(topic)
        BaseSource.gc_idle()
        assert TaskRunningSource('alas') is source
