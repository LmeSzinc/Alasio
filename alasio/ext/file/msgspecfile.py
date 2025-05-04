import msgspec
from msgspec.json import decode, encode

from alasio.ext.path.atomic import atomic_read_bytes, atomic_write


def read_msgspec(file, default_factory=dict):
    """
    Read json from file using msgspec, return default when having error

    Args:
        file (str):
        default_factory (callable):

    Returns:
        Any:
    """
    try:
        data = atomic_read_bytes(file)
    except FileNotFoundError:
        return default_factory()
    try:
        return decode(data)
    except msgspec.DecodeError:
        # msgspec.DecodeError: Input data was truncated
        return default_factory()


def write_msgspec(file, obj):
    """
    Encode obj and write into file using msgspec

    Args:
        file (str):
        obj (Any):

    Returns:
        Any:
    """
    data = encode(obj)
    atomic_write(file, data)
