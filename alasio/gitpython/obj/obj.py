from zlib import decompress

import msgspec

from alasio.ext.cache import cached_property
from alasio.gitpython.file.exception import ObjectBroken
from alasio.gitpython.obj.objblob import parse_blob
from alasio.gitpython.obj.objcommit import parse_commit
from alasio.gitpython.obj.objdelta import parse_ofs_delta, parse_ref_delta
from alasio.gitpython.obj.objtag import parse_tag
from alasio.gitpython.obj.objtree import parse_tree


class GitObject(msgspec.Struct, dict=True):
    # object type
    # 1 for commit
    # 2 for tree
    # 3 for blob
    # 4 for tag
    # 6 for OFS_DELTA
    # 7 for REF_DELTA
    type: int
    # original file size (size before compression)
    size: int
    # object data
    data: memoryview

    def decompress(self):
        """
        Only object type commit, tree, blob, tag can be decompressed
        Decompressing object type OFS_DELTA and REF_DELTA would raise ObjectBroken

        Returns:
            memoryview:

        Raises:
            ObjectBroken:
        """
        return decompress(self.data)

    @cached_property
    def decoded(self):
        objtype = self.type
        # sorted by object proportion
        # delete self.data to release reference to original file data
        if objtype == 3:
            result = parse_blob(self.data)
            del self.data
            return result
        if objtype == 6:
            result = parse_ofs_delta(self.data)
            del self.data
            return result
        if objtype == 7:
            result = parse_ref_delta(self.data)
            del self.data
            return result
        if objtype == 2:
            result = parse_tree(self.data)
            del self.data
            return result
        if objtype == 1:
            result = parse_commit(self.data)
            del self.data
            return result
        if objtype == 4:
            result = parse_tag(self.data)
            del self.data
            return result
        if objtype == 5:
            raise ObjectBroken(
                f'Object type {objtype} is a preserved value, it should not be used', self.data)
        raise ObjectBroken(
            f'Unknown object type {objtype}', self.data)


def parse_objdata(data):
    """
    The pack file format
    https://shafiul.github.io/gitbook/1_the_git_object_model.html
    https://shafiul.github.io/gitbook/7_the_packfile.html
    note that simple calculation is faster than bit calculation,
    e.g. `byte >= 128` is faster than `byte & \x80` in python,
    because python run them in int64

    Args:
        data (memoryview):

    Returns:
        GitObject:
    """
    # Decode obj type and size
    shift = 16
    objtype = 0
    size = 0
    index = 1
    # By iter, each byte auto turn into int

    # b'\xbcPx\x9c\x85'
    # b'\xbe\xaf\x01x\x9c'
    # b'\xb6\xf1\xc1\x88\x01'
    # print([d for d in data[:5]])
    for byte in data:
        if byte >= 128:
            if index > 1:
                # second and later bytes
                size += (byte - 128) * shift
                shift *= 128
            else:
                # first digit
                byte -= 128
                objtype = byte // 16
                size = byte % 16
            index += 1
        else:
            if index > 1:
                # ends at second and later byte
                size += byte * shift
                break
            else:
                # end at first byte
                objtype = byte // 16
                size = byte % 16
                break

    data = data[index:]
    return GitObject(type=objtype, size=size, data=data)
