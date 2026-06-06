from typing import Tuple, Union

import msgspec
from msgspec import NODEFAULT, Struct, field

from .const import ErrorType
from .parse_ctx import ErrorCtx, get_length_ctx, get_number_ctx, get_pattern_ctx
from .parse_path import KEY_at, KEY_at_check, KEY_got, get_error_path


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
    loc: Tuple[Union[int, str]] = ()
    # Additional info
    ctx: ErrorCtx = field(default_factory=ErrorCtx)


def get_error_type(error):
    """
    Parse error message and return MsgspecError with type and context set.

    Args:
        error (str):

    Returns:
        MsgspecError: Result with type and ctx set, loc is an empty tuple.
    """
    # Group 2: Structural Errors
    if error.startswith('Object missing required field '):
        return MsgspecError(msg=error, type=ErrorType.MISSING_FIELD)
    if error.startswith('Object contains unknown field '):
        return MsgspecError(msg=error, type=ErrorType.UNKNOWN_FIELD)

    # Group 1: Expected ... errors
    if error.startswith('Expected'):
        # Expected `array` of length
        # Check expect array before KEY_got, because it has KEY_got too
        if error.startswith('Expected `array` '):
            remaining = error[17:]
            ctx = get_length_ctx(remaining)
            if ctx is not NODEFAULT:
                return MsgspecError(msg=error, type=ErrorType.ARRAY_LENGTH_CONSTRAINT, ctx=ctx)
            return MsgspecError(msg=error, type=ErrorType.ARRAY_LENGTH_CONSTRAINT)

        # Expected `int`, got `str`
        if KEY_got in error:
            return MsgspecError(msg=error, type=ErrorType.TYPE_MISMATCH)

        # Expected datetime with (a|no) timezone component
        if error.startswith('Expected datetime with a timezone'):
            return MsgspecError(msg=error, type=ErrorType.TIMEZONE_CONSTRAINT, ctx=ErrorCtx(tz=True))
        if error.startswith('Expected datetime with no timezone'):
            return MsgspecError(msg=error, type=ErrorType.TIMEZONE_CONSTRAINT, ctx=ErrorCtx(tz=False))

        # Expected `str` matching regex `<pattern>` - at `<Path>`
        if error.startswith('Expected `str` matching regex '):
            _, _, remaining = error.partition('matching regex ')
            ctx = get_pattern_ctx(remaining)
            if ctx is not NODEFAULT:
                return MsgspecError(msg=error, type=ErrorType.PATTERN_CONSTRAINT, ctx=ctx)
            return MsgspecError(msg=error, type=ErrorType.PATTERN_CONSTRAINT)

        # Expected `object` of length
        if error.startswith('Expected `object` '):
            remaining = error[18:]
            ctx = get_length_ctx(remaining)
            if ctx is not NODEFAULT:
                return MsgspecError(msg=error, type=ErrorType.OBJECT_LENGTH_CONSTRAINT, ctx=ctx)
            return MsgspecError(msg=error, type=ErrorType.OBJECT_LENGTH_CONSTRAINT)

        # Expected `str` of length <= 32
        if error.startswith('Expected `str` of length '):
            # Strip "Expected `str` " (15 chars), keep "of length ..." prefix
            remaining = error[15:]
            ctx = get_length_ctx(remaining)
            if ctx is not NODEFAULT:
                return MsgspecError(msg=error, type=ErrorType.LENGTH_CONSTRAINT, ctx=ctx)
            return MsgspecError(msg=error, type=ErrorType.LENGTH_CONSTRAINT)

        # Expected `bytes` of length
        if error.startswith('Expected `bytes` of length '):
            # Strip "Expected `bytes` " (17 chars), keep "of length ..." prefix
            remaining = error[17:]
            ctx = get_length_ctx(remaining)
            if ctx is not NODEFAULT:
                return MsgspecError(msg=error, type=ErrorType.LENGTH_CONSTRAINT, ctx=ctx)
            return MsgspecError(msg=error, type=ErrorType.LENGTH_CONSTRAINT)

        # Expected `int` >= 0
        if error.startswith('Expected `int` '):
            remaining = error[15:]
            if not remaining.startswith(KEY_at_check):
                ctx = get_number_ctx(remaining, expected=int)
                if ctx is not NODEFAULT:
                    return MsgspecError(msg=error, type=ErrorType.NUMERIC_CONSTRAINT, ctx=ctx)
                return MsgspecError(msg=error, type=ErrorType.NUMERIC_CONSTRAINT)

        # Expected `float`
        if error.startswith('Expected `float` '):
            remaining = error[17:]
            if not remaining.startswith(KEY_at_check):
                ctx = get_number_ctx(remaining, expected=float)
                if ctx is not NODEFAULT:
                    return MsgspecError(msg=error, type=ErrorType.NUMERIC_CONSTRAINT, ctx=ctx)
                return MsgspecError(msg=error, type=ErrorType.NUMERIC_CONSTRAINT)

        # Expected `decimal`
        if error.startswith('Expected `decimal` '):
            remaining = error[19:]
            if not remaining.startswith(KEY_at_check):
                ctx = get_number_ctx(remaining, expected=float)
                if ctx is not NODEFAULT:
                    return MsgspecError(msg=error, type=ErrorType.NUMERIC_CONSTRAINT, ctx=ctx)
                return MsgspecError(msg=error, type=ErrorType.NUMERIC_CONSTRAINT)

        # UNEXPECTED_TOKEN: "Expected `<type>` - at <Path>" without ", got"
        # Must be after all specific Expected patterns to avoid false matches.
        if KEY_at in error:
            return MsgspecError(msg=error, type=ErrorType.UNEXPECTED_TOKEN)

    # Group 4: Invalid Value Errors
    if error.startswith('Invalid enum value '):
        return MsgspecError(msg=error, type=ErrorType.INVALID_ENUM_VALUE)
    if error.startswith('Invalid value '):
        return MsgspecError(msg=error, type=ErrorType.INVALID_TAG_VALUE)

    if error.startswith('Invalid RFC3339 encoded datetime'):
        return MsgspecError(msg=error, type=ErrorType.INVALID_DATETIME)
    if error.startswith('Invalid RFC3339 encoded date'):
        return MsgspecError(msg=error, type=ErrorType.INVALID_DATE)
    if error.startswith('Invalid RFC3339 encoded time'):
        return MsgspecError(msg=error, type=ErrorType.INVALID_TIME)
    if error.startswith('Invalid ISO8601 duration'):
        return MsgspecError(msg=error, type=ErrorType.INVALID_DURATION)
    if error.startswith("Only units"):
        return MsgspecError(msg=error, type=ErrorType.UNSUPPORTED_DURATION_UNITS)

    if error.startswith('Invalid MessagePack timestamp'):
        return MsgspecError(msg=error, type=ErrorType.INVALID_MSGPACK_TIMESTAMP)
    if error.startswith('Invalid epoch timestamp'):
        return MsgspecError(msg=error, type=ErrorType.INVALID_EPOCH_TIMESTAMP)

    if error.startswith('Invalid UUID'):
        return MsgspecError(msg=error, type=ErrorType.INVALID_UUID)
    if error.startswith('Invalid base64 encoded string'):
        return MsgspecError(msg=error, type=ErrorType.INVALID_BASE64_STRING)
    if error.startswith('Invalid decimal string'):
        return MsgspecError(msg=error, type=ErrorType.INVALID_DECIMAL_STRING)

    # Group 5: Out of Range Errors
    if error.startswith('Timestamp is out of range'):
        return MsgspecError(msg=error, type=ErrorType.TIMESTAMP_OUT_OF_RANGE)
    if error.startswith('Duration is out of range'):
        return MsgspecError(msg=error, type=ErrorType.DURATION_OUT_OF_RANGE)
    if error.startswith('Integer value out of range'):
        return MsgspecError(msg=error, type=ErrorType.INTEGER_OUT_OF_RANGE)
    if error.startswith('Number out of range'):
        return MsgspecError(msg=error, type=ErrorType.NUMBER_OUT_OF_RANGE)

    # Group 6: Other Errors - Wrapped Error (fallback)
    # Any other error message that doesn't match above patterns
    return MsgspecError(msg=error, type=ErrorType.WRAPPED_ERROR)


def parse_msgspec_error(error):
    """
    Parse plain text error message like "Expected `int`, got `str` - at `$.user.profile.age`"
    into structured MsgspecError object.
    This makes msgspec more pydantic.

    Args:
        error (str | msgspec.ValidationError): The raw error message or a
            msgspec.ValidationError instance.

    Returns:
        MsgspecError: Structured error with type, location, and context.

    Examples:
        Basic usage with a raw error string::

            >>> err = parse_msgspec_error(
            ...     "Expected `int`, got `str` - at `$.user.age`")
            >>> print(err)
            MsgspecError(msg='Expected `int`, got `str` - at `$.user.age`',
                         type=<ErrorType.TYPE_MISMATCH>,
                         loc=('user', 'age'))

        With a caught msgspec.ValidationError::

            >>> try:
            ...     msgspec.json.Decoder(int).decode(b'"not_an_int"')
            ... except msgspec.ValidationError as e:
            ...     err = parse_msgspec_error(e)
            ...     print(err.type)
            <ErrorType.TYPE_MISMATCH>
            ...     print(err.loc)
            ()

        Constraint errors carry context::

            >>> from typing_extensions import Annotated
            >>> T = Annotated[int, msgspec.Meta(ge=18)]
            >>> try:
            ...     msgspec.json.Decoder(T).decode(b'15')
            ... except msgspec.ValidationError as e:
            ...     err = parse_msgspec_error(e)
            ...     print(f"type={err.type}, ge={err.ctx.ge}")
            type=<ErrorType.NUMERIC_CONSTRAINT>, ge=18
    """
    msg = str(error)
    result = get_error_type(msg)
    result.loc = get_error_path(msg)
    return result
