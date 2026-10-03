"""
dt="multi-radio": multiple values out of "option", rendered as radio buttons.

Note: the "option" validation of the multi value datatypes is still the scalar
one (parse_arg_utils.validate_option compares the whole list against an option),
so no definition with "option" can pass today. The tests below describe the
intended behavior and are expected to fail until the dt gets its subset
validation (see doc/2026-10-03_filter-order.md, section 12.3).
"""

import pytest

from alasio.config_dev.parse.base import DefinitionError
from tests.config_dev.parse.parse_args.helpers import parse_arg

XFAIL_REASON = 'multi-radio has no subset validation of "option" yet'


class TestMultiRadioIntended:
    @pytest.mark.xfail(reason=XFAIL_REASON, strict=False)
    def test_value_subset_of_option(self):
        arg = parse_arg("""\
dt: multi-radio
value: [A, B]
option: [A, B, C]
""")
        assert list(arg.value) == ['A', 'B']

    @pytest.mark.xfail(reason=XFAIL_REASON, strict=False)
    def test_value_item_must_be_in_option(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: multi-radio
value: [A, D]
option: [A, B, C]
""")
        assert 'Default value "D" is not in "option"' in str(e.value)
