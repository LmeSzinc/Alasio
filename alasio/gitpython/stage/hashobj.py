from hashlib import sha1
from zlib import decompress

DICT_TYPE_TO_HEAD = {
    1: b'commit ',
    2: b'tree ',
    3: b'blob ',
    4: b'tag ',
}


def git_hash(data):
    """
    Calculate the git sha1 hash for a binary file

    Args:
        data (bytes): File content
        text (bool): Whether to remove CRLF
            Use False in binary files

    Returns:
        str: sha1
    """
    # {objtype} {length}\x00{data}
    data = b'blob ' + f'{len(data)}'.encode() + b'\0' + data

    return sha1(data).hexdigest()


def obj_hash(objtype, data):
    """
    Re-calculate sha1 from git object data, which is zlib compressed
    usually to be used to validate hash

    Args:
        objtype (int):
        data (bytes):

    Raises:
        KeyError: If git won't compress object type, so no need to validate
        zlib.error: If data broken
    """
    head = DICT_TYPE_TO_HEAD[objtype]
    data = decompress(data)

    # {objtype} {length}\x00{data}
    data = head + f'{len(data)}'.encode() + b'\0' + data
    return sha1(data).digest()
