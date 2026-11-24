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
        assert arg.get_anno() == "t.Tuple[t.Literal['Fleet-1', 'Fleet-2', 'Fleet-3', 'Fleet-4', 'Submarine'], ...]"

    def test_literal(self):
        """The value type is a tuple of the "option" literal."""
        arg = parse_arg("""\
dt: filter-order
value: Fleet-1 > Fleet-2
option: [Fleet-1, Fleet-2, Fleet-3]
""")
        assert arg.get_literal_groups() == [['Fleet-1', 'Fleet-2', 'Fleet-3']]
        assert arg.get_literal() == "t.Literal['Fleet-1', 'Fleet-2', 'Fleet-3']"
        assert arg.get_python_type() == "t.Tuple[t.Literal['Fleet-1', 'Fleet-2', 'Fleet-3'], ...]"

    def test_literal_ref(self):
        """The module level literal variable replaces the inline literal."""
        arg = parse_arg("""\
dt: filter-order
value: Fleet-1
option: [Fleet-1, Fleet-2]
""")
        assert arg.get_literal(literal_ref='LITERAL_Group_Arg') == 'LITERAL_Group_Arg'
        assert arg.get_python_type(literal_ref='LITERAL_Group_Arg') == 't.Tuple[LITERAL_Group_Arg, ...]'
        assert arg.get_anno(literal_ref='LITERAL_Group_Arg') == 't.Tuple[LITERAL_Group_Arg, ...]'

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

    def test_option_dict_is_rejected(self):
        """A two level order is its own datatype, the flat one must not take the
        secondary options of it."""
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order
value: PlateT4
option: [PlateT4]
option_dict:
  PlateT4: [gun]
""")
        assert e.value.msg == (
            'datatype "filter-order" must not have "option_dict" defined, '
            'use "filter-order-item" or "filter-order-pair"')

    def test_tokens_is_rejected(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order
value: []
option: [Fleet-1]
tokens: [shortest]
""")
        assert e.value.msg == (
            'datatype "filter-order" must not have "tokens" defined, use "filter-order-pair"')

    def test_level_separator_is_legal_without_secondary_options(self):
        """An arg without secondary options never splits an item, its names may
        hold the level separator: "Fleet-1" is a legal item here."""
        arg = parse_arg("""\
dt: filter-order
value: Fleet-1 > Fleet-2
option: [Fleet-1, Fleet-2]
""")
        assert arg.value == ('Fleet-1', 'Fleet-2')
        assert arg.get_literal_groups() == [['Fleet-1', 'Fleet-2']]

    def test_vert_layout_by_default(self):
        arg = parse_arg("""\
dt: filter-order
value: Fleet-1
option: [Fleet-1]
""")
        assert arg.layout == 'vert'


class TestFilterOrderItem:
    """
    dt="filter-order-item" with "option_dict": every item of the value carries
    one of its own secondary options as "item-option", a primary item is used
    once.
    """

    def test_composite_items(self):
        arg = parse_arg("""\
dt: filter-order-item
value: PlateT4-gun > BookT3-red > Chip
option: [PlateT4, BookT3, Chip, PR]
option_dict:
  PlateT4: [gun, general]
  BookT3: [red, blue]
""")
        assert arg.value == ('PlateT4-gun', 'BookT3-red', 'Chip')
        assert arg.option_dict == {'PlateT4': ['gun', 'general'], 'BookT3': ['red', 'blue']}
        assert arg.layout == 'vert'

    def test_literal_is_the_expanded_items(self):
        """The literal holds the pairs, a secondary option of another item is
        not a legal item of the value."""
        arg = parse_arg("""\
dt: filter-order-item
value: PlateT4-gun
option: [PlateT4, BookT3, Chip]
option_dict:
  PlateT4: [gun, general]
  BookT3: [red]
""")
        assert arg.get_literal_groups() == [[
            'PlateT4-gun', 'PlateT4-general', 'BookT3-red', 'Chip',
        ]]
        assert arg.get_python_type() == (
            "t.Tuple[t.Literal['PlateT4-gun', 'PlateT4-general', 'BookT3-red', 'Chip'], ...]")

    def test_literal_ref(self):
        arg = parse_arg("""\
dt: filter-order-item
value: PlateT4-gun
option: [PlateT4]
option_dict:
  PlateT4: [gun]
""")
        assert arg.get_anno(literal_ref='LITERAL_G_A') == 't.Tuple[LITERAL_G_A, ...]'

    def test_list_value(self):
        arg = parse_arg("""\
dt: filter-order-item
value: [PlateT4-general, Chip]
option: [PlateT4, Chip]
option_dict:
  PlateT4: [gun, general]
""")
        assert arg.value == ('PlateT4-general', 'Chip')

    def test_empty_value(self):
        arg = parse_arg("""\
dt: filter-order-item
value: []
option: [PlateT4]
option_dict:
  PlateT4: [gun]
""")
        assert arg.value == ()

    def test_primary_without_secondary(self):
        """An item without an entry in "option_dict" is stored as itself."""
        arg = parse_arg("""\
dt: filter-order-item
value: Chip
option: [PlateT4, Chip]
option_dict:
  PlateT4: [gun]
""")
        assert arg.value == ('Chip',)

    def test_primary_must_specify_secondary(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order-item
value: PlateT4
option: [PlateT4]
option_dict:
  PlateT4: [gun]
""")
        assert e.value.msg == 'Default value "PlateT4" must specify its secondary option'

    def test_secondary_must_belong_to_the_primary(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order-item
value: PlateT4-red
option: [PlateT4, BookT3]
option_dict:
  PlateT4: [gun]
  BookT3: [red]
""")
        assert e.value.msg == 'Default value "PlateT4-red" is not a valid item of "PlateT4"'

    def test_primary_without_secondary_must_not_carry_one(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order-item
value: Chip-gun
option: [Chip]
option_dict:
  Chip2: [gun]
""")
        # the option_dict key is reported first, it is not in "option"
        assert '"option_dict" key "Chip2" is not in "option"' in str(e.value)

    def test_primary_without_secondary_must_not_carry_one_with_valid_dict(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order-item
value: Chip-gun
option: [PlateT4, Chip]
option_dict:
  PlateT4: [gun]
""")
        assert e.value.msg == 'Default value "Chip-gun" is not a valid item of "Chip"'

    def test_primary_must_be_in_option(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order-item
value: PlateT5-gun
option: [PlateT4]
option_dict:
  PlateT4: [gun]
""")
        assert e.value.msg == 'Default value "PlateT5-gun" is not in "option"'

    def test_duplicate_primary(self):
        """A primary item is used once, the editor shows one row per primary."""
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order-item
value: PlateT4-gun > PlateT4-general
option: [PlateT4]
option_dict:
  PlateT4: [gun, general]
""")
        assert e.value.msg == 'Default value has duplicate item "PlateT4"'

    def test_empty_item(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order-item
value: PlateT4-gun > > Chip
option: [PlateT4, Chip]
option_dict:
  PlateT4: [gun]
""")
        assert 'Default value "" is not a valid item' in str(e.value)

    def test_empty_secondary(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order-item
value: 'PlateT4-'
option: [PlateT4]
option_dict:
  PlateT4: [gun]
""")
        assert 'is not a valid item' in str(e.value)

    def test_value_item_must_be_str(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order-item
value: [1]
option: [PlateT4]
option_dict:
  PlateT4: [gun]
""")
        assert 'must be a str if "option_dict" is defined' in str(e.value)

    def test_option_items_must_be_str(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order-item
value: ['1-gun']
option: [1]
option_dict:
  '1': [gun]
""")
        assert '"option" item "1" must be a str if "option_dict" is defined' in str(e.value)

    def test_option_dict_key_not_in_option(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order-item
value: PlateT4-gun
option: [PlateT4]
option_dict:
  PlateT5: [gun]
""")
        assert e.value.msg == '"option_dict" key "PlateT5" is not in "option"'

    @pytest.mark.parametrize('option_dict, message', [
        ('[gun]', '"option_dict" must be a dict'),
    ], ids=['list'])
    def test_option_dict_must_be_a_dict(self, option_dict, message):
        with pytest.raises(DefinitionError) as e:
            parse_arg(f"""\
dt: filter-order-item
value: []
option: [PlateT4]
option_dict: {option_dict}
""")
        assert e.value.msg == message

    def test_option_dict_value_must_be_a_list(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order-item
value: []
option: [PlateT4]
option_dict:
  PlateT4: gun
""")
        assert e.value.msg == '"option_dict" value of "PlateT4" must be a list'

    def test_option_dict_value_must_not_be_empty(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order-item
value: []
option: [PlateT4]
option_dict:
  PlateT4: []
""")
        assert e.value.msg == '"option_dict" value of "PlateT4" is empty'

    def test_option_dict_item_must_be_str(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order-item
value: []
option: [PlateT4]
option_dict:
  PlateT4: [1]
""")
        assert e.value.msg == '"option_dict" item "1" must be a str'

    def test_option_dict_duplicate_item(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order-item
value: []
option: [PlateT4]
option_dict:
  PlateT4: [gun, gun]
""")
        assert e.value.msg == '"option_dict" value of "PlateT4" has duplicate item "gun"'

    def test_separator_in_option(self):
        """A primary item can not hold the item-option separator."""
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order-item
value: []
option: ['Plate-T4']
option_dict:
  'Plate-T4': [gun]
""")
        assert e.value.msg == '"option" item "Plate-T4" must not contain "-", it separates the secondary option'

    def test_separator_in_secondary(self):
        """A secondary option may hold the separator, the split takes the first one."""
        arg = parse_arg("""\
dt: filter-order-item
value: PlateT4-gun-x
option: [PlateT4]
option_dict:
  PlateT4: ['gun-x']
""")
        assert arg.value == ('PlateT4-gun-x',)
        assert arg.get_literal_groups() == [['PlateT4-gun-x']]

    def test_list_separator_in_option(self):
        """>" separates the items of a value, so a primary item can not hold one."""
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order
value: []
option: ['Plate>T4']
""")
        assert e.value.msg == (
            '"option" item "Plate>T4" must not contain ">", it separates the items of the value')

    def test_list_separator_in_secondary(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order-item
value: []
option: [PlateT4]
option_dict:
  PlateT4: ['gun>x']
""")
        assert e.value.msg == (
            '"option_dict" item of "PlateT4" "gun>x" must not contain ">", '
            'it separates the items of the value')

    def test_list_separator_is_checked_without_secondary_options(self):
        """The rule is not limited to an arg with "option_dict"."""
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order
value: []
option: ['PlateT4>x']
""")
        assert 'must not contain ">"' in str(e.value)

    def test_separator_is_checked_in_value(self):
        """A value item holding a list separator is not a legal item either."""
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order-item
value: ['PlateT4-gun>x']
option: [PlateT4]
option_dict:
  PlateT4: [gun]
""")
        assert 'is not a valid item of "PlateT4"' in str(e.value)

    def test_value_must_be_list(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order-item
value: 1
option: [PlateT4]
option_dict:
  PlateT4: [gun]
""")
        assert 'Value of "filter-order-item" datatype must be a list' in str(e.value)


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
        assert 'must be None/bool/int/str' in str(e.value)

    @pytest.mark.parametrize('option', [
        '2026-10-26',
        '1.5',
        '!!binary QQ==',
    ], ids=['date', 'float', 'bytes'])
    def test_non_literal_option_item(self, option):
        """A hashable but non-literal item would generate an invalid Literal."""
        with pytest.raises(DefinitionError) as e:
            parse_arg(f"""\
dt: filter-order
value: []
option: [{option}, Fleet-1]
""")
        assert 'must be None/bool/int/str' in str(e.value)

    def test_bool_items(self):
        """bool is a literal item supported by msgspec."""
        arg = parse_arg("""\
dt: filter-order
value: [true, false]
option: [true, false]
""")
        assert arg.value == (True, False)

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
        assert 'must be None/bool/int/str' in str(e.value)

    def test_option_dict_required(self):
        """The two level datatypes are explicit about their second level."""
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order-item
value: []
option: [PlateT4]
""")
        assert e.value.msg == 'datatype "filter-order-item" must have "option_dict" defined'

    def test_tokens_is_rejected(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order-item
value: []
option: [PlateT4]
option_dict:
  PlateT4: [gun]
tokens: [shortest]
""")
        assert e.value.msg == (
            'datatype "filter-order-item" must not have "tokens" defined, '
            'use "filter-order-pair"')


class TestFilterOrderPair:
    """
    dt="filter-order-pair": the whole "item-option" is the identity, so several
    options of one item may coexist.
    """

    def test_pairs_of_one_item_coexist(self):
        arg = parse_arg("""\
dt: filter-order-pair
value: UrgentCube-1:30 > UrgentCube-3 > ExtraCube-0:30
option: [UrgentCube, ExtraCube, Major]
option_dict:
  UrgentCube: ['1:30', '3', '6']
  ExtraCube: ['0:30', '1:30']
""")
        assert arg.value == ('UrgentCube-1:30', 'UrgentCube-3', 'ExtraCube-0:30')
        assert arg.layout == 'vert'

    def test_literal_is_the_expanded_items(self):
        arg = parse_arg("""\
dt: filter-order-pair
value: UrgentCube-1:30
option: [UrgentCube, Major]
option_dict:
  UrgentCube: ['1:30', '3']
""")
        assert arg.get_literal_groups() == [[
            'UrgentCube-1:30', 'UrgentCube-3', 'Major',
        ]]

    def test_duplicate_pair(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order-pair
value: UrgentCube-1:30 > UrgentCube-1:30
option: [UrgentCube]
option_dict:
  UrgentCube: ['1:30', '3']
""")
        assert e.value.msg == 'Default value has duplicate value "UrgentCube-1:30"'

    def test_bare_primary_of_a_declared_item(self):
        """An item that declares options is written with one of them."""
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order-pair
value: UrgentCube > Major
option: [UrgentCube, Major]
option_dict:
  UrgentCube: ['1:30']
""")
        assert e.value.msg == 'Default value "UrgentCube" must specify its secondary option'

    def test_bare_primary_without_options(self):
        arg = parse_arg("""\
dt: filter-order-pair
value: Major > DailyChip
option: [Major, DailyChip, UrgentCube]
option_dict:
  UrgentCube: ['1:30']
""")
        assert arg.value == ('Major', 'DailyChip')

    def test_tokens(self):
        arg = parse_arg("""\
dt: filter-order-pair
value: UrgentCube-1:30 > shortest
option: [UrgentCube]
option_dict:
  UrgentCube: ['1:30']
tokens: [shortest]
""")
        assert arg.value == ('UrgentCube-1:30', 'shortest')
        assert arg.tokens == ['shortest']

    def test_empty_tokens_is_dropped(self):
        arg = parse_arg("""\
dt: filter-order-pair
value: []
option: [UrgentCube]
option_dict:
  UrgentCube: ['1:30']
tokens: []
""")
        assert 'tokens' not in arg.to_dict()

    def test_unknown_token(self):
        """An entry that is neither a pair nor a declared token is rejected."""
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order-pair
value: shortest
option: [UrgentCube]
option_dict:
  UrgentCube: ['1:30']
tokens: [reset]
""")
        assert e.value.msg == 'Default value "shortest" is not in "option"'

    def test_duplicate_token(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order-pair
value: shortest > shortest
option: [UrgentCube]
option_dict:
  UrgentCube: ['1:30']
tokens: [shortest]
""")
        assert e.value.msg == 'Default value has duplicate value "shortest"'

    @pytest.mark.parametrize('tokens, message', [
        ('shortest', '"tokens" must be a list'),
        ('[1]', '"tokens" item "1" must be a str'),
        ('[]', None),
    ], ids=['not-list', 'not-str', 'empty'])
    def test_tokens_shape(self, tokens, message):
        yaml = f"""\
dt: filter-order-pair
value: []
option: [UrgentCube]
option_dict:
  UrgentCube: ['1:30']
tokens: {tokens}
"""
        if message is None:
            assert parse_arg(yaml).value == ()
        else:
            with pytest.raises(DefinitionError) as e:
                parse_arg(yaml)
            assert e.value.msg == message

    def test_token_can_not_be_an_option(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order-pair
value: []
option: [Major, UrgentCube]
option_dict:
  UrgentCube: ['1:30']
tokens: [Major]
""")
        assert e.value.msg == '"tokens" item "Major" is in "option"'

    def test_token_can_not_hold_a_separator(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order-pair
value: []
option: [UrgentCube]
option_dict:
  UrgentCube: ['1:30']
tokens: ['some-token']
""")
        assert '"tokens" item "some-token" must not contain' in str(e.value)

    def test_option_dict_required(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-order-pair
value: []
option: [UrgentCube]
""")
        assert e.value.msg == 'datatype "filter-order-pair" must have "option_dict" defined'

    def test_custom_separator_is_not_a_level_separator(self):
        """The level is "item-option", a value in the secondary option may hold
        the level separator only after the first one."""
        arg = parse_arg("""\
dt: filter-order-pair
value: BookT3-1:30
option: [BookT3]
option_dict:
  BookT3: ['1:30']
""")
        assert arg.value == ('BookT3-1:30',)
