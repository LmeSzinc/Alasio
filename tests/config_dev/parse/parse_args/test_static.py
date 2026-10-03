"""
dt="static": a read-only value, plus the "static-hide" alias.

The option list is injected by the parser, the value is the only allowed item.
"""

from tests.config_dev.parse.parse_args.helpers import parse_arg


class TestStatic:
    def test_value(self):
        arg = parse_arg("""\
dt: static
value: static value
""")
        assert arg.dt == 'static'
        assert arg.value == 'static value'
        assert arg.option == ['static value']
        assert arg.get_anno() == "t.Literal['static value']"

    def test_static_hide_is_an_alias(self):
        """dt="static-hide" is dt="static" plus hide=True."""
        arg = parse_arg("""\
dt: static-hide
value: static value
""")
        assert arg.dt == 'static'
        assert arg.hide is True
        assert arg.value == 'static value'
        assert arg.option == ['static value']

    def test_hide_is_false_by_default(self):
        arg = parse_arg("""\
dt: static
value: static value
""")
        assert arg.hide is False
