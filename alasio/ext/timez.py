import datetime
from typing import Union


def get_local_tz() -> datetime.tzinfo:
    """
    The local timezone of the system.

    The call is isolated here so that the tests can patch it, to_local_naive()
    and to_local_aware() read the local timezone from this function only. The
    tzinfo is the fixed offset the system has now, the DST rules of a zone are
    not part of it.

    Returns:
        datetime.tzinfo: The local timezone
    """
    return datetime.datetime.now().astimezone().tzinfo


def _parse_isoformat(text):
    """
    datetime.fromisoformat() with the 'Z' suffix of UTC.

    The suffix is not supported by fromisoformat before Python 3.11.

    Args:
        text (str): An ISO 8601 formatted string

    Returns:
        datetime.datetime:

    Raises:
        ValueError: If the string is not a valid ISO 8601 format
    """
    if text.endswith('Z') or text.endswith('z'):
        text = text[:-1] + '+00:00'
    return datetime.datetime.fromisoformat(text)


def _to_datetime(time_input):
    """
    Convert a time input into a datetime object, as it is given.

    Args:
        time_input (str | datetime.datetime): ISO 8601 text, or a datetime
            object, aware or naive

    Returns:
        datetime.datetime: The datetime of the input, aware or naive as given

    Raises:
        TypeError: If the input is not a str or datetime.datetime object
        ValueError: If the string is not a valid ISO 8601 format
    """
    if isinstance(time_input, str):
        return _parse_isoformat(time_input)
    if isinstance(time_input, datetime.datetime):
        return time_input
    raise TypeError('Input must be a str or datetime.datetime object')


def fromisoformat(text) -> datetime.datetime:
    """
    A backport of datetime.fromisoformat that supports the 'Z' suffix for UTC
    on Python versions prior to 3.11.

    On Python 3.11+, this function is redundant but harmless. On older versions,
    it provides crucial missing functionality by translating 'Z' to '+00:00'
    before parsing.

    Args:
        text (str): An ISO 8601 formatted string. e.g., '2023-10-27T15:30:00Z'

    Returns:
        datetime.datetime:

    Raises:
        ValueError:
        TypeError:
    """
    if isinstance(text, str):
        return _parse_isoformat(text)
    return datetime.datetime.fromisoformat(text)


def to_local_naive(time_input: "Union[str, datetime.datetime]") -> datetime.datetime:
    """
    Converts a time input (string or datetime object) to a naive datetime
    object representing the local time.

    Logic:
    - If the input is timezone-aware, it's converted to the local timezone,
      and then the timezone info is removed.
    - If the input is timezone-naive, it's assumed to be in local time
      already and is returned as is.

    Args:
        time_input (Union[str, datetime.datetime]):
            - An ISO 8601 formatted string (e.g., '2023-10-27T15:30:00+08:00').
            - Or a datetime.datetime object (either aware or naive).

    Returns:
        datetime.datetime: A naive datetime object (tzinfo=None) in local time.

    Raises:
        TypeError: If the input is not a string or datetime object.
        ValueError: If the string is not a valid ISO 8601 format.
    """
    # 1. Unify the input into a datetime object
    dt_obj = _to_datetime(time_input)

    # 2. Process based on whether the datetime object is naive or aware
    if not dt_obj.tzinfo:
        # Input is a naive object -> assume it's already in local time and return it
        return dt_obj
    # Input is an aware object -> convert it to the local timezone,
    # get_local_tz() is the patchable source of it
    return dt_obj.astimezone(get_local_tz()).replace(tzinfo=None)


def to_local_aware(time_input: "Union[str, datetime.datetime]") -> datetime.datetime:
    """
    Converts a time input (string or datetime object) to an aware datetime
    object representing the local time with local timezone information.

    Logic:
    - If the input is timezone-aware, it's converted to the local timezone.
    - If the input is timezone-naive, it's assumed to be local time, and
      local timezone info is attached to it.

    Args:
        time_input (Union[str, datetime.datetime]):
            - An ISO 8601 formatted string (e.g., '2023-10-27T15:30:00+08:00').
            - Or a datetime.datetime object (either aware or naive).

    Returns:
        datetime.datetime: An aware datetime object with local timezone info.

    Raises:
        TypeError: If the input is not a string or datetime object.
        ValueError: If the string is not a valid ISO 8601 format.
    """
    # 1. Unify the input into a datetime object
    dt_obj = _to_datetime(time_input)

    # get_local_tz() is the patchable source of the local timezone
    local_tz = get_local_tz()

    # 2. Process based on whether the datetime object is naive or aware
    if dt_obj.tzinfo:
        # Input is an aware object -> convert it to the local timezone
        return dt_obj.astimezone(local_tz)
    else:
        # Input is a naive object -> assume it's local time and attach timezone info
        return dt_obj.replace(tzinfo=local_tz)
