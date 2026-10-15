"""
Tests for the daily bookkeeping and its schedule task
(alasio/backend/app/restart/daily.py)

The state reads the wall clock itself (no ``now`` arguments): the tests move
the clock with PatchTime. A naive datetime passed to PatchTime is read as UTC
while this module is local wall clock, so the instants of the tests are built
from a local timestamp (see ``at``): the mocked local time is then exact
whatever the timezone of the host that runs the tests.
"""
import builtins
import datetime
import time

import pytest
import trio
import trio.testing

from alasio.backend.app.restart import daily as daily_module
from alasio.backend.app.restart.daily import DAILY, DailyState, last_target, next_restart_delay, parse_restart_time
from alasio.backend.app.restart.restart_flow import FlowDaily
from alasio.testing.patch_time import PatchTime

# The daily target of the tests
TARGET = (3, 50)


def at(hour, minute=0, day=13, second=0):
    """
    The epoch second whose local wall clock is 2026-10-``day`` ``hour``:``minute``:``second``

    PatchTime treats a naive datetime as UTC, a local timestamp is what makes
    the mocked ``datetime.now()`` return exactly this local time.

    Args:
        hour (int): Local hour
        minute (int): Local minute
        day (int): Day of October 2026
        second (int): Local second

    Returns:
        float: The epoch second
    """
    return datetime.datetime(2026, 10, day, hour, minute, second).timestamp()


@pytest.fixture
def clock(monkeypatch):
    """
    The mocked wall clock of the tests, with the daily target bound to the config

    PatchTime patches datetime / time globally; trio's own clock is unaffected
    (it captured perf_counter at import), so the trio tests keep their sleeps
    and the loop tests can use ``autojump_clock``.
    """
    monkeypatch.setattr(daily_module, 'scheduled_restart_time', lambda: TARGET)
    with PatchTime(at(2, 0)) as clock:
        yield clock


class TestParseRestartTime:
    """The AutoRestartTime config value"""

    @pytest.mark.parametrize("value, expected", [
        (None, None),
        ('', None),
        ('   ', None),
        ('03:50', (3, 50)),
        ('3:50', (3, 50)),
        ('03', (3, 0)),
        ((3), (3, 0)),
        ('23:59', (23, 59)),
        ('00:00', (0, 0)),
        ('0:00', (0, 0)),
    ])
    def test_parse(self, value, expected):
        assert parse_restart_time(value) == expected

    @pytest.mark.parametrize("value", [
        '25:00',
        '03:60',
        '-1:00',
        'abc',
        '03:50:00',
        'weekday1-04:00',
        'monthday1-04:00',
        'weekday1-monthday2-04:00',
    ])
    def test_invalid(self, value):
        with pytest.raises(ValueError):
            parse_restart_time(value)


class TestLastTarget:
    """The instant a crossing walks over"""

    @pytest.mark.parametrize("now, expected", [
        # before the target: the occurrence of yesterday
        (datetime.datetime(2026, 10, 13, 0, 0), datetime.datetime(2026, 10, 12, 3, 50)),
        (datetime.datetime(2026, 10, 13, 3, 49), datetime.datetime(2026, 10, 12, 3, 50)),
        # at the target and after it: the occurrence of today
        (datetime.datetime(2026, 10, 13, 3, 50), datetime.datetime(2026, 10, 13, 3, 50)),
        (datetime.datetime(2026, 10, 13, 3, 50, 0, 1), datetime.datetime(2026, 10, 13, 3, 50)),
        (datetime.datetime(2026, 10, 13, 23, 59), datetime.datetime(2026, 10, 13, 3, 50)),
    ])
    def test_last_target(self, now, expected):
        assert last_target(now, TARGET) == expected

    def test_last_target_across_the_month(self):
        now = datetime.datetime(2026, 11, 1, 2, 0)
        assert last_target(now, TARGET) == datetime.datetime(2026, 10, 31, 3, 50)


class TestNextRestartDelay:
    """The wait to the next occurrence of the target (the sleep helper)"""

    @pytest.mark.parametrize("now, target, expected", [
        (datetime.datetime(2026, 10, 13, 3, 0), (3, 50), 3000.0),
        (datetime.datetime(2026, 10, 13, 3, 49), (3, 50), 60.0),
        # the occurrence exactly at now counts as passed: the sleep targets the
        # next crossing, whether the current one is served is the state's call
        (datetime.datetime(2026, 10, 13, 3, 50), (3, 50), 86400.0),
        (datetime.datetime(2026, 10, 13, 4, 0), (3, 50), 85800.0),
        (datetime.datetime(2026, 10, 13, 23, 59, 59), (0, 0), 1.0),
    ])
    def test_delay(self, now, target, expected):
        assert next_restart_delay(now, target) == expected


class TestTarget:
    """The target: read once from the deploy config, cached"""

    def test_disabled_target(self, monkeypatch):
        monkeypatch.setattr(daily_module, 'scheduled_restart_time', lambda: None)
        daily = DailyState()
        assert daily.target is None
        assert daily.is_due() is False
        assert daily.mark_done() is False

    def test_read_once(self, monkeypatch):
        calls = []

        def reader():
            calls.append(1)
            return TARGET

        monkeypatch.setattr(daily_module, 'scheduled_restart_time', reader)
        daily = DailyState()
        assert daily.target == TARGET
        assert daily.target == TARGET
        assert calls == [1]

    def test_reset_drops_the_cached_target(self, monkeypatch):
        monkeypatch.setattr(daily_module, 'scheduled_restart_time', lambda: TARGET)
        daily = DailyState()
        assert daily.target == TARGET
        # the next test patches the reader: the reset must not keep the target
        monkeypatch.setattr(daily_module, 'scheduled_restart_time', lambda: None)
        daily.reset()
        assert daily.target is None
        assert daily.is_due() is False
        assert daily.updated_all_at == 0.0
        assert daily.pending is None


class TestCrossing:
    """The trigger: the clock walked over the daily target"""

    def test_a_process_that_starts_after_the_target_owes_nothing(self, clock):
        """The crossings before the process started belong to the past"""
        clock.set(at(4, 0))
        daily = DailyState()
        assert daily.is_due() is False
        clock.set(at(23, 59))
        assert daily.is_due() is False
        # the crossing of the next day is this process's
        clock.set(at(3, 51, day=14))
        assert daily.is_due() is True
        assert daily.pending == datetime.datetime(2026, 10, 14, 3, 50)

    def test_the_clock_of_a_living_process_crosses_the_target(self, clock):
        clock.set(at(2, 0))
        daily = DailyState()
        assert daily.is_due() is False
        clock.set(at(3, 49))
        assert daily.is_due() is False
        clock.set(at(3, 50))
        assert daily.is_due() is True
        # the crossing is detected once: the following observations do not repeat it
        clock.set(at(4, 0))
        assert daily.is_due() is True
        clock.set(at(23, 0))
        assert daily.is_due() is True

    def test_mark_done_serves_the_crossing_once(self, clock):
        clock.set(at(3, 40))
        daily = DailyState()
        clock.set(at(4, 0))
        assert daily.mark_done() is True
        assert daily.is_due() is False
        assert daily.mark_done() is False
        clock.set(at(23, 0))
        assert daily.is_due() is False
        # the crossing of the next day is a new one
        clock.set(at(3, 51, day=14))
        assert daily.is_due() is True

    def test_a_jump_over_two_targets_is_one_crossing(self, clock):
        """A suspend / an NTP jump: the crossings are served by one restart"""
        clock.set(at(2, 0))
        daily = DailyState()
        clock.set(at(5, 0, day=15))
        assert daily.is_due() is True
        assert daily.mark_done() is True
        assert daily.is_due() is False

    def test_a_rollback_crosses_nothing(self, clock):
        """The clock walked backwards: no crossing, and the owed service is kept"""
        clock.set(at(2, 0))
        daily = DailyState()
        clock.set(at(4, 0))
        assert daily.is_due() is True
        clock.set(at(2, 0))
        assert daily.is_due() is False
        clock.set(at(3, 40))
        assert daily.is_due() is False
        # the natural flow crosses the target again
        clock.set(at(3, 50))
        assert daily.is_due() is True

    def test_the_daily_action_comes_back_after_a_rollback(self, clock):
        """
        Time flows forward again over the same target instant: a crossing again

        The dedup key of a service is the traversal, not the target instant: a
        served crossing does not suppress the one the second traversal owes
        (the wall clock was walked over the target twice, the backend restarts
        once per traversal).
        """
        clock.set(at(2, 0))
        daily = DailyState()
        clock.set(at(4, 0))
        assert daily.mark_done() is True
        assert daily.is_due() is False
        clock.set(at(2, 0))
        assert daily.is_due() is False
        clock.set(at(3, 50))
        assert daily.is_due() is True
        assert daily.mark_done() is True

    def test_a_rollback_across_midnight_does_not_trigger(self, clock):
        """The old date-keyed bug: the rollback itself was read as a crossing"""
        clock.set(at(4, 50))
        daily = DailyState()
        assert daily.is_due() is False
        clock.set(at(23, 0, day=12))
        assert daily.is_due() is False
        clock.set(at(3, 0))
        assert daily.is_due() is False
        # the traversal crosses the target of the 13th: a crossing, and only one
        clock.set(at(3, 50))
        assert daily.is_due() is True
        assert daily.mark_done() is True
        assert daily.is_due() is False


class TestFullUpdateCoverage:
    """updated_all_at and the coverage of the crossing"""

    def test_note_full_update_defaults_to_now(self, clock):
        daily = DailyState()
        # the completion time is an epoch: it travels to the next process in the
        # resume record, the local calendar of the module is no instant two
        # processes share
        before = time.time()
        daily.note_full_update()
        assert daily.updated_all_at == before

    @pytest.mark.parametrize("ts, covered", [
        (0.0, False),
        (at(3, 0), False),
        (at(3, 50), True),
        (at(4, 0), True),
    ])
    def test_covered_by(self, clock, ts, covered):
        clock.set(at(4, 0))
        daily = DailyState()
        assert daily.covered_by(ts) is covered

    def test_covered_by_without_a_target(self, monkeypatch):
        monkeypatch.setattr(daily_module, 'scheduled_restart_time', lambda: None)
        daily = DailyState()
        assert daily.covered_by(at(4, 0)) is False

    def test_mark_done_if_covered(self, clock):
        clock.set(at(3, 40))
        daily = DailyState()
        daily.note_full_update(at(4, 0))
        clock.set(at(4, 0))
        assert daily.mark_done_if_covered() is True
        assert daily.is_due() is False
        # nothing left to serve
        assert daily.mark_done_if_covered() is False

    def test_mark_done_if_covered_before_the_crossing(self, clock):
        clock.set(at(3, 40))
        daily = DailyState()
        daily.note_full_update(at(3, 0))
        clock.set(at(4, 0))
        assert daily.is_due() is True
        assert daily.mark_done_if_covered() is False
        assert daily.is_due() is True

    def test_module_singleton(self):
        assert isinstance(DAILY, DailyState)


class FakeManager:
    """The daily entry of the restart manager, recorded"""

    def __init__(self, claim=True):
        self.claim = claim
        self.calls = []

    def request_daily(self):
        self.calls.append('daily')
        if not self.claim:
            return None
        return FlowDaily(self)


class TestRun:
    """The lifespan task: wake, judge, claim"""

    @staticmethod
    def _fast(monkeypatch):
        """A task whose sleeps are tiny (the wake cap and the delay to the target)"""
        monkeypatch.setattr(daily_module, 'next_restart_delay', lambda now, target: 0.01)
        monkeypatch.setattr(daily_module, 'SCHEDULE_WAKE_CAP', 0.01)

    @staticmethod
    async def _wait_for(manager, count):
        """Let the task run until it claimed `count` times"""
        with trio.fail_after(2):
            while len(manager.calls) < count:
                await trio.sleep(0.01)

    @pytest.mark.trio
    async def test_disabled_without_a_target(self, monkeypatch):
        monkeypatch.setattr(daily_module, 'scheduled_restart_time', lambda: None)
        manager = FakeManager()
        await DailyState().run(manager=manager)
        assert manager.calls == []

    @pytest.mark.trio
    async def test_without_a_supervisor_it_returns_at_once(self, monkeypatch):
        monkeypatch.setattr(daily_module, 'scheduled_restart_time', lambda: TARGET)
        monkeypatch.delattr(builtins, '__mpipe_conn__', raising=False)
        manager = FakeManager()
        await DailyState().run(manager=manager)
        assert manager.calls == []

    @pytest.mark.trio
    async def test_claims_at_the_crossing(self, monkeypatch, autojump_clock):
        monkeypatch.setattr(daily_module, 'scheduled_restart_time', lambda: TARGET)
        self._fast(monkeypatch)
        manager = FakeManager()
        with PatchTime(at(2, 0)) as clock:
            daily = DailyState()
            async with trio.open_nursery() as nursery:
                nursery.start_soon(daily.run, manager)
                await trio.testing.wait_all_tasks_blocked()
                # before the target: the task only observes
                assert manager.calls == []
                clock.set(at(3, 50))
                await self._wait_for(manager, 1)
                assert manager.calls == ['daily']
                nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_a_jump_over_the_target_is_judged_on_the_next_wake(self, monkeypatch, autojump_clock):
        """A clock jump past the target is judged at the next wake-up of the loop"""
        monkeypatch.setattr(daily_module, 'scheduled_restart_time', lambda: TARGET)
        self._fast(monkeypatch)
        manager = FakeManager()
        with PatchTime(at(2, 0)) as clock:
            daily = DailyState()
            async with trio.open_nursery() as nursery:
                nursery.start_soon(daily.run, manager)
                await trio.testing.wait_all_tasks_blocked()
                assert manager.calls == []
                # 02:00 -> 04:00 in one step: the target of 03:50 was crossed
                clock.set(at(4, 0))
                await self._wait_for(manager, 1)
                assert manager.calls == ['daily']
                nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_a_rollback_claims_nothing_and_the_next_crossing_claims(self, monkeypatch, autojump_clock):
        monkeypatch.setattr(daily_module, 'scheduled_restart_time', lambda: TARGET)
        self._fast(monkeypatch)
        manager = FakeManager()
        with PatchTime(at(2, 0)) as clock:
            daily = DailyState()
            async with trio.open_nursery() as nursery:
                nursery.start_soon(daily.run, manager)
                await trio.testing.wait_all_tasks_blocked()
                clock.set(at(4, 0))
                await self._wait_for(manager, 1)
                assert manager.calls == ['daily']
                # the rollback: the clock crosses nothing, the task observes and waits
                clock.set(at(2, 0))
                for _ in range(10):
                    await trio.sleep(0.01)
                assert manager.calls == ['daily']
                # the natural flow crosses the target again
                clock.set(at(3, 50))
                await self._wait_for(manager, 2)
                assert manager.calls == ['daily', 'daily']
                nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_an_unserved_crossing_is_not_reclaimed_on_every_wake(self, monkeypatch, autojump_clock):
        """A claim is expensive (stop every worker, full update): RETRY_INTERVAL"""
        monkeypatch.setattr(daily_module, 'scheduled_restart_time', lambda: TARGET)
        self._fast(monkeypatch)
        manager = FakeManager(claim=False)
        with PatchTime(at(2, 0)) as clock:
            daily = DailyState()
            async with trio.open_nursery() as nursery:
                nursery.start_soon(daily.run, manager)
                await trio.testing.wait_all_tasks_blocked()
                clock.set(at(4, 0))
                await self._wait_for(manager, 1)
                # the crossing stays unserved (the slot is busy): the loop keeps
                # observing, the claim is throttled
                for _ in range(20):
                    await trio.sleep(0.01)
                assert manager.calls == ['daily']
                nursery.cancel_scope.cancel()
