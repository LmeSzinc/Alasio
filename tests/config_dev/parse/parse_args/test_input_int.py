"""
dt="input-int": integer input.
"""

import pytest

from alasio.config_dev.parse.base import DefinitionError
from tests.config_dev.parse.parse_args.helpers import parse_arg


class TestInputInt:
    def test_int(self):
        arg = parse_arg("""\
dt: input-int
value: 128
""")
        assert arg.value == 128
        assert arg.get_anno() == 'int'

    def test_str_is_converted(self):
        arg = parse_arg("""\
dt: input-int
value: '128'
""")
        assert arg.value == 128

    def test_invalid_value(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: input-int
value: some text
""")
        assert 'Value of "input-int" datatype must be int' in str(e.value)
