import typing as t

import msgspec as m
import pytest

from alasio.config.alasio.group_base import (
    DEFAULT_TIME, GroupBase, T_DATETIME, T_INT_GE0, _parse_literal_arg, _parse_literal_string)
from alasio.config.alasio.store_model import cap_value
from alasio.config.const import DataInconsistent


# ---- Test models ----
# Must inherit from BaseModel so that BaseModel.classmethod(cls, attr)
# can be called as TestModel.classmethod(attr) naturally.

class MetaTestModel(GroupBase):
    """Model with various msgspec meta annotations for testing BaseModel.get_meta"""
    count: T_INT_GE0 = 0
    name: str = 'default'
    scheduled: T_DATETIME = DEFAULT_TIME


class NoMetaModel(GroupBase):
    """Model with no Meta annotation on any field"""
    value: int = 0


class LiteralTestModel(GroupBase):
    """Model with literal annotation for testing BaseModel.get_option"""
    difficulty: "t.Literal['normal', 'hard']" = 'normal'


class NoLiteralTestModel(GroupBase):
    """Model with no literal annotation"""
    count: int = 0


# ---- Tests: parse_literal_arg ----

class TestParseLiteralArg:
    """Test suite for parse_literal_arg function"""

    def test_plain_string(self):
        """Test a plain string without quotes returns as-is"""
        result = _parse_literal_arg('normal')
        assert result == 'normal'

    def test_quoted_string(self):
        """Test a string wrapped in single quotes"""
        result = _parse_literal_arg("'normal'")
        assert result == 'normal'

    def test_quoted_with_underscore(self):
        """Test a quoted string with underscore"""
        result = _parse_literal_arg("'hard_mode'")
        assert result == 'hard_mode'

    def test_quoted_numeric(self):
        """Test a quoted numeric string"""
        result = _parse_literal_arg("'123'")
        assert result == '123'


# ---- Tests: parse_literal_string ----

class TestParseLiteralString:
    """Test suite for parse_literal_string function"""

    @pytest.mark.parametrize('anno, expected', [
        ("t.Literal['normal', 'hard']", ['normal', 'hard']),
        ("t.Literal[('normal', 'hard')]", ['normal', 'hard']),
        ("Literal['normal', 'hard']", ['normal', 'hard']),
        ("typing.Literal['normal', 'hard']", ['normal', 'hard']),
        ("t.Literal['normal']", ['normal']),
        ("t.Literal['easy', 'normal', 'hard']", ['easy', 'normal', 'hard']),
    ])
    def test_valid_literal(self, anno, expected):
        """Test valid literal annotations are parsed correctly"""
        result = _parse_literal_string(anno)
        assert result == expected

    @pytest.mark.parametrize('anno', [
        "str",
        "typo.Literal['a']",
        "List[int]",
    ])
    def test_not_literal_raises(self, anno):
        """Test non-literal strings raise ValueError"""
        with pytest.raises(ValueError, match='Annotation is not a literal'):
            _parse_literal_string(anno)

    # ---- Attack / edge-case tests ----
    # These document how parse_literal_string handles malformed inputs.
    # The function does not validate well-formedness of the literal; these
    # tests exist to surface and pin the current (often broken) behaviour.

    def test_unclosed_bracket(self):
        """Missing ] causes args extraction to return empty string"""
        result = _parse_literal_string("t.Literal['normal'")
        assert result == ['']

    def test_empty_brackets(self):
        """Empty [] yields a single empty option"""
        result = _parse_literal_string("t.Literal[]")
        assert result == ['']

    def test_unclosed_quote(self):
        """Unclosed single quote leaks the ' prefix into the option"""
        result = _parse_literal_string("t.Literal['normal, hard]")
        assert result == ["'normal", "hard"]

    def test_double_quotes(self):
        """Double-quoted options are stripped by parse_literal_arg"""
        result = _parse_literal_string('t.Literal["normal", "hard"]')
        assert result == ['normal', 'hard']

    def test_single_quote_inside_option(self):
        """Option containing a single quote like \"it's\" handled with double-quote delimiters"""
        anno = 't.Literal["it\'s", "that\'s"]'
        result = _parse_literal_string(anno)
        # parse_literal_arg handles both  single and double quotes
        assert result == ["it's", "that's"]


# ---- Tests: BaseModel.get_meta ----

class TestBaseModelGetMeta:
    """Test suite for BaseModel.get_meta"""

    def test_get_meta_int_ge0(self):
        """Test get_meta returns correct Meta for T_INT_GE0 field"""
        meta = MetaTestModel.get_meta('count')
        assert isinstance(meta, m.Meta)
        assert meta.ge == 0
        assert meta.le is None

    def test_get_meta_time_tz(self):
        """Test get_meta returns correct Meta for T_TIME field"""
        meta = MetaTestModel.get_meta('scheduled')
        assert isinstance(meta, m.Meta)
        assert meta.tz is True

    def test_get_meta_str_no_meta(self):
        """Test get_meta on field with no Meta annotation raises DataInconsistent"""
        with pytest.raises(DataInconsistent, match='Missing anno.__metadata__'):
            MetaTestModel.get_meta('name')

    def test_get_meta_missing_attribute_raises(self):
        """Test get_meta with nonexistent attribute raises DataInconsistent"""
        with pytest.raises(DataInconsistent, match='Missing annotation'):
            MetaTestModel.get_meta('nonexistent')

    def test_get_meta_field_without_meta_annotation_raises(self):
        """Test get_meta on field annotated with plain int and no Meta raises DataInconsistent"""
        with pytest.raises(DataInconsistent, match='Missing anno.__metadata__'):
            NoMetaModel.get_meta('value')


# ---- Tests: BaseModel.get_default ----

class TestBaseModelGetDefault:
    """Test suite for BaseModel.get_default"""

    def test_get_default_int(self):
        """Test get_default returns member descriptor for msgspec struct field"""
        default = MetaTestModel.get_default('count')
        # Note: getattr on msgspec Struct class returns a member_descriptor,
        # not the actual default value. This is current behavior of group_base.py.
        assert 'member' in str(type(default))

    def test_get_default_str(self):
        """Test get_default returns member descriptor for str field"""
        default = MetaTestModel.get_default('name')
        assert 'member' in str(type(default))

    def test_get_default_datetime(self):
        """Test get_default returns member descriptor for datetime field"""
        default = MetaTestModel.get_default('scheduled')
        assert 'member' in str(type(default))

    def test_get_default_missing_raises(self):
        """Test get_default with nonexistent attribute raises DataInconsistent"""
        with pytest.raises(DataInconsistent, match='Missing default'):
            MetaTestModel.get_default('nonexistent')


# ---- Tests: BaseModel.get_option ----

class TestBaseModelGetOption:
    """Test suite for BaseModel.get_option"""

    def test_get_option_literal_string_annotation(self):
        """Test get_option on field with string literal annotation"""
        options = LiteralTestModel.get_option('difficulty')
        assert options == ['normal', 'hard']

    def test_get_option_missing_attribute_raises(self):
        """Test get_option with nonexistent attribute raises DataInconsistent"""
        with pytest.raises(DataInconsistent, match='Missing annotation'):
            MetaTestModel.get_option('nonexistent')

    def test_get_option_non_literal_raises(self):
        """Test get_option on non-literal field raises DataInconsistent"""
        with pytest.raises(DataInconsistent, match='Arg has no literal|Arg is not a literal'):
            NoLiteralTestModel.get_option('count')


# ---- Tests: cap_value ----

class TestCapValue:
    """Test suite for cap_value function"""

    def test_cap_below_ge(self):
        """Test capping value below ge limit"""
        meta = m.Meta(ge=0)
        result = cap_value(-5, meta)
        assert result == 0

    def test_cap_above_le(self):
        """Test capping value above le limit"""
        meta = m.Meta(le=100)
        result = cap_value(200, meta)
        assert result == 100

    def test_cap_within_range(self):
        """Test value within range is unchanged"""
        meta = m.Meta(ge=0, le=100)
        result = cap_value(50, meta)
        assert result == 50

    def test_cap_at_ge_boundary(self):
        """Test value at ge boundary is unchanged"""
        meta = m.Meta(ge=0, le=100)
        result = cap_value(0, meta)
        assert result == 0

    def test_cap_at_le_boundary(self):
        """Test value at le boundary is unchanged"""
        meta = m.Meta(ge=0, le=100)
        result = cap_value(100, meta)
        assert result == 100

    def test_cap_no_ge_only_le(self):
        """Test cap with only le limit, value below le unchanged"""
        meta = m.Meta(le=50)
        result = cap_value(49, meta)
        assert result == 49

    def test_cap_no_le_only_ge(self):
        """Test cap with only ge limit, value above ge unchanged"""
        meta = m.Meta(ge=10)
        result = cap_value(15, meta)
        assert result == 15

    def test_cap_no_limits(self):
        """Test cap with no ge or le limits"""
        meta = m.Meta()
        result = cap_value(100, meta)
        assert result == 100

    def test_cap_negative_range_below(self):
        """Test cap with negative range, value below ge"""
        meta = m.Meta(ge=-10, le=-1)
        result = cap_value(-20, meta)
        assert result == -10

    def test_cap_negative_range_above(self):
        """Test cap with negative range, value above le"""
        meta = m.Meta(ge=-10, le=-1)
        result = cap_value(0, meta)
        assert result == -1
