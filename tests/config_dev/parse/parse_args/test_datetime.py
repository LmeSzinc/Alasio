"""
dt="datetime": timezone aware datetime.

Every datetime is stored with a timezone, a naive yaml timestamp defaults to UTC.
"""

from datetime import datetime, timezone

from tests.config_dev.parse.parse_args.helpers import parse_arg


class TestDatetime:
    def test_utc(self):
        arg = parse_arg("""\
dt: datetime
value: 2026-01-02T03:04:05Z
""")
        assert arg.value == datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        assert arg.get_anno() == 'a.T_DATETIME'

    def test_naive_defaults_to_utc(self):
        arg = parse_arg("""\
dt: datetime
value: 2026-01-02 03:04:05
""")
        assert arg.value == datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    def test_offset_is_kept(self):
        arg = parse_arg("""\
dt: datetime
value: 2026-01-02T03:04:05+08:00
""")
        assert arg.value.utcoffset().total_seconds() == 8 * 3600

    def test_default_value_is_the_shared_default(self):
        arg = parse_arg("""\
dt: datetime
value: 2026-01-02T03:04:05Z
""")
        assert repr(arg.get_value()) == 'a.DEFAULT_TIME'
