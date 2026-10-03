"""
dt="filter": an ordered list of filter items, a plain textarea in the frontend.

The default value is written as "item-A > item-B" (or as a list), the value is
split on ">" and stored as a tuple of str. The item order is the filter order.
"""

from msgspec import UNSET

from tests.config_dev.parse.parse_args.helpers import parse_arg


class TestFilter:
    def test_value_is_split(self):
        arg = parse_arg("""\
dt: filter
value: |-
  ActionPoint > PurpleCoins
""")
        assert arg.value == ('ActionPoint', 'PurpleCoins')
        assert arg.get_anno() == 'a.T_TUPLE_STR'

    def test_order_is_kept(self):
        arg = parse_arg("""\
dt: filter
value: |-
  GearPart > Book > Coin
""")
        assert arg.value == ('GearPart', 'Book', 'Coin')

    def test_spaces_are_stripped(self):
        arg = parse_arg("""\
dt: filter
value: ' A >  B >C '
""")
        assert arg.value == ('A', 'B', 'C')

    def test_single_item(self):
        arg = parse_arg("""\
dt: filter
value: ActionPoint
""")
        assert arg.value == ('ActionPoint',)

    def test_list_value(self):
        """A yaml list is accepted as well, only the items are part of the contract."""
        arg = parse_arg("""\
dt: filter
value: [A, B]
""")
        assert list(arg.value) == ['A', 'B']

    def test_option_is_not_required(self):
        arg = parse_arg("""\
dt: filter
value: A > B
""")
        assert arg.option is UNSET

    def test_vert_layout_by_default(self):
        arg = parse_arg("""\
dt: filter
value: A > B
""")
        assert arg.layout == 'vert'
