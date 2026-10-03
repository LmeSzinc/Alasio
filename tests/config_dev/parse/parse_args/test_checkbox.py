"""
dt="checkbox": on / off.
"""

from tests.config_dev.parse.parse_args.helpers import parse_arg


class TestCheckbox:
    def test_on(self):
        arg = parse_arg("""\
dt: checkbox
value: true
""")
        assert arg.value is True
        assert arg.get_anno() == 'bool'

    def test_off(self):
        arg = parse_arg("""\
dt: checkbox
value: false
""")
        assert arg.value is False
