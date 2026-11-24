"""
dt="filter-regex": a multi level filter decoded by "pattern".

"levels" declares one candidate table per level, "pattern" reads a stored item
back into one value per level. The value is an ordered list of predicates, the
order is the priority order. Two items may not decode to the same combination;
a command of "option_command" carries no level and is written as itself.
"""

import re

import pytest

from alasio.config_dev.parse.base import DefinitionError
from tests.config_dev.parse.parse_args.helpers import parse_arg

# Three levels of a research filter: the series, the family (genre, rarity or a
# ship) and the detail (a duration, or the number of a project). The pattern is
# the one of the game, the named groups are the level names.
RESEARCH_PATTERN = r'(?P<series>S[1-9])?-?(?P<family>DR|PRY|agir|[BCDEGHQT])?-?(?P<detail>\d{3}|\d\.\d|\d\d?)?'

RESEARCH_LEVELS = """\
  - name: series
    option: [S8, S9]
  - name: family
    option:
      '': [B, C, D, Q]
      S9: [B, C, D, Q, agir]
  - name: detail
    match: family
    option:
      '': ['0.5', '1', '2']
      B: ['4']
      Q: ['0.5', '1', '2', '4']
      S9-E: {match: [series, family], option: ['2', '031', '315']}
"""


def research_yaml(value='S9-DR-0.5', extra='', levels=RESEARCH_LEVELS, pattern=RESEARCH_PATTERN):
    """
    Build the yaml of a small research filter.

    Args:
        value (str): Default value, written "a > b"
        extra (str): Extra arg lines, e.g. an "option_command"
        levels (str): The "levels" block, indented as the child of "levels"
        pattern (str): The "pattern" of the arg

    Returns:
        str: Yaml document of the arg
    """
    return f"""\
dt: filter-regex
value: {value}
pattern: '{pattern}'
levels:
{levels}{extra}"""


# Patterns of the reduced level lists, the named groups are the level names
SERIES_PATTERN = r'(?P<series>S[1-9])'
SERIES_FAMILY_PATTERN = r'(?P<series>S[1-9])?-?(?P<family>[BC])?'
FAMILY_PATTERN = r'(?P<family>DR|PRY|agir|[BCDEGHQT])'


class TestFilterRegex:
    def test_value_is_split(self):
        arg = parse_arg(research_yaml(value='>-\n  S9-DR-0.5 > S9-E-315 > Q-1 > 0.5'))
        # a ">-" block scalar keeps the newline, the splitter strips it
        assert arg.dt == 'filter-regex'
        assert arg.value == ('S9-DR-0.5', 'S9-E-315', 'Q-1', '0.5')
        assert arg.layout == 'vert'

    def test_list_value_is_normalized_to_tuple(self):
        arg = parse_arg(research_yaml(value="['0.5', 'Q-1']"))
        assert arg.value == ('0.5', 'Q-1')

    def test_empty_value(self):
        arg = parse_arg(research_yaml(value='[]'))
        assert arg.value == ()

    def test_pattern_is_not_a_meta_constraint_on_the_tuple(self):
        """msgspec can not set "pattern" on a tuple, so it is not emitted as a
        Meta of the value; the pattern of the arg stays the decoder of the
        frontend, in javascript named group syntax."""
        arg = parse_arg(research_yaml())
        assert arg.pattern.startswith('(?<series>')
        assert arg.get_meta() == ''

    def test_annotation_constrains_every_item(self):
        """The pattern reaches the annotation as a constraint of the item type,
        anchored, plus the commands which carry no level."""
        arg = parse_arg(research_yaml(extra='option_command: [reset]\n'))
        assert arg.get_item_pattern() == (
            '^(?:(?:(?P<series>S[1-9])?-?(?P<family>DR|PRY|agir|[BCDEGHQT])?-?'
            '(?P<detail>\\d{3}|\\d\\.\\d|\\d\\d?)?)|(?:reset))$'
        )
        assert arg.get_anno(pattern_ref='PATTERN_Group_Arg') == (
            't.Tuple[e.Annotated[str, m.Meta(pattern=PATTERN_Group_Arg)], ...]'
        )
        # Without a reference the pattern is inlined
        pattern = arg.get_item_pattern()
        assert arg.get_python_type() == (
            f't.Tuple[e.Annotated[str, m.Meta(pattern={pattern!r})], ...]'
        )

    def test_item_pattern_without_commands(self):
        """Without a command the anchored pattern is the decoder alone."""
        arg = parse_arg(research_yaml())
        assert arg.get_item_pattern() == (
            '^(?:(?P<series>S[1-9])?-?(?P<family>DR|PRY|agir|[BCDEGHQT])?-?'
            '(?P<detail>\\d{3}|\\d\\.\\d|\\d\\d?)?)$'
        )

    def test_item_pattern_is_a_valid_regex(self):
        arg = parse_arg(research_yaml(extra='option_command: [shortest, first]\n'))
        compiled = re.compile(arg.get_item_pattern())
        assert compiled.fullmatch('S9-E-315')
        assert compiled.fullmatch('0.5')
        assert compiled.fullmatch('shortest')
        assert compiled.fullmatch('first')
        assert compiled.fullmatch('S9-XX-1') is None
        assert compiled.fullmatch('S9-E-315x') is None

    def test_pattern_is_converted_to_javascript_named_groups(self):
        arg = parse_arg(research_yaml())
        assert arg.pattern == RESEARCH_PATTERN.replace('(?P<', '(?<')

    def test_levels_are_normalized(self):
        """A level table is decoded once, the frontend looks it up by values."""
        arg = parse_arg(research_yaml())
        assert arg.levels == [
            {'name': 'series', 'option': [{'key': {}, 'option': ['S8', 'S9']}]},
            {'name': 'family', 'option': [
                {'key': {}, 'option': ['B', 'C', 'D', 'Q']},
                {'key': {'series': 'S9'}, 'option': ['B', 'C', 'D', 'Q', 'agir']},
            ]},
            {'name': 'detail', 'option': [
                {'key': {}, 'option': ['0.5', '1', '2']},
                {'key': {'family': 'B'}, 'option': ['4']},
                {'key': {'family': 'Q'}, 'option': ['0.5', '1', '2', '4']},
                {'key': {'series': 'S9', 'family': 'E'}, 'option': ['2', '031', '315']},
            ]},
        ]

    def test_level_match_default_is_all_previous_levels(self):
        """A key that does not declare its match is keyed by every previous
        level, so the full prefix is written."""
        levels = """\
  - name: series
    option: [S8, S9]
  - name: family
    match: series
    option:
      S9: [B, C]
  - name: detail
    option:
      S9-C: ['1', '2']
"""
        arg = parse_arg(research_yaml(value='S9-C-1', levels=levels))
        assert arg.levels[2]['option'] == [
            {'key': {'series': 'S9', 'family': 'C'}, 'option': ['1', '2']},
        ]

    def test_null_match_uses_the_level_match(self):
        levels = """\
  - name: series
    option: [S8, S9]
  - name: family
    option: [B, C]
  - name: detail
    match: family
    option:
      B: {match: null, option: ['4']}
"""
        arg = parse_arg(research_yaml(value='B-4', levels=levels))
        assert arg.levels[2]['option'] == [{'key': {'family': 'B'}, 'option': ['4']}]

    def test_alas_style_value_is_accepted(self):
        """A value without the separators decodes to the same levels."""
        arg = parse_arg(research_yaml(value='S9-DR0.5 > S9-E-315'))
        assert arg.value == ('S9-DR0.5', 'S9-E-315')

    def test_option_command(self):
        arg = parse_arg(research_yaml(value='S9-DR-0.5 > reset', extra='option_command: [reset]\n'))
        assert arg.value == ('S9-DR-0.5', 'reset')
        assert arg.option_command == ['reset']

    def test_empty_option_command_is_dropped(self):
        arg = parse_arg(research_yaml(extra='option_command: []\n'))
        assert 'option_command' not in arg.to_dict()

    def test_level_names_are_kept_in_the_declared_order(self):
        """The pattern group order is the order of the regex, the level order is
        the order of "levels", the key dict follows the level order."""
        levels = """\
  - name: series
    option: [S8, S9]
  - name: family
    option:
      '': [B, C]
      S9: [B, C]
"""
        arg = parse_arg(research_yaml(
            value='S9-B', levels=levels, pattern=SERIES_FAMILY_PATTERN))
        assert [level['name'] for level in arg.levels] == ['series', 'family']
        assert arg.levels[1]['option'][1]['key'] == {'series': 'S9'}


class TestFilterRegexValidate:
    def test_levels_required(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg(f"""\
dt: filter-regex
value: []
pattern: '{RESEARCH_PATTERN}'
""")
        assert e.value.msg == 'datatype "filter-regex" must have "levels" defined'

    def test_pattern_required(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg(f"""\
dt: filter-regex
value: []
levels:
{RESEARCH_LEVELS}""")
        assert e.value.msg == 'datatype "filter-regex" must have "pattern" defined'

    def test_option_is_rejected(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml() + 'option: [A]\n')
        assert e.value.msg == (
            'datatype "filter-regex" must not have "option" defined, declare the candidates in "levels"')

    def test_option_dict_is_rejected(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml() + 'option_dict:\n  A: [B]\n')
        assert e.value.msg == (
            'datatype "filter-regex" must not have "option_dict" defined, '
            'declare the candidates in "levels"')

    def test_levels_must_be_a_list(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg(f"""\
dt: filter-regex
value: []
pattern: '{RESEARCH_PATTERN}'
levels: A
""")
        assert e.value.msg == '"levels" must be a list'

    def test_levels_must_not_be_empty(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg(f"""\
dt: filter-regex
value: []
pattern: '{RESEARCH_PATTERN}'
levels: []
""")
        assert e.value.msg == '"levels" is empty'

    def test_level_must_be_a_dict(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg(f"""\
dt: filter-regex
value: []
pattern: '{RESEARCH_PATTERN}'
levels: [A]
""")
        assert e.value.msg.startswith('"levels" item 0 is invalid, Expected `object`')

    def test_unknown_level_key(self):
        levels = """\
  - name: series
    width: 15
    option: [S9]
"""
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value='', levels=levels))
        assert e.value.msg == 'Level "series" is invalid, Object contains unknown field `width`'

    def test_level_name_required(self):
        levels = """\
  - option: [S9]
"""
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value='', levels=levels))
        assert e.value.msg.startswith('"levels" item 0 is invalid, Object missing required field `name`')

    def test_level_name_must_be_a_str(self):
        levels = """\
  - name: [S9]
    option: [S9]
"""
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value='', levels=levels))
        assert e.value.msg.startswith('"levels" item 0 is invalid, Expected `str`')

    def test_level_name_must_be_an_identifier(self):
        levels = """\
  - name: 9series
    option: [S9]
"""
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value='', levels=levels))
        assert e.value.msg.startswith('Level "9series" is invalid, Expected `str` matching regex')

    def test_duplicate_level_name(self):
        levels = """\
  - name: series
    option: [S9]
  - name: series
    option: [S8]
"""
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value='', levels=levels))
        assert e.value.msg == '"levels" has duplicate name "series"'

    def test_pattern_groups_must_be_the_level_names(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value='', levels="""\
  - name: series
    option: [S9]
"""))
        assert e.value.msg == (
            "\"pattern\" named groups ['detail', 'family', 'series'] "
            "must be the names of \"levels\" ['series']")

    def test_pattern_must_be_a_str(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-regex
value: []
pattern: 1
levels:
  - name: series
    option: [S9]
""")
        assert e.value.msg == '"pattern" must be a str'

    def test_pattern_must_compile(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-regex
value: []
pattern: '(?<series>S9'
levels:
  - name: series
    option: [S9]
""")
        assert e.value.msg.startswith('"pattern" is not a valid regex')

    def test_pattern_must_have_named_groups(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-regex
value: []
pattern: 'S9'
levels:
  - name: series
    option: [S9]
""")
        assert e.value.msg == '"pattern" must have named groups, one per level'

    def test_pattern_backreference_is_rejected(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-regex
value: []
pattern: '(?P<series>S9)(?P=series)'
levels:
  - name: series
    option: [S9]
""")
        assert e.value.msg == (
            '"pattern" must decode by its named groups only, "(?P=name)" is not supported')

    def test_level_option_required(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value='', levels="""\
  - name: series
""", pattern=SERIES_PATTERN))
        assert e.value.msg == 'Level "series" is invalid, Object missing required field `option`'

    def test_level_option_must_be_a_list_or_a_dict(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value='', levels="""\
  - name: series
    option: S9
""", pattern=SERIES_PATTERN))
        assert e.value.msg.startswith(
            'Level "series" is invalid, Expected `object | array`')

    def test_level_option_must_not_be_empty(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value='', levels="""\
  - name: series
    option: {}
""", pattern=SERIES_PATTERN))
        assert e.value.msg == 'Level "series" option is empty'

    @pytest.mark.parametrize('match, message', [
        ('family', 'Level "series" match "family" is not a previous level'),
        ('series', 'Level "series" match "series" is not a previous level'),
        ('[series, series]', 'Level "series" match "series" is not a previous level'),
    ], ids=['unknown', 'self', 'duplicate'])
    def test_invalid_level_match(self, match, message):
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value='', levels=f"""\
  - name: series
    match: {match}
    option: [S9]
""", pattern=SERIES_PATTERN))
        assert e.value.msg == message

    @pytest.mark.parametrize('match, expected', [
        ('1', 'Level "series" is invalid, Expected `str | array | null`, got `int`'),
        ('[1]', 'Level "series" is invalid, Expected `str`, got `int`'),
    ], ids=['not-a-list', 'item-not-str'])
    def test_level_match_shape_is_checked_by_msgspec(self, match, expected):
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value='', levels=f"""\
  - name: series
    match: {match}
    option: [S9]
""", pattern=SERIES_PATTERN))
        assert e.value.msg.startswith(expected)

    def test_duplicate_match_level(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value='', levels="""\
  - name: series
    option: [S9]
  - name: family
    match: [series, series]
    option: [B]
""", pattern=SERIES_FAMILY_PATTERN))
        assert e.value.msg == 'Level "family" match has duplicate level "series"'

    def test_unknown_entry_key(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value='', levels="""\
  - name: series
    option: [S9]
  - name: family
    option:
      S9: {match: series, option: [B], width: 10}
""", pattern=SERIES_FAMILY_PATTERN))
        assert e.value.msg.startswith('Level "family" is invalid, Object contains unknown field `width`')

    def test_entry_option_required(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value='', levels="""\
  - name: series
    option: [S9]
  - name: family
    option:
      S9: {match: series}
""", pattern=SERIES_FAMILY_PATTERN))
        assert e.value.msg.startswith(
            'Level "family" is invalid, Object missing required field `option`')

    def test_key_must_be_decoded(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value='', levels="""\
  - name: series
    option: [S9]
  - name: family
    option:
      'nope': [B]
""", pattern=SERIES_FAMILY_PATTERN))
        assert e.value.msg == 'Level "family" key "nope" is not decoded by "pattern"'

    def test_key_must_decode_to_its_match(self):
        """The key "D" is the family "D", so it has to declare "match: family":
        the default of the level is every previous level."""
        levels = """\
  - name: series
    option: [S8, S9]
  - name: family
    option: [B, C]
  - name: detail
    option:
      D: ['4']
"""
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value='', levels=levels))
        assert e.value.msg == (
            'Level "detail" key "D" decodes to [\'family\'], but its match is '
            "['family', 'series']")

    def test_empty_key_must_not_declare_a_match(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value='', levels="""\
  - name: series
    option: [S9]
  - name: family
    option:
      '': {match: series, option: [B]}
""", pattern=SERIES_FAMILY_PATTERN))
        assert e.value.msg == 'Level "family" key "" is the table of every prefix, it has no match'

    def test_two_matches_of_the_same_length(self):
        """Two entries keyed by the same number of levels can not be ordered,
        because both of them could be the one meant."""
        levels = """\
  - name: series
    option: [S9]
  - name: family
    option: [B, C]
  - name: detail
    option:
      B: {match: family, option: ['4']}
      S9: {match: series, option: ['2']}
"""
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value='', levels=levels))
        assert e.value.msg == 'Level "detail" has two matches of 1 levels: family and series'

    def test_duplicate_candidate(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value='', levels="""\
  - name: series
    option: [S8, S8]
""", pattern=SERIES_PATTERN))
        assert e.value.msg == 'Level "series" has duplicate candidate "S8"'

    def test_empty_candidate(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value='', levels="""\
  - name: series
    option: ['', S9]
""", pattern=SERIES_PATTERN))
        assert e.value.msg == (
            'Level "series" candidate must not be empty, '
            'a level that may be unset is always offered as "any"')

    def test_candidate_must_be_a_str(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value='', levels="""\
  - name: series
    option: [9]
""", pattern=SERIES_PATTERN))
        assert e.value.msg.startswith('Level "series" is invalid, Expected `str`, got `int`')

    def test_candidate_must_not_hold_a_separator(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value='', levels="""\
  - name: series
    option: ['S9-x']
""", pattern=SERIES_PATTERN))
        assert e.value.msg == (
            'Level "series" candidate "S9-x" must not contain "-" or ">"')

    def test_candidate_must_be_read_as_its_own_level(self):
        """A detail value in the family table would be edited as a family and
        stored as a detail, the pattern is the only source of truth."""
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value='', levels="""\
  - name: family
    option: ['315']
""", pattern=FAMILY_PATTERN))
        assert e.value.msg == (
            'Level "family" candidate "315" is not decoded by "pattern" as a "family" value')

    def test_candidate_must_not_be_read_as_another_level(self):
        """The pattern reads "A" as the first level, so it can not be a
        candidate of the second one."""
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: filter-regex
value: []
pattern: '(?P<first>A)?-?(?P<second>A)?'
levels:
  - name: first
    option: [A]
  - name: second
    option: [A]
""")
        assert e.value.msg == (
            'Level "second" candidate "A" is not decoded by "pattern" as a "second" value')

    def test_value_must_be_decoded(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value='S9-X-1'))
        assert e.value.msg == 'Default value "S9-X-1" is not decoded by "pattern"'

    def test_value_must_not_be_empty(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value="'S9-DR-0.5 > > 0.5'"))
        assert e.value.msg == 'Value of "filter-regex" datatype must not hold an empty item'

    def test_duplicate_combination(self):
        """The uniqueness is on the decoded levels, so the ALAS spelling of the
        same predicate is a duplicate too."""
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value='S9-DR-0.5 > S9-DR0.5'))
        assert e.value.msg == 'Default value has duplicate value "S9-DR0.5"'

    def test_value_item_must_be_str(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value='[1]'))
        assert e.value.msg == 'Value of "filter-regex" datatype must be a str, got "1"'

    def test_value_must_be_a_list(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value='1'))
        assert e.value.msg == 'Value of "filter-regex" datatype must be a list, got "1"'

    def test_command_must_not_be_read_by_the_pattern(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(value='S9-DR-0.5', extra='option_command: [Q]\n'))
        assert e.value.msg == (
            '"option_command" item "Q" is read by "pattern", a command must not carry a level')

    @pytest.mark.parametrize('option_command, message', [
        ('reset', '"option_command" must be a list'),
        ('[1]', '"option_command" item "1" must be a str'),
        ('[\'a-b\']', '"option_command" item "a-b" must not contain "-" or ">"'),
        ('[reset, reset]', '"option_command" has duplicate item "reset"'),
    ], ids=['not-list', 'not-str', 'separator', 'duplicate'])
    def test_option_command_shape(self, option_command, message):
        with pytest.raises(DefinitionError) as e:
            parse_arg(research_yaml(extra=f'option_command: {option_command}\n'))
        assert e.value.msg == message
