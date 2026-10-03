"""
dt="radio": like "select", the frontend renders single options.

The stored data is identical to "select", only the input widget differs.
"""

import pytest

from alasio.config_dev.parse.base import DefinitionError
from tests.config_dev.parse.parse_args.helpers import parse_arg


class TestRadio:
    def test_value(self):
        arg = parse_arg("""\
dt: radio
value: option-A
option: [option-A, option-B]
""")
        assert arg.dt == 'radio'
        assert arg.value == 'option-A'
        assert arg.get_anno() == "t.Literal['option-A', 'option-B']"

    def test_option_required(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: radio
value: option-A
""")
        assert 'datatype "radio" must have "option" defined' in str(e.value)

    def test_default_value_must_be_in_option(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: radio
value: option-C
option: [option-A, option-B]
""")
        assert 'Default value "option-C" is not in "option"' in str(e.value)
