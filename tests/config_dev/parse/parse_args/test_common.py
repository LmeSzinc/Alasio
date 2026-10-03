"""
dt-independent behavior of parse_args.

Covers the shorthand inference (populate_arg), the generic errors, the range
expansion and the ArgData fields used by the code generator and the GUI
payload. The dt-specific behavior has one test file per dt in this package.
"""

import pytest
from msgspec import UNSET

from alasio.config_dev.parse.base import DefinitionError
from alasio.config_dev.parse.parse_args import preprocess_arg
from tests.config_dev.parse.parse_args.helpers import parse_arg


class TestPopulateArgInference:
    """Shorthand args of {nav}.args.yaml predict their own "dt"."""

    def test_bool_is_checkbox(self):
        arg = parse_arg("true")
        assert arg.dt == 'checkbox'
        assert arg.value is True

    def test_int_is_input_int(self):
        arg = parse_arg("128")
        assert arg.dt == 'input-int'
        assert arg.value == 128

    def test_float_is_input_float(self):
        arg = parse_arg("0.85")
        assert arg.dt == 'input-float'
        assert arg.value == 0.85

    def test_str_is_input(self):
        arg = parse_arg("some text")
        assert arg.dt == 'input'
        assert arg.value == 'some text'

    def test_value_with_option_is_select(self):
        arg = parse_arg("""\
value: option-A
option: [option-A, option-B]
""")
        assert arg.dt == 'select'
        assert arg.option == ['option-A', 'option-B']

    def test_value_with_range_predicts_dt(self):
        arg = parse_arg("""\
value: 1
range: 1~15
""")
        assert arg.dt == 'input-int'
        assert arg.ge == 1
        assert arg.le == 15

    def test_cannot_predict_dt(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("value: [1, 2]")
        assert 'Cannot predict "dt"' in str(e.value)


class TestPreprocessArgErrors:
    """Errors raised by preprocess_arg itself, before the ArgData validation."""

    def test_missing_dt(self):
        # populate_arg predicts or rejects the "dt" first, so preprocess_arg is
        # called directly to reach its own check
        with pytest.raises(DefinitionError) as e:
            preprocess_arg({'value': 1})
        assert 'Missing "dt" attribute' in str(e.value)

    def test_missing_value(self):
        with pytest.raises(DefinitionError) as e:
            preprocess_arg({'dt': 'input'})
        assert 'Missing "value" attribute' in str(e.value)

    def test_missing_value_in_yaml(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("dt: input")
        assert 'Missing "value" attribute' in str(e.value)

    def test_invalid_dt(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: unknown-dt
value: 1
""")
        assert 'Invalid datatype "unknown-dt"' in str(e.value)


class TestRange:
    """`range` is expanded into msgspec constraints."""

    def test_int_range(self):
        arg = parse_arg("""\
dt: input-int
value: 1000
range: 1~15
""")
        assert arg.ge == 1
        assert arg.le == 15
        assert arg.get_anno() == 'e.Annotated[int, m.Meta(ge=1, le=15)]'

    def test_float_range(self):
        arg = parse_arg("""\
dt: input-float
value: 0.4
range: 0.0~1.0
""")
        assert arg.ge == 0.0
        assert arg.le == 1.0

    def test_invalid_range(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: input-int
value: 5
range: a~b
""")
        assert 'Cannot parse range "a~b"' in str(e.value)


class TestLayout:
    """The "layout" field: unset by default, explicit values are kept."""

    def test_layout_is_unset_by_default(self):
        arg = parse_arg("""\
dt: checkbox
value: true
""")
        assert arg.layout is UNSET

    def test_explicit_layout_is_kept(self):
        arg = parse_arg("""\
dt: input
value: some text
layout: vert-rev
""")
        assert arg.layout == 'vert-rev'

    def test_invalid_layout(self):
        with pytest.raises(DefinitionError) as e:
            parse_arg("""\
dt: input
value: some text
layout: diagonal
""")
        assert 'Invalid enum value' in str(e.value)


class TestArgDataHelpers:
    """The ArgData fields used by the code generator and the GUI payload."""

    def test_get_meta_is_empty_without_constraints(self):
        arg = parse_arg("""\
dt: input
value: some text
""")
        assert arg.get_meta() == ''

    def test_get_meta(self):
        arg = parse_arg("""\
dt: input-int
value: 5
range: 1~15
""")
        assert arg.get_meta() == 'm.Meta(ge=1, le=15)'

    def test_get_value(self):
        arg = parse_arg("""\
dt: input
value: some text
""")
        assert arg.get_value() == 'some text'

    def test_get_value_of_datetime_is_the_shared_default(self):
        arg = parse_arg("""\
dt: datetime
value: 2026-01-02T03:04:05Z
""")
        assert repr(arg.get_value()) == 'a.DEFAULT_TIME'

    def test_to_dict_omits_defaults(self):
        arg = parse_arg("""\
dt: checkbox
value: true
""")
        assert arg.to_dict() == {'dt': 'checkbox', 'value': True}
