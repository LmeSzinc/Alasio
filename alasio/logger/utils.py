import sys
from collections import deque
from types import TracebackType
from typing import Any, Optional, Tuple, Type

from exceptiongroup import BaseExceptionGroup

ExcInfo = Tuple[Type[BaseException], BaseException, Optional[TracebackType]]


# from structlog.processors._figure_out_exc_info
def figure_out_exc_info(v: Any) -> Optional[ExcInfo]:
    """
    Try to convert *v* into an ``exc_info`` tuple.

    Return ``None`` if *v* does not represent an exception or if there is no
    current exception.
    """
    if isinstance(v, BaseException):
        return (v.__class__, v, v.__traceback__)

    if isinstance(v, tuple) and len(v) == 3:
        has_type = isinstance(v[0], type) and issubclass(v[0], BaseException)
        has_exc = isinstance(v[1], BaseException)
        has_tb = v[2] is None or isinstance(v[2], TracebackType)
        if has_type and has_exc and has_tb:
            return v

    if v:
        result = sys.exc_info()
        if result == (None, None, None):
            return None
        return result

    return None


# a function that accepts anything and does nothing
def empty_function(*args, **kwargs):
    pass


class SafeDict(dict):
    def __missing__(self, key):
        # Return placeholder when key is missing, for better debugging
        return f"<key {key} missing>"


def has_user_keys(event_dict):
    """
    Check if event_dict contains user-provided keys (not just built-in fields)
    Built-in fields: 'event', 'exception'
    """
    # Fast path: empty or single item (must be built-in)
    if not event_dict:
        return False

    # Calculate how many built-in fields are present
    length = len(event_dict)
    builtin_count = 0
    if 'exception' in event_dict:
        builtin_count += 1
    return length > builtin_count


def event_format(event, event_dict):
    """
    build message from `event.format(event_dict)`, ignore errors
    check event_dict also, if someone log like this:
      modules = set('combat_ui')
      logger.info(f'Assets generate, modules={modules}')
    we won't log:
      Assets generate, modules=<key 'combat_ui' missing>

    Args:
        event (str):
        event_dict (dict[str, Any]):

    Returns:
        str:
    """
    if '{' in event and event_dict and has_user_keys(event_dict):
        try:
            event = event.format(**event_dict)
        except KeyError:
            try:
                event = event.format_map(SafeDict(event_dict))
            except Exception:
                pass
        except Exception:
            # maybe {} is unpaired
            pass
    return event


def join_event_dict(event, event_dict):
    """
    Format event_dict into event, like:
        Hello {user}, user='May'

    Args:
        event (str):
        event_dict (dict[str, Any]):

    Returns:
        str:
    """
    if not event_dict:
        return event
    items = [f'{k}={repr(v)}' for k, v in event_dict.items() if k != 'exception']
    if not items:
        return event
    items.insert(0, event)
    event = ', '.join(items)
    return event


def gen_exception_tree(exc: BaseException):
    """
    Generate exception tree like:
    - sub exception 1, depth 1
      - sub exception 1, depth 2
    - sub exception 2
      - sub exception 2, depth 1
      - sub exception 2, depth 2
    """
    # (exc, depth)
    stack = deque()
    stack.append((exc, 0))

    while stack:
        current_exc, depth = stack.pop()

        prefix = "  " * depth
        yield f'{prefix}- {type(current_exc).__name__}: {current_exc}'

        if BaseExceptionGroup and isinstance(current_exc, BaseExceptionGroup):
            children = [(e, depth + 1) for e in current_exc.exceptions]
            stack.extend(reversed(children))


def replace_unicode_table(output):
    """
    Convert unicode table to ascii

    Args:
        output (str):

    Returns:
        str:
    """
    mapping = {
        "┌": "+", "┐": "+", "└": "+", "┘": "+",
        "├": "+", "┤": "+", "┬": "+", "┴": "+",
        "┼": "+", "─": "-", "│": "|", "═": "=",
        "║": "|", "╒": "+", "╓": "+", "╔": "+",
        "╕": "+", "╖": "+", "╗": "+", "╘": "+",
        "╙": "+", "╚": "+", "╛": "+", "╜": "+",
        "╝": "+", "╞": "+", "╟": "+", "╠": "+",
        "╡": "+", "╢": "+", "╣": "+", "╤": "+",
        "╥": "+", "╦": "+", "╧": "+", "╨": "+",
        "╩": "+", "╪": "+", "╫": "+", "╬": "+",
        "…": "~",
    }
    for unicode_char, ascii_char in mapping.items():
        output = output.replace(unicode_char, ascii_char)
    return output
