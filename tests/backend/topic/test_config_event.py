"""
Tests for the unified ConfigArg event entry
(alasio/backend/topic/config_event.py): scheduler-hit detection, viewport
dispatch, the TaskQueue linkage (fire-and-forget, debounced) and exception
containment.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import trio

from alasio.backend.reactive.source import ViewportEventSource
from alasio.backend.topic import config_event
from alasio.backend.topic.config_event import _need_task_queue_reinit
from alasio.backend.topic.que import TaskQueueSource
from alasio.backend.ws.context import GLOBAL_CONTEXT
from alasio.config.entry.model import ConfigSetEvent
from alasio.logger import logger


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
    """Clear TaskQueueSource named singletons after each test"""
    yield
    TaskQueueSource.singleton_clear()


class TestNeedTaskQueueReinit:
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
        assert _need_task_queue_reinit(responses) is True

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
        assert _need_task_queue_reinit(responses) is False

    @pytest.mark.parametrize('responses', [
        # the historical check only inspects group / arg, not task
        {'task': 'General', 'group': 'Scheduler', 'arg': 'Enable', 'value': True},
        {'group': 'Scheduler', 'arg': 'Enable', 'value': True},
    ])
    def test_hit_ignores_task_field(self, responses):
        """hits are decided on (group, arg) only, replicating the old check"""
        assert _need_task_queue_reinit(responses) is True


class TestOnConfigEventDispatch:
    @pytest.mark.trio
    async def test_dispatch_called_once_with_payload(self):
        """on_config_event dispatches exactly once with (config_name, event)"""
        event = other_event()
        with patch.object(ViewportEventSource, 'dispatch', MagicMock()) as dispatch:
            config_event.on_config_event('alas', event)
        dispatch.assert_called_once_with('alas', event)


class TestTaskQueueLinkage:
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
    async def test_hit_runs_forced_reinit(self, trio_context):
        """a hit schedules exactly one forced reinit of the config source"""
        done = trio.Event()
        calls = []

        async def fake_reinit(self, force=False):
            calls.append(force)
            done.set()

        with patch.object(TaskQueueSource, 'reinit', fake_reinit):
            config_event.on_config_event('alas', [scheduler_event()])
            with trio.fail_after(2):
                await done.wait()
            # debounce clear happens after the reinit finished
            await trio.testing.wait_all_tasks_blocked()
        assert calls == [True]
        assert TaskQueueSource('alas')._reinit_pending is False

    @pytest.mark.trio
    async def test_miss_does_not_schedule(self, trio_context):
        """an unrelated event never schedules a reinit"""
        with patch.object(TaskQueueSource, 'reinit', AsyncMock()) as reinit:
            config_event.on_config_event('alas', [other_event()])
            await trio.testing.wait_all_tasks_blocked()
            await trio.sleep(0.01)
        reinit.assert_not_called()

    @pytest.mark.trio
    async def test_debounce_merges_consecutive_hits(self, trio_context):
        """
        Debounce: a hit while a forced reinit is pending / running merges
        into it -- the reinit runs once.
        """
        started = trio.Event()
        release = trio.Event()
        calls = []

        async def fake_reinit(self, force=False):
            calls.append(force)
            started.set()
            await release.wait()

        with patch.object(TaskQueueSource, 'reinit', fake_reinit):
            config_event.on_config_event('alas', scheduler_event())
            with trio.fail_after(2):
                await started.wait()
            # second hit while the first reinit is still running
            config_event.on_config_event('alas', next_run_event())
            await trio.sleep(0.05)
            assert len(calls) == 1
            release.set()
            await trio.testing.wait_all_tasks_blocked()
            await trio.sleep(0.05)
        # the merged hit never spawned a second execution
        assert len(calls) == 1
        assert TaskQueueSource('alas')._reinit_pending is False

    @pytest.mark.trio
    async def test_hit_after_completion_schedules_again(self, trio_context):
        """a hit arriving after the previous reinit finished runs again"""
        calls = []

        async def fake_reinit(self, force=False):
            calls.append(force)

        with patch.object(TaskQueueSource, 'reinit', fake_reinit):
            config_event.on_config_event('alas', scheduler_event())
            await trio.testing.wait_all_tasks_blocked()
            await trio.sleep(0.02)
            config_event.on_config_event('alas', scheduler_event())
            await trio.testing.wait_all_tasks_blocked()
            await trio.sleep(0.02)
        assert calls == [True, True]

    @pytest.mark.trio
    async def test_linkage_works_from_thread(self, trio_context):
        """
        The linkage can be triggered from a worker thread: the reinit runs
        on the Trio thread, the calling thread never blocks.
        """
        done = trio.Event()
        calls = []

        async def fake_reinit(self, force=False):
            calls.append(force)
            done.set()

        with patch.object(TaskQueueSource, 'reinit', fake_reinit):
            # call from a plain thread, like the worker recv thread
            await trio.to_thread.run_sync(
                config_event.on_config_event, 'alas', [scheduler_event()])
            with trio.fail_after(2):
                await done.wait()
        assert calls == [True]

    @pytest.mark.trio
    async def test_linkage_exception_contained(self, trio_context):
        """
        A reinit exception is logged and never bubbles to the nursery
        (it would crash the backend otherwise).
        """
        async def fake_reinit(self, force=False):
            raise RuntimeError('reinit boom')

        with patch.object(TaskQueueSource, 'reinit', fake_reinit):
            with logger.mock_capture_writer() as capture:
                config_event.on_config_event('alas', scheduler_event())
                await trio.sleep(0.05)
                assert capture.fd.any_contains('reinit boom')
        assert TaskQueueSource('alas')._reinit_pending is False
