"""
Tests for the time-driven refresh of the TaskQueue topic
(alasio/backend/topic/que.py): the earliest-waiting-NextRun helper, the
deadline registry of TaskQueueUpdateManager, the refresh-request
lifecycle (executed by the loop, or satisfied by a full worker push) and
the manager loop itself (due refreshes, wake-ups, wall-clock jumps).
"""

import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import trio

from alasio.backend.topic.que import (
    TASK_QUEUE_UPDATE_MANAGER, TaskQueueSource, TaskQueueUpdateManager, next_waiting_time
)
from alasio.config.entry.model import TaskItem
from alasio.logger import logger


def item(task_name, offset):
    """
    A TaskItem whose NextRun is ``offset`` seconds from now (aware)

    Args:
        task_name (str):
        offset (float):

    Returns:
        TaskItem:
    """
    return TaskItem(TaskName=task_name, NextRun=datetime.now().astimezone() + timedelta(seconds=offset))


class JumpClock:
    """
    Stand-in for the time module of que.py: the real wall clock plus a
    flip-able offset, so the loop can be shown a jump while the test keeps
    running on real time.
    """

    def __init__(self):
        self.offset = 0.0

    def time(self):
        return time.time() + self.offset


@pytest.fixture(autouse=True)
def cleanup_singletons():
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


class TestNextWaitingTime:
    """next_waiting_time: earliest usable waiting NextRun, as an epoch."""

    def test_empty(self):
        """an empty list / None has no next update time"""
        assert next_waiting_time([]) is None
        assert next_waiting_time(None) is None

    def test_task_items(self):
        """TaskItem structs (disk read shape) give the earliest NextRun"""
        now = time.time()
        waiting = [item('A', 30), item('B', 10), item('C', 60)]
        assert next_waiting_time(waiting) == pytest.approx(now + 10, abs=1)

    def test_unsorted_takes_the_minimum(self):
        """
        waiting[0] is not trusted (only mod.get_task_schedule sorts the
        list): the earliest item wins
        """
        now = time.time()
        waiting = [item('A', 60), item('B', 5), item('C', 30)]
        assert next_waiting_time(waiting) == pytest.approx(now + 5, abs=1)

    @pytest.mark.parametrize('bad', [
        None,
        {},
        {'TaskName': 'A'},
        {'TaskName': 'A', 'NextRun': '2026-09-23T00:00:00+08:00'},
        'A',
        object(),
    ])
    def test_unusable_items_are_skipped(self, bad):
        """items without a usable NextRun are skipped, the rest still count"""
        now = time.time()
        assert next_waiting_time([bad, item('B', 20)]) == pytest.approx(now + 20, abs=1)
        assert next_waiting_time([bad]) is None


class TestRegisterAndUnregister:
    """register / unregister: the deadline registry and the due snapshot."""

    @pytest.fixture(autouse=True)
    def no_floor(self, monkeypatch):
        """
        MIN_LEAD has its own test: drop the floor here so the deadline
        arithmetic stays exact
        """
        monkeypatch.setattr(TaskQueueUpdateManager, 'MIN_LEAD', 0)

    def test_far_deadline_kept(self):
        manager = TaskQueueUpdateManager()
        deadline = time.time() + 600
        manager.register('alas', deadline)
        assert manager._watched['alas'] == pytest.approx(deadline, abs=1)

    def test_none_deadline_is_tracked(self):
        """a watched config with nothing waiting is tracked with None"""
        manager = TaskQueueUpdateManager()
        manager.register('alas', None)
        assert manager._watched['alas'] is None
        assert manager._due(time.time() + 1000) == []

    def test_unregister_clears_deadline_and_request(self):
        manager = TaskQueueUpdateManager()
        manager.register('alas', time.time() + 10)
        manager.request_refresh('alas')
        manager.unregister('alas')
        assert manager._watched == {}
        assert manager._requested == set()

    def test_due_snapshot(self):
        """only passed deadlines are due; None / future are not"""
        manager = TaskQueueUpdateManager()
        now = time.time()
        manager.register('past', now - 1)
        manager.register('future', now + 60)
        manager.register('never', None)
        assert manager._due(now) == ['past']
        assert sorted(manager._due(now + 600)) == ['future', 'past']

    def test_all_registered_snapshot(self):
        """a backward jump makes every tracked config due, None included"""
        manager = TaskQueueUpdateManager()
        now = time.time()
        manager.register('a', now + 600)
        manager.register('b', None)
        manager.request_refresh('c')
        assert sorted(manager._due(now, all_registered=True)) == ['a', 'b', 'c']


class TestMinLeadFloor:
    """The MIN_LEAD floor: adjacent NextRuns accumulate into one recompute."""

    def test_near_deadline_is_floored(self):
        manager = TaskQueueUpdateManager()
        now = time.time()
        manager.register('alas', now + 1)
        assert manager._watched['alas'] == pytest.approx(now + TaskQueueUpdateManager.MIN_LEAD, abs=1)

    def test_adjacent_waiting_tasks_share_one_deadline(self):
        """3 tasks 1s apart register the same floored instant"""
        manager = TaskQueueUpdateManager()
        now = time.time()
        manager.register('alas', now + 1)
        first = manager._watched['alas']
        manager.register('alas', now + 3)
        assert manager._watched['alas'] == first


class TestRefreshRequest:
    """request_refresh: immediate, unfloored, never dropped for being early."""

    def test_request_is_due_at_once(self):
        manager = TaskQueueUpdateManager()
        manager.request_refresh('alas')
        assert manager._due(time.time()) == ['alas']
        assert manager._requested == {'alas'}

    def test_request_is_not_floored(self):
        """
        a user / worker action is executed now, while a near deadline goes
        through the MIN_LEAD floor
        """
        manager = TaskQueueUpdateManager()
        now = time.time()
        manager.register('deadline', now + 1)
        manager.request_refresh('request')
        due = manager._due(now)
        assert due == ['request']

    def test_register_does_not_satisfy_the_request(self):
        """a derived registration (subscribe / reinit / partial push) keeps it"""
        manager = TaskQueueUpdateManager()
        manager.request_refresh('alas')
        manager.register('alas', time.time() + 600)
        assert manager._requested == {'alas'}

    def test_register_fresh_satisfies_the_request(self):
        """a full worker push is the recompute the request asked for"""
        manager = TaskQueueUpdateManager()
        manager.request_refresh('alas')
        manager.register('alas', time.time() + 600, fresh=True)
        assert manager._requested == set()

    def test_repeated_requests_collapse(self):
        manager = TaskQueueUpdateManager()
        manager.request_refresh('alas')
        manager.request_refresh('alas')
        assert manager._due(time.time()) == ['alas']


class TestManagerLoop:
    """
    TaskQueueUpdateManager.run: due refreshes, the immediate request path,
    wake-ups on new registrations, wall-clock jumps and failure tolerance.
    """

    @pytest.fixture(autouse=True)
    def fast_intervals(self, monkeypatch):
        """Millisecond intervals: never wait the real MIN_LEAD / MAX_WAIT"""
        monkeypatch.setattr(TaskQueueUpdateManager, 'MIN_LEAD', 0.05)
        monkeypatch.setattr(TaskQueueUpdateManager, 'MAX_WAIT', 0.05)

    @pytest.fixture
    def refreshes(self):
        """Spy on the manager refresh path (TaskQueueSource.reinit)"""
        spy = AsyncMock()
        with patch.object(TaskQueueSource, 'reinit', spy):
            yield spy

    @staticmethod
    async def wait_calls(spy, count):
        """
        Wait until the refresh spy was called ``count`` times

        Args:
            spy (AsyncMock):
            count (int):
        """
        with trio.fail_after(2):
            while spy.await_count < count:
                await trio.sleep(0.005)

    @pytest.mark.trio
    async def test_due_deadline_is_refreshed(self, refreshes):
        """a passed deadline is recomputed with the forced read"""
        manager = TaskQueueUpdateManager()
        async with trio.open_nursery() as nursery:
            nursery.start_soon(manager.run)
            manager.register('alas', time.time() - 1)
            await self.wait_calls(refreshes, 1)
            nursery.cancel_scope.cancel()
        assert refreshes.await_args.kwargs == {'force': True}

    @pytest.mark.trio
    async def test_request_is_executed(self, refreshes):
        """
        an immediate request is executed even when nothing waits, and it is
        consumed by the attempt
        """
        manager = TaskQueueUpdateManager()
        async with trio.open_nursery() as nursery:
            nursery.start_soon(manager.run)
            manager.register('alas', None)
            manager.request_refresh('alas')
            await self.wait_calls(refreshes, 1)
            assert manager._requested == set()
            nursery.cancel_scope.cancel()
        assert refreshes.await_count == 1

    @pytest.mark.trio
    async def test_request_is_not_floored_by_the_loop(self, refreshes, monkeypatch):
        """
        the loop honors a request even while MIN_LEAD is far larger than the
        test window (a request is not a time-derived deadline)
        """
        monkeypatch.setattr(TaskQueueUpdateManager, 'MIN_LEAD', 30)
        manager = TaskQueueUpdateManager()
        async with trio.open_nursery() as nursery:
            nursery.start_soon(manager.run)
            manager.request_refresh('alas')
            await self.wait_calls(refreshes, 1)
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_full_push_satisfies_the_request(self, refreshes):
        """
        a full worker push landing before the loop wakes cancels the read
        (the worker recomputed the table for the same save)
        """
        manager = TaskQueueUpdateManager()
        async with trio.open_nursery() as nursery:
            nursery.start_soon(manager.run)
            # no checkpoint between the two calls: the loop cannot run yet
            manager.request_refresh('alas')
            manager.register('alas', time.time() + 600, fresh=True)
            await trio.sleep(0.2)
            assert refreshes.await_count == 0
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_idle_when_nothing_is_tracked(self, refreshes):
        """an empty registry is a true idle: no refresh happens"""
        manager = TaskQueueUpdateManager()
        async with trio.open_nursery() as nursery:
            nursery.start_soon(manager.run)
            await trio.testing.wait_all_tasks_blocked()
            await trio.sleep(0.2)
            assert refreshes.await_count == 0
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_registration_wakes_the_sleeper(self, refreshes):
        """
        an earlier registration wakes the loop at once instead of at the end
        of its (deliberately huge) wait
        """
        manager = TaskQueueUpdateManager()
        async with trio.open_nursery() as nursery:
            nursery.start_soon(manager.run)
            manager.register('alas', time.time() + 60)
            await trio.testing.wait_all_tasks_blocked()
            manager.register('alas', time.time() + 0.01)
            await self.wait_calls(refreshes, 1)
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_unregistered_config_is_not_refreshed(self, refreshes):
        """a config cleared before the loop wakes is skipped"""
        manager = TaskQueueUpdateManager()
        async with trio.open_nursery() as nursery:
            nursery.start_soon(manager.run)
            manager.request_refresh('alas')
            manager.unregister('alas')
            await trio.sleep(0.2)
            assert refreshes.await_count == 0
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_backward_jump_refreshes_everything_tracked(self, refreshes, monkeypatch):
        """
        the wall clock moving backwards refreshes every tracked config --
        a config with no waiting task only ever refreshes this way
        """
        clock = JumpClock()
        monkeypatch.setattr('alasio.backend.topic.que.time', clock)
        manager = TaskQueueUpdateManager()
        async with trio.open_nursery() as nursery:
            nursery.start_soon(manager.run)
            manager.register('alas', None)
            await trio.testing.wait_all_tasks_blocked()
            assert refreshes.await_count == 0
            clock.offset = -600
            await self.wait_calls(refreshes, 1)
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_forward_jump_makes_deadlines_due(self, refreshes, monkeypatch):
        """the wall clock moving forward makes a far deadline due"""
        clock = JumpClock()
        monkeypatch.setattr('alasio.backend.topic.que.time', clock)
        monkeypatch.setattr(TaskQueueUpdateManager, 'MAX_WAIT', 0.05)
        manager = TaskQueueUpdateManager()
        async with trio.open_nursery() as nursery:
            nursery.start_soon(manager.run)
            manager.register('alas', time.time() + 600)
            await trio.testing.wait_all_tasks_blocked()
            assert refreshes.await_count == 0
            clock.offset = 3600
            await self.wait_calls(refreshes, 1)
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_failed_refresh_keeps_the_loop_alive(self, monkeypatch):
        """a broken config is logged and skipped, the loop keeps serving"""
        calls = []

        async def flaky(self, force=False):
            calls.append(self.config_name)
            if self.config_name == 'bad':
                raise RuntimeError('boom')

        monkeypatch.setattr(TaskQueueSource, 'reinit', flaky)
        manager = TaskQueueUpdateManager()
        with logger.mock_capture_writer() as capture:
            async with trio.open_nursery() as nursery:
                nursery.start_soon(manager.run)
                manager.request_refresh('bad')
                manager.request_refresh('good')
                with trio.fail_after(2):
                    while len(calls) < 2:
                        await trio.sleep(0.005)
                assert sorted(calls) == ['bad', 'good']
                # the loop survived: a later request is still served
                manager.request_refresh('good')
                with trio.fail_after(2):
                    while len(calls) < 3:
                        await trio.sleep(0.005)
                nursery.cancel_scope.cancel()
        assert calls.count('bad') == 1
        assert capture.fd.any_contains('Scheduled refresh failed: bad')

    @pytest.mark.trio
    async def test_cancellation_stops_the_loop(self, refreshes):
        """the nursery cancel reaches the loop: it exits and drops the token"""
        manager = TaskQueueUpdateManager()
        async with trio.open_nursery() as nursery:
            nursery.start_soon(manager.run)
            manager.request_refresh('alas')
            await self.wait_calls(refreshes, 1)
            nursery.cancel_scope.cancel()
        assert manager._trio_token is None
        assert manager._wake is None


def test_module_handle_is_the_singleton():
    """
    app.py starts TASK_QUEUE_UPDATE_MANAGER.run, the sources republish to
    the same instance
    """
    assert TASK_QUEUE_UPDATE_MANAGER is TaskQueueUpdateManager()
