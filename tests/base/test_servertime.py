from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from alasio.base.servertime import ServerTime, nearest_future, parse_second, parse_server_update, parse_timezone


class TestParseTimezone:
    @pytest.mark.parametrize("input_val, expected_offset_hours, expected_offset_minutes", [
        # Test cases for timezone objects
        (timezone.utc, 0, 0),
        (timezone(timedelta(hours=8)), 8, 0),

        # Test cases for timedelta objects
        (timedelta(hours=-5), -5, 0),
        (timedelta(hours=3, minutes=30), 3, 30),

        # Test cases for integers
        (8, 8, 0),
        (-7, -7, 0),
        (0, 0, 0),

        # Test cases for strings (HH:MM format)
        ("09:30", 9, 30),
        ("-05:00", -5, 0),
        ("0:0", 0, 0),

        # Test cases for strings (Single integer format)
        ("8", 8, 0),
        ("-7", -7, 0),
        ("0", 0, 0),
    ])
    def test_parse_timezone_success(self, input_val, expected_offset_hours, expected_offset_minutes):
        """Test successful conversion for all supported types and formats."""
        result = parse_timezone(input_val)
        assert isinstance(result, timezone)

        expected_delta = timedelta(hours=expected_offset_hours, minutes=expected_offset_minutes)
        assert result.utcoffset(None) == expected_delta

    @pytest.mark.parametrize("invalid_input, match_msg", [
        # timedelta out of range
        (timedelta(hours=30), "offset must be a timedelta strictly between"),

        # Integer out of range
        (24, "Timezone hour must within -23~23"),
        (-25, "Timezone hour must within -23~23"),

        # String HH:MM out of range or invalid
        ("25:00", "Timezone hour must within -23~23"),
        ("05:61", "Timezone hour must within 0~59"),
        ("abc:00", "Failed to parse timezone as HH:MM"),

        # String integer out of range or invalid
        ("24", "Timezone hour must within -23~23"),
        ("xyz", "Failed to parse timezone as hour"),

        # Invalid types
        (1.5, "Invalid timezone input"),
        (None, "Invalid timezone input"),
        ([], "Invalid timezone input"),
    ])
    def test_parse_timezone_exceptions(self, invalid_input, match_msg):
        """Test that invalid inputs raise ValueError with appropriate messages."""
        with pytest.raises(ValueError, match=match_msg):
            parse_timezone(invalid_input)

    def test_identity_check(self):
        """Ensure that if a timezone object is passed, the same object (or equivalent) is returned."""
        tz = timezone(timedelta(hours=2))
        assert parse_timezone(tz) is tz


class TestServerUpdate:
    @pytest.mark.parametrize("input_val, expected", [
        # Single string input
        ("00:00", [(0, 0)]),
        # Comma separated string with spaces
        ("00:00, 12:00", [(0, 0), (12, 0)]),
        # Unsorted list input
        (["18:00", "00:00", "12:00"], [(0, 0), (12, 0), (18, 0)]),
        # String with internal spaces (e.g., " 09 : 30 ")
        (" 09 : 30 , 08 : 00 ", [(8, 0), (9, 30)]),
        # Mixed sorting check
        (["23:59", "01:01", "12:00"], [(1, 1), (12, 0), (23, 59)]),
    ])
    def test_parse_server_update_success(self, input_val, expected):
        """Test valid inputs including strings, lists, and sorting logic."""
        assert parse_server_update(input_val) == expected

    @pytest.mark.parametrize("invalid_input, match_msg", [
        # Missing colon separator
        ("1200", "Failed to parse server_update as HH:MM"),
        (["12:00", "1300"], "Failed to parse server_update as HH:MM"),

        # Non-integer values
        ("12:aa", "Failed to parse server_update as HH:MM"),
        ("hh:mm", "Failed to parse server_update as HH:MM"),
        ("12.5:00", "Failed to parse server_update as HH:MM"),

        # Hour out of range (0-23)
        ("24:00", "hour must within 0~23"),
        ("-1:00", "hour must within 0~23"),

        # Minute out of range (0-59)
        ("12:60", "hour must within 0~59"),
        ("12:-1", "hour must within 0~59"),

        # Empty inputs
        ("", "Empty server_update"),
        ([], "Empty server_update"),

        # Invalid data types
        (12345, "Invalid server_update input"),
        (None, "Invalid server_update input"),
    ])
    def test_parse_server_update_exceptions(self, invalid_input, match_msg):
        """Test that malformed strings or invalid ranges raise ValueError."""
        with pytest.raises(ValueError, match=match_msg):
            parse_server_update(invalid_input)

    def test_parsing_consistency(self):
        """Verify that string input and list input result in the same output."""
        raw_str = "18:00, 06:00"
        raw_list = ["18:00", "06:00"]
        assert parse_server_update(raw_str) == parse_server_update(raw_list)


class TestNearstFuture:

    def test_empty_list_raises_error(self):
        """Should raise ValueError if the input list is empty."""
        with pytest.raises(ValueError, match="Empty future list"):
            nearest_future([])

    def test_single_element(self):
        """Should return the only element present regardless of threshold."""
        now = datetime(2023, 1, 1, 12, 0, 0)
        assert nearest_future([now]) == now
        assert nearest_future([now], threshold=60) == now

    def test_no_threshold_returns_earliest(self):
        """With threshold=0, it should return the absolute earliest datetime."""
        base = datetime(2023, 1, 1, 12, 0, 0)
        d2 = base + timedelta(seconds=10)
        d3 = base + timedelta(seconds=20)

        # Passing them out of order to ensure sorting works
        assert nearest_future([d3, base, d2], threshold=0) == base

    def test_negative_threshold_returns_earliest(self):
        """A negative threshold should behave like zero threshold."""
        base = datetime(2023, 1, 1, 12, 0, 0)
        d2 = base + timedelta(seconds=10)
        assert nearest_future([base, d2], threshold=-5) == base

    def test_within_threshold_returns_latest_in_window(self):
        """Should return the latest datetime that is still within the threshold from the earliest."""
        base = datetime(2023, 1, 1, 12, 0, 0)
        d2 = base + timedelta(seconds=5)  # Inside
        d3 = base + timedelta(seconds=10)  # Exactly on boundary
        d4 = base + timedelta(seconds=11)  # Outside

        # Limit is base + 10s = d3.
        assert nearest_future([base, d2, d3, d4], threshold=10) == d3

    def test_all_within_threshold(self):
        """Should return the very last item if all items fall within the threshold."""
        base = datetime(2023, 1, 1, 12, 0, 0)
        d2 = base + timedelta(seconds=5)
        assert nearest_future([base, d2], threshold=100) == d2

    def test_unsorted_input_with_threshold(self):
        """Ensure the function handles unsorted input correctly when using a threshold."""
        base = datetime(2023, 1, 1, 12, 0, 0)
        d_far = base + timedelta(hours=1)
        d_near = base + timedelta(seconds=10)

        # Earliest is 'base'. Threshold is 30s. d_near is inside, d_far is not.
        assert nearest_future([d_far, base, d_near], threshold=30) == d_near

    @pytest.mark.parametrize("threshold, expected_offset", [
        (0, 0),  # Returns base
        (5, 5),  # Returns base + 5
        (9, 5),  # Returns base + 5 (10 is too far)
        (10, 10),  # Returns base + 10 (boundary inclusive)
    ])
    def test_threshold_logic_boundaries(self, threshold, expected_offset):
        """Parametrized test to check various threshold boundaries."""
        base = datetime(2023, 1, 1, 10, 0, 0)
        futures = [
            base,
            base + timedelta(seconds=5),
            base + timedelta(seconds=10),
            base + timedelta(seconds=15)
        ]
        expected = base + timedelta(seconds=expected_offset)
        assert nearest_future(futures, threshold=threshold) == expected


class TestParseSecond:

    @pytest.mark.parametrize("input_val, expected", [
        # Integer and Float inputs
        (3, (3, 3)),
        (0, (0, 0)),
        (10.5, (10, 10)),

        # Simple String inputs
        ("3", (3, 3)),
        (" 10 ", (10, 10)),

        # Collection inputs (Tuple/List)
        ((1, 4), (1, 4)),
        ([5, 10], (5, 10)),
        ((0, 0), (0, 0)),

        # String Range inputs with different delimiters
        ("10~30", (10, 30)),
        ("10, 30", (10, 30)),
        ("10-30", (10, 30)),
        (" 5 ~ 15 ", (5, 15)),
        ("0-0", (0, 0)),
    ])
    def test_parse_second_valid_inputs(self, input_val, expected):
        """Tests that various valid input formats return the correct (low, high) tuple."""
        assert parse_second(input_val) == expected

    def test_parse_second_negative_number(self):
        """Should raise error for negative integers or strings."""
        with pytest.raises(ValueError, match="Second must >=0"):
            parse_second(-5)
        with pytest.raises(ValueError, match="Second must >=0"):
            parse_second("-10")

    def test_parse_second_invalid_collection_size(self):
        """Should raise error if list/tuple doesn't have exactly 2 elements."""
        with pytest.raises(ValueError, match=r"Expect format \(low, high\)"):
            parse_second([1])
        with pytest.raises(ValueError, match=r"Expect format \(low, high\)"):
            parse_second([1, 2, 3])

    def test_parse_second_high_less_than_low(self):
        """Should raise error if the range is logically impossible (e.g., 30 to 10)."""
        with pytest.raises(ValueError, match="High bound must >= lower bound"):
            parse_second("30-10")
        with pytest.raises(ValueError, match="High bound must >= lower bound"):
            parse_second((10, 5))

    def test_parse_second_non_integer_bounds(self):
        """Should raise error if strings contain non-numeric characters in a range."""
        with pytest.raises(ValueError, match="Low bound and high bound must be integer"):
            parse_second("10-abc")
        with pytest.raises(ValueError, match="Low bound and high bound must be integer"):
            parse_second("foo~bar")

    def test_parse_second_invalid_type(self):
        """Should raise error for completely invalid types like None or dict."""
        with pytest.raises(ValueError, match="Invalid second input"):
            parse_second(None)
        with pytest.raises(ValueError, match="Invalid second input"):
            parse_second({"seconds": 10})

    def test_parse_second_negative_bounds_in_range(self):
        """Should raise error if a range contains negative numbers."""
        # Note: The code specifically checks for "10--20" vs startswith('-')
        with pytest.raises(ValueError, match="Low bound and high bound must >= 0"):
            parse_second("-10, 20")
        with pytest.raises(ValueError, match="Low bound and high bound must >= 0"):
            parse_second((-5, 10))


@pytest.fixture
def server():
    # Using UTC+8 for testing
    return ServerTime(tz=8)


def to_utc(dt: datetime):
    """Helper to normalize datetime to UTC for assertion comparisons."""
    return dt.astimezone(timezone.utc)


class TestServerUpdateLogic:

    # --- GET_NEXT_UPDATE TESTS ---

    def test_get_next_update_later_today(self, server):
        # Mock now to 10:00 AM
        fixed_now = datetime(2023, 1, 1, 10, 0, tzinfo=server.tz)
        with patch.object(ServerTime, 'now', return_value=fixed_now):
            # Updates at 09:00 and 11:00. Next should be 11:00 today.
            res = server.get_next_update("09:00, 11:00")
            expected = datetime(2023, 1, 1, 11, 0, tzinfo=server.tz)
            assert to_utc(res) == to_utc(expected)

    def test_get_next_update_tomorrow(self, server):
        # Mock now to 12:00 PM (all updates for today have passed)
        fixed_now = datetime(2023, 1, 1, 12, 0, tzinfo=server.tz)
        with patch.object(ServerTime, 'now', return_value=fixed_now):
            res = server.get_next_update("08:00, 09:00")
            # Next should be 08:00 AM on Jan 2nd
            expected = datetime(2023, 1, 2, 8, 0, tzinfo=server.tz)
            assert to_utc(res) == to_utc(expected)

    def test_get_next_update_weekday_today(self, server):
        # 2023-01-02 is Monday (weekday 0)
        fixed_now = datetime(2023, 1, 2, 10, 0, tzinfo=server.tz)
        with patch.object(ServerTime, 'now', return_value=fixed_now):
            # Target Monday, it is Monday, and 11:00 is in future
            res = server.get_next_update("11:00", weekday=0)
            expected = datetime(2023, 1, 2, 11, 0, tzinfo=server.tz)
            assert to_utc(res) == to_utc(expected)

    def test_get_next_update_weekday_future(self, server):
        # 2023-01-02 is Monday (0). Target Wednesday (2).
        fixed_now = datetime(2023, 1, 2, 10, 0, tzinfo=server.tz)
        with patch.object(ServerTime, 'now', return_value=fixed_now):
            res = server.get_next_update("11:00", weekday=2)
            # Should be Jan 4th (Wednesday)
            expected = datetime(2023, 1, 4, 11, 0, tzinfo=server.tz)
            assert to_utc(res) == to_utc(expected)

    def test_get_next_update_monthday_next_month(self, server):
        # Jan 20th, target the 5th. Should roll to Feb 5th.
        fixed_now = datetime(2023, 1, 20, 10, 0, tzinfo=server.tz)
        with patch.object(ServerTime, 'now', return_value=fixed_now):
            res = server.get_next_update("10:00", monthday=5)
            expected = datetime(2023, 2, 5, 10, 0, tzinfo=server.tz)
            assert to_utc(res) == to_utc(expected)

    # --- GET_LAST_UPDATE TESTS ---

    def test_get_last_update_earlier_today(self, server):
        # Mock now to 10:00 AM
        fixed_now = datetime(2023, 1, 1, 10, 0, tzinfo=server.tz)
        with patch.object(ServerTime, 'now', return_value=fixed_now):
            # Updates 09:00 and 11:00. Last was 09:00 today.
            res = server.get_last_update("09:00, 11:00")
            expected = datetime(2023, 1, 1, 9, 0, tzinfo=server.tz)
            assert to_utc(res) == to_utc(expected)

    def test_get_last_update_yesterday(self, server):
        # Mock now to 07:00 AM (Today's updates haven't happened yet)
        fixed_now = datetime(2023, 1, 2, 7, 0, tzinfo=server.tz)
        with patch.object(ServerTime, 'now', return_value=fixed_now):
            res = server.get_last_update("08:00, 20:00")
            # Last was 20:00 on Jan 1st
            expected = datetime(2023, 1, 1, 20, 0, tzinfo=server.tz)
            assert to_utc(res) == to_utc(expected)

    def test_get_last_update_weekday_past(self, server):
        # Monday Jan 2nd. Target Sunday (6).
        fixed_now = datetime(2023, 1, 2, 10, 0, tzinfo=server.tz)
        with patch.object(ServerTime, 'now', return_value=fixed_now):
            res = server.get_last_update("12:00", weekday=6)
            # Should be Jan 1st (Sunday)
            expected = datetime(2023, 1, 1, 12, 0, tzinfo=server.tz)
            assert to_utc(res) == to_utc(expected)

    def test_get_last_update_monthday_past(self, server):
        # Jan 2nd, target the 28th. Should roll back to Dec 28th.
        fixed_now = datetime(2023, 1, 2, 10, 0, tzinfo=server.tz)
        with patch.object(ServerTime, 'now', return_value=fixed_now):
            res = server.get_last_update("10:00", monthday=28)
            expected = datetime(2022, 12, 28, 10, 0, tzinfo=server.tz)
            assert to_utc(res) == to_utc(expected)

    # --- ERROR CASES ---

    def test_invalid_input_combination(self, server):
        # Setting both weekday and monthday should raise ValueError
        with pytest.raises(ValueError, match="Cannot set weekday and monthday"):
            server.get_next_update("12:00", weekday=1, monthday=1)

    def test_logic_boundary_bug_check(self, server):
        """
        Tests if server_updates is parsed correctly even if today is skipped.
        In the original code, if today is not valid, the code skips to a loop
        where it iterates over server_updates. If it's still a string, it will crash.
        """
        # Today is Jan 2 (Monday). Filter for Tuesday.
        fixed_now = datetime(2023, 1, 2, 10, 0, tzinfo=server.tz)
        with patch.object(ServerTime, 'now', return_value=fixed_now):
            # If the code crashes here, server_updates was not parsed before the loop.
            res = server.get_next_update("12:00", weekday=1)
            assert res is not None
