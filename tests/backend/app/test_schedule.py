"""
Tests for the daily scheduled restart (alasio/backend/app/schedule.py)

The wall clock is mocked with alasio.testing.patch_time.PatchTime: the loop
reads datetime.datetime.now, which PatchTime freezes and shifts. The wait is
mocked by replacing trio.sleep for the duration of the test: the fake sleeper
sleeps through PatchTime's mocked time.sleep (the fake clock advances by the
slept amount and pt.sleep_calls records every wake) and parks the loop once
the trigger was seen, so the test asserts on a settled loop instead of a
spinning one.
"""
import datetime
import time
from types import SimpleNamespace

import pytest
import trio
import trio.testing

from alasio.backend.app import schedule
from alasio.backend.app.restart import RestartInProgress
from alasio.backend.app.schedule import SCHEDULE_WAKE_CAP, next_restart_delay, parse_restart_time, task_daily_restart
from alasio.deploy.config import model as deploy_model
from alasio.logger import logger
from alasio.testing.patch_time import PatchTime

# A fixed offset: the delay math is timezone independent (both sides are
# local), a concrete offset only makes the expectations readable.
TZ_PLUS_8 = datetime.timezone(datetime.timedelta(hours=8))


def local(*parts):
    """
    Build an aware datetime in the test timezone

    Args:
        *parts: datetime constructor arguments (year, month, day, ...)

    Returns:
        datetime.datetime:
    """
    # tzinfo is a keyword argument: passing it positionally would land in
    # the second slot (minute) of the datetime constructor
    return datetime.datetime(*parts, tzinfo=TZ_PLUS_8)


async def accept_request(reason=''):
    """Request stand-in that accepts the trigger"""


async def refuse_request(reason=''):
    """Request stand-in of a refused trigger (a restart owns the backend)"""
    raise RestartInProgress()


async def fail_request(reason=''):
    """Request stand-in of a failing trigger (the backend has no supervisor)"""
    raise PermissionError('Cannot restart backend running without supervisor')


async def run_loop(monkeypatch, pt, hour_minute, request):
    """
    Run task_daily_restart on the PatchTime-mocked clock until the loop settles

    The injected sleeper sleeps through PatchTime's mocked time.sleep (the
    fake clock advances by the slept amount, pt.sleep_calls records the wake)
    and parks forever once the trigger was seen: the production loop never
    returns by itself, so the test observes a settled task and cancels it.
    request_graceful_restart is stubbed as well, the real request is covered
    by TestRequestGracefulRestart (tests/backend/app/test_restart_request.py).

    Args:
        monkeypatch: pytest monkeypatch fixture
        pt (PatchTime): The active PatchTime context (the mocked clock)
        hour_minute (tuple[int, int]): The configured restart time
        request (callable): Async stand-in of request_graceful_restart

    Returns:
        tuple[list[str], list[float]]: (requested reasons, sleep durations)
    """
    requested = []
    triggered = trio.Event()

    async def fake_sleep(seconds):
        # PatchTime patches time.sleep: the call advances the fake clock and
        # is recorded in pt.sleep_calls
        time.sleep(seconds)
        if triggered.is_set():
            # the trigger was seen: park the loop for the assertions
            await trio.sleep_forever()

    async def fake_request(reason='', nursery=None):
        requested.append(reason)
        triggered.set()
        await request(reason)

    monkeypatch.setattr(schedule, 'scheduled_restart_time', lambda: hour_minute)
    monkeypatch.setattr(schedule, 'request_graceful_restart', fake_request)
    # the loop sleeps through trio.sleep: replace the module attribute for
    # the duration of the test (monkeypatch restores it)
    monkeypatch.setattr(schedule.trio, 'sleep', fake_sleep)
    async with trio.open_nursery() as nursery:
        nursery.start_soon(task_daily_restart)
        await trio.testing.wait_all_tasks_blocked()
        nursery.cancel_scope.cancel()
    return requested, pt.sleep_calls


class TestParseRestartTime:
    """parse_restart_time: the AutoRestartTime config value"""

    @pytest.mark.parametrize("value", [None, '', '  '])
    def test_disabled_values(self, value):
        """None / blank means the scheduled restart is disabled"""
        assert parse_restart_time(value) is None

    @pytest.mark.parametrize("value, expected", [
        ('03:50', (3, 50)),
        ('3:50', (3, 50)),
        ('00:00', (0, 0)),
        ('23:59', (23, 59)),
        # parse_server_update forms: surrounding blanks and a bare hour
        (' 04:05 ', (4, 5)),
        ('18', (18, 0)),
    ])
    def test_valid_values(self, value, expected):
        """The server_update time forms are parsed into (hour, minute)"""
        assert parse_restart_time(value) == expected

    @pytest.mark.parametrize("value", [
        'abc',
        '24:00',
        '23:60',
        '25:00',
        '3:5:0',
    ])
    def test_invalid_values(self, value):
        """A value that is not a time is rejected explicitly"""
        with pytest.raises(ValueError, match='Invalid restart time'):
            parse_restart_time(value)

    @pytest.mark.parametrize("value", [
        # the server_update parser accepts restrictions, the daily restart must not
        'weekday1-04:00',
        'monthday1-04:00',
    ])
    def test_restricted_values_are_rejected(self, value):
        """A weekday / monthday restriction is not a daily time"""
        with pytest.raises(ValueError, match='only a daily HH:MM time'):
            parse_restart_time(value)


class TestNextRestartDelay:
    """next_restart_delay: seconds to the next occurrence of the daily target"""

    @pytest.mark.parametrize("start, target, expected", [
        # later today: 3h10m20s of wall time left
        (local(2026, 10, 2, 0, 39, 40), (3, 50), 11420.0),
        # earlier today: the remainder of today plus the target of tomorrow
        (local(2026, 10, 2, 12, 0, 0), (3, 50), 57000.0),
        # a second after the target: the rest of today after the seconds
        # carried, plus the target of tomorrow
        (local(2026, 10, 2, 3, 50, 1), (3, 50), 86399.0),
        (local(2026, 10, 2, 3, 50, 30), (3, 50), 86370.0),
        (local(2026, 10, 2, 3, 51, 0), (3, 50), 86340.0),
        # exactly at the target: the occurrence counts as passed
        (local(2026, 10, 2, 3, 50, 0), (3, 50), 86400.0),
        # midnight crosses the day boundary
        (local(2026, 10, 2, 23, 59, 30), (0, 0), 30.0),
        (local(2026, 10, 2, 0, 5, 0), (0, 10), 300.0),
        # month and year boundary
        (local(2026, 12, 31, 23, 30, 0), (0, 30), 3600.0),
    ])
    def test_seconds_to_target(self, start, target, expected):
        """The delay covers the day boundary, the carried seconds and the hours"""
        with PatchTime(start):
            now = datetime.datetime.now(TZ_PLUS_8)
            assert next_restart_delay(now, target) == expected


class FakeDeployConfig:
    """DeployConfig stand-in exposing only the AutoRestartTime read path"""

    def __init__(self, value):
        """
        Args:
            value: Value of Deploy.Update.AutoRestartTime
        """
        update = SimpleNamespace(AutoRestartTime=value)
        data = SimpleNamespace(Update=update)
        self.config = SimpleNamespace(data=data)


class TestScheduledRestartTime:
    """scheduled_restart_time: reading Deploy.Update.AutoRestartTime"""

    @pytest.mark.parametrize("value, expected", [
        ('03:50', (3, 50)),
        ('3:50', (3, 50)),
        (None, None),
        ('', None),
    ])
    def test_configured_value(self, monkeypatch, value, expected):
        """The config value is parsed into (hour, minute), null / blank disables"""
        monkeypatch.setattr(deploy_model, 'DeployConfig', lambda: FakeDeployConfig(value))
        assert schedule.scheduled_restart_time() == expected

    def test_invalid_value_disables_and_reports(self, monkeypatch):
        """A bad value (unreachable through the config pattern) disables the scheduler"""
        monkeypatch.setattr(deploy_model, 'DeployConfig', lambda: FakeDeployConfig('24:00'))
        with logger.mock_capture_writer() as capture:
            assert schedule.scheduled_restart_time() is None
        assert capture.fd.any_contains('Invalid restart time')
        assert capture.fd.any_contains('scheduled restart is disabled')


class TestTaskDailyRestart:
    """task_daily_restart: the lifespan loop driving the graceful restart"""

    @pytest.mark.trio
    async def test_disabled_config_ends_the_task(self, monkeypatch):
        """No configured time: the task returns instead of waiting forever"""
        monkeypatch.setattr(schedule, 'scheduled_restart_time', lambda: None)
        done = False
        with PatchTime(local(2026, 10, 2, 3, 0, 0)):
            with trio.move_on_after(1):
                await task_daily_restart()
                done = True
        # the task returned by itself (a still waiting one would be cancelled)
        assert done

    @pytest.mark.trio
    async def test_trigger_at_the_computed_time(self, monkeypatch):
        """The loop sleeps the remaining time, then requests the restart once"""
        with PatchTime(local(2026, 10, 2, 3, 49, 30, 0)) as pt:
            requested, sleeps = await run_loop(monkeypatch, pt, (3, 50), accept_request)
        # 30s were slept (03:49:30 -> 03:50:00), then the restart was
        # requested and the loop moved on to the next day
        assert requested == ['scheduled restart 03:50']
        assert sleeps[:2] == [30.0, SCHEDULE_WAKE_CAP]

    @pytest.mark.trio
    async def test_far_target_is_reached_through_bounded_wakes(self, monkeypatch):
        """A far target is reached through wakes bounded by the cap"""
        with PatchTime(local(2026, 10, 2, 3, 5, 0)) as pt:
            requested, sleeps = await run_loop(monkeypatch, pt, (3, 50), accept_request)
        # 45 minutes of fake time in wakes of the cap, then the same trigger
        assert sleeps[:45] == [SCHEDULE_WAKE_CAP] * 45
        assert requested == ['scheduled restart 03:50']

    @pytest.mark.trio
    async def test_skips_when_a_restart_is_already_in_progress(self, monkeypatch):
        """An in-flight restart wins: the trigger is skipped, not retried"""
        with PatchTime(local(2026, 10, 2, 3, 49, 30, 0)) as pt:
            requested, sleeps = await run_loop(monkeypatch, pt, (3, 50), refuse_request)
        # the refusal is not fatal and not retried: the loop waits for the
        # next day instead of hammering the running restart
        assert requested == ['scheduled restart 03:50']
        assert sleeps[:2] == [30.0, SCHEDULE_WAKE_CAP]

    @pytest.mark.trio
    async def test_a_failing_request_does_not_kill_the_loop(self, monkeypatch):
        """A trigger error (e.g. no supervisor) is reported and left to tomorrow"""
        with PatchTime(local(2026, 10, 2, 3, 49, 30, 0)) as pt:
            requested, sleeps = await run_loop(monkeypatch, pt, (3, 50), fail_request)
        # the loop survived the error and moved on to the next day
        assert requested == ['scheduled restart 03:50']
        assert sleeps[:2] == [30.0, SCHEDULE_WAKE_CAP]
