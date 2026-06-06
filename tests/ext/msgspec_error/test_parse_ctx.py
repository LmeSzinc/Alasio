import msgspec
import pytest
from msgspec import NODEFAULT

from alasio.ext.msgspec_error.parse_ctx import ErrorCtx, get_length_ctx, get_number_ctx, get_pattern_ctx


class TestErrorCtx:
    """Tests for the ErrorCtx struct and its __repr__ method."""

    def test_empty_repr(self):
        """All fields None -> ErrorCtx()"""
        ctx = ErrorCtx()
        assert repr(ctx) == 'ErrorCtx()'

    def test_single_field_repr(self):
        """A single non-None field -> ErrorCtx(field=value)"""
        ctx = ErrorCtx(pattern='[a-z]+')
        assert repr(ctx) == "ErrorCtx(pattern='[a-z]+')"

    def test_two_fields_repr(self):
        """Two non-None fields -> ErrorCtx(field1=value1, field2=value2)"""
        ctx = ErrorCtx(min_length=3, max_length=5)
        assert repr(ctx) == 'ErrorCtx(min_length=3, max_length=5)'

    def test_three_fields_repr(self):
        """Three non-None fields -> ErrorCtx(field1=value1, field2=value2, field3=value3)"""
        ctx = ErrorCtx(gt=0, lt=100, multiple_of=5)
        assert repr(ctx) == 'ErrorCtx(gt=0, lt=100, multiple_of=5)'

    def test_all_numeric_fields_repr(self):
        """All numeric constraint fields set -> ErrorCtx(gt=..., ge=..., lt=..., le=..., ...)"""
        ctx = ErrorCtx(gt=0, ge=1, lt=100, le=99, multiple_of=2)
        assert repr(ctx) == 'ErrorCtx(gt=0, ge=1, lt=100, le=99, multiple_of=2)'

    def test_tz_field_repr(self):
        """tz=True -> ErrorCtx(tz=True)"""
        ctx = ErrorCtx(tz=True)
        assert repr(ctx) == 'ErrorCtx(tz=True)'

    def test_mixed_fields_repr(self):
        """Mixed type fields -> ErrorCtx(ge=18, pattern='abc', max_length=32)"""
        ctx = ErrorCtx(ge=18, max_length=32, pattern='abc')
        # Order: gt, ge, lt, le, multiple_of, pattern, min_length, max_length, tz
        assert repr(ctx) == "ErrorCtx(ge=18, pattern='abc', max_length=32)"

    def test_field_order_consistency(self):
        """Repr field order is consistent: gt, ge, lt, le, multiple_of, pattern, min_length, max_length, tz"""
        ctx = ErrorCtx(
            ge=10, le=20, gt=5, lt=25,
            pattern='test', min_length=1, max_length=100, multiple_of=3, tz=True,
        )
        # The order is: gt, ge, lt, le, multiple_of, pattern, min_length, max_length, tz
        expected = 'ErrorCtx(gt=5, ge=10, lt=25, le=20, multiple_of=3, pattern=\'test\', min_length=1, max_length=100, tz=True)'
        assert repr(ctx) == expected

    def test_equality(self):
        """ErrorCtx should compare equal by field values (Struct default)"""
        a = ErrorCtx(min_length=3, max_length=5)
        b = ErrorCtx(min_length=3, max_length=5)
        assert a == b

    def test_inequality(self):
        """Different field values -> not equal"""
        a = ErrorCtx(min_length=3)
        b = ErrorCtx(min_length=4)
        assert a != b

    def test_omit_defaults(self):
        """Struct omit_defaults=True — all-None fields are omitted in serialization"""
        raw = msgspec.json.encode(ErrorCtx())
        assert raw == b'{}'

    def test_value_types(self):
        """Fields have correct types: int, float, str, bool, None"""
        ctx = ErrorCtx(
            gt=1, ge=2.5, lt=3, le=4.5,
            multiple_of=5, pattern='foo',
            min_length=6, max_length=7, tz=False,
        )
        assert ctx.gt == 1
        assert ctx.ge == 2.5
        assert ctx.lt == 3
        assert ctx.le == 4.5
        assert ctx.multiple_of == 5
        assert ctx.pattern == 'foo'
        assert ctx.min_length == 6
        assert ctx.max_length == 7
        assert ctx.tz is False



class TestGetPatternCtx:
    """Tests for get_pattern_ctx — extracts the regex pattern from error context strings.

    The input to get_pattern_ctx is the remaining string after the call site in error.py
    strips the 'Expected str matching regex ' prefix from the full error message.

    Actual msgspec error format (0.21.1):
      "Expected `str` matching regex '<pattern>' - at `$.<path>`"
    """

    @pytest.mark.parametrize(
        "msg, expected",
        [
            # === Basic cases with path ===
            pytest.param(
                "'[a-z]+' - at `$.field`",
                ErrorCtx(pattern='[a-z]+'),
                id="basic_pattern_with_path",
            ),
            pytest.param(
                "'\\d{4}' - at `$.name`",
                ErrorCtx(pattern='\\d{4}'),
                id="digits_pattern_with_path",
            ),
            pytest.param(
                "'.*' - at `$.field`",
                ErrorCtx(pattern='.*'),
                id="wildcard_pattern_with_path",
            ),
            pytest.param(
                "'[a-zA-Z0-9_]+' - at `$.data.field`",
                ErrorCtx(pattern='[a-zA-Z0-9_]+'),
                id="complex_pattern_dot_path",
            ),
            pytest.param(
                "'^prefix' - at `$.x.y.z`",
                ErrorCtx(pattern='^prefix'),
                id="anchor_pattern_deep_path",
            ),
            # Path with array index
            pytest.param(
                "'[0-9]+' - at `$.items[0].name`",
                ErrorCtx(pattern='[0-9]+'),
                id="pattern_path_with_index",
            ),

            # === No path (top-level field) ===
            # Patterns without a path suffix are now handled correctly:
            # the function checks for KEY_at existence before rpartition.
            pytest.param(
                "'[a-z]+'",
                ErrorCtx(pattern='[a-z]+'),
                id="no_path_pattern_extracted",
            ),
            pytest.param(
                "'abc'",
                ErrorCtx(pattern='abc'),
                id="simple_no_path_pattern_extracted",
            ),

            # === Empty pattern / edge cases ===
            pytest.param(
                "'' - at `$.field`",
                NODEFAULT,
                id="empty_pattern_with_path",
            ),
            pytest.param(
                "''",
                NODEFAULT,
                id="empty_pattern_no_path",
            ),
            pytest.param(
                '',
                NODEFAULT,
                id="empty_string",
            ),

            # === Various pattern special characters ===
            pytest.param(
                "'\\s+\\d+\\.\\d+' - at `$.value`",
                ErrorCtx(pattern='\\s+\\d+\\.\\d+'),
                id="special_chars_pattern",
            ),
            pytest.param(
                "'\\\\' - at `$.path`",
                ErrorCtx(pattern='\\\\'),
                id="single_backslash_pattern",
            ),
        ],
    )
    def test_get_pattern_ctx(self, msg, expected):
        """get_pattern_ctx returns ErrorCtx with pattern or NODEFAULT."""
        result = get_pattern_ctx(msg)
        if expected is NODEFAULT:
            assert result is NODEFAULT, f"Expected NODEFAULT, got {result!r}"
        else:
            assert result == expected, f"Expected {expected!r}, got {result!r}"


class TestGetLengthCtx:
    """Tests for get_length_ctx — extracts min_length/max_length from error context strings.

    The input to get_length_ctx is the remaining string after the call site in error.py
    strips the 'Expected <type> ' prefix from the full error message.

    Actual msgspec error formats (0.21.1):
      "of length >= <min>"           (min_length constraint)
      "of length <= <max>"           (max_length constraint)
      "of length <exact>"            (fixed-length tuple/struct)
      "of length <min> to <max>"     (both min and max)

    The function also handles:
      "of at least length <min>"     (alternative min_length form)
      "of at most length <max>"      (alternative max_length form)
    """

    @pytest.mark.parametrize(
        "msg, expected",
        [
            # === >= (min_length) ===
            pytest.param(
                "of length >= 3 - at `$.tags`",
                ErrorCtx(min_length=3),
                id="min_length_with_path",
            ),
            pytest.param(
                "of length >= 3",
                ErrorCtx(min_length=3),
                id="min_length_no_path",
            ),
            pytest.param(
                "of length >= 5",
                ErrorCtx(min_length=5),
                id="min_length_value_5",
            ),
            pytest.param(
                "of length >= 1",
                ErrorCtx(min_length=1),
                id="min_length_value_1",
            ),
            pytest.param(
                "of length >= 0",
                ErrorCtx(min_length=0),
                id="min_length_value_0",
            ),
            # With ", got <actual>" part stripped
            pytest.param(
                "of length >= 3, got 2",
                ErrorCtx(min_length=3),
                id="min_length_with_got",
            ),
            pytest.param(
                "of length >= 3, got 2 - at `$.tags`",
                ErrorCtx(min_length=3),
                id="min_length_got_and_path",
            ),

            # === <= (max_length) ===
            pytest.param(
                "of length <= 1",
                ErrorCtx(max_length=1),
                id="max_length_no_path",
            ),
            pytest.param(
                "of length <= 32 - at `$.name`",
                ErrorCtx(max_length=32),
                id="max_length_with_path",
            ),
            pytest.param(
                "of length <= 100",
                ErrorCtx(max_length=100),
                id="max_length_value_100",
            ),
            # With ", got <actual>" part stripped
            pytest.param(
                "of length <= 5, got 6 - at `$.name`",
                ErrorCtx(max_length=5),
                id="max_length_got_and_path",
            ),

            # === Exact length (tuple, NamedTuple) ===
            pytest.param(
                "of length 2",
                ErrorCtx(min_length=2, max_length=2),
                id="exact_length_2",
            ),
            pytest.param(
                "of length 5 - at `$.coordinates`",
                ErrorCtx(min_length=5, max_length=5),
                id="exact_length_with_path",
            ),
            pytest.param(
                "of length 0",
                ErrorCtx(min_length=0, max_length=0),
                id="exact_length_0",
            ),
            # With ", got <actual>" stripped -> exact length
            pytest.param(
                "of length 2, got 3",
                ErrorCtx(min_length=2, max_length=2),
                id="exact_length_with_got",
            ),

            # === Range (min to max) ===
            pytest.param(
                "of length 2 to 5",
                ErrorCtx(min_length=2, max_length=5),
                id="range_2_to_5",
            ),
            pytest.param(
                "of length 2 to 5 - at `$.field`",
                ErrorCtx(min_length=2, max_length=5),
                id="range_with_path",
            ),
            pytest.param(
                "of length 1 to 10, got 0",
                ErrorCtx(min_length=1, max_length=10),
                id="range_with_got",
            ),

            # === "of at least length N" (alternative min form) ===
            pytest.param(
                "of at least length 3",
                ErrorCtx(min_length=3),
                id="at_least_length_3",
            ),
            pytest.param(
                "of at least length 1",
                ErrorCtx(min_length=1),
                id="at_least_length_1",
            ),

            # === "of at most length N" (alternative max form) ===
            pytest.param(
                "of at most length 5",
                ErrorCtx(max_length=5),
                id="at_most_length_5",
            ),
            pytest.param(
                "of at most length 100",
                ErrorCtx(max_length=100),
                id="at_most_length_100",
            ),

            # === Invalid numbers / no match ===
            pytest.param(
                "of length abc",
                NODEFAULT,
                id="invalid_parse_bare",
            ),
            pytest.param(
                "of length >= abc",
                NODEFAULT,
                id="invalid_parse_ge",
            ),
            pytest.param(
                "of length <= abc",
                NODEFAULT,
                id="invalid_parse_le",
            ),
            pytest.param(
                "of length abc to def",
                NODEFAULT,
                id="invalid_parse_range",
            ),
            pytest.param(
                "of at least length abc",
                NODEFAULT,
                id="invalid_parse_at_least",
            ),
            pytest.param(
                "of at most length abc",
                NODEFAULT,
                id="invalid_parse_at_most",
            ),
            pytest.param(
                "some random text",
                NODEFAULT,
                id="no_match",
            ),
            pytest.param(
                "",
                NODEFAULT,
                id="empty_string",
            ),
            pytest.param(
                "of length",
                NODEFAULT,
                id="prefix_only_no_value",
            ),
        ],
    )
    def test_get_length_ctx(self, msg, expected):
        """get_length_ctx returns ErrorCtx with length fields or NODEFAULT."""
        result = get_length_ctx(msg)
        if expected is NODEFAULT:
            assert result is NODEFAULT, f"Expected NODEFAULT, got {result!r}"
        else:
            assert result == expected, f"Expected {expected!r}, got {result!r}"


class TestGetNumberCtx:
    """Tests for get_number_ctx — extracts numeric constraints from error context strings.

    The input to get_number_ctx is the remaining string after the call site in error.py
    strips the 'Expected `int` ' (or `float`/`decimal`) prefix from the full error message.

    Actual msgspec error formats (0.21.1):
      ">= <value>"                   (ge)
      "<= <value>"                   (le)
      "that's a multiple of <value>" (multiple_of)

    Also handles:
      ">  <value>"                   (gt)
      "<  <value>"                   (lt)

    Note: float constraint values (e.g. >= 1.5) fail because the parser uses int().
    """

    @pytest.mark.parametrize(
        "msg, expected",
        [
            # === >= (ge) ===
            pytest.param(
                ">= 18 - at `$.age`",
                ErrorCtx(ge=18),
                id="ge_with_path",
            ),
            pytest.param(
                ">= 18",
                ErrorCtx(ge=18),
                id="ge_no_path",
            ),
            pytest.param(
                ">= 0",
                ErrorCtx(ge=0),
                id="ge_zero",
            ),
            pytest.param(
                ">= -5",
                ErrorCtx(ge=-5),
                id="ge_negative",
            ),
            pytest.param(
                ">= 1",
                ErrorCtx(ge=1),
                id="ge_one",
            ),
            pytest.param(
                ">= 100",
                ErrorCtx(ge=100),
                id="ge_large",
            ),

            # === <= (le) ===
            pytest.param(
                "<= 10",
                ErrorCtx(le=10),
                id="le_no_path",
            ),
            pytest.param(
                "<= 100 - at `$.max_val`",
                ErrorCtx(le=100),
                id="le_with_path",
            ),
            pytest.param(
                "<= 0",
                ErrorCtx(le=0),
                id="le_zero",
            ),
            pytest.param(
                "<= -1",
                ErrorCtx(le=-1),
                id="le_negative",
            ),
            # get_number_ctx strips ", got <actual>" before parsing.
            pytest.param(
                "<= 10, got 15",
                ErrorCtx(le=10),
                id="le_with_got",
            ),

            # === > (gt) ===
            pytest.param(
                "> 0",
                ErrorCtx(gt=0),
                id="gt_zero",
            ),
            pytest.param(
                "> 0 - at `$.val`",
                ErrorCtx(gt=0),
                id="gt_with_path",
            ),
            pytest.param(
                "> -10",
                ErrorCtx(gt=-10),
                id="gt_negative",
            ),

            # === < (lt) ===
            pytest.param(
                "< 100",
                ErrorCtx(lt=100),
                id="lt_hundred",
            ),
            pytest.param(
                "< 100 - at `$.max_val`",
                ErrorCtx(lt=100),
                id="lt_with_path",
            ),
            pytest.param(
                "< 0",
                ErrorCtx(lt=0),
                id="lt_zero",
            ),

            # === multiple_of ===
            pytest.param(
                "that's a multiple of 6 - at `$.val`",
                ErrorCtx(multiple_of=6),
                id="multiple_of_with_path",
            ),
            pytest.param(
                "that's a multiple of 6",
                ErrorCtx(multiple_of=6),
                id="multiple_of_no_path",
            ),
            pytest.param(
                "that's a multiple of 0",
                ErrorCtx(multiple_of=0),
                id="multiple_of_zero",
            ),
            pytest.param(
                "that's a multiple of 1",
                ErrorCtx(multiple_of=1),
                id="multiple_of_one",
            ),

            # === Float values (known limitation: int() can't parse float strings) ===
            pytest.param(
                ">= 0.5",
                NODEFAULT,
                id="float_ge_round_trip_nodedefault",
            ),
            pytest.param(
                ">= 0.5 - at `$.val`",
                NODEFAULT,
                id="float_ge_with_path_nodedefault",
            ),
            pytest.param(
                "<= 3.14",
                NODEFAULT,
                id="float_le_nodedefault",
            ),
            pytest.param(
                "that's a multiple of 0.5",
                NODEFAULT,
                id="float_multiple_of_nodedefault",
            ),
            # Note: gt/lt would also fail with float, but msgspec's error format
            # for float Meta(gt=...) still uses ">= 0.5" or "> 0.5" style strings.
            pytest.param(
                "> 1.5",
                NODEFAULT,
                id="float_gt_nodedefault",
            ),

            # === Invalid int parsing ===
            pytest.param(
                ">= abc",
                NODEFAULT,
                id="ge_invalid_int",
            ),
            pytest.param(
                "<= xyz",
                NODEFAULT,
                id="le_invalid_int",
            ),
            pytest.param(
                "> 'five'",
                NODEFAULT,
                id="gt_invalid_int",
            ),
            pytest.param(
                "< ''",
                NODEFAULT,
                id="lt_invalid_int",
            ),
            pytest.param(
                "that's a multiple of abc",
                NODEFAULT,
                id="multiple_of_invalid_int",
            ),

            # === No match ===
            pytest.param(
                "unknown format - at `$.pos`",
                NODEFAULT,
                id="no_match_with_path",
            ),
            pytest.param(
                "just random text",
                NODEFAULT,
                id="no_match_plain",
            ),
            pytest.param(
                "",
                NODEFAULT,
                id="empty_string",
            ),
            pytest.param(
                "   ",
                NODEFAULT,
                id="whitespace_only",
            ),

            # === Prefix matches but partial content ===
            pytest.param(
                ">= ",
                NODEFAULT,
                id="ge_missing_value",
            ),
            pytest.param(
                "<= ",
                NODEFAULT,
                id="le_missing_value",
            ),
            pytest.param(
                "that's a multiple of ",
                NODEFAULT,
                id="multiple_of_missing_value",
            ),
        ],
    )
    def test_get_number_ctx(self, msg, expected):
        """get_number_ctx returns ErrorCtx with numeric fields or NODEFAULT (expected='int')."""
        result = get_number_ctx(msg)
        if expected is NODEFAULT:
            assert result is NODEFAULT, f"Expected NODEFAULT, got {result!r}"
        else:
            assert result == expected, f"Expected {expected!r}, got {result!r}"

    @pytest.mark.parametrize(
        "msg, expected_param, expected",
        [
            # === Float constraint values with expected=float ===
            pytest.param(
                ">= 0.5",
                float,
                ErrorCtx(ge=0.5),
                id="float_ge",
            ),
            pytest.param(
                ">= 1.5 - at `$.val`",
                float,
                ErrorCtx(ge=1.5),
                id="float_ge_with_path",
            ),
            pytest.param(
                "<= 3.14",
                float,
                ErrorCtx(le=3.14),
                id="float_le",
            ),
            pytest.param(
                "> 0.1",
                float,
                ErrorCtx(gt=0.1),
                id="float_gt",
            ),
            pytest.param(
                "< 99.9",
                float,
                ErrorCtx(lt=99.9),
                id="float_lt",
            ),
            pytest.param(
                "that's a multiple of 0.5",
                float,
                ErrorCtx(multiple_of=0.5),
                id="float_multiple_of",
            ),
            # Integer values parsed as float produce float output
            pytest.param(
                ">= 18",
                float,
                ErrorCtx(ge=18.0),
                id="int_ge_as_float",
            ),

            # === Invalid value still returns NODEFAULT ===
            pytest.param(
                ">= abc",
                float,
                NODEFAULT,
                id="float_ge_invalid_int",
            ),
            pytest.param(
                ">= ",
                float,
                NODEFAULT,
                id="float_ge_missing_value",
            ),
            pytest.param(
                "unknown format",
                float,
                NODEFAULT,
                id="float_no_match",
            ),
        ],
    )
    def test_get_number_ctx_with_expected(self, msg, expected_param, expected):
        """get_number_ctx with explicit expected parameter."""
        result = get_number_ctx(msg, expected=expected_param)
        if expected is NODEFAULT:
            assert result is NODEFAULT, f"Expected NODEFAULT, got {result!r}"
        else:
            assert result == expected, f"Expected {expected!r}, got {result!r}"
