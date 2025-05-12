import zlib
from zlib import decompress

import msgspec

from alasio.gitpython.file.exception import ObjectBroken

SET_VALID_MODE = {
    b'100644',  # Regular file
    b'100755',  # Executable file
    b'40000',  # Directory (reference another tree)
    b'120000',  # Symlink
    b'160000',  # Gitlink (submodule, reference another commit)
}


class EntryObject(msgspec.Struct):
    mode: bytes
    sha1: str
    name: str


def parse_tree(data):
    """
    Get a list if entries from a git tree object.

    Args:
        data (memoryview):

    Returns:
        list[EntryObject]:
    """
    try:
        remain = decompress(data)
    except zlib.error as e:
        raise ObjectBroken(str(e), data)

    list_entry = []
    append = list_entry.append
    while 1:
        # {mode} {name}\00{sha1}
        # b'100644 fleet.py\x00:\xe0\x13\xa8s\x8e\x1c\x7fL\x84\xa1\xea4\xac\x15\xda\x1f\x8e\x9b\x85'
        # b'100644 grid.py\x00A\x91\x9a1v\x14\x94\xe0\x985\xb0\x9b\x82e@\xf5\xb4\xace\x93'
        # Note that you cannot just split(b'\x00') because rows are continuous and sha1 may contain \x00
        head, _, remain = remain.partition(b'\x00')
        mode, _, name = head.partition(b' ')
        if mode not in SET_VALID_MODE:
            raise ObjectBroken(f'Invalid filemode: "{mode}"', data)
        try:
            name = name.decode('utf-8')
        except UnicodeDecodeError:
            raise ObjectBroken(f'Failed to decode filename: "{name}"', data)
        sha1 = remain[:20]
        remain = remain[20:]
        if remain:
            # Having remain (which means sha1 length=20)
            entry = EntryObject(mode=mode, sha1=sha1.hex(), name=name)
            append(entry)
        else:
            # Empty remain, let's check sha1
            if len(sha1) == 20:
                # End of tree object
                entry = EntryObject(mode=mode, sha1=sha1.hex(), name=name)
                append(entry)
                break
            else:
                # Empty sha1
                raise ObjectBroken(f'Invalid entry sha1: "{sha1.hex()}"', data)

    return list_entry
