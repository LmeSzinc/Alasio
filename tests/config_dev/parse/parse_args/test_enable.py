"""
dt="enable": a checkbox with the "true" / "false" options of the card badge.

The options are injected by the parser and can not be defined by the config,
so the arg never takes part in the generic option validation.
"""

from tests.config_dev.parse.parse_args.helpers import parse_arg


class TestEnable:
    def test_on(self):
        arg = parse_arg("""\
dt: enable
value: true
""")
        assert arg.value is True
        assert arg.option == ['true', 'false']
        assert arg.get_anno() == 'bool'

    def test_off(self):
        arg = parse_arg("""\
dt: enable
value: false
""")
        assert arg.value is False
        assert arg.option == ['true', 'false']

    def test_option_is_injected(self):
        arg = parse_arg("""\
dt: enable
value: true
option: [yes, no]
""")
        assert arg.option == ['true', 'false']
