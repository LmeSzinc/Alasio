"""
Unit tests of the literal item and "option" validators of the args.

The dt-specific pipeline tests live in tests/config_dev/parse/parse_args/, these
tests call validate_literal_item(), validate_option() and
validate_filter_order() directly, so their contract is pinned independently of
the yaml pipeline: every rejected definition raises DefinitionError with an
exact message, and the accepted items are python literal items
(None/bool/int/str), matching msgspec's typing.Literal. float and bytes are
rejected because msgspec does not support them in Literal.
"""

from datetime import date

import pytest

from alasio.config_dev.parse.base import DefinitionError
from alasio.config_dev.parse.parse_arg_utils import validate_filter_order, validate_literal_item, validate_option

# placeholder value, used when the option itself is the subject of the test
ANY_VALUE = 'A'


def _assert_error(func, *args, message):
    """
    Call a validator and assert it raises DefinitionError with the exact message.

    Args:
        func (callable): Validator to test
        *args: Arguments of the validator
        message (str): Expected DefinitionError.msg

    Returns:
        DefinitionError: The raised error
    """
    with pytest.raises(DefinitionError) as e:
        func(*args)
    assert e.value.msg == message
    return e.value


class TestValidateLiteralItem:
    @pytest.mark.parametrize('item', [
        'A',
        '',
        '0',
        0,
        1,
        -1,
        True,
        False,
        None,
    ], ids=['str', 'empty-str', 'str-number', 'zero', 'int', 'negative-int', 'true', 'false', 'none'])
    def test_valid_literal_item(self, item):
        assert validate_literal_item(item) is None

    @pytest.mark.parametrize('item', [
        1.0,
        1.5,
        b'A',
        b'',
        [1, 2],
        (1, 2),
        {'key': 1},
        {1},
        date(2026, 10, 26),
    ], ids=['float-int', 'float', 'bytes', 'empty-bytes', 'list', 'tuple', 'dict', 'set', 'date'])
    def test_invalid_literal_item(self, item):
        """Only None/bool/int/str are supported by msgspec's Literal."""
        _assert_error(
            validate_literal_item, item,
            message=f'Value of "{item}" must be None/bool/int/str',
        )


class TestValidateOption:
    @pytest.mark.parametrize('option', [
        1,
        1.5,
        True,
        None,
        'A',
        ('A', 'B'),
        {'A', 'B'},
        {'A': 1},
    ], ids=['int', 'float', 'bool', 'none', 'str', 'tuple', 'set', 'dict'])
    def test_option_must_be_a_list(self, option):
        """A non-list option can not be traversed, reject it before anything else."""
        _assert_error(validate_option, option, ANY_VALUE, message='"option" must be a list')

    def test_option_must_not_be_empty(self):
        _assert_error(validate_option, [], ANY_VALUE, message='"option" is empty')

    @pytest.mark.parametrize('option, value', [
        (['A', 'B'], 'C'),
        ([1, 2], 3),
        ([1, 2], True),
        ([True], 1),
        ([None], 'None'),
        (['A'], None),
    ], ids=['str', 'int', 'bool-vs-int', 'int-vs-bool', 'none-vs-str', 'none'])
    def test_default_value_must_be_an_option(self, option, value):
        """The default value must be an option of the same type."""
        _assert_error(validate_option, option, value, message=f'Default value "{value}" is not in "option"')

    @pytest.mark.parametrize('option, value', [
        (['A'], 'A'),
        (['A', 'B', 'C'], 'B'),
        ([1, 2, 3], 2),
        ([1, -1, 0], -1),
        ([True, False], False),
        ([None, 'A'], None),
        (['', 0, None], ''),
    ], ids=['only', 'middle', 'int', 'negative-int', 'bool', 'none', 'mixed-literal'])
    def test_valid_option(self, option, value):
        assert validate_option(option, value) is None

    @pytest.mark.parametrize('option, value, duplicate', [
        (['A', 'A'], 'A', 'A'),
        (['A', 'B', 'A'], 'B', 'A'),
        ([1, 2, 1], 2, 1),
        ([True, True], True, True),
        ([None, None], None, None),
    ], ids=['adjacent', 'str', 'int', 'bool', 'none'])
    def test_duplicate_option(self, option, value, duplicate):
        _assert_error(validate_option, option, value, message=f'"option" has duplicate value "{duplicate}"')

    @pytest.mark.parametrize('item', [
        {'key': 1},
        [1, 2],
        {1},
    ], ids=['dict', 'list', 'set'])
    def test_unhashable_option_item(self, item):
        """An unhashable item can not be a literal item, name the item in the error."""
        _assert_error(
            validate_option, ['A', item], 'A',
            message=f'Value of "{item}" must be None/bool/int/str',
        )

    @pytest.mark.parametrize('option, wrong', [
        ([1.5], 1.5),
        ([1, 2.5], 2.5),
        ([b'A'], b'A'),
        ([1, date(2026, 10, 26)], date(2026, 10, 26)),
    ], ids=['float', 'mixed', 'bytes', 'date'])
    def test_non_literal_option_items(self, option, wrong):
        """Only None/bool/int/str are literal items, float/bytes/date are not."""
        _assert_error(
            validate_option, option, 1,
            message=f'Value of "{wrong}" must be None/bool/int/str',
        )

    @pytest.mark.parametrize('value', [
        1.0,
        1.5,
        b'A',
        b'',
        [1, 2],
        {'key': 1},
        date(2026, 10, 26),
    ], ids=['float-int', 'float', 'bytes', 'empty-bytes', 'list', 'dict', 'date'])
    def test_default_value_must_be_a_literal_item(self, value):
        """A default value of another type is not dynamically coerced to an option."""
        _assert_error(
            validate_option, [1, 2], value,
            message=f'Value of "{value}" must be None/bool/int/str',
        )

    def test_default_value_is_checked_before_duplicates(self):
        """A wrong default value is reported before a duplicate option."""
        _assert_error(validate_option, ['A', 'A'], 'B', message='Default value "B" is not in "option"')


class TestValidateFilterOrder:
    @pytest.mark.parametrize('option', [
        1,
        1.5,
        True,
        None,
        'A',
        ('A', 'B'),
        {'A', 'B'},
        {'A': 1},
    ], ids=['int', 'float', 'bool', 'none', 'str', 'tuple', 'set', 'dict'])
    def test_option_must_be_a_list(self, option):
        _assert_error(validate_filter_order, option, (), message='"option" must be a list')

    def test_option_must_not_be_empty(self):
        _assert_error(validate_filter_order, [], (), message='"option" is empty')

    @pytest.mark.parametrize('option, duplicate', [
        (['A', 'A'], 'A'),
        (['A', 'B', 'A'], 'A'),
        ([1, 2, 1], 1),
        ([True, True], True),
        ([None, None], None),
    ], ids=['adjacent', 'str', 'int', 'bool', 'none'])
    def test_duplicate_option(self, option, duplicate):
        _assert_error(
            validate_filter_order, option, (),
            message=f'"option" has duplicate value "{duplicate}"',
        )

    @pytest.mark.parametrize('item', [
        {'key': 1},
        [1, 2],
        {1},
    ], ids=['dict', 'list', 'set'])
    def test_unhashable_option_item(self, item):
        """An unhashable item can not be a literal item, name the item in the error."""
        _assert_error(
            validate_filter_order, ['A', item], (),
            message=f'Value of "{item}" must be None/bool/int/str',
        )

    @pytest.mark.parametrize('option, wrong', [
        ([1.5], 1.5),
        ([1, 2.5], 2.5),
        ([b'A'], b'A'),
        ([1, date(2026, 10, 26)], date(2026, 10, 26)),
    ], ids=['float', 'mixed', 'bytes', 'date'])
    def test_non_literal_option_items(self, option, wrong):
        """Only None/bool/int/str are literal items, float/bytes/date are not."""
        _assert_error(
            validate_filter_order, option, (),
            message=f'Value of "{wrong}" must be None/bool/int/str',
        )

    @pytest.mark.parametrize('value', [
        'A',
        '',
        1,
        1.5,
        True,
        False,
        None,
        {'A'},
        {'A': 1},
    ], ids=['str', 'empty-str', 'int', 'float', 'true', 'false', 'none', 'set', 'dict'])
    def test_value_must_be_a_list_or_tuple(self, value):
        _assert_error(
            validate_filter_order, ['A', 'B'], value,
            message=f'Value of "filter-order" datatype must be a list, got "{value}"',
        )

    @pytest.mark.parametrize('option, value, missing', [
        (['A', 'B'], ('C',), 'C'),
        (['A', 'B'], ('A', 'C'), 'C'),
        ([1, 2], (3,), 3),
        ([1, 2], (True,), True),
        ([True], (1,), 1),
        ([None], ('None',), 'None'),
        (['A'], ['C'], 'C'),
    ], ids=['only', 'middle', 'int', 'bool-vs-int', 'int-vs-bool', 'none-vs-str', 'list-value'])
    def test_value_item_must_be_in_option(self, option, value, missing):
        _assert_error(
            validate_filter_order, option, value,
            message=f'Default value "{missing}" is not in "option"',
        )

    @pytest.mark.parametrize('item', [
        {'key': 1},
        [1, 2],
        {1},
    ], ids=['dict', 'list', 'set'])
    def test_unhashable_value_item(self, item):
        """An unhashable value item can not be looked up, name the item in the error."""
        _assert_error(
            validate_filter_order, ['A'], (item,),
            message=f'Value of "{item}" must be None/bool/int/str',
        )

    @pytest.mark.parametrize('item', [
        1.0,
        1.5,
        b'A',
        b'',
        date(2026, 10, 26),
    ], ids=['float-int', 'float', 'bytes', 'empty-bytes', 'date'])
    def test_non_literal_value_items(self, item):
        """A float/bytes/date value item is rejected by the literal type check."""
        _assert_error(
            validate_filter_order, ['A', 1, 2], (item,),
            message=f'Value of "{item}" must be None/bool/int/str',
        )

    @pytest.mark.parametrize('option, value, duplicate', [
        (['A', 'B'], ('A', 'A'), 'A'),
        (['A', 'B'], ['A', 'B', 'A'], 'A'),
        ([1, 2], (1, 2, 1), 1),
        ([True, False], (True, True), True),
        ([None, 'A'], (None, None), None),
    ], ids=['adjacent', 'str', 'int', 'bool', 'none'])
    def test_duplicate_value_item(self, option, value, duplicate):
        _assert_error(
            validate_filter_order, option, value,
            message=f'Default value has duplicate value "{duplicate}"',
        )

    @pytest.mark.parametrize('option, value', [
        (['A', 'B', 'C'], ('A', 'B', 'C')),
        (['A', 'B', 'C'], ('C', 'B', 'A')),
        (['A', 'B', 'C'], ('C', 'A')),
        (['A', 'B', 'C'], ['B']),
        (['A', 'B', 'C'], ()),
        (['A', 'B', 'C'], []),
        ([1, 2, 3], (3, 1)),
        ([1, 2, 3], [3, 2, 1]),
        ([True, False], (False,)),
        ([None, 'A', 0, ''], (None, 'A')),
        ([1, 'A', None], (None, 'A')),
    ], ids=[
        'full', 'reversed', 'partial-tuple', 'partial-list', 'empty-tuple', 'empty-list',
        'int', 'int-list', 'bool', 'mixed-literal', 'mixed-type',
    ])
    def test_valid_value(self, option, value):
        assert validate_filter_order(option, value) is None

    @pytest.mark.parametrize('option, message', [
        ('A', '"option" must be a list'),
        ([], '"option" is empty'),
        (['A', 'A'], '"option" has duplicate value "A"'),
    ], ids=['not-list', 'empty', 'duplicate'])
    def test_option_is_checked_before_value(self, option, message):
        """A malformed option is reported before the value type."""
        _assert_error(validate_filter_order, option, 'not-a-list-or-tuple', message=message)

    def test_missing_value_item_is_checked_before_duplicates(self):
        """A value item outside of "option" is reported before a duplicate value item."""
        _assert_error(
            validate_filter_order, ['A', 'B'], ('C', 'C'),
            message='Default value "C" is not in "option"',
        )
