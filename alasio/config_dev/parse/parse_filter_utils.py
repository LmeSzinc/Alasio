"""
Validators of the dt="filter-order" and dt="filter-regex" families

Kept apart from parse_arg_utils.py, which holds the literal items, the "option"
list and the range constraints of an arg: the three ordered filter datatypes and
the pattern decoded one share the separators, the item characters and the level
tables below, and nothing of that is used by the other datatypes.
"""

import re
from collections import Counter
from typing import Any, Dict, List, Union

import msgspec
from msgspec import UNSET, Struct, UnsetType
from typing_extensions import Annotated

from alasio.base.filter import FILTER_ORDER_SEPARATOR
from alasio.config_dev.parse.base import DefinitionError
from alasio.config_dev.parse.parse_arg_utils import _literal_key, validate_literal_item

# Define which "dt" is an ordered list of items, see validate_filter_order()
TYPE_ARG_FILTER_ORDER = frozenset((
    'filter-order', 'filter-order-item', 'filter-order-pair',
))

# Define which "dt" decodes its items with a "pattern", see validate_filter_regex()
TYPE_ARG_FILTER_REGEX = frozenset((
    'filter-regex',
))

# Separators of a dt="filter-order" value. An item is written
# "{item}-{option}", the items of a value are separated by ">" (`item-A > item-B`).
# FILTER_ORDER_SEPARATOR is defined in alasio.base.filter, it is shared with the
# runtime splitter of the item. A primary item of an arg with secondary options
# can not hold either of them, a secondary option can hold the level separator
# (the item is split on its first one, so "PlateT4-gun-x" is the item "PlateT4"
# with the option "gun-x") but never the list one.
FILTER_ORDER_LIST_SEPARATOR = '>'


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
    Expand "option" and "option_dict" into the legal items of a two level
    dt="filter-order-item" or dt="filter-order-pair"

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
    Validate "option_dict" of the two level filter-order datatypes

    "option_dict" maps a primary item to the secondary options it offers. A
    primary item with an entry must be used with one of them, an item without
    an entry has no secondary option at all. Both sides become part of a
    composite item, so they must be str; the character rules are checked here
    for the options and by _validate_filter_order_option() for the items.

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


def _validate_filter_order_option(option, secondary):
    """
    Validate the "option" list of a filter-order datatype

    The list separator is rejected for every filter-order: the value is split on
    it either way and a stray one would be read as a separator at runtime. The
    level separator is only a problem when the arg declares secondary options:
    an arg without them never splits an item, and "Fleet-1" is a legal item
    name there.

    Args:
        option (list): Option list of the arg
        secondary (bool): True when the arg declares "option_dict"

    Returns:
        set: Type aware keys of the items, see _literal_key()

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
        if type(item) is str:
            _validate_filter_order_item_chars(item, '"option" item', check_separator=secondary)
        counts[item] += 1
        option_keys.add(_literal_key(item))
    for item, count in counts.items():
        if count > 1:
            raise DefinitionError(f'"option" has duplicate value "{item}"')
    return option_keys


def _normalize_filter_order_value(value, dt='filter-order'):
    """
    Check the type of a filter-order default value

    Args:
        value (Any): Default value of the arg
        dt (str): Datatype, for the error message

    Returns:
        tuple: The value as a tuple

    Raises:
        DefinitionError:
    """
    if type(value) not in (list, tuple):
        raise DefinitionError(f'Value of "{dt}" datatype must be a list, got "{value}"')
    return tuple(value)


def _validate_filter_order_option_command(option_command, option):
    """
    Validate "option_command" of a filter-order datatype

    A command is an entry of the value that carries no item at all, like the
    "shortest" of a commission filter. It is written as itself, so it can not
    hold a separator, and it must not be an item of "option" either, otherwise
    a stored value would be ambiguous.

    Args:
        option_command (Any): The "option_command" as written in the yaml, None for none
        option (list): Primary items of the arg, already validated

    Returns:
        tuple[str]: The validated commands

    Raises:
        DefinitionError:
    """
    if option_command is None:
        return ()
    if not isinstance(option_command, list):
        raise DefinitionError('"option_command" must be a list')
    option_items = set(option)
    counts = Counter()
    for command in option_command:
        if type(command) is not str:
            raise DefinitionError(f'"option_command" item "{command}" must be a str')
        if not command:
            raise DefinitionError('"option_command" item must not be empty')
        if FILTER_ORDER_SEPARATOR in command or FILTER_ORDER_LIST_SEPARATOR in command:
            raise DefinitionError(
                f'"option_command" item "{command}" must not contain "{FILTER_ORDER_SEPARATOR}" '
                f'or "{FILTER_ORDER_LIST_SEPARATOR}"')
        if command in option_items:
            raise DefinitionError(f'"option_command" item "{command}" is in "option"')
        counts[command] += 1
    for command, count in counts.items():
        if count > 1:
            raise DefinitionError(f'"option_command" has duplicate item "{command}"')
    return tuple(option_command)


def _validate_filter_order_levels(value, option, option_dict, dt, unique, option_command=()):
    """
    Validate the default value of a two level filter-order datatype

    Args:
        value (Any): Default value of the arg
        option (list): Primary items of the arg, already validated
        option_dict (dict): Secondary options of each primary item, already
            validated
        dt (str): Datatype, for the error message
        unique (str): "item" when a primary item may be used once, "pair" when
            the whole "{item}-{option}" is the identity
        option_command (tuple): Entries that carry no item, see
            _validate_filter_order_option_command()

    Raises:
        DefinitionError:
    """
    value = _normalize_filter_order_value(value, dt)
    command_items = set(option_command)
    primary_items = set(option)
    used = Counter()
    for item in value:
        if type(item) is not str:
            raise DefinitionError(
                f'Value of "{dt}" datatype must be a str if "option_dict" is defined, '
                f'got "{item}"')
        if item in command_items:
            used[item] += 1
            continue
        primary, sep, secondary = item.partition(FILTER_ORDER_SEPARATOR)
        if not primary or (sep and not secondary):
            raise DefinitionError(
                f'Default value "{item}" is not a valid item, expected "item" or '
                f'"item{FILTER_ORDER_SEPARATOR}option"')
        if primary not in primary_items:
            raise DefinitionError(f'Default value "{item}" is not in "option"')
        options = option_dict.get(primary, None)
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
        used[primary if unique == 'item' else item] += 1
    for key, count in used.items():
        if count > 1:
            if unique == 'item':
                raise DefinitionError(f'Default value has duplicate item "{key}"')
            raise DefinitionError(f'Default value has duplicate value "{key}"')


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
    option_keys = _validate_filter_order_option(option, secondary=False)
    value = _normalize_filter_order_value(value)

    value_counts = Counter()
    for item in value:
        validate_literal_item(item)
        if _literal_key(item) not in option_keys:
            raise DefinitionError(f'Default value "{item}" is not in "option"')
        value_counts[item] += 1
    for item, count in value_counts.items():
        if count > 1:
            raise DefinitionError(f'Default value has duplicate value "{item}"')


def validate_filter_order_item(option: list, value: Any, option_dict: Any) -> None:
    """
    Validate dt="filter-order-item", a two level filter-order

    The primary item is the identity: it is used once, carrying one of its own
    secondary options as "{item}-{option}", or without one when it declares
    none. Two rows can not hold the same primary item, see
    validate_filter_order_pair() for the datatype that allows it.

    Args:
        option (list): Primary items of the arg
        value (Any): Default value of the arg
        option_dict (Any): Secondary options of each primary item

    Raises:
        DefinitionError:
    """
    _validate_filter_order_option(option, secondary=True)
    _validate_filter_order_option_dict(option, option_dict)
    _validate_filter_order_levels(
        value, option, option_dict, dt='filter-order-item', unique='item')


def validate_filter_order_pair(option: list, value: Any, option_dict: Any, option_command: Any = None) -> None:
    """
    Validate dt="filter-order-pair", a two level filter-order

    The whole "{item}-{option}" is the identity: several options of one primary
    item may coexist ("UrgentCube-1:30" and "UrgentCube-3" of the same shop
    family), each one appears once. Entries of "option_command" are written as
    themselves and take part in the uniqueness check.

    Args:
        option (list): Primary items of the arg
        value (Any): Default value of the arg
        option_dict (Any): Secondary options of each primary item
        option_command (Any): Entries that carry no item, None for none

    Raises:
        DefinitionError:
    """
    _validate_filter_order_option(option, secondary=True)
    _validate_filter_order_option_dict(option, option_dict)
    option_command = _validate_filter_order_option_command(option_command, option)
    _validate_filter_order_levels(
        value, option, option_dict, dt='filter-order-pair', unique='pair', option_command=option_command)


# A javascript named group "(?<name>" is "(?P<name>" in python. A lookbehind
# starts with "(?<=" or "(?<!", so the group name is what tells them apart
FILTER_REGEX_JS_GROUP = re.compile(r'\(\?<(?=[A-Za-z_])')


def to_python_pattern(pattern):
    """
    Convert the javascript named groups of a pattern back to python

    The pattern of a dt="filter-regex" is stored in the javascript syntax, so
    the frontend can decode the items with it; python spells a named group
    "(?P<name>...)". The two dialects are the same for the patterns a filter
    uses, so only the group syntax is converted.

    Args:
        pattern (str): Pattern in javascript syntax

    Returns:
        str: The same pattern in python syntax
    """
    return FILTER_REGEX_JS_GROUP.sub('(?P<', pattern)


def filter_item_pattern(pattern, option_command=()):
    """
    The regex every item of a dt="filter-regex" value has to match

    A stored item is a level predicate decoded by "pattern", or one of the
    commands of the arg, which carry no level. The generated model constrains
    every item with this regex, so a value the pattern can not read is reported
    when a config is loaded, instead of silently never matching.

    The regex is anchored: msgspec applies a pattern anywhere in the value, an
    unanchored one would accept "S9-XX-1" because its head "S9" matches.

    Args:
        pattern (str): "pattern" of the arg, in javascript syntax
        option_command (iterable[str]): Commands of the arg, they are literals

    Returns:
        str: The anchored python regex
    """
    alternatives = [to_python_pattern(pattern)]
    alternatives.extend(re.escape(command) for command in option_command or ())
    if len(alternatives) == 1:
        return f'^(?:{alternatives[0]})$'
    body = '|'.join(f'(?:{alternative})' for alternative in alternatives)
    return f'^(?:{body})$'


# A level name becomes the name of a regex group, so it is a plain identifier
FILTER_REGEX_LEVEL_NAME = r'^[A-Za-z_][A-Za-z0-9_]*$'


class FilterRegexEntry(Struct, omit_defaults=True, forbid_unknown_fields=True):
    """
    One entry of the candidate table of a dt="filter-regex" level

    The table of a level is a dict when its candidates depend on the levels
    before it: the key is the prefix written as a value of this arg, and the
    entry may declare its own "match" (a key of a different level count, e.g.
    "S9-E" next to "E"). The shape is checked by msgspec, the semantics by
    _validate_filter_regex_entries().
    """
    # Levels the entry is keyed by, absent for the match of the level
    option: List[str]
    match: Union[str, List[str], None, UnsetType] = UNSET


class FilterRegexLevel(Struct, omit_defaults=True, forbid_unknown_fields=True):
    """
    One level of a dt="filter-regex"

    "option" is a list when the candidates do not depend on the levels before
    this one (the whole level is one dropdown), and a dict of entries keyed by
    the prefix when they do. The shape is checked by msgspec, the semantics by
    _normalize_filter_regex_levels().
    """
    # The name is the name of a group of "pattern", so it has to be an identifier
    name: Annotated[str, msgspec.Meta(pattern=FILTER_REGEX_LEVEL_NAME)]
    option: Union[List[str], Dict[str, Union[List[str], FilterRegexEntry]]]
    # Levels the table is keyed by, absent for every level before this one
    match: Union[str, List[str], None, UnsetType] = UNSET


def _compile_filter_regex_pattern(pattern):
    """
    Compile "pattern" of a dt="filter-regex" and convert it for javascript

    The pattern is the decoder of a stored item: one named group per level, the
    whole item matched. The frontend decodes with the same pattern, and
    javascript spells a named group "(?<name>...)", so the python form is
    converted once here and the stored pattern is the javascript one.

    Args:
        pattern (Any): The "pattern" as written in the yaml

    Returns:
        tuple: (compiled python pattern, the same pattern for javascript)

    Raises:
        DefinitionError:
    """
    if type(pattern) is not str:
        raise DefinitionError('"pattern" must be a str')
    try:
        compiled = re.compile(pattern)
    except re.error as e:
        raise DefinitionError(f'"pattern" is not a valid regex, {e}')
    if not compiled.groupindex:
        raise DefinitionError('"pattern" must have named groups, one per level')
    js_pattern = pattern.replace('(?P<', '(?<')
    if '(?P' in js_pattern:
        raise DefinitionError(
            '"pattern" must decode by its named groups only, "(?P=name)" is not supported')
    # The generated model constrains the items with the python form of the
    # pattern, so the conversion has to be reversible
    if to_python_pattern(js_pattern) != pattern:
        raise DefinitionError('"pattern" contains a "(?<" that is not a named group')
    return compiled, js_pattern


def _decode_filter_regex(pattern, text):
    """
    Decode one item of a dt="filter-regex" into its level values

    Args:
        pattern (re.Pattern): Compiled "pattern"
        text (str): Item of the value

    Returns:
        dict[str, str]: {level name: value} of every group of the pattern, an
            unset group is an empty str; None when the pattern does not read
            the whole item
    """
    match = pattern.fullmatch(text)
    if match is None:
        return None
    return {name: value or '' for name, value in match.groupdict().items()}


def _resolve_filter_regex_match(level_name, match, ancestors):
    """
    Resolve the "match" of a level, or of one entry of its table

    "match" names the levels the candidate table is keyed by, so it may only
    name the levels before this one. None means the default of the level, the
    caller passes it in as ``ancestors``.

    Args:
        level_name (str): Name of the level, for the error message
        match (Any): "match" as written in the yaml, UNSET or None for the default
        ancestors (list[str]): Names of the levels before this one

    Returns:
        tuple[str]: The named levels, in the order of the levels

    Raises:
        DefinitionError:
    """
    if match is UNSET or match is None:
        return tuple(ancestors)
    if type(match) is str:
        match = [match]
    names = set()
    for name in match:
        if name not in ancestors:
            raise DefinitionError(f'Level "{level_name}" match "{name}" is not a previous level')
        if name in names:
            raise DefinitionError(f'Level "{level_name}" match has duplicate level "{name}"')
        names.add(name)
    return tuple(name for name in ancestors if name in names)


def _validate_filter_regex_candidates(level_name, candidates, pattern):
    """
    Validate the candidates of one entry of a level table

    A candidate is one value of this level and of no other level: it is written
    as itself, so decoding it has to give it back. The empty value is not a
    candidate, a level may always be left unset ("any").

    Args:
        level_name (str): Name of the level, for the error message
        candidates (list[str]): The candidate list, its type is checked by msgspec
        pattern (re.Pattern): Compiled "pattern"

    Returns:
        list[str]: The candidates, in the declared order

    Raises:
        DefinitionError:
    """
    values = []
    counts = Counter()
    for value in candidates:
        if not value:
            raise DefinitionError(
                f'Level "{level_name}" candidate must not be empty, '
                'a level that may be unset is always offered as "any"')
        if FILTER_ORDER_SEPARATOR in value or FILTER_ORDER_LIST_SEPARATOR in value:
            raise DefinitionError(
                f'Level "{level_name}" candidate "{value}" must not contain '
                f'"{FILTER_ORDER_SEPARATOR}" or "{FILTER_ORDER_LIST_SEPARATOR}"')
        decoded = _decode_filter_regex(pattern, value)
        if decoded is None or decoded.get(level_name, '') != value:
            raise DefinitionError(
                f'Level "{level_name}" candidate "{value}" is not decoded by "pattern" '
                f'as a "{level_name}" value')
        counts[value] += 1
        values.append(value)
    for value, count in counts.items():
        if count > 1:
            raise DefinitionError(f'Level "{level_name}" has duplicate candidate "{value}"')
    return values


def _validate_filter_regex_entries(level_name, match, option, pattern, names):
    """
    Validate the candidate table of one level

    The table is written as a list when the candidates do not depend on the
    levels before this one, and as a dict when they do: the key is the prefix
    written as a value of this arg, and the entry may declare its own "match"
    (a key of a different level count, e.g. "S9-E" next to "E"). The shape of
    the table is checked by msgspec, this checks the semantics.

    Args:
        level_name (str): Name of the level, for the error message
        match (tuple[str]): Default match of the level
        option (list[str] | dict): "option" of the level, already converted
        pattern (re.Pattern): Compiled "pattern"
        names (list[str]): Names of every level, in the declared order

    Returns:
        list[dict]: Entries, each {"match": tuple, "key": dict, "option": list}

    Raises:
        DefinitionError:
    """
    if isinstance(option, list):
        # The candidates of every prefix: the level is edited without looking at
        # the levels before it, so the table is the empty key
        return [{
            'match': (),
            'key': {},
            'option': _validate_filter_regex_candidates(level_name, option, pattern),
        }]
    if not option:
        raise DefinitionError(f'Level "{level_name}" option is empty')

    ancestors = names[:names.index(level_name)]
    entries = []
    for key, value in option.items():
        if isinstance(value, FilterRegexEntry):
            candidates = value.option
            # An entry that declares no match uses the match of its level, a
            # "match: null" says the same thing explicitly
            declared = value.match is not UNSET
            if value.match is UNSET or value.match is None:
                entry_match = match
            else:
                entry_match = _resolve_filter_regex_match(level_name, value.match, ancestors)
        else:
            candidates = value
            declared = False
            entry_match = match

        if key == '':
            # The key of every prefix is the fallback of the level: it is not
            # keyed by anything, whatever the level declares as its match
            if declared and entry_match:
                raise DefinitionError(
                    f'Level "{level_name}" key "" is the table of every prefix, it has no match')
            entry_match = ()
            decoded_key = {}
        else:
            decoded = _decode_filter_regex(pattern, key)
            if decoded is None:
                raise DefinitionError(
                    f'Level "{level_name}" key "{key}" is not decoded by "pattern"')
            decoded_key = {name: decoded[name] for name in names if decoded[name]}
            if set(decoded_key) != set(entry_match):
                raise DefinitionError(
                    f'Level "{level_name}" key "{key}" decodes to {sorted(decoded_key)}, '
                    f'but its match is {sorted(entry_match)}')
        entries.append({
            'match': entry_match,
            'key': decoded_key,
            'option': _validate_filter_regex_candidates(level_name, candidates, pattern),
        })

    # Two entries of the same key length must be keyed by the same levels,
    # otherwise the more specific one of them can not be chosen
    by_size = {}
    for entry in entries:
        size = len(entry['match'])
        previous = by_size.setdefault(size, entry['match'])
        if set(previous) != set(entry['match']):
            raise DefinitionError(
                f'Level "{level_name}" has two matches of {size} levels: '
                f'{", ".join(previous) or "empty"} and {", ".join(entry["match"]) or "empty"}')
    return entries


def _convert_filter_regex_levels(levels):
    """
    Check the shape of a "levels" list with msgspec

    The struct rejects an unknown key, a missing key and a wrong type, so only
    the semantics are left to the code below. A level is converted on its own so
    that an error names the level it belongs to.

    Args:
        levels (Any): "levels" as written in the yaml

    Returns:
        list[FilterRegexLevel]: The converted levels

    Raises:
        DefinitionError:
    """
    if not isinstance(levels, list):
        raise DefinitionError('"levels" must be a list')
    converted = []
    for index, level in enumerate(levels):
        try:
            converted.append(msgspec.convert(level, FilterRegexLevel))
        except msgspec.ValidationError as e:
            name = level.get('name', None) if isinstance(level, dict) else None
            where = f'Level "{name}"' if type(name) is str and name else f'"levels" item {index}'
            raise DefinitionError(f'{where} is invalid, {e}')
    return converted


def _normalize_filter_regex_levels(levels, pattern):
    """
    Validate "levels" of a dt="filter-regex" and normalize it for the frontend

    Args:
        levels (Any): "levels" as written in the yaml
        pattern (re.Pattern): Compiled "pattern"

    Returns:
        list[dict]: Levels, each {"name": str, "option": [entry, ...]}, the
            entry keys are decoded to level values so that the frontend looks a
            table up by comparing values, never by parsing a prefix

    Raises:
        DefinitionError:
    """
    levels = _convert_filter_regex_levels(levels)
    if not levels:
        raise DefinitionError('"levels" is empty')

    names = []
    for level in levels:
        if level.name in names:
            raise DefinitionError(f'"levels" has duplicate name "{level.name}"')
        names.append(level.name)
    groups = set(pattern.groupindex)
    if groups != set(names):
        raise DefinitionError(
            f'"pattern" named groups {sorted(groups)} must be the names of "levels" {names}')

    normalized = []
    for level in levels:
        name = level.name
        ancestors = names[:names.index(name)]
        match = _resolve_filter_regex_match(name, level.match, ancestors)
        entries = _validate_filter_regex_entries(name, match, level.option, pattern, names)
        normalized.append({
            'name': name,
            'option': [{'key': entry['key'], 'option': entry['option']} for entry in entries],
        })
    return normalized


def _validate_filter_regex_option_command(option_command, pattern):
    """
    Validate "option_command" of a dt="filter-regex"

    A command carries no level at all, like the "reset" of a research filter.
    It is written as itself, so it must not be readable by "pattern", otherwise
    a stored item would be ambiguous.

    Args:
        option_command (Any): The "option_command" as written in the yaml, None for none
        pattern (re.Pattern): Compiled "pattern"

    Returns:
        tuple[str]: The validated commands

    Raises:
        DefinitionError:
    """
    if option_command is None:
        return ()
    if not isinstance(option_command, list):
        raise DefinitionError('"option_command" must be a list')
    counts = Counter()
    for command in option_command:
        if type(command) is not str:
            raise DefinitionError(f'"option_command" item "{command}" must be a str')
        if not command:
            raise DefinitionError('"option_command" item must not be empty')
        if FILTER_ORDER_SEPARATOR in command or FILTER_ORDER_LIST_SEPARATOR in command:
            raise DefinitionError(
                f'"option_command" item "{command}" must not contain "{FILTER_ORDER_SEPARATOR}" '
                f'or "{FILTER_ORDER_LIST_SEPARATOR}"')
        if pattern.fullmatch(command):
            raise DefinitionError(
                f'"option_command" item "{command}" is read by "pattern", '
                'a command must not carry a level')
        counts[command] += 1
    for command, count in counts.items():
        if count > 1:
            raise DefinitionError(f'"option_command" has duplicate item "{command}"')
    return tuple(option_command)


def validate_filter_regex(levels: Any, pattern: Any, option_command: Any, value: Any):
    """
    Validate dt="filter-regex" and its default value

    The value is an ordered list of predicates: every item is decoded by
    "pattern" into one value per level, and two items may not decode to the same
    combination. A command of "option_command" carries no level and takes part in
    the uniqueness check as itself. A candidate table only has to keep the user
    from writing nonsense, a decoded value that is not a candidate is not an
    error: the predicate simply never matches.

    Args:
        levels (Any): "levels" as written in the yaml
        pattern (Any): "pattern" as written in the yaml
        option_command (Any): "option_command" as written in the yaml, None for none
        value (Any): Default value of the arg

    Returns:
        tuple: (normalized levels for the frontend, pattern in javascript syntax)

    Raises:
        DefinitionError:
    """
    compiled, js_pattern = _compile_filter_regex_pattern(pattern)
    normalized = _normalize_filter_regex_levels(levels, compiled)
    commands = _validate_filter_regex_option_command(option_command, compiled)
    # The generated model constrains every item with this regex, a pattern that
    # only compiles on its own would fail when the model file is imported
    try:
        re.compile(filter_item_pattern(js_pattern, commands))
    except re.error as e:
        raise DefinitionError(f'"pattern" can not be used as an item constraint, {e}')
    value = _normalize_filter_order_value(value, dt='filter-regex')

    names = [level['name'] for level in normalized]
    command_items = set(commands)
    used = {}
    for item in value:
        if type(item) is not str:
            raise DefinitionError(
                f'Value of "filter-regex" datatype must be a str, got "{item}"')
        if not item:
            raise DefinitionError('Value of "filter-regex" datatype must not hold an empty item')
        if item in command_items:
            key = item
        else:
            decoded = _decode_filter_regex(compiled, item)
            if decoded is None:
                raise DefinitionError(f'Default value "{item}" is not decoded by "pattern"')
            key = tuple(decoded[name] for name in names)
        if key in used:
            raise DefinitionError(f'Default value has duplicate value "{item}"')
        used[key] = item
    return normalized, js_pattern
