"""
Tests of DefinitionError.

DefinitionError is the only exception type of the config parser, every error
must carry enough location information (file / keys / value) to let a config
author find the broken definition. __str__ is the user facing format, so the
tests assert the complete rendered text instead of a substring.
"""

import pytest

from alasio.config_dev.parse.base import DefinitionError


class TestDefinitionErrorAttributes:
    def test_default_attributes(self):
        e = DefinitionError('broken')
        assert e.msg == 'broken'
        assert e.file == ''
        assert e.keys == ''
        # value has a private "not provided" sentinel, it is not None
        assert str(e) == """\
DefinitionError,
    error='broken'"""

    def test_args_hold_the_message(self):
        """Code that reads Exception.args must see the same message as str()."""
        assert DefinitionError('broken').args == ('broken',)
        assert DefinitionError('broken', file='/a', keys=['G'], value=1).args == ('broken',)

    def test_wrapped_exception_args_is_the_formatted_message(self):
        """args must not leak the wrapped exception object, only its message."""
        e = DefinitionError(ValueError('bad value'))
        assert e.msg == 'ValueError(bad value)'
        assert e.args == ('ValueError(bad value)',)
        assert repr(e) == "DefinitionError('ValueError(bad value)')"

    def test_repr(self):
        """repr is built from args, so it shows the message only."""
        e = DefinitionError('broken', file='/a/main.args.yaml', keys=['Group'], value='x')
        assert repr(e) == "DefinitionError('broken')"

    def test_explicit_attributes(self):
        e = DefinitionError('broken', file='/a/main.args.yaml', keys=['Group', 'Arg'], value='x')
        assert e.msg == 'broken'
        assert e.file == '/a/main.args.yaml'
        assert e.keys == ['Group', 'Arg']
        assert e.value == 'x'

    def test_exception_is_converted_to_str(self):
        """An exception message is rendered as ClassName(message)."""
        e = DefinitionError(ValueError('bad value'))
        assert e.msg == 'ValueError(bad value)'
        assert str(e) == """\
DefinitionError,
    error='ValueError(bad value)'"""

    def test_nested_definition_error_is_not_flattened(self):
        e = DefinitionError(DefinitionError('inner'))
        assert e.msg.startswith('DefinitionError(')
        assert str(e) == '''\
DefinitionError,
    error="DefinitionError(DefinitionError,\\n    error='inner')"'''

    def test_can_be_raised_and_caught(self):
        with pytest.raises(DefinitionError) as caught:
            raise DefinitionError('broken')
        assert caught.value.msg == 'broken'


class TestDefinitionErrorStr:
    def test_message_only(self):
        assert str(DefinitionError('broken')) == """\
DefinitionError,
    error='broken'"""

    def test_file_only(self):
        assert str(DefinitionError('broken', file='/a/main.args.yaml')) == """\
DefinitionError,
    file='/a/main.args.yaml',
    error='broken'"""

    def test_keys_only(self):
        assert str(DefinitionError('broken', keys=['Group'])) == """\
DefinitionError,
    keys=['Group'],
    error='broken'"""

    def test_keys_as_str(self):
        assert str(DefinitionError('broken', keys='Group')) == """\
DefinitionError,
    keys='Group',
    error='broken'"""

    @pytest.mark.parametrize('value', [0, '', False, [], {}, None], ids=[
        'zero', 'empty-str', 'false', 'empty-list', 'empty-dict', 'none',
    ])
    def test_falsy_value_is_printed(self, value):
        """A falsy value is still a location, only the sentinel is omitted."""
        assert str(DefinitionError('broken', value=value)) == f"""\
DefinitionError,
    value={value!r},
    error='broken'"""

    def test_all_locations(self):
        e = DefinitionError('broken', file='/a/main.args.yaml', keys=['Group', 'Arg'], value='x')
        assert str(e) == """\
DefinitionError,
    file='/a/main.args.yaml',
    keys=['Group', 'Arg'],
    value='x',
    error='broken'"""

    @pytest.mark.parametrize('file, keys', [
        ('', ''),
        ('', []),
        (None, None),
    ], ids=['empty-str', 'empty-list', 'none'])
    def test_no_location(self, file, keys):
        assert str(DefinitionError('broken', file=file, keys=keys)) == """\
DefinitionError,
    error='broken'"""

    def test_message_is_repr(self):
        """The message is quoted with repr, a quote in it is escaped."""
        assert str(DefinitionError("it's broken")) == '''\
DefinitionError,
    error="it's broken"'''
