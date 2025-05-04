import json
import sys

from alasio.ext.path.atomic import atomic_read_bytes, atomic_write

if sys.version_info >= (3, 9):
    # Since py3.9, json.loads removed `encoding`
    def json_loads(data):
        """
        Args:
            data (bytes):

        Returns:
            Any:
        """
        return json.loads(data)
else:
    # Under py3.9, set encoding
    def json_loads(data):
        """
        Args:
            data (bytes):

        Returns:
            Any:
        """
        return json.loads(data, encoding='utf-8')


def json_dumps(obj):
    """
    Args:
        obj (Any):

    Returns:
        bytes:
    """
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False, default=str)


def read_json(file, default_factory=dict):
    """
    Read json from file, return default when having error

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
        return json_loads(data)
    except json.JSONDecodeError:
        return default_factory()


def write_json(file, obj):
    """
    Encode obj to json and write into file

    Args:
        file (str):
        obj (Any):
    """
    data = json_dumps(obj)
    atomic_write(file, data)
