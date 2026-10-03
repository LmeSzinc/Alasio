"""
dt="filter-order": an ordered subset of "option".

"option" is the universe of items the editor can add, the value is the user
order of a part of it: every item of the value must be an option and no item of
either list may repeat. The value is defined as "item-A > item-B" (or as a
list), it is normalized to a tuple before validation and storage.
"""

import pytest

from alasio.config_dev.parse.base import DefinitionError
from tests.config_dev.parse.parse_args.helpers import parse_arg


class TestFilterOrder:
    def test_value_is_split(self):
        arg = parse_arg("""\
dt: filter-order
value: |-
  Fleet-1 > Fleet-2 > Submarine
option: [Fleet-1, Fleet-2, Fleet-3, Fleet-4, Submarine]
""")
        assert arg.dt == 'filter-order'
        assert arg.value == ('Fleet-1', 'Fleet-2', 'Submarine')
        assert arg.get_anno() == 'a.T_TUPLE_STR'

    def test_order_is_kept(self):
        """The value order is the priority order, keep it as defined."""
        arg = parse_arg("""\
dt: filter-order
value: Submarine > Fleet-4 > Fleet-1
option: [Fleet-1, Fleet-4, Submarine]
""")
        assert arg.value == ('Submarine', 'Fleet-4', 'Fleet-1')

    def test_spaces_are_stripped(self):
        arg = parse_arg("""\
dt: filter-order
value: ' Fleet-1   >Fleet-2>  Submarine '
option: [Fleet-1, Fleet-2, Submarine]
""")
        assert arg.value == ('Fleet-1', 'Fleet-2', 'Submarine')

    def test_list_value_is_normalized_to_tuple(self):
        arg = parse_arg("""\
dt: filter-order
value: [Fleet-2, Fleet-1]
option: [Fleet-1, Fleet-2, Fleet-3]
""")
        assert arg.value == ('Fleet-2', 'Fleet-1')

    def test_items_are_not_limited_to_str(self):
        """The items are python literal items, not necessarily str."""
        arg = parse_arg("""\
dt: filter-order
value: [2, 1]
option: [1, 2, 3]
""")
        assert arg.value == (2, 1)
        assert arg.option == [1, 2, 3]

    def test_empty_value(self):
        arg = parse_arg("""\
dt: filter-order
value: []
option: [Fleet-1, Fleet-2]
""")
        assert arg.value == ()

    def test_partial_subset(self):
        """Unused options stay in "option", they are the source of the editor."""
        arg = parse_arg("""\
dt: filter-order
value: Fleet-1
option: [Fleet-1, Fleet-2, Fleet-3]
""")
        assert arg.value == ('Fleet-1',)
        assert arg.option == ['Fleet-1', 'Fleet-2', 'Fleet-3']

    def test_option_order_is_kept(self):
        """The option order is what the frontend offers, keep it as defined."""
        arg = parse_arg("""\
dt: filter-order
value: Fleet-1
option: [Fleet-3, Fleet-1, Fleet-2]
""")
        assert arg.option == ['Fleet-3', 'Fleet-1', 'Fleet-2']

    def test_option_dict_is_dropped(self):
        arg = parse_arg("""\
dt: filter-order
value: Fleet-1
option: [Fleet-1]
option_dict:
  group: [Fleet-1]
""")
        assert 'option_dict' not in arg.to_dict()

    def test_vert_layout_by_default(self):
        arg = parse_arg("""\
dt: filter-order
value: Fleet-1
option: [Fleet-1]
""")
        assert arg.layout == 'vert'


class TestFilterOrderValidate:
    def test_option_required(self):
        """The editor adds items out of "option", so it is not optional."""
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order
value: Fleet-1 > Fleet-2
""")
        assert 'datatype "filter-order" must have "option" defined' in str(e.value)

    def test_option_must_be_list(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order
value: Fleet-1
option: Fleet-1
""")
        assert '"option" must be a list' in str(e.value)

    def test_option_must_not_be_empty(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order
value: Fleet-1
option: []
""")
        assert '"option" is empty' in str(e.value)

    def test_unhashable_option_item(self):
        """A dict item can not be an option, report it as a definition error."""
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order
value: Fleet-1
option: [{key: 1}, Fleet-1]
""")
        assert 'must be bool/str/int/float/bytes/None' in str(e.value)

    def test_duplicate_option(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order
value: Fleet-1
option: [Fleet-1, Fleet-1, Fleet-2]
""")
        assert '"option" has duplicate value "Fleet-1"' in str(e.value)

    def test_value_item_must_be_in_option(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order
value: Fleet-1 > Fleet-9
option: [Fleet-1, Fleet-2]
""")
        assert 'Default value "Fleet-9" is not in "option"' in str(e.value)

    def test_duplicate_value_item(self):
        """An item can not be used twice, the editor shows it once per row."""
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order
value: Fleet-1 > Fleet-2 > Fleet-1
option: [Fleet-1, Fleet-2]
""")
        assert 'Default value has duplicate value "Fleet-1"' in str(e.value)

    def test_value_must_be_list(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order
value: 1
option: ['1', '2']
""")
        assert 'Value of "filter-order" datatype must be a list' in str(e.value)

    def test_unhashable_value_item(self):
        """The option lookup hashes the value item, report it as a definition error."""
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order
value: [{key: 1}]
option: [Fleet-1]
""")
        assert 'must be bool/str/int/float/bytes/None' in str(e.value)
