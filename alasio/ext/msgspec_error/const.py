from enum import Enum


# StrEnum is introduced in py3.11
class ErrorType(str, Enum):
    """
    An enumeration of all possible `ValidationError` forms from msgspec,
    grouped by the literal error message format.

    Each member documents the error format, provides an example, and explains
    the various conditions that can trigger it. The placeholder `<Path>`
    represents a JSONPath-like string (e.g., "$.field") indicating the
    error's location.
    """

    def __repr__(self):
        return f'<{self.__class__.__name__}.{self.name}>'

    # ======================================================================
    # Group 1: Type Mismatch Errors (Format: "Expected ..., got ...")
    # ======================================================================

    TYPE_MISMATCH = "TYPE_MISMATCH"
    """
    Format: "Expected <A>, got <B> - at <Path>"

    This is the most common category of error, triggered when a decoded value's
    type is incompatible with the expected schema type.
    Note that if <path> is root, " - at <path>" won't be shown

    Examples:
    - "Expected `int`, got `str` - at `$.age`"
    - "Expected `str`, got `int` - at $.name"
    - "Expected `MyCustomClass`, got `str` - at `$.custom_field`"
    - "Expected `int`, got `str` - at `$.type`" (for tagged unions)

    Triggered by:
    1.  **General Mismatch**: A decoded value's basic type (str, int, list, etc.)
        doesn't match the target Python type.
    2.  **MsgPack-Specific Mismatch**: A msgpack-encoded value (e.g., a msgpack int)
        cannot be coerced into the target Python type (e.g., str).
    3.  **Custom Type Mismatch**: A `dec_hook` returns an object that is not an
        instance of the expected custom type.
    4.  **Tag Type Mismatch**: The tag field in a tagged union has an incorrect
        type (e.g., an `int` was found when a `str` was expected).
    """

    # ======================================================================
    # Group 2: Structural Errors (Format: "Object ... <field_name> ...")
    # ======================================================================

    MISSING_FIELD = "MISSING_FIELD"
    """
    Format: "Object missing required field `<field_name>` - at `<Path>`"
    Example: "Object missing required field `id`"
    Triggered by: A Struct, TypedDict, or dataclass is missing a required field
                 that has no default value.
    """

    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    """
    Format: "Object contains unknown field `<field_name>` - at `<Path>`"
    Example: "Object contains unknown field 'favorite_color' - at `$`"
    Triggered by: Decoding a Struct with `forbid_unknown_fields=True` and encountering
                 a field in the message that is not defined on the struct.
    """

    # ======================================================================
    # Group 3: Constraint and Length Errors
    # ======================================================================

    ARRAY_LENGTH_CONSTRAINT = "ARRAY_LENGTH_CONSTRAINT"
    """
    Format: "Expected `array` of at (least|most) length <expected>, got <actual> - at <Path>"
            "Expected `array` of length <min> to <max>, got <actual> - at <Path>"
            "Expected `array` of length <expected> - at <Path>"
    Example: "Expected `array` of length 2, got 3 - at $.coordinates"
    Triggered by: Decoding a fixed-size tuple (e.g., tuple[int, int]) or a NamedTuple,
                 and the array in the message has a different number of elements.
    """

    OBJECT_LENGTH_CONSTRAINT = "OBJECT_LENGTH_CONSTRAINT"
    """
    Format: "Expected `object` of length <op> <value> - at <Path>"
    Example: "Expected `object` of length >= 1 - at $.metadata"
    Triggered by: A dictionary-like object (`dict`, `TypedDict`, `Struct`) violates
                 a `min_length` or `max_length` constraint defined in a `Meta` object.
                 Note: "object" here is JSON/MsgPack terminology for a key-value map.
    """

    LENGTH_CONSTRAINT = "LENGTH_CONSTRAINT"
    """
    Format: "Expected <type> of length <op> <value> - at <Path>"
    Example: "Expected str of length <= 32 - at $.name"
    Triggered by: A variable-length sequence (`list`, `set`, `frozenset`) or a sized
                 scalar (`str`, `bytes`) violates a `min_length` or `max_length`
                 constraint defined in a `Meta` object.
    """

    PATTERN_CONSTRAINT = "PATTERN_CONSTRAINT"
    """
    Format: "Expected str matching regex <pattern> - at <Path>"
    Example: "Expected str matching regex '\\d{4}-\\d{2}-\\d{2}' - at $.date_str"
    Triggered by: A string value does not match the regex `pattern` constraint from
                 a `Meta` object.
    """

    NUMERIC_CONSTRAINT = "NUMERIC_CONSTRAINT"
    """
    Format: "Expected <type> <op> <value> - at <Path>"
            "Expected <type> that's a multiple of <value> - at <Path>"
    Example: "Expected `int` >= 0 - at $.age"
             "Expected `int` that's a multiple of 6"
    Triggered by: A numeric value violates a constraint from a `Meta` object (e.g.,
                 ge, lt, multiple_of).
    """

    TIMEZONE_CONSTRAINT = "TIMEZONE_CONSTRAINT"
    """
    Format: "Expected datetime with (a|no) timezone component - at <Path>"
    Triggered by: A datetime/time object does not satisfy the `tz=True` (aware) or
                 `tz=False` (naive) constraint from a `Meta` object.
    """

    # ======================================================================
    # Group 4: Invalid Value Errors
    # ======================================================================

    INVALID_ENUM_VALUE = "INVALID_ENUM_VALUE"
    """
    Format: "Invalid enum value <value> - at <Path>"
    Example: "Invalid enum value 'admin' - at $.role"
    Triggered by: A value that is not a valid member of the target `Enum` or
                 `Literal` type.
    """

    INVALID_TAG_VALUE = "INVALID_TAG_VALUE"
    """
    Format: "Invalid value <value>" or "Invalid value `<value>`" - at <Path>
    Example: "Invalid value 3 - at $.type"
    Triggered by: (Tagged Unions) The tag field's type is correct (e.g., str, int),
                 but its specific value does not match any of the expected tag
                 values for the types in the union.
    """

    INVALID_DATETIME = "INVALID_DATETIME"
    """
    Format: "Invalid RFC3339 encoded datetime - at <Path>"
    Example: "Invalid RFC3339 encoded datetime - at $.timestamp"
    Triggered by: A string intended for a `datetime.datetime` type is malformed.
    """

    INVALID_DATE = "INVALID_DATE"
    """
    Format: "Invalid RFC3339 encoded date - at <Path>"
    Example: "Invalid RFC3339 encoded date - at $.birth_date"
    Triggered by: A string intended for a `datetime.date` type is malformed.
    """

    INVALID_TIME = "INVALID_TIME"
    """
    Format: "Invalid RFC3339 encoded time - at <Path>"
    Example: "Invalid RFC3339 encoded time - at $.event_time"
    Triggered by: A string intended for a `datetime.time` type is malformed.
    """

    INVALID_DURATION = "INVALID_DURATION"
    """
    Format: "Invalid ISO8601 duration - at <Path>"
    Example: "Invalid ISO8601 duration - at $.period"
    Triggered by: A string intended for a `datetime.timedelta` type is malformed.
    """

    UNSUPPORTED_DURATION_UNITS = "UNSUPPORTED_DURATION_UNITS"
    """
    Format: "Only units 'D', 'H', 'M', and 'S' are supported when parsing ISO8601 durations - at <Path>"
    Triggered by: An ISO8601 duration string contains unsupported units like
                 'W' (weeks) or 'Y' (years).
    """

    INVALID_MSGPACK_TIMESTAMP = "INVALID_MSGPACK_TIMESTAMP"
    """
    Format: "Invalid MessagePack timestamp[: nanoseconds out of range] - at <Path>"
    Triggered by: (MsgPack only) A MessagePack extension with type code -1
                 (timestamp) is malformed due to incorrect data length or an
                 out-of-bounds nanosecond value.
    """

    INVALID_UUID = "INVALID_UUID"
    """
    Format: "Invalid UUID - at <Path>"
            "Invalid UUID bytes - at <Path>"
    Triggered by: A string or byte sequence intended for a UUID type is malformed.
    """

    INVALID_BASE64_STRING = "INVALID_BASE64_STRING"
    """
    Format: "Invalid base64 encoded string - at <Path>"
    Triggered by: A string intended for a bytes/bytearray/memoryview type is not
                 valid base64.
    """

    INVALID_DECIMAL_STRING = "INVALID_DECIMAL_STRING"
    """
    Format: "Invalid decimal string - at <Path>"
    Triggered by: A string intended for a `decimal.Decimal` type cannot be parsed.
    """

    INVALID_EPOCH_TIMESTAMP = "INVALID_EPOCH_TIMESTAMP"
    """
    Format: "Invalid epoch timestamp - at <Path>"
    Triggered by: A non-finite float (inf, -inf, nan) is being decoded as
                 a datetime object.
    """

    # ======================================================================
    # Group 5: Out of Range Errors
    # ======================================================================

    TIMESTAMP_OUT_OF_RANGE = "TIMESTAMP_OUT_OF_RANGE"
    """
    Format: "Timestamp is out of range - at <Path>"
    Triggered by: A numeric or string value that represents a datetime outside the
                 range supported by Python's `datetime` object.
    """

    DURATION_OUT_OF_RANGE = "DURATION_OUT_OF_RANGE"
    """
    Format: "Duration is out of range - at <Path>"
    Triggered by: A numeric or string value that represents a duration outside the
                 range supported by Python's `timedelta` object.
    """

    INTEGER_OUT_OF_RANGE = "INTEGER_OUT_OF_RANGE"
    """
    Format: "Integer value out of range - at <Path>"
    Triggered by: A string representing an integer is too large to be processed
                 (e.g., has more than 4300 digits).
    """

    NUMBER_OUT_OF_RANGE = "NUMBER_OUT_OF_RANGE"
    """
    Format: "Number out of range - at <Path>"
    Triggered by: A string representing a number (int or float) is outside the
                 range of a `double` precision float.
    """

    # ======================================================================
    # Group 6: Other Errors
    # ======================================================================

    WRAPPED_ERROR = "WRAPPED_ERROR"
    """
    Format: "<Original Error Message> - at <Path>"
    Example: "passwords cannot be the same - at $"
    Triggered by: `msgspec` catches a `TypeError` or `ValueError` from user code
                 (e.g., in a `dec_hook`, `__post_init__`, or a custom type's
                 `__init__`) and wraps it in a `ValidationError`.
    """

    UNICODE_DECODE_ERROR = 'UNICODE_DECODE_ERROR'
    """
    Our custom error type to wrap UnicodeDecodeError.
    msgspec doesn't give detail loc of UnicodeDecodeError, we will do that.
    Format: 'utf-8' codec can't decode byte 0x80 in position 3: invalid start byte
    Triggered by: Invalid unicode in bytes
    """
