from hashlib import sha1
from zlib import decompress, decompressobj, error as zlib_error

import msgspec

from alasio.ext.cache import cached_property
from alasio.git.file.exception import ObjectBroken
from alasio.git.obj.objcommit import parse_commit
from alasio.git.obj.objdelta import apply_delta, parse_ofs_delta, parse_ref_delta
from alasio.git.obj.objtag import parse_tag
from alasio.git.obj.objtree import parse_tree

OBJTYPE_BASIC = {1, 2, 3, 4}
OBJTYPE_DELTA = {6, 7}
DICT_OBJTYPE_TO_HEADER = {
    1: b'commit ',
    2: b'tree ',
    3: b'blob ',
    4: b'tag ',
}
DICT_HEADER_TO_OBJTYPE = {
    b'commit': 1,
    b'tree': 2,
    b'blob': 3,
    b'tag': 4,
}


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
    # object data in pack file
    # if object decoded, `data` will be set to decompressed data
    data: memoryview

    @cached_property
    def decoded(self):
        """
        Decoded value of this object, its type depends on the object type

        A blob returns its content as ``bytes``, always: the plain (zlib) path
        and the delta path (see resolved_from) both return bytes, so
        a caller can scan (``b'\\x00' in content``), decode or compress the
        content with no conversion. A tree / commit / tag returns its parse
        result, a not yet resolved delta returns its delta instructions.

        ``data`` stays a ``memoryview``: it is the buffer the delta chains
        slice in GitObjectManager.cat().

        Returns:
            bytes | TreeObj | CommitObj | TagObj | OfsDeltaObj | RefDeltaObj
        """
        objtype = self.type
        # sorted by object proportion
        # delete self.data to release reference to original file data
        if objtype == 3:
            # blob, always bytes, never a memoryview: the content is scanned
            # and decoded by the callers, doing that on a memoryview is ~600x
            # slower or impossible
            try:
                data = decompress(self.data)
            except zlib_error as e:
                raise ObjectBroken(str(e), self.data)
            self.data = memoryview(data)
            return data
        if objtype == 7:
            # REF_DELTA
            result = parse_ref_delta(self.data)
            self.size = result.result_size
            # self.data = b''
            return result
        if objtype == 6:
            # OFS_DELTA
            result = parse_ofs_delta(self.data)
            self.size = result.result_size
            # keep data, the delta instructions stay packed until
            # GitObject.resolved_from() applies them, `data` of a delta is
            # never sliced by a delta chain
            return result
        if objtype == 2:
            # tree
            try:
                data = decompress(self.data)
            except zlib_error as e:
                raise ObjectBroken(str(e), self.data)
            result = parse_tree(data)
            self.data = memoryview(data)
            return result
        if objtype == 1:
            # commit
            try:
                data = decompress(self.data)
            except zlib_error as e:
                raise ObjectBroken(str(e), self.data)
            result = parse_commit(data)
            self.data = memoryview(data)
            return result
        if objtype == 4:
            # tag
            try:
                data = decompress(self.data)
            except zlib_error as e:
                raise ObjectBroken(str(e), self.data)
            result = parse_tag(data)
            self.data = memoryview(data)
            return result
        if objtype == 5:
            raise ObjectBroken(
                f'Object type {objtype} is a preserved value, it should not be used', self.data)
        raise ObjectBroken(
            f'Unknown object type {objtype}', self.data)

    def resolved_from(self, source: "GitObject"):
        """
        Apply the delta of this object to source, and return the result as a new
        object.

        Neither this object nor source is modified, so the object dict of
        GitObjectManager.cat() can solve one delta chain on several threads at
        the same time, every thread builds its own result.

        This object must be a DELTA object and source must not be a DELTA object

        The result follows GitObject.decoded: bytes for a blob, a parse result
        for a tree / commit / tag.

        Args:
            source (GitObject): Object the delta is applied on

        Returns:
            GitObject:
        """
        data = apply_delta(source.data, self.decoded)
        # no need to check because apply_delta() already checked
        # if len(data) != self.size:
        #     raise ObjectBroken(f'Unexpected data length after apply_data, size={self.size}, actual={len(data)}')

        objtype = source.type
        if objtype == 3:
            # blob, the content is always bytes, same as the plain path of
            # decoded(), `data` of a blob is never sliced by a delta chain
            decoded = data
        elif objtype == 2:
            decoded = parse_tree(data)
        elif objtype == 1:
            decoded = parse_commit(data)
        elif objtype == 4:
            decoded = parse_tag(data)
        else:
            raise ObjectBroken(f'Unexpected source type: {objtype}, source={source}')

        # the object is built finished: decoded is cached and data is set to
        # the plain bytes before the object can be seen
        obj = GitObject(type=objtype, size=self.size, data=memoryview(data))
        cached_property.set(obj, 'decoded', decoded)
        return obj

    def sha1(self):
        """
        Returns:
            bytes:
        """
        # {objtype} {length}\x00{data}
        try:
            objtype = DICT_OBJTYPE_TO_HEADER[self.type]
        except KeyError:
            # this shouldn't happen
            raise ObjectBroken(f'Objtype "{self.type}" is not in DICT_OBJTYPE_TO_HEADER', data=self.data)

        data = self.data
        header = b''.join([objtype, f'{len(data)}'.encode(), b'\0'])
        sha = sha1(header)
        sha.update(data)
        return sha.digest()


def parse_objdata(data):
    """
    Parse object data in pack file into GitObject

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


def parse_objdata_return_info(data):
    """
    See parse_objdata()

    Args:
        data (memoryview):

    Returns:
        tuple[int, int, int]: objtype, size, index
    """
    # Decode obj type and size
    shift = 16
    objtype = 0
    size = 0
    index = 1
    # By iter, each byte auto turn into int

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

    return objtype, size, index


def parse_objtype(data):
    """
    Parse object data in pack file into obj_type

    Args:
        data (memoryview):

    Returns:
        int:
    """
    # objtype at first byte
    try:
        byte = data[0]
    except IndexError:
        return 0
    if byte >= 128:
        byte -= 128
    return byte // 16


def parse_objtype_rest(data):
    """
    Parse object data in pack file into obj_type

    Args:
        data (memoryview):

    Returns:
        int:
    """
    # objtype at first byte
    try:
        byte = data[0]
    except IndexError:
        return 0
    if byte >= 128:
        byte -= 128
    objtype = byte // 16
    if objtype in OBJTYPE_BASIC:
        return objtype, None
    # skip obj size
    for index, byte in enumerate(data, start=1):
        if byte < 128:
            break
    # ignore IDE warning
    # because we have data[0], so data is not empty and index is guaranteed
    return objtype, data[index:]


def parse_objdata_type_offset(data):
    """
    Get objtype, ref or offset from obj data
    A fast path of parse_objdata, parse_ofs_delta_offset, parse_ref_delta_ref

    Args:
        data (memoryview):

    Returns:
        tuple[int, int | str]: (objtype, ref_or_offset)
    """
    # objtype at first byte
    byte = data[0]
    if byte >= 128:
        byte -= 128
    objtype = byte // 16
    if objtype in OBJTYPE_BASIC:
        return objtype, 0
    # skip obj size
    for index, byte in enumerate(data, start=1):
        if byte < 128:
            break
    # ignore IDE warning
    # because we have data[0], so data is not empty and index is guaranteed
    data = data[index:]
    if objtype == 7:
        # 20 bytes sha1 in header
        # copy memory view to bytes
        return objtype, data[:20].hex()
    if objtype == 6:
        # read reverse offset
        offset = 0
        index = 1
        for byte in data:
            # add in the next 7 bits of data
            if byte >= 128:
                # byte & 0x7f + 1
                offset += byte - 127
                offset *= 128
                index += 1
            else:
                # end reverse_offset, start source_size
                offset += byte
                break
        return objtype, offset
    # this shouldn't happen
    return objtype, 0


class GitLooseObject(msgspec.Struct, dict=True):
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
    data: bytes

    @cached_property
    def decoded(self):
        """
        Decoded value of this object, see GitObject.decoded

        A blob returns its content as ``bytes``, a tree / commit / tag returns
        its parse result. A loose object is never a delta.

        Returns:
            bytes | TreeObj | CommitObj | TagObj
        """
        objtype = self.type
        # sorted by object proportion
        # delete self.data to release reference to original file data
        if objtype == 3:
            # blob, always bytes, see GitObject.decoded
            return self.data
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
        # loose objects don't have delta
        if objtype == 6 or objtype == 7:
            raise ObjectBroken(
                f'Loose object should not have type {objtype}', self.data)
        if objtype == 5:
            raise ObjectBroken(
                f'Object type {objtype} is a preserved value, it should not be used', self.data)
        raise ObjectBroken(
            f'Unknown object type {objtype}', self.data)


def parse_loosedata(data):
    """
    Parse loose object into GitObject

    Args:
        data (bytes):

    Returns:
        GitLooseObject:
    """
    try:
        data = decompress(data)
    except zlib_error as e:
        raise ObjectBroken(str(e), data)

    # {objtype} {length}\x00{data}
    header, _, data = data.partition(b'\x00')
    header, _, size = header.partition(b' ')
    # convert data type
    try:
        objtype = DICT_HEADER_TO_OBJTYPE[header]
    except KeyError:
        raise ObjectBroken(f'Invalid loose header: {header}', data)
    try:
        size = int(size, 10)
    except ValueError:
        raise ObjectBroken(f'Invalid loose size: {size}', data)
    # validate size
    if len(data) != size:
        raise ObjectBroken(f'Invalid loose size not match, size={size}, len(data)={len(data)}', data)

    return GitLooseObject(type=objtype, size=size, data=data)


def read_loose_objtype(file, chunk_size=128):
    """
    Read objtype from a loose file, avoid reading the entire file.

    Args:
        file (str):
        chunk_size (int):

    Returns:
        int: 1 to 4

    Raises:
        ObjectBroken:
    """
    decompressor = decompressobj().decompress
    content = b''

    try:
        # we read a very small chunk, so disable buffering to reduce read
        with open(file, 'rb', buffering=0) as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    # file ends
                    break

                chunk = decompressor(chunk)
                if not chunk:
                    # not enough data to decompress
                    continue

                content += chunk
                if len(content) >= 6:
                    break
    except FileNotFoundError:
        raise ObjectBroken(f'Missing loose object file={file}')
    except zlib_error as e:
        raise ObjectBroken(str(e))

    # check header, order by appear frequency
    if content.startswith(b'blob'):
        return 3
    if content.startswith(b'tree'):
        return 2
    if content.startswith(b'commit'):
        return 1
    if content.startswith(b'tag'):
        return 4
    raise ObjectBroken(f'Invalid loose header: {content}')
