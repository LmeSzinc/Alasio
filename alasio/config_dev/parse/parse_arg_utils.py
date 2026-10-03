"""
Validators of the "option" list of the args

Kept next to parse_args.py instead of inside it, so the option semantics (the
scalar literal datatypes vs the ordered subset of dt="filter-order") stay
readable in one place.
"""

from collections import Counter
from typing import Any

from alasio.config_dev.parse.base import DefinitionError


def _count_items(items) -> Counter:
    """
    Count the items of an option list or of a default value

    The items of an option list are python literal items; an unhashable item
    (e.g. a dict or a list written by mistake) can not even be counted, a bare
    Counter() would raise a TypeError

    Args:
        items: Items to count

    Returns:
        Counter: Occurrence count of the items

    Raises:
        DefinitionError:
    """
    counts = Counter()
    for item in items:
        try:
            counts[item] += 1
        except TypeError:
            raise DefinitionError(f'Value of "{item}" must be bool/str/int/float/bytes/None') from None
    return counts


def validate_option(option: list, value: Any) -> None:
    """
    Validate "option" and the default value of the scalar datatypes

    The option must be a list (a non-iterable one, e.g. `option: 1` written by
    mistake, can not even be traversed), the default value must be one of the
    options and no option may repeat.

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
    if value not in option:
        raise DefinitionError(f'Default value "{value}" is not in "option"')
    counts = _count_items(option)
    for _option, _count in counts.items():
        if _count > 1:
            raise DefinitionError(f'"option" has duplicate value "{_option}"')


def validate_filter_order(option: list, value: Any) -> None:
    """
    Validate "option" and the default value of dt="filter-order"

    "option" is the universe of items the frontend editor can add, the value
    is an ordered subset of it: every item of the value must be defined in
    "option", and neither "option" nor the value may repeat an item.
    The items are python literal items, like the options of the other datatypes.

    Args:
        option (list): Option list of the arg
        value (Any): Default value of the arg, a tuple of items

    Raises:
        DefinitionError:
    """
    if not isinstance(option, list):
        raise DefinitionError('"option" must be a list')
    if not option:
        raise DefinitionError('"option" is empty')
    counts = _count_items(option)
    for item, count in counts.items():
        if count > 1:
            raise DefinitionError(f'"option" has duplicate value "{item}"')
    if type(value) not in (list, tuple):
        raise DefinitionError(f'Value of "filter-order" datatype must be a list, got "{value}"')
    for item in value:
        # the lookup hashes the item, an unhashable item can not be a value of
        # the datatype and would raise a bare TypeError
        try:
            in_option = item in counts
        except TypeError:
            raise DefinitionError(f'Value of "{item}" must be bool/str/int/float/bytes/None') from None
        if not in_option:
            raise DefinitionError(f'Default value "{item}" is not in "option"')
    value_counts = _count_items(value)
    for item, count in value_counts.items():
        if count > 1:
            raise DefinitionError(f'Default value has duplicate value "{item}"')
