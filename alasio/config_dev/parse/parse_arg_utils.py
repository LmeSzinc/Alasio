"""
Validators of the "option" list, of its literal items and of the range
constraints of an arg

Kept next to parse_args.py instead of inside it, so the option semantics (the
scalar literal datatypes vs the ordered subset of dt="filter-order") and the
range semantics stay readable in one place.
"""

from collections import Counter
from typing import Any

from msgspec import UNSET

from alasio.base.filter import FILTER_ORDER_SEPARATOR
from alasio.config_dev.parse.base import DefinitionError

# Define which "dt" is literal type
# Example:
#   {'dt': 'select'} is a literal in python: Literal["option-A", "option-B"]
#   {'dt': 'filter-order'} is a tuple of the literal: Tuple[Literal["option-A", "option-B"], ...]
#     a "dt" in TYPE_ARG_TUPLE wraps the literal into a tuple
# The value of a literal datatype is an item of "option", it is never a number,
# so these datatypes have no range at all.
TYPE_ARG_LITERAL = frozenset((
    'static',
    'select', 'radio', 'secondary-select',
    'filter-order',
))

# Separators of a dt="filter-order" value. An item is written
# "{item}-{option}", the items of a value are separated by ">" (`item-A > item-B`).
# FILTER_ORDER_SEPARATOR is defined in alasio.base.filter, it is shared with the
# runtime splitter of the item. A primary item of an arg with secondary options
# can not hold either of them, a secondary option can hold the level separator
# (the item is split on its first one, so "PlateT4-gun-x" is the item "PlateT4"
# with the option "gun-x") but never the list one.
FILTER_ORDER_LIST_SEPARATOR = '>'

# Msgspec range constraints of an arg, in the order they are reported
# key: constraint key of ArgData, value: whether the boundary value itself is allowed
RANGE_CONSTRAINT = (
    ('gt', False),
    ('ge', True),
    ('lt', False),
    ('le', True),
)


def validate_literal_item(item: Any) -> None:
    """
    Validate an item of a literal datatype

    The items of "option" are python literal items. Only the types supported by
    msgspec's typing.Literal are accepted: None, bool, int and str. float and
    bytes are rejected, they are not supported by msgspec.

    Args:
        item (Any): Item to validate

    Raises:
        DefinitionError:
    """
    # type() is exact, so bool and int stay distinct even though bool is an int subclass
    if type(item) in (type(None), bool, int, str):
        return
    raise DefinitionError(f'Value of "{item}" must be None/bool/int/str')


def _literal_key(item):
    """
    Build a type aware key of a literal item

    The type is part of the key, because python considers True == 1, which
    would make a bool value match an int option.

    Args:
        item (Any): A validated literal item, hashable

    Returns:
        tuple: (type, value)
    """
    return type(item), item


def validate_option(option: list, value: Any) -> None:
    """
    Validate "option" and the default value of the scalar datatypes

    The option must be a list (a non-iterable one, e.g. `option: 1` written by
    mistake, can not even be traversed), the default value must be one of the
    options and no option may repeat. Items are python literal items, the type
    is part of the identity: `True` is not the int option `1`.

    Args:
        option (list): Option list of the arg
        value (Any): Default value of the arg

    Raises:
        DefinitionError:
    """
    if not isinstance(option, list):
        raise DefinitionError('"option" must be a list')
    if not option:
        raise DefinitionError('"option" is empty')

    counts = Counter()
    option_keys = set()
    for item in option:
        validate_literal_item(item)
        counts[item] += 1
        option_keys.add(_literal_key(item))

    validate_literal_item(value)
    if _literal_key(value) not in option_keys:
        raise DefinitionError(f'Default value "{value}" is not in "option"')
    for item, count in counts.items():
        if count > 1:
            raise DefinitionError(f'"option" has duplicate value "{item}"')


def _validate_filter_order_item_chars(item, where, check_separator=True):
    """
    Validate the characters of a dt="filter-order" item

    ">" separates the items of a value, "-" separates the levels of an item, so
    neither may appear where the parser would read it as a separator. A
    secondary option may contain the level separator (the split takes the first
    one only), a primary item may not. An arg without secondary options is never
    split, so its items may contain the level separator.

    Args:
        item (str): Primary item or secondary option to check
        where (str): Location of the item, for the error message
        check_separator (bool): True for a primary item of an arg that declares
            secondary options, it must not hold the level separator

    Raises:
        DefinitionError:
    """
    if FILTER_ORDER_LIST_SEPARATOR in item:
        raise DefinitionError(
            f'{where} "{item}" must not contain "{FILTER_ORDER_LIST_SEPARATOR}", '
            'it separates the items of the value')
    if check_separator and FILTER_ORDER_SEPARATOR in item:
        raise DefinitionError(
            f'{where} "{item}" must not contain "{FILTER_ORDER_SEPARATOR}", '
            'it separates the secondary option')


def expand_filter_order_items(option, option_dict=None):
    """
    Expand "option" and "option_dict" into the legal items of dt="filter-order"

    An item without a secondary option stays itself, an item with one becomes
    every "{item}-{option}" combination. The expanded items are the literal of
    the generated model, so msgspec checks that a stored value holds a primary
    item with one of its own secondary options, not just two legal parts.

    Args:
        option (list): Primary items of the arg
        option_dict (dict): Secondary options of each primary item, None or an
            empty dict means no item has a secondary option

    Returns:
        list[str]: Legal items, in "option" order, the secondary options of an
            item following it
    """
    option_dict = option_dict or {}
    items = []
    for item in option:
        options = option_dict.get(item, None)
        if options:
            items.extend(f'{item}{FILTER_ORDER_SEPARATOR}{o}' for o in options)
        else:
            items.append(item)
    return items


def _validate_filter_order_option_dict(option, option_dict):
    """
    Validate "option_dict" of dt="filter-order"

    "option_dict" maps a primary item to the secondary options it offers. A
    primary item with an entry must be used with one of them, an item without
    an entry has no secondary option at all. Both sides become part of a
    composite item, so they must be str; the character rules are checked by
    validate_filter_order() for the items and here for the options.

    Args:
        option (list): Primary items of the arg, already validated
        option_dict (Any): The "option_dict" as written in the yaml

    Raises:
        DefinitionError:
    """
    if not isinstance(option_dict, dict):
        raise DefinitionError('"option_dict" must be a dict')
    primary_items = set()
    for item in option:
        if type(item) is not str:
            raise DefinitionError(
                f'"option" item "{item}" must be a str if "option_dict" is defined')
        primary_items.add(item)

    for primary, options in option_dict.items():
        if type(primary) is not str:
            raise DefinitionError(f'"option_dict" key "{primary}" must be a str')
        if primary not in primary_items:
            raise DefinitionError(f'"option_dict" key "{primary}" is not in "option"')
        if not isinstance(options, list):
            raise DefinitionError(f'"option_dict" value of "{primary}" must be a list')
        if not options:
            raise DefinitionError(f'"option_dict" value of "{primary}" is empty')
        counts = Counter()
        for option_item in options:
            if type(option_item) is not str:
                raise DefinitionError(f'"option_dict" item "{option_item}" must be a str')
            _validate_filter_order_item_chars(
                option_item, f'"option_dict" item of "{primary}"', check_separator=False)
            counts[option_item] += 1
        for option_item, count in counts.items():
            if count > 1:
                raise DefinitionError(
                    f'"option_dict" value of "{primary}" has duplicate item "{option_item}"')


def validate_filter_order(option: list, value: Any, option_dict: Any = None) -> None:
    """
    Validate "option", "option_dict" and the default value of dt="filter-order"

    "option" is the universe of items the frontend editor can add, the value
    is an ordered subset of it: every item of the value must be defined in
    "option", and neither "option" nor the value may repeat an item.
    The items are python literal items, like the options of the other datatypes,
    the type is part of the identity: `True` is not the int option `1`.

    When "option_dict" is given, every primary item declares its own secondary
    options and an item of the value is a composite "{item}-{option}", see
    expand_filter_order_items(). A primary item may then be used once, with one
    of its own secondary options or without one when it declares none.

    Args:
        option (list): Option list of the arg
        value (Any): Default value of the arg, a tuple of items
        option_dict (Any): Secondary options of each item, None or an empty
            dict for a filter-order without secondary options

    Raises:
        DefinitionError:
    """
    if not isinstance(option, list):
        raise DefinitionError('"option" must be a list')
    if not option:
        raise DefinitionError('"option" is empty')

    counts = Counter()
    option_keys = set()
    for item in option:
        validate_literal_item(item)
        # The list separator is rejected for every filter-order, the value is
        # split on it either way and a stray one would be read as a separator at
        # runtime. The level separator is only a problem when the arg declares
        # secondary options: an arg without them never splits an item, and
        # "Fleet-1" is a legal item name there
        if type(item) is str:
            _validate_filter_order_item_chars(
                item, '"option" item', check_separator=bool(option_dict))
        counts[item] += 1
        option_keys.add(_literal_key(item))
    for item, count in counts.items():
        if count > 1:
            raise DefinitionError(f'"option" has duplicate value "{item}"')

    if type(value) not in (list, tuple):
        raise DefinitionError(f'Value of "filter-order" datatype must be a list, got "{value}"')

    if not option_dict:
        # flat items, no secondary options
        value_counts = Counter()
        for item in value:
            validate_literal_item(item)
            if _literal_key(item) not in option_keys:
                raise DefinitionError(f'Default value "{item}" is not in "option"')
            value_counts[item] += 1
        for item, count in value_counts.items():
            if count > 1:
                raise DefinitionError(f'Default value has duplicate value "{item}"')
        return

    _validate_filter_order_option_dict(option, option_dict)
    primary_items = set(option)
    secondary_options = {item: options for item, options in option_dict.items()}
    used = Counter()
    for item in value:
        if type(item) is not str:
            raise DefinitionError(
                f'Value of "filter-order" datatype must be a str if "option_dict" is defined, '
                f'got "{item}"')
        primary, sep, secondary = item.partition(FILTER_ORDER_SEPARATOR)
        if not primary or (sep and not secondary):
            raise DefinitionError(
                f'Default value "{item}" is not a valid item, expected "item" or '
                f'"item{FILTER_ORDER_SEPARATOR}option"')
        if primary not in primary_items:
            raise DefinitionError(f'Default value "{item}" is not in "option"')
        options = secondary_options.get(primary, None)
        if options:
            if not sep:
                raise DefinitionError(
                    f'Default value "{primary}" must specify its secondary option')
            if secondary not in options:
                raise DefinitionError(
                    f'Default value "{item}" is not a valid item of "{primary}"')
        elif sep:
            raise DefinitionError(
                f'Default value "{item}" is not a valid item of "{primary}"')
        used[primary] += 1
    for primary, count in used.items():
        if count > 1:
            raise DefinitionError(f'Default value has duplicate item "{primary}"')


def _iter_range_constraint(arg):
    """
    Iterate over the range constraints set on an arg

    Args:
        arg (ArgData): Arg to inspect

    Yields:
        tuple: (key, bound, inclusive) of each set constraint, in
            RANGE_CONSTRAINT order
    """
    for key, inclusive in RANGE_CONSTRAINT:
        bound = getattr(arg, key)
        if bound is not UNSET:
            yield key, bound, inclusive


def has_range_constraint(arg):
    """
    Check whether an arg sets any range constraint

    Args:
        arg (ArgData): Arg to inspect

    Returns:
        bool: True if gt / ge / lt / le is set
    """
    return any(getattr(arg, key) is not UNSET for key, _ in RANGE_CONSTRAINT)


def validate_range_default(arg):
    """
    Validate the range constraints of an arg and its default value

    The constraints are plain fields of ArgData, msgspec applies them to the
    generated model annotation only, never to the default value, so an out of
    range default would be generated into the model and only fail when a config
    is loaded. A literal datatype has no numeric range at all, its value is an
    item of "option", see validate_option().

    Args:
        arg (ArgData): Arg to validate

    Raises:
        DefinitionError: A literal datatype has range constraints, a constraint
            is set on a non numeric value, the constraints exclude each other,
            or the default value is out of range
    """
    constraints = list(_iter_range_constraint(arg))
    if not constraints:
        return
    if arg.dt in TYPE_ARG_LITERAL:
        key = constraints[0][0]
        raise DefinitionError(
            f'datatype "{arg.dt}" is a literal, it must not have range constraint "{key}"')
    # Range constraints are numeric, a str / bool / tuple / datetime value can
    # not be compared with a bound
    value = arg.value
    if type(value) not in (int, float):
        key = constraints[0][0]
        raise DefinitionError(
            f'Range constraint "{key}" requires a numeric default value, got {value!r}')

    lower = [item for item in constraints if item[0] in ('gt', 'ge')]
    upper = [item for item in constraints if item[0] in ('lt', 'le')]
    # The largest lower bound and the smallest upper bound are the effective
    # ones, an exclusive bound wins a tie because it excludes the boundary value
    lower = max(lower, key=lambda item: (item[1], item[0] == 'gt')) if lower else None
    upper = min(upper, key=lambda item: (item[1], item[0] == 'le')) if upper else None

    if lower is not None and upper is not None:
        low_key, low, low_inclusive = lower
        high_key, high, high_inclusive = upper
        if low > high or (low == high and not (low_inclusive and high_inclusive)):
            raise DefinitionError(
                f'Range is empty: {low_key}={low!r} and {high_key}={high!r}')

    # The bounds do not exclude each other, so a value out of range violates
    # exactly one side
    violated = None
    if lower is not None:
        key, bound, inclusive = lower
        if value < bound or (value == bound and not inclusive):
            violated = (key, bound)
    if violated is None and upper is not None:
        key, bound, inclusive = upper
        if value > bound or (value == bound and not inclusive):
            violated = (key, bound)
    if violated is not None:
        key, bound = violated
        raise DefinitionError(f'Default value {value!r} violates {key}={bound!r}')
