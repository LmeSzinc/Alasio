from typing import Tuple, Union

from msgspec import NODEFAULT, Struct, field

from .const import ErrorType
from .parse_ctx import ErrorCtx, get_length_ctx, get_number_ctx, get_pattern_ctx
from .parse_path import KEY_got, get_error_path


class MsgspecError(Struct, omit_defaults=True):
    # Raw error message
    msg: str
    # Error type
    type: ErrorType
    # Error path
    # ('user', 'profile', 'age')
    # ('...', 'RepairThreshold')
    # ('matrix', 0, 1, 'value')
    # note that dict keys are shown as [...] in msgspec, so they are parsed as "..."
    loc: Tuple[Union[int, str]]
    # Additional info
    ctx: ErrorCtx = field(default_factory=ErrorCtx)


def get_error_type(error):
    """
    Args:
        error (str):

    Returns:
        (error_type, context)
    """
    # Group 2: Structural Errors
    if error.startswith('Object missing required field '):
        return ErrorType.MISSING_FIELD, NODEFAULT
    if error.startswith('Object contains unknown field '):
        return ErrorType.UNKNOWN_FIELD, NODEFAULT

    # Group 1: Expected ... errors
    if error.startswith('Expected'):
        # Expected `array` of length
        # Check expect array before KEY_got, because it has KEY_got too
        if error.startswith('Expected `array` '):
            remaining = error[17:]
            ctx = get_length_ctx(remaining)
            return ErrorType.ARRAY_LENGTH_CONSTRAINT, ctx

        # Expected `int`, got `str`
        if KEY_got in error:
            return ErrorType.TYPE_MISMATCH, NODEFAULT

        # Expected datetime with (a|no) timezone component
        if error.startswith('Expected datetime with a timezone'):
            return ErrorType.TIMEZONE_CONSTRAINT, ErrorCtx(tz=True)
        if error.startswith('Expected datetime with no timezone'):
            return ErrorType.TIMEZONE_CONSTRAINT, ErrorCtx(tz=False)

        # Expected str matching regex `<pattern>` - at `<Path>`
        if error.startswith('Expected str matching regex '):
            _, _, remaining = error.partition('matching regex ')
            ctx = get_pattern_ctx(remaining)
            return ErrorType.PATTERN_CONSTRAINT, ctx

        # Expected `object` of length
        if error.startswith('Expected `object` '):
            remaining = error[18:]
            ctx = get_length_ctx(remaining)
            return ErrorType.OBJECT_LENGTH_CONSTRAINT, ctx

        # Expected `str` of length <= 32
        if error.startswith('Expected `str` of length '):
            remaining = error[25:]
            ctx = get_length_ctx(remaining)
            return ErrorType.LENGTH_CONSTRAINT, ctx

        # Expected `bytes` of length
        if error.startswith('Expected `bytes` of length '):
            remaining = error[27:]
            ctx = get_length_ctx(remaining)
            return ErrorType.LENGTH_CONSTRAINT, ctx

        # Expected `int` >= 0
        if error.startswith('Expected `int` '):
            remaining = error[15:]
            ctx = get_number_ctx(remaining, expected=int)
            return ErrorType.NUMERIC_CONSTRAINT, ctx

        # Expected `float`
        if error.startswith('Expected `float` '):
            remaining = error[17:]
            ctx = get_number_ctx(remaining, expected=float)
            return ErrorType.NUMERIC_CONSTRAINT, ctx

        # Expected `decimal`
        if error.startswith('Expected `decimal` '):
            remaining = error[19:]
            ctx = get_number_ctx(remaining, expected=float)
            return ErrorType.NUMERIC_CONSTRAINT, ctx

    # Group 4: Invalid Value Errors
    if error.startswith('Invalid enum value '):
        return ErrorType.INVALID_ENUM_VALUE, NODEFAULT
    if error.startswith('Invalid value '):
        return ErrorType.INVALID_TAG_VALUE, NODEFAULT

    if error.startswith('Invalid RFC3339 encoded datetime'):
        return ErrorType.INVALID_DATETIME, NODEFAULT
    if error.startswith('Invalid RFC3339 encoded date'):
        return ErrorType.INVALID_DATE, NODEFAULT
    if error.startswith('Invalid RFC3339 encoded time'):
        return ErrorType.INVALID_TIME, NODEFAULT
    if error.startswith('Invalid ISO8601 duration'):
        return ErrorType.INVALID_DURATION, NODEFAULT
    if error.startswith("Only units"):
        return ErrorType.UNSUPPORTED_DURATION_UNITS, NODEFAULT

    if error.startswith('Invalid MessagePack timestamp'):
        return ErrorType.INVALID_MSGPACK_TIMESTAMP, NODEFAULT
    if error.startswith('Invalid epoch timestamp'):
        return ErrorType.INVALID_EPOCH_TIMESTAMP, NODEFAULT

    if error.startswith('Invalid UUID'):
        return ErrorType.INVALID_UUID, NODEFAULT
    if error.startswith('Invalid base64 encoded string'):
        return ErrorType.INVALID_BASE64_STRING, NODEFAULT
    if error.startswith('Invalid decimal string'):
        return ErrorType.INVALID_DECIMAL_STRING, NODEFAULT

    # Group 5: Out of Range Errors
    if error.startswith('Timestamp is out of range'):
        return ErrorType.TIMESTAMP_OUT_OF_RANGE, NODEFAULT
    if error.startswith('Duration is out of range'):
        return ErrorType.DURATION_OUT_OF_RANGE, NODEFAULT
    if error.startswith('Integer value out of range'):
        return ErrorType.INTEGER_OUT_OF_RANGE, NODEFAULT
    if error.startswith('Number out of range'):
        return ErrorType.NUMBER_OUT_OF_RANGE, NODEFAULT

    # Group 6: Other Errors - Wrapped Error (fallback)
    # Any other error message that doesn't match above patterns
    return ErrorType.WRAPPED_ERROR, NODEFAULT


def parse_msgspec_error(error):
    """
    Parse plain text error message like "Expected `int`, got `str` - at `$.user.profile.age`"
    into structured MsgspecError object.
    This makes msgspec more pydantic.

    Args:
        error (str | msgspec.ValidationError):

    Returns:
        MsgspecError:
    """
    msg = str(error)
    typ, ctx = get_error_type(msg)
    loc = get_error_path(msg)
    if ctx is NODEFAULT:
        return MsgspecError(msg=msg, type=typ, loc=loc)
    else:
        return MsgspecError(msg=msg, type=typ, loc=loc, ctx=ctx)
