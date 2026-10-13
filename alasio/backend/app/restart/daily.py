"""
Daily maintenance state (Deploy.Update.AutoRestartTime)

The daily action exists for two reasons: check every mod for updates and
restart the workers before a mod that leaks memory can grow for a whole day.
Every restart of the backend (a user restart, an update, a daily one) already
serves both -- the new process checks every mod at startup and the workers are
young again -- so the daily action is a *due* decision, not a scheduled
command:

- a process that starts after the target time has just checked every mod and
  its workers have no accumulated runtime: the day is done by the startup
  itself;
- a process that is alive at the target time and is idle then runs the daily
  flow (stop the workers, full update, handover);
- a flow (restart / update) that is in flight at the target time is upgraded
  into the daily maintenance before its handover instead of starting a second
  one;
- a process that was asleep and wakes up after the target time without
  restarting runs the daily flow at the next evaluation.

This module owns the bookkeeping of that decision: the target of the day, the
completion mark of the day and the completion time of the last full update of
this process. It is state only (no task, no IO): the daily task of
``alasio.backend.app.restart.daily`` drives it, the flows read it at their
handover.

Deliberately dependency free apart from the standard library: the manager, the
step layer and the daily task all import it.

The clock of this module is the **local wall clock** (``datetime.now()``): the
daily action restarts the backend in the local small hours, when the user is
least likely to be playing, so the target follows the user's local time --
including DST shifts and a manually changed clock, which only move when the
action happens (the task re-reads the clock on every wake). Durations that
compare one instant of this process with another one (e.g. the fresh window of
the update check) do not use this clock: they use ``time.monotonic()``, a jump
of the wall clock must not change them.
"""
import datetime
import time

import trio

from alasio.base.servertime import parse_server_update
from alasio.logger import logger


class DailyState:
    """
    Bookkeeping of the daily maintenance of one backend process

    Attributes:
        target (tuple[int, int] | None): (hour, minute) of the daily action,
            None when the config disables it. Read once per process (a changed
            config takes effect with the next restart)
        done_day (datetime.date | None): Day whose daily action is finished
        updated_all_at (float): Completion time (unix timestamp) of the last
            full update of THIS process, 0 when it ran none
    """

    def __init__(self, target=None):
        """
        Args:
            target (tuple[int, int]): (hour, minute) of the daily action, None
                (the default) when disabled
        """
        self.target = target
        self.done_day = None
        self.updated_all_at = 0.0

    def reset(self):
        """
        Return to the initial state (tests: a fresh process has fresh state)
        """
        self.__init__()

    # ------------------------------------------------------------------
    # Target of the day
    # ------------------------------------------------------------------

    def target_datetime(self, now: "datetime.datetime | None" = None) -> "datetime.datetime | None":
        """
        The target moment of the day `now` belongs to

        Args:
            now (datetime.datetime): Local time, defaults to the current time

        Returns:
            datetime.datetime | None: Today at the target hour:minute, or None
                when the daily action is disabled
        """
        if self.target is None:
            return None
        now = now if now is not None else datetime.datetime.now()
        hour, minute = self.target
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def target_passed(self, now: "datetime.datetime | None" = None) -> bool:
        """
        Whether the target moment of the day was reached

        Args:
            now (datetime.datetime): Local time, defaults to the current time

        Returns:
            bool: True when the daily action is configured and due
        """
        target = self.target_datetime(now)
        if target is None:
            return False
        now = now if now is not None else datetime.datetime.now()
        return now >= target

    def is_done(self, now: "datetime.datetime | None" = None) -> bool:
        """
        Whether the daily action of the day `now` belongs to is finished

        Args:
            now (datetime.datetime): Local time, defaults to the current time

        Returns:
            bool: True when the day was marked done (see mark_done)
        """
        now = now if now is not None else datetime.datetime.now()
        return self.done_day == now.date()

    def mark_done(self, now: "datetime.datetime | None" = None):
        """
        Mark the daily action of the day `now` belongs to as finished

        Args:
            now (datetime.datetime): Local time, defaults to the current time
        """
        now = now if now is not None else datetime.datetime.now()
        self.done_day = now.date()

    def should_upgrade(self, now: "datetime.datetime | None" = None) -> bool:
        """
        Whether a flow in flight must be upgraded into the daily maintenance

        A restart / update flow that reaches this check after the target moment
        of a day whose action is not finished yet covers the daily action with
        its own handover (stop the workers + full update + restart): the flow
        is upgraded instead of letting the daily task start a second
        maintenance right after it.

        Args:
            now (datetime.datetime): Local time, defaults to the current time

        Returns:
            bool: True when the flow must upgrade itself
        """
        if self.is_done(now):
            return False
        return self.target_passed(now)

    # ------------------------------------------------------------------
    # Full update bookkeeping
    # ------------------------------------------------------------------

    def note_full_update(self, ts: "float | None" = None):
        """
        Record that a full update finished (the update steps call this)

        Args:
            ts (float): Completion time (time.time()), defaults to now
        """
        self.updated_all_at = ts if ts is not None else datetime.datetime.now().timestamp()

    def covered_by(self, ts: float, now: "datetime.datetime | None" = None) -> bool:
        """
        Whether a full update completed at `ts` covers the daily action of the
        day `now` belongs to (it finished at or after the target moment)

        Args:
            ts (float): Completion time of the full update (unix timestamp),
                0 when there was none
            now (datetime.datetime): Local time, defaults to the current time

        Returns:
            bool: True when the update covers today's daily action
        """
        if not ts:
            return False
        target = self.target_datetime(now)
        if target is None:
            return False
        return datetime.datetime.fromtimestamp(ts) >= target

    def mark_done_if_covered(self, now: "datetime.datetime | None" = None) -> bool:
        """
        Mark the daily action done when the last full update of this process
        already covers it (the handover bookkeeping)

        Args:
            now (datetime.datetime): Local time, defaults to the current time

        Returns:
            bool: True when the day was marked done by this call
        """
        if not self.covered_by(self.updated_all_at, now):
            return False
        self.mark_done(now)
        return True


# =============================================================================
# The daily task
# =============================================================================

# Re-evaluation cap of the wait: the next wake is the sooner of the remaining
# delay and this cap, so a clock jump / DST shift / suspend crossing the target
# is noticed within it instead of after a full day
SCHEDULE_WAKE_CAP = 60.0
# Reminder interval of a daily action that could not be served yet (a handover
# that failed): the retry itself runs on every evaluation, only the log is
# throttled
RETRY_LOG_INTERVAL = 60.0 * 60.0


def parse_restart_time(value):
    """
    Parse the AutoRestartTime config value into (hour, minute)

    The time forms are the server_update forms of
    :func:`alasio.base.servertime.parse_server_update` ("HH:MM", "H:MM" or a
    bare hour, which means minute 0); the config field additionally pins the
    stored value to the strict HH:MM pattern.

    Args:
        value (str | None): Configured time; None or a blank value disables
            the daily action

    Returns:
        tuple[int, int] | None: (hour, minute), or None when disabled

    Raises:
        ValueError: When the value is not a valid time, or carries a weekday /
            monthday restriction (the daily action is daily, a
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
    Read the configured daily time (once per process)

    Returns:
        tuple[int, int] | None: (hour, minute), or None when disabled or
            invalid (a bad value is reported once at startup and the daily task
            stays off: a typo must not restart the backend daily at an
            arbitrary time)
    """
    # local import: the deploy config module is only safe to import after
    # set_project_root() ran (see asgi.create_config)
    from alasio.deploy.config.model import DeployConfig

    value = DeployConfig().config.data.Update.AutoRestartTime
    try:
        return parse_restart_time(value)
    except ValueError as e:
        logger.error(f'[Schedule] {e}, the daily maintenance is disabled')
        return None


async def task_daily(manager=None, daily=None):
    """
    Lifespan background task: the daily maintenance evaluation

    One task per backend process. The target time is read once (the deploy
    config; changing it takes effect with the next restart); with the daily
    action disabled the task ends right away. It wakes in bounded steps and
    re-reads the wall clock on every wake, so a clock jump / DST shift / a
    suspend crossing the target is noticed shortly after instead of a day
    later.

    One evaluation: the day is served -> nothing; before the target -> nothing;
    otherwise the manager decides -- an idle slot claims the daily flow (it
    stops the workers, runs the full update and hands the process over), a
    restart / update flow in flight covers the action itself (it upgrades into
    the daily maintenance before its handover), a backend that is starting up
    has just checked every mod and has no accumulated runtime (it marks the day
    served). The task never interrupts a flow: it only re-evaluates.

    A daily action whose flow could not hand the process over is retried on the
    next evaluation (the day was not marked), the retry log is throttled.

    Args:
        manager (RestartManager): The restart manager, defaults to the process
            singleton
        daily (DailyState): The bookkeeping, defaults to the process singleton
    """
    if manager is None:
        # local import: the manager imports this module (its bookkeeping), a
        # module level import would be circular
        from alasio.backend.app.restart.restart_manager import RESTART_MANAGER
        manager = RESTART_MANAGER
    daily = daily if daily is not None else DAILY
    target = scheduled_restart_time()
    daily.target = target
    if target is None:
        return
    logger.info(f'[Schedule] Daily maintenance scheduled at {target[0]:02d}:{target[1]:02d}')
    last_retry_log = 0.0
    while True:
        # sleep in bounded steps and re-evaluate the wall clock on every wake
        remaining = next_restart_delay(datetime.datetime.now(), target)
        while remaining > SCHEDULE_WAKE_CAP:
            await trio.sleep(SCHEDULE_WAKE_CAP)
            remaining = next_restart_delay(datetime.datetime.now(), target)
        await trio.sleep(max(remaining, 0.0))

        if daily.is_done() or not daily.target_passed():
            continue
        try:
            flow = manager.request_daily()
        except Exception as e:
            # a failed request must never kill the lifespan task
            logger.error(f'[Schedule] Failed to start the daily maintenance: {e}')
            continue
        if flow is not None:
            logger.info(f'[Schedule] Daily maintenance started by the schedule ("{flow.name}" flow)')
            continue
        # no flow was claimed: either one is in flight (it covers the day by
        # itself), or the startup just served the day. A due action that could
        # not be served is retried here, the log is throttled
        if daily.is_done():
            continue
        now = time.time()
        if now - last_retry_log >= RETRY_LOG_INTERVAL:
            last_retry_log = now
            logger.info('[Schedule] The daily action of today is not served yet, retrying')


# The process-wide daily bookkeeping
DAILY = DailyState()
