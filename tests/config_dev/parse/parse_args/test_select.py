"""
dt="select": one value out of "option".

The options become a python "t.Literal", so they must be valid literal items
and the default value must be one of them.
"""

import pytest
from msgspec import UNSET

from alasio.config_dev.parse.base import DefinitionError
from tests.config_dev.parse.parse_args.helpers import parse_arg


class TestSelect:
    def test_str(self):
        arg = parse_arg("""\
dt: select
value: option-A
option: [option-A, option-B]
""")
        assert arg.value == 'option-A'
        assert arg.option == ['option-A', 'option-B']
        assert arg.get_anno() == "t.Literal['option-A', 'option-B']"

    def test_int_options(self):
        arg = parse_arg("""\
dt: select
value: 1
option: [1, 2, 3]
""")
        assert arg.value == 1
        assert arg.get_anno() == 't.Literal[1, 2, 3]'

    def test_literal_ref(self):
        """The module level literal variable replaces the inline literal."""
        arg = parse_arg("""\
dt: select
value: option-A
option: [option-A, option-B]
""")
        assert arg.get_literal_groups() == [['option-A', 'option-B']]
        assert arg.get_literal() == "t.Literal['option-A', 'option-B']"
        assert arg.get_literal(literal_ref='LITERAL_Group_Arg') == 'LITERAL_Group_Arg'
        assert arg.get_python_type(literal_ref='LITERAL_Group_Arg') == 'LITERAL_Group_Arg'
        assert arg.get_anno(literal_ref='LITERAL_Group_Arg') == 'LITERAL_Group_Arg'

    def test_option_dict_is_dropped(self):
        arg = parse_arg("""\
dt: select
value: option-A
option: [option-A, option-B]
option_dict:
  group: [option-A]
""")
        assert arg.option_dict is UNSET
        assert arg.option == ['option-A', 'option-B']

    def test_option_dict_does_not_replace_option(self):
        """dt="select" has a single level of options."""
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: select
value: option-A
option_dict:
  group: [option-A]
""")
        assert 'datatype "select" must have "option" defined' in str(e.value)


class TestSelectValidate:
    def test_option_required(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: select
value: option-A
""")
        assert 'datatype "select" must have "option" defined' in str(e.value)

    def test_default_value_must_be_in_option(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: select
value: option-C
option: [option-A, option-B]
""")
        assert 'Default value "option-C" is not in "option"' in str(e.value)

    def test_option_must_not_be_empty(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: select
value: option-A
option: []
""")
        assert '"option" is empty' in str(e.value)

    def test_duplicate_option(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: select
value: option-A
option: [option-A, option-A]
""")
        assert '"option" has duplicate value "option-A"' in str(e.value)

    @pytest.mark.parametrize('option', ['A', '1', '1.5', 'true', '{key: 1}'])
    def test_option_must_be_list(self, option):
        """A non list option (even a non-iterable one) is a definition error."""
        with pytest.raises(DefinitionError) as e:
            parse_arg(f"""\
dt: select
value: A
option: {option}
""")
        assert '"option" must be a list' in str(e.value)

    def test_option_items_must_be_literal_items(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: select
value: B
option:
  - 2026-01-02T03:04:05Z
  - B
""")
        assert 'must be bool/str/int/float/bytes/None' in str(e.value)

    def test_unhashable_option_item(self):
        """A dict item can not be an option, report it as a definition error."""
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: select
value: B
option:
  - {key: 1}
  - B
""")
        assert 'must be bool/str/int/float/bytes/None' in str(e.value)

    def test_list_option_item(self):
        """A list item is unhashable as well."""
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: select
value: B
option:
  - [1, 2]
  - B
""")
        assert 'must be bool/str/int/float/bytes/None' in str(e.value)
