"""
dt="input-float": float input.
"""

import pytest

from alasio.config_dev.parse.base import DefinitionError
from tests.config_dev.parse.parse_args.helpers import parse_arg


class TestInputFloat:
    def test_float(self):
        arg = parse_arg("""\
dt: input-float
value: 0.85
""")
        assert arg.value == 0.85
        assert arg.get_anno() == 'float'

    def test_str_is_converted(self):
        arg = parse_arg("""\
dt: input-float
value: '0.85'
""")
        assert arg.value == 0.85

    def test_invalid_value(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: input-float
value: some text
""")
        assert 'Value of "input-float" datatype must be float' in str(e.value)
