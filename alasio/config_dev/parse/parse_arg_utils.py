"""
Validators of the "option" list and of its literal items

Kept next to parse_args.py instead of inside it, so the option semantics (the
scalar literal datatypes vs the ordered subset of dt="filter-order") stay
readable in one place.
"""

from collections import Counter
from typing import Any

from alasio.config_dev.parse.base import DefinitionError


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


def validate_filter_order(option: list, value: Any) -> None:
    """
    Validate "option" and the default value of dt="filter-order"

    "option" is the universe of items the frontend editor can add, the value
    is an ordered subset of it: every item of the value must be defined in
    "option", and neither "option" nor the value may repeat an item.
    The items are python literal items, like the options of the other datatypes,
    the type is part of the identity: `True` is not the int option `1`.

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

    counts = Counter()
    option_keys = set()
    for item in option:
        validate_literal_item(item)
        counts[item] += 1
        option_keys.add(_literal_key(item))
    for item, count in counts.items():
        if count > 1:
            raise DefinitionError(f'"option" has duplicate value "{item}"')

    if type(value) not in (list, tuple):
        raise DefinitionError(f'Value of "filter-order" datatype must be a list, got "{value}"')
    value_counts = Counter()
    for item in value:
        validate_literal_item(item)
        if _literal_key(item) not in option_keys:
            raise DefinitionError(f'Default value "{item}" is not in "option"')
        value_counts[item] += 1
    for item, count in value_counts.items():
        if count > 1:
            raise DefinitionError(f'Default value has duplicate value "{item}"')
