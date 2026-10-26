import re


def validate_task_name(name: str) -> bool:
    """
    Validate a task name, group name or arg name.
    Must match ^[A-Z][a-zA-Z0-9]*$

    Args:
        name (str): Name to validate, a non-str is invalid

    Returns:
        bool: True if the name is valid, False for other types
    """
    try:
        return bool(re.match(r'^[A-Z][a-zA-Z0-9]*$', name))
    except TypeError:
        # Not a str, e.g. a yaml key like `1:`
        return False


def validate_nav_name(name: str) -> bool:
    """
    Validate a nav name.
    Must match ^[a-z][a-z0-9]*$

    Args:
        name (str): Name to validate, a non-str is invalid

    Returns:
        bool: True if the name is valid, False for other types
    """
    try:
        return bool(re.match(r'^[a-z][a-z0-9]*$', name))
    except TypeError:
        # Not a str, e.g. a yaml key like `1:`
        return False
