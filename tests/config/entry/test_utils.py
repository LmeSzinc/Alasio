"""
Tests of the task / nav name validators.

Both validators receive names from yaml keys and python call sites, so a
non-str value must be reported as an invalid name instead of raising TypeError
from re.match().
"""

import pytest

from alasio.config.entry.utils import validate_nav_name, validate_task_name

# values that are not a str, all of them used to raise TypeError in re.match()
NON_STR = [
    None,
    0,
    1,
    -1,
    1.5,
    True,
    False,
    b'A',
    b'',
    [],
    [1],
    {},
    {'A': 1},
    (),
    (1,),
    object(),
]
NON_STR_IDS = [
    'none', 'zero', 'int', 'negative-int', 'float', 'true', 'false',
    'bytes', 'empty-bytes', 'empty-list', 'list', 'empty-dict', 'dict',
    'empty-tuple', 'tuple', 'object',
]


class TestValidateTaskName:
    @pytest.mark.parametrize('name', [
        'A',
        'Z',
        'Abc',
        'ABC',
        'A1',
        'A0',
        'Abc123',
        'A' + 'a' * 1000,
    ], ids=['single', 'last-letter', 'mixed', 'upper', 'digit', 'zero', 'alnum', 'long'])
    def test_valid(self, name):
        assert validate_task_name(name) is True

    @pytest.mark.parametrize('name', [
        '',
        'a',
        'abc',
        '1A',
        '1',
        '_A',
        'A_B',
        'A-B',
        'A B',
        ' A',
        'A ',
        'A.',
        'A!',
        'Ä',
        '你好',
    ], ids=[
        'empty', 'lower', 'lower-word', 'digit-first', 'digit', 'underscore-first',
        'underscore', 'dash', 'space', 'leading-space', 'trailing-space', 'dot',
        'symbol', 'non-ascii', 'cjk',
    ])
    def test_invalid_str(self, name):
        assert validate_task_name(name) is False

    @pytest.mark.parametrize('name', NON_STR, ids=NON_STR_IDS)
    def test_non_str(self, name):
        """A non-str name is invalid, not a TypeError."""
        assert validate_task_name(name) is False

    def test_str_subclass(self):
        class Name(str):
            pass

        assert validate_task_name(Name('Abc')) is True
        assert validate_task_name(Name('abc')) is False


class TestValidateNavName:
    @pytest.mark.parametrize('name', [
        'a',
        'z',
        'abc',
        'abc123',
        'a1',
        'a0',
        'a0b9',
        'a' + 'b' * 1000,
    ], ids=['single', 'last-letter', 'word', 'alnum', 'digit', 'zero', 'mixed-alnum', 'long'])
    def test_valid(self, name):
        assert validate_nav_name(name) is True

    @pytest.mark.parametrize('name', [
        '',
        'A',
        'Abc',
        '1a',
        '1',
        '_a',
        'a_b',
        'a-b',
        'a b',
        ' a',
        'a ',
        'a.',
        'a!',
        'ä',
        '你好',
    ], ids=[
        'empty', 'upper', 'upper-word', 'digit-first', 'digit', 'underscore-first',
        'underscore', 'dash', 'space', 'leading-space', 'trailing-space', 'dot',
        'symbol', 'non-ascii', 'cjk',
    ])
    def test_invalid_str(self, name):
        assert validate_nav_name(name) is False

    @pytest.mark.parametrize('name', NON_STR, ids=NON_STR_IDS)
    def test_non_str(self, name):
        """A non-str name is invalid, not a TypeError."""
        assert validate_nav_name(name) is False

    def test_str_subclass(self):
        class Name(str):
            pass

        assert validate_nav_name(Name('abc')) is True
        assert validate_nav_name(Name('Abc')) is False
