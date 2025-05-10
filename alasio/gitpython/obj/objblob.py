import zlib
from zlib import decompress

from alasio.gitpython.file.exception import ObjectBroken


def parse_blob(data):
    """
    Get file data from a git blob object

    Args:
        data (memoryview):

    Returns:
        memoryview:
    """
    try:
        data = decompress(data)
    except zlib.error as e:
        raise ObjectBroken(str(e), data)
    return memoryview(data)
