from collections import deque
from typing import Tuple, Union

from msgspec import NODEFAULT, Struct

from .const import ErrorType
from .path import KEY_at, KEY_got, get_error_path


class ErrorCtx(Struct, omit_defaults=True):
    """
    An optional object which contains extra info
    """
    gt: Union[int, float, None] = None
    ge: Union[int, float, None] = None
    lt: Union[int, float, None] = None
    le: Union[int, float, None] = None
    multiple_of: Union[int, float, None] = None
    pattern: Union[str, None] = None
    min_length: Union[int, None] = None
    max_length: Union[int, None] = None
    tz: Union[bool, None] = None

    def __repr__(self):
        # show non-None fields only
        field_names = ['gt', 'ge', 'lt', 'le', 'multiple_of', 'pattern', 'min_length', 'max_length', 'tz']

        attrs = deque()
        for key in field_names:
            value = getattr(self, key, None)
            if value is not None:
                attrs.append(f'{key}={value!r}')

        if attrs:
            attrs_str = ', '.join(attrs)
            return f'{self.__class__.__name__}({attrs_str})'
        else:
            return f'{self.__class__.__name__}()'


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
    ctx: ErrorCtx


KEY_to = ' to '
KEY_gt = '> '
KEY_ge = '>= '
KEY_lt = '< '
KEY_le = '<= '
KEY_multiple_of = 'multiple of '


def get_pattern_ctx(msg):
    """
    Args:
        msg (str): Error message with reason removed
            "`<pattern>` - at `<Path>`"

    Returns:
        ErrorCtx: with `pattern` set
            If failed to parse, return empty ErrorCtx
    """
    # remove path and leave regex
    msg, _, _ = msg.rpartition(KEY_at)
    # remove paired ``
    if len(msg) >= 2 and msg.startswith("'") and msg.endswith("'"):
        msg = msg[1:-1]

    if msg:
        return ErrorCtx(pattern=msg)
    else:
        # Empty regex
        return ErrorCtx()


def get_length_ctx(msg):
    """
    Args:
        msg (str): Error message with reason removed
            "of at (least|most) length 3"
            "of length <expected>, got <actual> - at <Path>"
            "of length <min> to <max>"
            "of length >= 3"
            "of length 5"

    Returns:
        ErrorCtx: with `min_length` or `max_length` set
            If failed to parse, return empty ErrorCtx
    """
    msg, _, _ = msg.partition(KEY_at)
    msg, _, _ = msg.partition(KEY_got)
    if msg.startswith('of length '):
        msg = msg[10:]
        # of length <min> to <max>
        if KEY_to in msg:
            min_length, _, max_length = msg.partition(KEY_to)
            try:
                return ErrorCtx(min_length=int(min_length), max_length=int(max_length))
            except ValueError:
                return ErrorCtx()
        # of length >= 3
        if msg.startswith(KEY_ge):
            msg = msg[3:]
            try:
                return ErrorCtx(min_length=int(msg))
            except ValueError:
                return ErrorCtx()
        if msg.startswith(KEY_le):
            msg = msg[3:]
            try:
                return ErrorCtx(max_length=int(msg))
            except ValueError:
                return ErrorCtx()
        # 3 (bare number)
        try:
            return ErrorCtx(min_length=int(msg), max_length=int(msg))
        except ValueError:
            return ErrorCtx()

    # of at (least|most) length 3
    elif msg.startswith('of at least length '):
        msg = msg[19:]
        try:
            return ErrorCtx(min_length=int(msg))
        except ValueError:
            return ErrorCtx()
    elif msg.startswith('of at most length '):
        msg = msg[18:]
        try:
            return ErrorCtx(max_length=int(msg))
        except ValueError:
            return ErrorCtx()
    else:
        # No match
        return ErrorCtx()


def get_number_ctx(msg):
    """
    Args:
        msg (str): Error message with reason removed
            ">= 3"
            "<= 32"
            "that's a multiple of 6"

    Returns:
        ErrorCtx: with `ge`, or `gt`, or `le`, or `lt` set
            If failed to parse, return empty ErrorCtx
    """
    msg, _, _ = msg.partition(KEY_at)
    if msg.startswith(KEY_ge):
        msg = msg[3:]
        try:
            return ErrorCtx(ge=int(msg))
        except ValueError:
            return ErrorCtx()
    if msg.startswith(KEY_le):
        msg = msg[3:]
        try:
            return ErrorCtx(le=int(msg))
        except ValueError:
            return ErrorCtx()
    if msg.startswith(KEY_gt):
        msg = msg[2:]
        try:
            return ErrorCtx(gt=int(msg))
        except ValueError:
            return ErrorCtx()
    if msg.startswith(KEY_lt):
        msg = msg[2:]
        try:
            return ErrorCtx(lt=int(msg))
        except ValueError:
            return ErrorCtx()
    if KEY_multiple_of in msg:
        _, _, msg = msg.rpartition(KEY_multiple_of)
        try:
            return ErrorCtx(multiple_of=int(msg))
        except ValueError:
            return ErrorCtx()
    # unknown
    return ErrorCtx()


def get_error_type(error):
    """
    Args:
        error (str):

    Returns:
        tuple[ErrorType, ErrorCtx]: (error_type, context)
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
            ctx = get_number_ctx(remaining)
            return ErrorType.NUMERIC_CONSTRAINT, ctx

        # Expected `float`
        if error.startswith('Expected `float` '):
            remaining = error[17:]
            ctx = get_number_ctx(remaining)
            return ErrorType.NUMERIC_CONSTRAINT, ctx

        # Expected `decimal`
        if error.startswith('Expected `decimal` '):
            remaining = error[19:]
            ctx = get_number_ctx(remaining)
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
    Args:
        error (msgspec.ValidationError):

    Returns:
        MsgspecError:
    """
    msg = str(error)
    typ, ctx = get_error_type(msg)
    loc = get_error_path(msg)
    if ctx is NODEFAULT:
        ctx = ErrorCtx()
        return MsgspecError(msg=msg, type=typ, loc=loc, ctx=ctx)
    else:
        return MsgspecError(msg=msg, type=typ, loc=loc, ctx=ctx)
