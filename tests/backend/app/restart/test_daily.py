"""
Tests for the daily bookkeeping (alasio/backend/app/restart/daily.py)
"""
import datetime

import pytest
import trio

from alasio.backend.app.restart import daily as daily_module
from alasio.backend.app.restart.daily import DAILY, DailyState, next_restart_delay, parse_restart_time, task_daily
from alasio.backend.app.restart.restart_flow import FlowDaily

# A fixed local moment: 2026-10-13 04:00
NOW = datetime.datetime(2026, 10, 13, 4, 0)
TARGET = (3, 50)


def at(hour, minute=0, day=13):
    """
    A local datetime of the same month, for the date-related branches
    """
    return datetime.datetime(2026, 10, day, hour, minute)


class TestTarget:
    """The target of the day: parsing-free state, the task reads the config"""

    def test_disabled_target(self):
        daily = DailyState()
        assert daily.target_datetime(NOW) is None
        assert daily.target_passed(NOW) is False
        assert daily.should_upgrade(NOW) is False

    @pytest.mark.parametrize("now, passed", [
        (at(3, 49), False),
        (at(3, 50), True),
        (at(4, 0), True),
        (at(23, 59), True),
    ])
    def test_target_passed(self, now, passed):
        daily = DailyState(target=TARGET)
        assert daily.target_passed(now) is passed
        assert daily.target_datetime(now) == now.replace(hour=3, minute=50, second=0, microsecond=0)

    def test_target_passed_ignores_the_microseconds(self):
        daily = DailyState(target=TARGET)
        assert daily.target_passed(at(3, 50).replace(microsecond=1)) is True


class TestDoneDay:
    """The completion mark of the day"""

    def test_mark_done_is_per_day(self):
        daily = DailyState(target=TARGET)
        assert daily.is_done(NOW) is False
        daily.mark_done(NOW)
        assert daily.is_done(NOW) is True
        assert daily.is_done(at(4, 0, day=14)) is False

    def test_mark_done_defaults_to_now(self, frozen_clock):
        daily = DailyState(target=TARGET)
        daily.mark_done()
        # the clock is frozen: the day of the mark is the same for both reads
        # (a real clock could roll over midnight between them)
        assert daily.done_day == datetime.datetime.now().date()

    def test_should_upgrade(self):
        daily = DailyState(target=TARGET)
        # before the target: nothing to upgrade
        assert daily.should_upgrade(at(3, 0)) is False
        # after the target and not done: upgrade the flow in flight
        assert daily.should_upgrade(at(4, 0)) is True
        # the daily action of the day is already served
        daily.mark_done(at(4, 0))
        assert daily.should_upgrade(at(4, 30)) is False
        # the next day starts over
        assert daily.should_upgrade(at(4, 0, day=14)) is True

    def test_reset(self):
        daily = DailyState(target=TARGET)
        daily.mark_done(NOW)
        daily.note_full_update(1.0)
        daily.reset()
        assert daily.done_day is None
        assert daily.updated_all_at == 0.0
        assert daily.target is None


class TestFullUpdateCoverage:
    """updated_all_at and the coverage of the daily action"""

    def test_note_full_update_defaults_to_now(self, frozen_clock):
        daily = DailyState(target=TARGET)
        before = datetime.datetime.now().timestamp()
        daily.note_full_update()
        # the clock is frozen: the recorded instant is exactly the read one
        assert daily.updated_all_at == before

    @pytest.mark.parametrize("ts, covered", [
        (0.0, False),
        (at(3, 0).timestamp(), False),
        (at(3, 50).timestamp(), True),
        (at(4, 0).timestamp(), True),
    ])
    def test_covered_by(self, ts, covered):
        daily = DailyState(target=TARGET)
        assert daily.covered_by(ts, now=NOW) is covered

    def test_covered_by_without_a_target(self):
        daily = DailyState()
        assert daily.covered_by(at(4, 0).timestamp(), now=NOW) is False

    def test_mark_done_if_covered(self):
        daily = DailyState(target=TARGET)
        daily.note_full_update(at(4, 0).timestamp())
        assert daily.mark_done_if_covered(now=NOW) is True
        assert daily.is_done(NOW) is True

    def test_mark_done_if_covered_before_the_target(self):
        daily = DailyState(target=TARGET)
        daily.note_full_update(at(3, 0).timestamp())
        assert daily.mark_done_if_covered(now=NOW) is False
        assert daily.is_done(NOW) is False

    def test_module_singleton(self):
        assert isinstance(DAILY, DailyState)


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


class TestNextRestartDelay:
    """The wait to the next occurrence of the target"""

    @pytest.mark.parametrize("now, target, expected", [
        (datetime.datetime(2026, 10, 13, 3, 0), (3, 50), 3000.0),
        (datetime.datetime(2026, 10, 13, 3, 49), (3, 50), 60.0),
        # the occurrence exactly at now counts as passed (a backend that
        # comes back inside the same minute must not trigger again)
        (datetime.datetime(2026, 10, 13, 3, 50), (3, 50), 86400.0),
        (datetime.datetime(2026, 10, 13, 4, 0), (3, 50), 85800.0),
        (datetime.datetime(2026, 10, 13, 23, 59, 59), (0, 0), 1.0),
    ])
    def test_delay(self, now, target, expected):
        assert next_restart_delay(now, target) == expected


class FakeManager:
    """The daily entry of the restart manager, recorded"""

    def __init__(self, state, claim=True):
        self.state = state
        self.claim = claim
        self.calls = []

    def request_daily(self, now=None):
        self.calls.append('daily')
        if not self.claim:
            return None
        # a claimed daily flow serves the day (its handover marks it)
        self.state.mark_done(now)
        return FlowDaily()


class TestTaskDaily:
    """The lifespan task: wake, evaluate, claim / skip"""

    @staticmethod
    def _fast(monkeypatch, target=(0, 0)):
        """A task that evaluates immediately, with a target of today"""
        monkeypatch.setattr(daily_module, 'scheduled_restart_time', lambda: target)
        monkeypatch.setattr(daily_module, 'next_restart_delay', lambda now, target: 0.01)

    @pytest.mark.trio
    async def test_disabled_without_a_target(self, monkeypatch):
        monkeypatch.setattr(daily_module, 'scheduled_restart_time', lambda: None)
        state = DailyState()
        manager = FakeManager(state)
        await task_daily(manager=manager, daily=state)
        assert manager.calls == []
        assert state.target is None

    @pytest.mark.trio
    async def test_claims_the_daily_flow_at_the_target(self, monkeypatch):
        self._fast(monkeypatch)
        state = DailyState()
        manager = FakeManager(state)
        with trio.move_on_after(1):
            await task_daily(manager=manager, daily=state)
        assert manager.calls == ['daily']
        assert state.is_done() is True

    @pytest.mark.trio
    async def test_skips_an_already_served_day(self, monkeypatch):
        self._fast(monkeypatch)
        state = DailyState()
        state.mark_done()
        manager = FakeManager(state)
        with trio.move_on_after(0.5):
            await task_daily(manager=manager, daily=state)
        assert manager.calls == []

    @pytest.mark.trio
    async def test_skips_before_the_target(self, monkeypatch):
        self._fast(monkeypatch)
        state = DailyState()
        monkeypatch.setattr(state, 'target_passed', lambda now=None: False)
        manager = FakeManager(state)
        with trio.move_on_after(0.5):
            await task_daily(manager=manager, daily=state)
        assert manager.calls == []

    @pytest.mark.trio
    async def test_retries_when_the_action_is_not_served(self, monkeypatch):
        # a flow in flight (or a startup) refuses the claim without serving the
        # day: the task keeps evaluating (the retry log is throttled)
        self._fast(monkeypatch)
        state = DailyState()
        manager = FakeManager(state, claim=False)
        with trio.move_on_after(0.5):
            await task_daily(manager=manager, daily=state)
        assert len(manager.calls) > 1
        assert state.is_done() is False
