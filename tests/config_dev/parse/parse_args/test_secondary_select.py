"""
dt="secondary-select": one value out of a grouped option list.

The groups are only a navigation aid of the frontend, the stored value is the
option only. The default value may live in any group, but it must be an option.
"""

import pytest
from msgspec import UNSET

from alasio.config_dev.parse.base import DefinitionError
from tests.config_dev.parse.parse_args.helpers import parse_arg


class TestSecondarySelect:
    def test_option_dict(self):
        arg = parse_arg("""\
dt: secondary-select
value: 1-2
option_dict:
  chapter1: [1-1, 1-2]
  chapter2: [2-1, 2-2]
""")
        assert arg.dt == 'secondary-select'
        assert arg.value == '1-2'
        assert arg.option_dict == {
            'chapter1': ['1-1', '1-2'],
            'chapter2': ['2-1', '2-2'],
        }
        assert arg.option is UNSET

    def test_option_dict_keeps_group_order(self):
        """Group order is what the frontend shows, keep it as defined."""
        arg = parse_arg("""\
dt: secondary-select
value: jp-0
option_dict:
  cn_android: [cn_android-0, cn_android-1]
  en: [en-0]
  jp: [jp-0]
""")
        assert list(arg.option_dict) == ['cn_android', 'en', 'jp']

    def test_option_dict_groups_may_be_prefixed_values(self):
        """Group names may be the i18n prefix of the values, not a value."""
        arg = parse_arg("""\
dt: secondary-select
value: cn_android-1
option_dict:
  cn_android: [cn_android-0, cn_android-1]
  jp: [jp-0]
""")
        assert arg.option_dict['cn_android'] == ['cn_android-0', 'cn_android-1']

    def test_option_converted_to_option_dict(self):
        """A dict given as "option" is moved to "option_dict"."""
        arg = parse_arg("""\
dt: secondary-select
value: 1-1
option:
  chapter1: [1-1, 1-2]
""")
        assert arg.option_dict == {'chapter1': ['1-1', '1-2']}
        assert arg.option is UNSET


class TestSecondarySelectValidate:
    def test_option_and_option_dict(self):
        """Both defined at the same time is ambiguous."""
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: secondary-select
value: 1-1
option:
  chapter1: [1-1]
option_dict:
  chapter1: [1-1]
""")
        assert 'cannot be defined at the same time' in str(e.value)

    def test_missing_option_and_option_dict(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: secondary-select
value: 1-1
""")
        assert 'must have "option" or "option_dict" defined' in str(e.value)

    def test_empty_option_dict(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: secondary-select
value: 1-1
option_dict: {}
""")
        assert '"option_dict" is empty' in str(e.value)

    def test_default_value_must_be_in_options(self):
        """The default value is one of the options, no matter the group."""
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: secondary-select
value: 2-3
option_dict:
  chapter1: [1-1, 1-2]
  chapter2: [2-1, 2-2]
""")
        assert 'Default value "2-3" is not in "option"' in str(e.value)

    def test_duplicate_option_in_groups(self):
        """An option may only appear once, the value is the option only."""
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: secondary-select
value: 1-1
option_dict:
  chapter1: [1-1, 1-2]
  chapter2: [1-1, 2-2]
""")
        assert '"option" has duplicate value "1-1"' in str(e.value)

    def test_group_name_can_not_be_an_option(self):
        """A group name colliding with an option makes the label ambiguous."""
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: secondary-select
value: disabled
option_dict:
  disabled: [disabled]
""")
        assert '"option_dict" group name "disabled" cannot be in "option"' in str(e.value)

    def test_option_dict_must_be_dict_of_list(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: secondary-select
value: 1-1
option_dict: [1-1]
""")
        assert 'must be a dict[str, list]' in str(e.value)

    def test_option_dict_items_must_be_literal_items(self):
        """An unhashable item is reported as an invalid option, not as a bad option_dict."""
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: secondary-select
value: 1-2
option_dict:
  chapter1: [1-1, 1-2]
  chapter2: [{key: 1}, 2-2]
""")
        assert 'must be bool/str/int/float/bytes/None' in str(e.value)
