import builtins
import keyword
import re

from alasio.base.op import random_id
from alasio.ext.path.validate import validate_filename


def validate_asset_name(s):
    """
    Validate if a string is a valid asset name.

    A valid asset name must:
    - Be non-empty
    - Contain only a-zA-Z0-9 and underscore
    - Not start with a digit
    - Not be a Python keyword
    - Not be a Python builtin name

    Args:
        s (str): String to validate

    Raises:
        ValueError: If the string is not a valid asset name, with detailed reason
    """
    validate_filename(s)

    if s.startswith('~'):
        raise ValueError(f'Asset name cannot start with "~", otherwise being considered as source file')

    # Check if contains only valid characters (a-zA-Z0-9_)
    if not re.match(r'^[a-zA-Z0-9_]+$', s):
        invalid_chars = set(re.findall(r'[^a-zA-Z0-9_]', s))
        raise ValueError(
            f'Asset name contains invalid characters: "{sorted(invalid_chars)}". '
            'Only a-zA-Z0-9 and underscore are allowed'
        )

    # Check if starts with digit
    if s[0].isdigit():
        raise ValueError(
            f'Asset name cannot start with digit "{s[0]}". It must start with a letter or underscore'
        )

    # Check if is Python keyword
    if keyword.iskeyword(s):
        raise ValueError(f'Asset name "{s}" is a Python keyword and cannot be used')

    # Check if is builtin
    if s in dir(builtins):
        raise ValueError(f'Asset name "{s}" is a Python builtin name and cannot be used')

    return True


def validate_resource_name(s):
    """
    Validate if a string is a valid resource name.

    A valid asset name must:
    - Be non-empty
    - Startswith "~"

    Args:
        s (str): String to validate

    Raises:
        ValueError: If the string is not a valid resource name, with detailed reason
    """
    validate_filename(s)
    if not s.startswith('~'):
        raise ValueError(f'Source file must start with "~", got "{s}"')


def to_asset_name(s):
    """
    Convert any text to asset name,
    which is a valid python variable name and uses a-zA-Z0-9 only

    Args:
        s (str):

    Returns:
        str:
    """
    if s.startswith('~'):
        s = s.lstrip('~')
    if not s:
        return f'ASSET_{random_id()}'

    # replace
    s = re.sub(r'[^a-zA-Z0-9_]', '_', s)
    # Collapse consecutive underscores into single underscore
    s = re.sub(r'_+', '_', s).strip('_')
    if not s:
        return f'ASSET_{random_id()}'

    # cannot startswith digit
    if s[0].isdigit():
        s = f'ASSET_{s}'
    # cannot be python keywords
    if keyword.iskeyword(s):
        return f'ASSET_{s}'
    # cannot be builtin functions
    if s in dir(builtins):
        return f'ASSET_{s}'

    return s
