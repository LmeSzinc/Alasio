from msgspec import NODEFAULT, Struct

from .parse_path import KEY_at, KEY_got


class ErrorCtx(Struct, omit_defaults=True):
    """
    An optional object which contains extra info
    """
    gt: "int | float | None" = None
    ge: "int | float | None" = None
    lt: "int | float | None" = None
    le: "int | float | None" = None
    multiple_of: "int | float | None" = None
    pattern: "str | None" = None
    min_length: "int | None" = None
    max_length: "int | None" = None
    tz: "bool | None" = None

    def _iter_repr_fields(self):
        # show non-None fields only
        field_names = ['gt', 'ge', 'lt', 'le', 'multiple_of', 'pattern', 'min_length', 'max_length', 'tz']
        for key in field_names:
            value = getattr(self, key, None)
            if value is not None:
                yield f'{key}={value!r}'

    def __repr__(self):
        attrs_str = ', '.join(self._iter_repr_fields())
        return f'{self.__class__.__name__}({attrs_str})'


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
        ErrorCtx | Type[NODEFAULT]:
    """
    # remove path and leave regex
    left, sep, _ = msg.rpartition(KEY_at)
    if sep:
        msg = left
    # remove paired ``
    if len(msg) >= 2 and msg.startswith("'") and msg.endswith("'"):
        msg = msg[1:-1]

    if msg:
        return ErrorCtx(pattern=msg)
    else:
        # Empty regex
        return NODEFAULT


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
        ErrorCtx | Type[NODEFAULT]:
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
                return NODEFAULT
        # of length >= 3
        if msg.startswith(KEY_ge):
            msg = msg[3:]
            try:
                return ErrorCtx(min_length=int(msg))
            except ValueError:
                return NODEFAULT
        if msg.startswith(KEY_le):
            msg = msg[3:]
            try:
                return ErrorCtx(max_length=int(msg))
            except ValueError:
                return NODEFAULT
        # 3 (bare number)
        try:
            return ErrorCtx(min_length=int(msg), max_length=int(msg))
        except ValueError:
            return NODEFAULT

    # of at (least|most) length 3
    elif msg.startswith('of at least length '):
        msg = msg[19:]
        try:
            return ErrorCtx(min_length=int(msg))
        except ValueError:
            return NODEFAULT
    elif msg.startswith('of at most length '):
        msg = msg[18:]
        try:
            return ErrorCtx(max_length=int(msg))
        except ValueError:
            return NODEFAULT
    else:
        # No match
        return NODEFAULT


def get_number_ctx(msg, expected=int):
    """
    Args:
        msg (str): Error message with reason removed
            ">= 3"
            "<= 32"
            "that's a multiple of 6"
        expected (callable): Parser for constraint values.
            int (default), float, etc. Used to convert the extracted
            value string into the appropriate type.

    Returns:
        ErrorCtx | Type[NODEFAULT]:
    """
    msg, _, _ = msg.partition(KEY_at)
    # Remove trailing ", got <actual>" if present
    msg, _, _ = msg.partition(KEY_got)
    if msg.startswith(KEY_ge):
        msg = msg[3:]
        try:
            return ErrorCtx(ge=expected(msg))
        except ValueError:
            return NODEFAULT
    if msg.startswith(KEY_le):
        msg = msg[3:]
        try:
            return ErrorCtx(le=expected(msg))
        except ValueError:
            return NODEFAULT
    if msg.startswith(KEY_gt):
        msg = msg[2:]
        try:
            return ErrorCtx(gt=expected(msg))
        except ValueError:
            return NODEFAULT
    if msg.startswith(KEY_lt):
        msg = msg[2:]
        try:
            return ErrorCtx(lt=expected(msg))
        except ValueError:
            return NODEFAULT
    if KEY_multiple_of in msg:
        _, _, msg = msg.rpartition(KEY_multiple_of)
        try:
            return ErrorCtx(multiple_of=expected(msg))
        except ValueError:
            return NODEFAULT
    # unknown
    return NODEFAULT
