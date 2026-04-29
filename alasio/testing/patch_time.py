import datetime
import time
from typing import Optional, Union

import pytest


class PatchTime:
    def __init__(self, target_time: Optional[Union[datetime.datetime, int, float]] = None):
        """
        Initialize PatchTime.

        Args:
            target_time: datetime object, timestamp in seconds, or None (uses current time)
        """
        self._monkeypatch = pytest.MonkeyPatch()
        # Internally use a UTC timestamp (float) as the single source of truth
        self._timestamp = 0.0
        self._sleep_calls = []
        self.set(target_time)

    def set(self, target_time: Optional[Union[datetime.datetime, int, float]] = None):
        """Set the current mocked time."""
        if target_time is None:
            # If not provided, use the actual current time
            self._timestamp = time.time()
        elif isinstance(target_time, (int, float)):
            # Timestamp in seconds
            self._timestamp = float(target_time)
        elif isinstance(target_time, datetime.datetime):
            # Datetime object
            if target_time.tzinfo is None:
                # Timezone naive, treat as UTC by default
                target_time = target_time.replace(tzinfo=datetime.timezone.utc)
            self._timestamp = target_time.timestamp()
        else:
            raise TypeError("Unsupported time type. Must be datetime, int, float, or None.")

    def shift(self, delta: Union[int, float, datetime.timedelta]):
        """Shift the time forward or backward."""
        if isinstance(delta, datetime.timedelta):
            self._timestamp += delta.total_seconds()
        elif isinstance(delta, (int, float)):
            self._timestamp += float(delta)
        else:
            raise TypeError("Delta must be int, float (seconds), or timedelta.")

    def __enter__(self):
        """Apply monkey patches when entering the context manager."""

        self._sleep_calls = []

        # Use a lambda to dynamically fetch the updated timestamp inside the mocked methods
        def get_ts():
            return self._timestamp

        # 1. Mock Datetime
        class MockDatetime(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                # Generate datetime from the mocked timestamp
                dt = datetime.datetime.fromtimestamp(get_ts(), tz=datetime.timezone.utc)
                if tz is None:
                    # If no tz is specified, return local naive datetime
                    return datetime.datetime.fromtimestamp(get_ts())
                return dt.astimezone(tz)

            @classmethod
            def utcnow(cls):
                # Return UTC naive datetime
                return datetime.datetime.fromtimestamp(get_ts(), tz=datetime.timezone.utc).replace(tzinfo=None)

            @classmethod
            def today(cls):
                return cls.now()

        # 2. Mock Date
        class MockDate(datetime.date):
            @classmethod
            def today(cls):
                # date.today() is equivalent to local datetime.now().date()
                return datetime.datetime.fromtimestamp(get_ts()).date()

        # 3. Mock Time Functions
        def mock_time():
            return get_ts()

        def mock_time_ns():
            return int(get_ts() * 1_000_000_000)

        # Map monotonic and perf_counter to the simulated timestamp
        # This guarantees that time differences (t2 - t1) correctly reflect timer.shift()
        def mock_monotonic():
            return get_ts()

        def mock_monotonic_ns():
            return int(get_ts() * 1_000_000_000)

        def mock_perf_counter():
            return get_ts()

        def mock_perf_counter_ns():
            return int(get_ts() * 1_000_000_000)

        # 4. Mock sleep — automatically advances time and records calls
        def mock_sleep(seconds):
            self._sleep_calls.append(seconds)
            self.shift(seconds)

        # Apply patches to the datetime module
        self._monkeypatch.setattr(datetime, "datetime", MockDatetime)
        self._monkeypatch.setattr(datetime, "date", MockDate)

        # Apply patches to the time module
        self._monkeypatch.setattr(time, "time", mock_time)
        self._monkeypatch.setattr(time, "time_ns", mock_time_ns)
        self._monkeypatch.setattr(time, "monotonic", mock_monotonic)
        self._monkeypatch.setattr(time, "monotonic_ns", mock_monotonic_ns)
        self._monkeypatch.setattr(time, "perf_counter", mock_perf_counter)
        self._monkeypatch.setattr(time, "perf_counter_ns", mock_perf_counter_ns)
        self._monkeypatch.setattr(time, "sleep", mock_sleep)

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Undo patches when exiting the context manager."""
        self._monkeypatch.undo()

    @property
    def sleep_calls(self):
        """Recorded sleep call arguments (in seconds)."""
        return list(self._sleep_calls)

    def clear_sleep_calls(self):
        """Clear recorded sleep calls."""
        self._sleep_calls.clear()