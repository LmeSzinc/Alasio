import datetime
from typing import Union


def get_local_tz() -> datetime.tzinfo:
    """
    Isolates the call to datetime.now() so it can be easily patched during tests.
    Returns the current system's local timezone info object.
    """
    return datetime.datetime.now().astimezone().tzinfo


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
        if text.endswith('Z') or text.endswith('z'):
            text = text[:-1] + '+00:00'
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
    if isinstance(time_input, str):
        # The 'Z' suffix is not supported in fromisoformat before Python 3.11.
        if time_input.endswith('Z') or time_input.endswith('z'):
            time_input = time_input[:-1] + '+00:00'
        dt_obj = datetime.datetime.fromisoformat(time_input)

    elif isinstance(time_input, datetime.datetime):
        dt_obj = time_input
    else:
        raise TypeError('Input must be a str or datetime.datetime object')

    # 2. Process based on whether the datetime object is naive or aware
    if dt_obj.tzinfo:
        # Input is an aware object -> convert it to the system's local timezone.
        # Calling astimezone() with no argument converts to the local timezone.
        local_dt = dt_obj.astimezone(None)
        # Remove timezone info to make it naive
        return local_dt.replace(tzinfo=None)
    else:
        # Input is a naive object -> assume it's already in local time and return it
        return dt_obj


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
    if isinstance(time_input, str):
        # The 'Z' suffix is not supported in fromisoformat before Python 3.11.
        if time_input.endswith('Z') or time_input.endswith('z'):
            time_input = time_input[:-1] + '+00:00'
        dt_obj = datetime.datetime.fromisoformat(time_input)
    elif isinstance(time_input, datetime.datetime):
        dt_obj = time_input
    else:
        raise TypeError('Input must be a str or datetime.datetime object')

    # Get the local timezone object
    local_tz = datetime.datetime.now().astimezone().tzinfo

    # 2. Process based on whether the datetime object is naive or aware
    if dt_obj.tzinfo:
        # Input is an aware object -> convert it to the local timezone
        return dt_obj.astimezone(local_tz)
    else:
        # Input is a naive object -> assume it's local time and attach timezone info
        return dt_obj.replace(tzinfo=local_tz)
