from hashlib import sha1
from zlib import decompress

from alasio.ext.gitpython.eol import eol_crlf_remove
from alasio.ext.path.atomic import atomic_read_bytes

DICT_TYPE_TO_HEAD = {
    1: b'commit ',
    2: b'tree ',
    3: b'blob ',
    4: b'tag ',
}


def git_hash(data):
    """
    Calculate the git sha1 hash from binary data

    Args:
        data (bytes): File content

    Returns:
        str: sha1
    """
    # {objtype} {length}\x00{data}
    data = b'blob ' + f'{len(data)}'.encode() + b'\0' + data

    return sha1(data).hexdigest()


def git_file_hash(file):
    """
    Calculate the git sha1 hash from file

    Args:
        file (str): Absolute path to file

    Returns:
        str: sha1

    Raises:
        FileNotFountError:
    """
    data = atomic_read_bytes(file)
    data = eol_crlf_remove(file, data)
    sha1 = git_hash(data)
    return sha1


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
