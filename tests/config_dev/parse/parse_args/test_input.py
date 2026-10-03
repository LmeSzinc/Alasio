"""
dt="input": free text.

An "input" arg redirects itself to input-int / input-float when its default
value is a number, see populate_input().
"""

import pytest

from alasio.config_dev.parse.base import DefinitionError
from tests.config_dev.parse.parse_args.helpers import parse_arg


class TestInput:
    def test_str(self):
        arg = parse_arg("""\
dt: input
value: some text
""")
        assert arg.dt == 'input'
        assert arg.value == 'some text'
        assert arg.get_anno() == 'str'

    def test_int_redirects_to_input_int(self):
        arg = parse_arg("""\
dt: input
value: 128
""")
        assert arg.dt == 'input-int'
        assert arg.value == 128

    def test_float_redirects_to_input_float(self):
        arg = parse_arg("""\
dt: input
value: 0.85
""")
        assert arg.dt == 'input-float'
        assert arg.value == 0.85

    def test_invalid_default_type(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: input
value: [a, b]
""")
        assert 'Value of "input-*" datatype must be str/int/float' in str(e.value)
