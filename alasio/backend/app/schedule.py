"""
Daily scheduled backend restart

One config knob drives it: ``config/deploy.yaml`` -> ``Deploy.Update.AutoRestartTime``
("HH:MM" in the host local timezone, null to disable). The scheduler is a
lifespan background task: it wakes at the configured time and requests the
same graceful restart as the settings page (ConnState.restart) -- every
running worker is stopped gracefully and resumed by the new backend, the
frontend reconnects over the existing ws reconnect path.

The value is parsed with :func:`alasio.base.servertime.parse_server_update`
(the shared "HH:MM" parser); the accepted form is pinned by the msgspec
pattern of the config field (see AUTO_RESTART_TIME_PATTERN in
alasio/deploy/config/model.py), so a typo is caught when the config is read
instead of restarting the backend at a surprising time.

The delay to the next occurrence is recomputed from the wall clock on every
wake, so a system clock jump, a DST shift or a suspend/resume landing past
the target time restarts shortly after the machine comes back instead of
waiting a full day. The wake is bounded (SCHEDULE_WAKE_CAP): a long sleep is
never entrusted to a single timer.

The config is read once per backend process, so changing the time takes
effect with the next restart (same cached-to-restart semantics as the other
deploy settings). A restart that is already in flight at the target time
(e.g. a user clicked restart seconds before) skips that day's trigger: the
scheduled restart never interrupts a user command.
"""
import datetime

import trio

from alasio.backend.app.restart import RestartInProgress, RestartUnavailable, request_graceful_restart
from alasio.base.servertime import parse_server_update
from alasio.logger import logger

# Re-evaluation cap of the wait: the next wake is the sooner of the remaining
# delay and this cap, so a clock jump / DST shift / suspend is noticed within
# it instead of after a full day.
SCHEDULE_WAKE_CAP = 60.0


def parse_restart_time(value):
    """
    Parse the AutoRestartTime config value into (hour, minute)

    The time forms are the server_update forms of
    :func:`alasio.base.servertime.parse_server_update` ("HH:MM", "H:MM" or a
    bare hour, which means minute 0); the config field additionally pins the
    stored value to the strict HH:MM pattern.

    Args:
        value (str | None): Configured restart time; None or a blank value
            disables the scheduled restart

    Returns:
        tuple[int, int] | None: (hour, minute), or None when disabled

    Raises:
        ValueError: When the value is not a valid time, or carries a weekday /
            monthday restriction (the scheduled restart is daily, a
            weekday-restricted time is a different feature)
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        condition = parse_server_update(text)
    except ValueError as e:
        raise ValueError(f'Invalid restart time "{value}": {e}') from None
    if condition.weekday is not None or condition.monthday is not None:
        raise ValueError(f'Invalid restart time "{value}": only a daily HH:MM time is supported')
    return condition.hour, condition.minute or 0


def next_restart_delay(now, hour_minute):
    """
    Seconds from `now` to the next occurrence of `hour_minute` in local time

    Computed numerically from `now` (local datetime), so the local time of the
    target is the constant and DST-shifted days are handled by the arithmetic
    itself. The occurrence exactly at `now` counts as passed: a backend that
    restarts at the target and comes back inside the same minute must not
    trigger again.

    Args:
        now (datetime.datetime): Current local time
        hour_minute (tuple[int, int]): (hour, minute) of the daily target

    Returns:
        float: Seconds to the next occurrence, always within (0, 86400]
    """
    hour, minute = hour_minute
    # seconds since midnight on both sides: one unit, no mixed arithmetic
    now_seconds = (now.hour * 60 + now.minute) * 60 + now.second
    target_seconds = (hour * 60 + minute) * 60
    if target_seconds > now_seconds:
        # later today
        return target_seconds - now_seconds
    # passed (or exactly now, see above): the remainder of today plus the
    # whole of tomorrow up to the target
    return 86400 - now_seconds + target_seconds


def scheduled_restart_time():
    """
    Read the configured restart time

    Returns:
        tuple[int, int] | None: (hour, minute), or None when disabled or
            invalid (a bad value is reported once at startup and the
            scheduler stays off, a typo must not restart the backend daily
            at an arbitrary time)
    """
    # local import: the deploy config module is only safe to import after
    # set_project_root() ran (see asgi.create_config), and this module is
    # also imported by tests that never reach the backend startup
    from alasio.deploy.config.model import DeployConfig

    value = DeployConfig().config.data.Update.AutoRestartTime
    try:
        return parse_restart_time(value)
    except ValueError as e:
        logger.error(f'[Schedule] {e}, scheduled restart is disabled')
        return None


async def task_daily_restart():
    """
    Lifespan background task: restart the backend at the configured time every day

    One task per backend process: after a successful trigger the process
    shuts down and the task ends with it, the schedule starts over in the
    new process. A disabled / invalid config ends the task right away, the
    log stays silent otherwise (the value is visible in the deploy config).
    """
    hour_minute = scheduled_restart_time()
    if hour_minute is None:
        return
    reason = f'scheduled restart {hour_minute[0]:02d}:{hour_minute[1]:02d}'
    while True:
        # sleep in bounded steps and re-evaluate the wall clock on every
        # wake: a clock jump / DST shift / suspend crossing the target is
        # caught shortly after instead of waiting the full delay
        remaining = next_restart_delay(datetime.datetime.now(), hour_minute)
        while remaining > SCHEDULE_WAKE_CAP:
            await trio.sleep(SCHEDULE_WAKE_CAP)
            remaining = next_restart_delay(datetime.datetime.now(), hour_minute)
        await trio.sleep(max(remaining, 0.0))

        try:
            # the guard of request_graceful_restart is the single authority:
            # a user command (or the update flow) owning the backend at this
            # moment wins and this trigger is skipped for the day
            await request_graceful_restart(reason)
        except RestartInProgress:
            logger.warning('[Schedule] A restart is already in progress, skipping the scheduled restart')
        except RestartUnavailable as e:
            # an update transaction / the startup window owns the backend:
            # skip the day like any other conflict (latest command wins)
            logger.warning(f'[Schedule] The scheduled restart is refused, skipping it: {e}')
        except Exception as e:
            # a failing trigger (e.g. no supervisor) must never kill the
            # lifespan task: report and leave the next trigger to tomorrow
            logger.error(f'[Schedule] Failed to request the scheduled restart: {e}')
        # the request is accepted: the next iteration computes the delay to
        # tomorrow, which the process shutdown (or this sleep) crosses
