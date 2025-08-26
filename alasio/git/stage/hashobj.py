from hashlib import sha1
from zlib import compressobj

from alasio.ext.path.atomic import atomic_read_bytes
from alasio.git.eol import eol_crlf_remove


def blob_hash(data):
    """
    Calculate the git sha1 hash from binary data

    Args:
        data (bytes | memoryview): File content

    Returns:
        str: sha1
    """
    # {objtype} {length}\x00{data}

    # data = b''.join([b'blob ', f'{len(data)}'.encode(), b'\0', data])
    # return sha1(data).hexdigest()

    # 1.05x time cost, but less memory impact
    header = b''.join([b'blob ', f'{len(data)}'.encode(), b'\0'])
    sha = sha1(header)
    sha.update(data)
    return sha.hexdigest()


def encode_loosedata(data):
    """
    Create content of loose object from raw file content

    Args:
        data (bytes | memoryview):

    Returns:
        bytes:
    """
    # data = b''.join([b'blob ', f'{len(data)}'.encode(), b'\0', data])
    # return compress(data, level=9)

    # 0.96x timecost, and also less memory impact
    compressor = compressobj(level=9)
    header = b''.join([b'blob ', f'{len(data)}'.encode(), b'\0'])
    c1 = compressor.compress(header)
    c2 = compressor.compress(data)
    c3 = compressor.flush()
    return b''.join([c1, c2, c3])


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
    sha = blob_hash(data)
    return sha
