"""
Comprehensive tests for get_error_type().

Tests every ErrorType member defined in const.py against raw error message
strings, covering all message variants and constraints formats.
"""
import pytest

from alasio.ext.msgspec_error.const import ErrorType
from alasio.ext.msgspec_error.parse_error import MsgspecError, get_error_type
from alasio.ext.msgspec_error.parse_ctx import ErrorCtx


# ------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------

def check(msg, expected_type, expected_ctx=None):
    """Assert get_error_type returns correct type and ctx. loc is always ()."""
    result = get_error_type(msg)
    assert isinstance(result, MsgspecError), f"Expected MsgspecError, got {type(result)}"
    assert result.type == expected_type, (
        f"msg={msg!r}: expected {expected_type}, got {result.type}"
    )
    assert result.ctx == (expected_ctx if expected_ctx is not None else ErrorCtx()), (
        f"msg={msg!r}: ctx mismatch. expected={expected_ctx}, got={result.ctx}"
    )
    assert result.loc == (), "loc should be empty tuple from get_error_type"
    assert result.msg == msg, "msg should preserve the original input"


# ======================================================================
# Group 1: Type Mismatch & Unexpected Token
# ======================================================================

class TestTypeMismatch:
    """TYPE_MISMATCH — messages containing ', got `' inside the Expected block."""

    @pytest.mark.parametrize("msg", [
        # root-level (no path suffix)
        "Expected `int`, got `str`",
        "Expected `str`, got `int`",
        "Expected `bool`, got `null`",
        "Expected `array`, got `str`",
        "Expected `object`, got `int`",
        # with path suffix
        "Expected `int`, got `str` - at `$.age`",
        "Expected `str`, got `float` - at `$.name`",
        "Expected `MyCustomClass`, got `str` - at `$.custom_field`",
        "Expected `int`, got `str` - at `$.type`",
        # list index in path
        "Expected `int`, got `str` - at `$.items[0]`",
        # annotated types
        "Expected `Annotated[int, ...]`, got `str` - at `$.score`",
    ])
    def test_type_mismatch(self, msg):
        check(msg, ErrorType.TYPE_MISMATCH)


class TestUnexpectedToken:
    """UNEXPECTED_TOKEN — messages like "Expected `<type>` - at <Path>"
    without ", got `<B>`"."""

    @pytest.mark.parametrize("msg", [
        "Expected `str` - at `$.kind`",
        "Expected `int` - at `$.kind`",
        "Expected `str` - at `key` in `$`",
        "Expected `float` - at `$.value`",
        "Expected `decimal` - at `$.price`",
    ])
    def test_unexpected_token(self, msg):
        """All UNEXPECTED_TOKEN messages now correctly classified."""
        check(msg, ErrorType.UNEXPECTED_TOKEN)


# ======================================================================
# Group 2: Structural Errors
# ======================================================================

class TestMissingField:
    """MISSING_FIELD — 'Object missing required field ...'."""

    @pytest.mark.parametrize("msg", [
        "Object missing required field `id`",
        "Object missing required field `name` - at `$.user`",
        "Object missing required field `age` - at `$.person`",
        "Object missing required field `email` - at `$.contact`",
    ])
    def test_missing_field(self, msg):
        check(msg, ErrorType.MISSING_FIELD)


class TestUnknownField:
    """UNKNOWN_FIELD — 'Object contains unknown field ...'."""

    @pytest.mark.parametrize("msg", [
        "Object contains unknown field `favorite_color`",
        "Object contains unknown field `extra` - at `$.config`",
        "Object contains unknown field `unknown_flag` - at `$`",
    ])
    def test_unknown_field(self, msg):
        check(msg, ErrorType.UNKNOWN_FIELD)


# ======================================================================
# Group 3: Constraint and Length Errors
# ======================================================================

class TestArrayLengthConstraint:
    """ARRAY_LENGTH_CONSTRAINT — array length violations."""

    @pytest.mark.parametrize("msg, ctx", [
        # exact length (tuple size mismatch) — min=max
        ("Expected `array` of length 2, got 3 - at `$.coords`",
         ErrorCtx(min_length=2, max_length=2)),
        ("Expected `array` of length 5, got 1 - at `$.data`",
         ErrorCtx(min_length=5, max_length=5)),
        # range via "of length <min> to <max>"
        ("Expected `array` of length 1 to 5, got 6 - at `$.items`",
         ErrorCtx(min_length=1, max_length=5)),
        # >=
        ("Expected `array` of length >= 3",
         ErrorCtx(min_length=3)),
        ("Expected `array` of length >= 1 - at `$.tags`",
         ErrorCtx(min_length=1)),
        # <=
        ("Expected `array` of length <= 10",
         ErrorCtx(max_length=10)),
        ("Expected `array` of length <= 0 - at `$.empty`",
         ErrorCtx(max_length=0)),
        # "at least" / "at most" (msgspec 0.19+)
        ("Expected `array` of at least length 2",
         ErrorCtx(min_length=2)),
        ("Expected `array` of at most length 10 - at `$.buffer`",
         ErrorCtx(max_length=10)),
    ])
    def test_array_length_constraint(self, msg, ctx):
        check(msg, ErrorType.ARRAY_LENGTH_CONSTRAINT, ctx)


class TestObjectLengthConstraint:
    """OBJECT_LENGTH_CONSTRAINT — dict/object length violations."""

    @pytest.mark.parametrize("msg, ctx", [
        # >=
        ("Expected `object` of length >= 1 - at `$.metadata`",
         ErrorCtx(min_length=1)),
        ("Expected `object` of length >= 1",
         ErrorCtx(min_length=1)),
        # <=
        ("Expected `object` of length <= 5",
         ErrorCtx(max_length=5)),
        # >= and <= are both present when constraint is set
        ("Expected `object` of length >= 2",
         ErrorCtx(min_length=2)),
    ])
    def test_object_length_constraint(self, msg, ctx):
        check(msg, ErrorType.OBJECT_LENGTH_CONSTRAINT, ctx)


class TestLengthConstraint:
    """LENGTH_CONSTRAINT — str/bytes length violations."""

    @pytest.mark.parametrize("msg, ctx", [
        # str variants — le
        ("Expected `str` of length <= 32",
         ErrorCtx(max_length=32)),
        ("Expected `str` of length <= 0",
         ErrorCtx(max_length=0)),
        # str variants — ge
        ("Expected `str` of length >= 1 - at `$.name`",
         ErrorCtx(min_length=1)),
        ("Expected `str` of length >= 8",
         ErrorCtx(min_length=8)),
        # str variants — range
        ("Expected `str` of length 5 to 10 - at `$.code`",
         ErrorCtx(min_length=5, max_length=10)),
        # bytes variants — exact
        ("Expected `bytes` of length 16",
         ErrorCtx(min_length=16, max_length=16)),
        # bytes variants — ge
        ("Expected `bytes` of length >= 8 - at `$.key`",
         ErrorCtx(min_length=8)),
        ("Expected `bytes` of length >= 1",
         ErrorCtx(min_length=1)),
        # bytes variants — le
        ("Expected `bytes` of length <= 1024",
         ErrorCtx(max_length=1024)),
    ])
    def test_length_constraint(self, msg, ctx):
        check(msg, ErrorType.LENGTH_CONSTRAINT, ctx)


class TestPatternConstraint:
    """PATTERN_CONSTRAINT — regex pattern violations."""

    @pytest.mark.parametrize("msg, ctx", [
        # simple patterns
        ("Expected `str` matching regex '\\d{4}-\\d{2}-\\d{2}' - at `$.date`",
         ErrorCtx(pattern='\\d{4}-\\d{2}-\\d{2}')),
        ("Expected `str` matching regex '^[a-z]+$'",
         ErrorCtx(pattern='^[a-z]+$')),
        # special characters
        ("Expected `str` matching regex 'https?://.*' - at `$.url`",
         ErrorCtx(pattern='https?://.*')),
        # pattern with backticks (the pattern itself is in quotes)
        ("Expected `str` matching regex '[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}'",
         ErrorCtx(pattern='[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}')),
    ])
    def test_pattern_constraint(self, msg, ctx):
        check(msg, ErrorType.PATTERN_CONSTRAINT, ctx)


class TestNumericConstraint:
    """NUMERIC_CONSTRAINT — int/float/decimal constraint violations."""

    @pytest.mark.parametrize("msg, ctx", [
        # int: ge
        ("Expected `int` >= 0",
         ErrorCtx(ge=0)),
        ("Expected `int` >= 18 - at `$.age`",
         ErrorCtx(ge=18)),
        # int: le
        ("Expected `int` <= 100",
         ErrorCtx(le=100)),
        ("Expected `int` <= 10 - at `$.count`",
         ErrorCtx(le=10)),
        # int: gt
        ("Expected `int` > 0",
         ErrorCtx(gt=0)),
        # int: lt
        ("Expected `int` < 10 - at `$.rank`",
         ErrorCtx(lt=10)),
        # int: multiple_of
        ("Expected `int` that's a multiple of 6",
         ErrorCtx(multiple_of=6)),
        ("Expected `int` that's a multiple of 2 - at `$.even`",
         ErrorCtx(multiple_of=2)),
        # float
        ("Expected `float` >= 0.0",
         ErrorCtx(ge=0.0)),
        ("Expected `float` <= 100.5 - at `$.price`",
         ErrorCtx(le=100.5)),
        ("Expected `float` > 0.0",
         ErrorCtx(gt=0.0)),
        ("Expected `float` < 1.0 - at `$.ratio`",
         ErrorCtx(lt=1.0)),
        ("Expected `float` that's a multiple of 0.5",
         ErrorCtx(multiple_of=0.5)),
        # decimal (stored as float in ctx)
        ("Expected `decimal` >= 0.0",
         ErrorCtx(ge=0.0)),
        ("Expected `decimal` <= 999999.99 - at `$.amount`",
         ErrorCtx(le=999999.99)),
        ("Expected `decimal` that's a multiple of 0.01",
         ErrorCtx(multiple_of=0.01)),
    ])
    def test_numeric_constraint(self, msg, ctx):
        check(msg, ErrorType.NUMERIC_CONSTRAINT, ctx)


class TestTimezoneConstraint:
    """TIMEZONE_CONSTRAINT — datetime/time timezone awareness."""

    @pytest.mark.parametrize("msg, ctx", [
        ("Expected datetime with a timezone component",
         ErrorCtx(tz=True)),
        ("Expected datetime with a timezone component - at `$.event_at`",
         ErrorCtx(tz=True)),
        ("Expected datetime with no timezone component",
         ErrorCtx(tz=False)),
        ("Expected datetime with no timezone component - at `$.naive_dt`",
         ErrorCtx(tz=False)),
    ])
    def test_timezone_constraint(self, msg, ctx):
        check(msg, ErrorType.TIMEZONE_CONSTRAINT, ctx)


# ======================================================================
# Group 4: Invalid Value Errors
# ======================================================================

class TestInvalidEnumValue:
    """INVALID_ENUM_VALUE — value not in enum/literal."""

    @pytest.mark.parametrize("msg", [
        "Invalid enum value 'admin'",
        "Invalid enum value 'admin' - at `$.role`",
        "Invalid enum value 'RED' - at `$.color`",
        "Invalid enum value 3 - at `$.status`",
        "Invalid enum value 'user' - at `$.permissions[0]`",
    ])
    def test_invalid_enum(self, msg):
        check(msg, ErrorType.INVALID_ENUM_VALUE)


class TestInvalidTagValue:
    """INVALID_TAG_VALUE — unrecognized tag in tagged union."""

    @pytest.mark.parametrize("msg", [
        "Invalid value 3",
        "Invalid value 'unknown' - at `$.type`",
        "Invalid value `Bird` - at `$.kind`",
        "Invalid value 404 - at `$.tag`",
    ])
    def test_invalid_tag(self, msg):
        check(msg, ErrorType.INVALID_TAG_VALUE)


class TestInvalidDateTime:
    """INVALID_DATETIME — malformed RFC3339 datetime string."""

    @pytest.mark.parametrize("msg", [
        "Invalid RFC3339 encoded datetime - at `$.timestamp`",
        "Invalid RFC3339 encoded datetime - at `$`",
        "Invalid RFC3339 encoded datetime",
    ])
    def test_invalid_datetime(self, msg):
        check(msg, ErrorType.INVALID_DATETIME)


class TestInvalidDate:
    """INVALID_DATE — malformed RFC3339 date string."""

    @pytest.mark.parametrize("msg", [
        "Invalid RFC3339 encoded date - at `$.birth_date`",
        "Invalid RFC3339 encoded date - at `$`",
        "Invalid RFC3339 encoded date",
    ])
    def test_invalid_date(self, msg):
        check(msg, ErrorType.INVALID_DATE)


class TestInvalidTime:
    """INVALID_TIME — malformed RFC3339 time string."""

    @pytest.mark.parametrize("msg", [
        "Invalid RFC3339 encoded time - at `$.event_time`",
        "Invalid RFC3339 encoded time - at `$`",
        "Invalid RFC3339 encoded time",
    ])
    def test_invalid_time(self, msg):
        check(msg, ErrorType.INVALID_TIME)


class TestInvalidDuration:
    """INVALID_DURATION — malformed ISO8601 duration string."""

    @pytest.mark.parametrize("msg", [
        "Invalid ISO8601 duration - at `$.period`",
        "Invalid ISO8601 duration - at `$`",
        "Invalid ISO8601 duration",
    ])
    def test_invalid_duration(self, msg):
        check(msg, ErrorType.INVALID_DURATION)


class TestUnsupportedDurationUnits:
    """UNSUPPORTED_DURATION_UNITS — unsupported ISO8601 units."""

    @pytest.mark.parametrize("msg", [
        "Only units 'D', 'H', 'M', and 'S' are supported when parsing ISO8601 durations",
        "Only units 'D', 'H', 'M', and 'S' are supported when parsing ISO8601 durations - at `$.period`",
    ])
    def test_unsupported_duration_units(self, msg):
        check(msg, ErrorType.UNSUPPORTED_DURATION_UNITS)


class TestInvalidMsgpackTimestamp:
    """INVALID_MSGPACK_TIMESTAMP — malformed msgpack timestamp."""

    @pytest.mark.parametrize("msg", [
        "Invalid MessagePack timestamp - at `$`",
        "Invalid MessagePack timestamp: nanoseconds out of range",
        "Invalid MessagePack timestamp: nanoseconds out of range - at `$`",
    ])
    def test_invalid_msgpack_ts(self, msg):
        check(msg, ErrorType.INVALID_MSGPACK_TIMESTAMP)


class TestInvalidEpochTimestamp:
    """INVALID_EPOCH_TIMESTAMP — non-finite float for datetime."""

    @pytest.mark.parametrize("msg", [
        "Invalid epoch timestamp - at `$`",
        "Invalid epoch timestamp",
    ])
    def test_invalid_epoch(self, msg):
        check(msg, ErrorType.INVALID_EPOCH_TIMESTAMP)


class TestInvalidUUID:
    """INVALID_UUID — malformed UUID string or bytes."""

    @pytest.mark.parametrize("msg", [
        "Invalid UUID - at `$.id`",
        "Invalid UUID",
        "Invalid UUID bytes",
        "Invalid UUID bytes - at `$.binary_id`",
    ])
    def test_invalid_uuid(self, msg):
        check(msg, ErrorType.INVALID_UUID)


class TestInvalidBase64:
    """INVALID_BASE64_STRING — invalid base64 content."""

    @pytest.mark.parametrize("msg", [
        "Invalid base64 encoded string - at `$.data`",
        "Invalid base64 encoded string",
        "Invalid base64 encoded string - at `$.payload`",
    ])
    def test_invalid_base64(self, msg):
        check(msg, ErrorType.INVALID_BASE64_STRING)


class TestInvalidDecimal:
    """INVALID_DECIMAL_STRING — unparseable decimal string."""

    @pytest.mark.parametrize("msg", [
        "Invalid decimal string - at `$.price`",
        "Invalid decimal string",
        "Invalid decimal string - at `$.amount`",
    ])
    def test_invalid_decimal(self, msg):
        check(msg, ErrorType.INVALID_DECIMAL_STRING)


# ======================================================================
# Group 5: Out of Range Errors
# ======================================================================

class TestTimestampOutOfRange:
    """TIMESTAMP_OUT_OF_RANGE."""

    @pytest.mark.parametrize("msg", [
        "Timestamp is out of range",
        "Timestamp is out of range - at `$.ts`",
    ])
    def test_timestamp_range(self, msg):
        check(msg, ErrorType.TIMESTAMP_OUT_OF_RANGE)


class TestDurationOutOfRange:
    """DURATION_OUT_OF_RANGE."""

    @pytest.mark.parametrize("msg", [
        "Duration is out of range",
        "Duration is out of range - at `$.period`",
    ])
    def test_duration_range(self, msg):
        check(msg, ErrorType.DURATION_OUT_OF_RANGE)


class TestIntegerOutOfRange:
    """INTEGER_OUT_OF_RANGE."""

    @pytest.mark.parametrize("msg", [
        "Integer value out of range",
        "Integer value out of range - at `$.big_int`",
    ])
    def test_integer_range(self, msg):
        check(msg, ErrorType.INTEGER_OUT_OF_RANGE)


class TestNumberOutOfRange:
    """NUMBER_OUT_OF_RANGE."""

    @pytest.mark.parametrize("msg", [
        "Number out of range",
        "Number out of range - at `$.big_num`",
    ])
    def test_number_range(self, msg):
        check(msg, ErrorType.NUMBER_OUT_OF_RANGE)


# ======================================================================
# Group 6: Wrapped & Fallback Errors
# ======================================================================

class TestWrappedError:
    """WRAPPED_ERROR — fallback for unmatched messages."""

    @pytest.mark.parametrize("msg", [
        # User-generated errors from __post_init__, dec_hook, etc.
        "passwords cannot be the same - at $",
        "Custom validation failed",
        "ValueError: invalid literal for int()",
        # Random strings that don't match any known pattern
        "Some completely unexpected error",
        "",
        "Expected `str`" + " " * 100,
        # "Expected `int`" (no " - at", no constraint) — not matched by any
        # specific Expected pattern and has no " - at " — falls to WRAPPED_ERROR
        "Expected `int`",
        "Expected `float`",
        "Expected `decimal`",
    ])
    def test_wrapped_error(self, msg):
        check(msg, ErrorType.WRAPPED_ERROR)


# ======================================================================
# Edge Cases — Constraint Helpers Returning NODEFAULT
# ======================================================================

class TestHelperNODEFAULTFallback:
    """
    When get_length_ctx / get_number_ctx / get_pattern_ctx return NODEFAULT
    (e.g. unparseable constraints), get_error_type should still return the
    correct MsgspecError with default empty ErrorCtx.
    """

    @pytest.mark.parametrize("msg, expected_type", [
        # ARRAY_LENGTH_CONSTRAINT with unparseable length text
        ("Expected `array` of length ???",
         ErrorType.ARRAY_LENGTH_CONSTRAINT),
        # LENGTH_CONSTRAINT with unparseable bytes length
        ("Expected `bytes` of length ???",
         ErrorType.LENGTH_CONSTRAINT),
        # LENGTH_CONSTRAINT with unparseable str length
        ("Expected `str` of length ???",
         ErrorType.LENGTH_CONSTRAINT),
        # OBJECT_LENGTH_CONSTRAINT with unparseable length
        ("Expected `object` of length ???",
         ErrorType.OBJECT_LENGTH_CONSTRAINT),
        # NUMERIC_CONSTRAINT with unparseable constraint
        ("Expected `int` >= ???",
         ErrorType.NUMERIC_CONSTRAINT),
        # NUMERIC_CONSTRAINT with unparseable float constraint
        ("Expected `float` ???",
         ErrorType.NUMERIC_CONSTRAINT),
    ])
    def test_unparseable_ctx_fallback(self, msg, expected_type):
        result = get_error_type(msg)
        assert result.type == expected_type, (
            f"msg={msg!r}: expected {expected_type}, got {result.type}"
        )
        assert result.ctx == ErrorCtx(), (
            f"msg={msg!r}: expected empty ErrorCtx, got {result.ctx}"
        )


# ======================================================================
# Edge Cases — Arbitrary Input Resilience
# ======================================================================

class TestEdgeCases:
    """Resilience against malformed or unexpected input."""

    def test_empty_string(self):
        """Empty string should fall through to WRAPPED_ERROR."""
        result = get_error_type("")
        assert result.type == ErrorType.WRAPPED_ERROR
        assert result.ctx == ErrorCtx()

    def test_single_word(self):
        """A single unexpected word should be WRAPPED_ERROR."""
        result = get_error_type("Something")
        assert result.type == ErrorType.WRAPPED_ERROR

    def test_very_long_message(self):
        """A very long message that doesn't match should be WRAPPED_ERROR."""
        msg = "A" * 10000
        result = get_error_type(msg)
        assert result.type == ErrorType.WRAPPED_ERROR

    def test_prefix_match_with_suffix_garbage(self):
        """
        Patterns match on startswith; garbage after the matched prefix
        may cause ctx helpers to return NODEFAULT, but type should still
        be correct.
        """
        result = get_error_type("Expected `int` >= 0 garbage_trailing_text")
        assert result.type == ErrorType.NUMERIC_CONSTRAINT
        # "garbage_trailing_text" after "0" causes int("0 garbage_trailing_text")
        # to raise ValueError, so get_number_ctx returns NODEFAULT -> empty ctx
        assert result.ctx == ErrorCtx()

    def test_partial_match_does_not_leak(self):
        """'Expected `int`' alone (no constraint suffix) should not match NUMERIC_CONSTRAINT."""
        # This message starts with "Expected" but doesn't match any specific pattern
        # It would go to the 'Expected `int` ' check... wait, "Expected `int`" (without trailing space)...
        # Actually, "Expected `int` " needs the trailing space for startswith.
        # "Expected `int`" (no trailing space) does NOT start with "Expected `int` ".
        # So it doesn't match NUMERIC_CONSTRAINT.
        result = get_error_type("Expected `int`")
        assert result.type == ErrorType.WRAPPED_ERROR

    def test_at_keyword_in_middle_of_message(self):
        """VERBATIM 'at' in field names shouldn't confuse path parsing in get_error_type."""
        # get_error_type doesn't do path parsing itself, it delegates to helpers.
        # This tests that the type classification is not affected by 'at' in the message.
        msg = "Object missing required field `data` - at `$.meta.at`"
        check(msg, ErrorType.MISSING_FIELD)

    def test_type_mismatch_with_backtick_types(self):
        """Types with backticks in their msgspec representation."""
        cases = [
            "Expected `Union[int, str]`, got `int`",
            "Expected `Optional[str]`, got `null`",
            "Expected `list[int]`, got `str`",
        ]
        for msg in cases:
            check(msg, ErrorType.TYPE_MISMATCH)

    def test_numeric_constraint_with_all_operators(self):
        """All five numeric constraint operators for int type."""
        cases = [
            ("Expected `int` >= 0", ErrorCtx(ge=0)),
            ("Expected `int` <= 100", ErrorCtx(le=100)),
            ("Expected `int` > 0", ErrorCtx(gt=0)),
            ("Expected `int` < 50", ErrorCtx(lt=50)),
            ("Expected `int` that's a multiple of 10", ErrorCtx(multiple_of=10)),
        ]
        for msg, ctx in cases:
            check(msg, ErrorType.NUMERIC_CONSTRAINT, ctx)

    def test_all_float_constraint_operators(self):
        """All five numeric constraint operators for float type."""
        cases = [
            ("Expected `float` >= 0.5", ErrorCtx(ge=0.5)),
            ("Expected `float` <= 1.5", ErrorCtx(le=1.5)),
            ("Expected `float` > 0.0", ErrorCtx(gt=0.0)),
            ("Expected `float` < 100.0", ErrorCtx(lt=100.0)),
            ("Expected `float` that's a multiple of 0.25", ErrorCtx(multiple_of=0.25)),
        ]
        for msg, ctx in cases:
            check(msg, ErrorType.NUMERIC_CONSTRAINT, ctx)


class TestReturnType:
    """Verify get_error_type always returns a MsgspecError."""

    @pytest.mark.parametrize("msg", [
        "Expected `int`, got `str`",
        "Object missing required field `id`",
        "Expected `array` of length >= 3",
        "Invalid enum value 'admin'",
        "Some random error",
        "",
    ])
    def test_always_returns_msgspec_error(self, msg):
        result = get_error_type(msg)
        assert isinstance(result, MsgspecError)
        assert isinstance(result.msg, str)
        assert isinstance(result.type, ErrorType)
        assert isinstance(result.ctx, ErrorCtx)
        assert result.loc == ()
