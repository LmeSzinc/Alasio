from datetime import time, timedelta, timezone, datetime
from typing import List, Optional, Tuple, Union

from alasio.base.op import random_normal_distribution_int

TYPE_TIMEZONE_INPUT = Union[str, int, timedelta, timezone]
TYPE_SERVER_UPDATE_INPUT = Union[str, int, List[str]]


def parse_timezone(tz: TYPE_TIMEZONE_INPUT) -> timezone:
    """
    Convert to timezone as possible
    """
    if isinstance(tz, timezone):
        return tz
    if isinstance(tz, timedelta):
        # may raise ValueError:
        # offset must be a timedelta strictly between -timedelta(hours=24) and timedelta(hours=24), not ...
        return timezone(tz)
    if isinstance(tz, int):
        # -7 for timedelta(hours=-7)
        if not -23 <= tz <= 23:
            raise ValueError(f'Timezone hour must within -23~23, got {tz}')
        return timezone(timedelta(hours=tz))
    if isinstance(tz, str):
        if ':' in tz:
            h, _, m = tz.partition(':')
            # "09:30" for timedelta(hours=9, minutes=30)
            try:
                h = int(h)
                m = int(m)
            except (ValueError, TypeError):
                raise ValueError(f'Failed to parse timezone as HH:MM, got {tz}') from None
            if not -23 <= h <= 23:
                raise ValueError(f'Timezone hour must within -23~23, got {tz}')
            if not 0 <= m <= 59:
                raise ValueError(f'Timezone hour must within 0~59, got {tz}')
            return timezone(timedelta(hours=h, minutes=m))
        else:
            # "8" for timedelta(hours=8)
            try:
                h = int(tz)
            except (ValueError, TypeError):
                raise ValueError(f'Failed to parse timezone as hour, got {tz}') from None
            if not -23 <= h <= 23:
                raise ValueError(f'Timezone hour must within -23~23, got {tz}')
            return timezone(timedelta(hours=h))

    raise ValueError(f'Invalid timezone input: {tz}')


def parse_server_update(updates: TYPE_SERVER_UPDATE_INPUT) -> List[Tuple[int, int]]:
    """
    Parse user input to sorted [(hour, minute), ...]

    "00:00" to [(0, 0)]
    [ "18:00", "00:00", "12:00",] to [(0, 0), (12, 0), (18, 0)]
    "00:00, 12:00" to [(0, 0), (12, 0)]
    """
    if isinstance(updates, str):
        updates = updates.strip().replace(' ', '')
        if not updates:
            raise ValueError('Empty server_update')
        updates = updates.split(',')
    if isinstance(updates, list):
        out = []
        for update in updates:
            h, sep, m = update.partition(':')
            if not sep:
                raise ValueError(f'Failed to parse server_update as HH:MM, got {update}, server_update={updates}')
            try:
                h = int(h)
                m = int(m)
            except (ValueError, TypeError):
                raise ValueError(
                    f'Failed to parse server_update as HH:MM, got {update}, server_update={updates}') from None
            if not 0 <= h <= 23:
                raise ValueError(f'server_update hour must within 0~23, got {h}, server_update={updates}')
            if not 0 <= m <= 59:
                raise ValueError(f'server_update hour must within 0~59, got {m}, server_update={updates}')
            out.append((h, m))
        if not out:
            raise ValueError('Empty server_update')
        return sorted(out)

    raise ValueError(f'Invalid server_update input: {updates}')


def nearest_future(futures: List[datetime], threshold=0) -> datetime:
    """
    Finds the earliest datetime, and if a threshold is provided,
    returns the latest datetime that falls within that window.

    Args:
        futures:
        threshold: in seconds
    """
    if not futures:
        raise ValueError(f'Empty future list')

    # 1. Sort the list to find the earliest datetime
    sorted_dates = sorted(futures)
    earliest = sorted_dates[0]

    # 2. If no threshold, just return the smallest
    if threshold <= 0:
        return earliest

    # 3. Calculate the cut-off point
    limit = earliest + timedelta(seconds=threshold)

    # 4. Find the last date that is within the threshold of the earliest
    # Since it's sorted, we can just iterate and keep the last valid one
    last_within_threshold = earliest
    for dt in sorted_dates[1:]:
        if dt <= limit:
            last_within_threshold = dt
        else:
            # Since the list is sorted, we can stop searching early
            break

    return last_within_threshold


def parse_second(second) -> Tuple[int, int]:
    """
    Args:
        second (int, str, tuple[int, int]):
            3, "3", (1, 4), [1, 4], "10~30", "10, 30", "10-30"
            second must >= 0
    """
    if isinstance(second, (tuple, list)):
        try:
            low, high = second
        except ValueError:
            raise ValueError(f'Expect format (low, high), got {second}') from None
    elif isinstance(second, str):
        second = second.strip().replace(' ', '')
        if ',' in second:
            low, _, high = second.partition(',')
        elif '~' in second:
            low, _, high = second.partition('~')
        elif '-' in second and not second.startswith('-'):
            # check startswith('-'), second might be negative value like "-10"
            low, _, high = second.partition('-')
        else:
            try:
                second = int(second)
            except (ValueError, TypeError):
                # this shouldn't happen
                raise ValueError(f'Expect second in integer, got "{second}"') from None
            if second < 0:
                raise ValueError(f'Second must >=0 , got {second}')
            return second, second
    elif isinstance(second, (int, float)):
        if second < 0:
            raise ValueError(f'Second must >=0 , got {second}')
        second = int(second)
        return second, second
    else:
        raise ValueError(f'Invalid second input, got {second}')

    try:
        low = int(low)
        high = int(high)
    except (ValueError, TypeError):
        raise ValueError(f'Low bound and high bound must be integer, got {second}') from None
    if low > high:
        raise ValueError(f'High bound must >= lower bound, got {second}')
    if low < 0 or high < 0:
        raise ValueError(f'Low bound and high bound must >= 0, got {second}')
    return low, high


def random_time(second, n=3, precision=3):
    """
    Args:
        second (int, str, tuple[int, int]):
            3, "3", (1, 4), [1, 4], "10~30", "10, 30", "10-30"
            second >= 0
        n (int): The amount of numbers in simulation. Default to 3.
        precision (int): Decimals.

    Returns:
        float:
    """
    low, high = parse_second(second)
    if low < high:
        multiply = 10 * precision
        return random_normal_distribution_int(low * multiply, high * multiply, n) / multiply
    else:
        return float(low)


class ServerTime:
    def __init__(self, tz: TYPE_TIMEZONE_INPUT):
        """
        Args:
            tz: -7 for timedelta(hours=-7)
                "8" for timedelta(hours=8)
                "09:30" for timedelta(hours=9, minutes=30)
        """
        self.tz = parse_timezone(tz)

    def now(self) -> datetime:
        """
        datetime.now in server timezone
        """
        return datetime.now(self.tz)

    def get_next_update(
            self,
            server_updates: TYPE_SERVER_UPDATE_INPUT,
            weekday: Optional[int] = None,
            monthday: Optional[int] = None,
    ) -> datetime:
        """
        Args:
            server_updates: "HH:MM", "HH:MM, HH:MM", ["HH:MM", "HH:MM"]
            weekday: 0~6, 0 for Monday
            monthday: 1~31

        Returns:
            next server update in local timezone
        """
        if weekday is not None and monthday is not None:
            raise ValueError('Cannot set weekday and monthday at the same time')
        now = self.now()
        server_updates = parse_server_update(server_updates)
        # try to get next server update of today
        today = now.date()
        is_today_valid = True
        if weekday is not None and today.weekday() != weekday:
            is_today_valid = False
        if monthday is not None and today.day != monthday:
            is_today_valid = False
        if is_today_valid:
            for h, m in server_updates:
                candidate = datetime.combine(today, time(hour=h, minute=m), self.tz)
                if candidate > now:
                    return candidate.astimezone()
        # try to get first server update of tomorrow
        for _ in range(35):
            today += timedelta(days=1)
            if weekday is not None and today.weekday() != weekday:
                continue
            if monthday is not None and today.day != monthday:
                continue
            for h, m in server_updates:
                candidate = datetime.combine(today, time(hour=h, minute=m), self.tz)
                return candidate.astimezone()
        # shouldn't reach here
        raise ValueError('Empty server_update')

    def get_last_update(
            self,
            server_updates: TYPE_SERVER_UPDATE_INPUT,
            weekday: Optional[int] = None,
            monthday: Optional[int] = None,
    ) -> datetime:
        """
        Args:
            server_updates: "HH:MM", "HH:MM, HH:MM", ["HH:MM", "HH:MM"]
            weekday: 0~6, 0 for Monday
            monthday: 1~31

        Returns:
            last server update in local timezone
        """
        if weekday is not None and monthday is not None:
            raise ValueError('Cannot set weekday and monthday at the same time')
        now = self.now()
        server_updates = parse_server_update(server_updates)
        # try to get next server update of today
        today = now.date()
        is_today_valid = True
        if weekday is not None and today.weekday() != weekday:
            is_today_valid = False
        if monthday is not None and today.day != monthday:
            is_today_valid = False
        if is_today_valid:
            for h, m in reversed(server_updates):
                candidate = datetime.combine(today, time(hour=h, minute=m), self.tz)
                if candidate < now:
                    return candidate.astimezone()
        # try to get last server update of yesterday
        for _ in range(35):
            today -= timedelta(days=1)
            if weekday is not None and today.weekday() != weekday:
                continue
            if monthday is not None and today.day != monthday:
                continue
            for h, m in reversed(server_updates):
                candidate = datetime.combine(today, time(hour=h, minute=m), self.tz)
                return candidate.astimezone()
        # shouldn't reach here
        raise ValueError('Empty server_update')
