from collections import deque
from hashlib import sha1
from struct import error as struct_error, pack, unpack
from zlib import crc32, decompressobj

from msgspec import Struct

from alasio.ext.cache import cached_property
from alasio.git.file.exception import ObjectBroken, PackBroken
from alasio.git.file.gitobject import GitObjectManager
from alasio.git.obj.obj import GitObject, OBJTYPE_BASIC, parse_objdata_return_info
from alasio.git.obj.objdelta import OfsDeltaObj, RefDeltaObj, parse_delta_object, parse_ofs_delta_info
from alasio.logger import logger


def validate_pack(data):
    """
    Args:
        data (memoryview):

    Returns:
        bool: True if success

    Raises:
        PackBroken: Raised if sha1 not match
    """
    content = data[:-20]
    checksum = data[-20:]
    if len(checksum) != 20:
        raise PackBroken(f'Pack validate failed, unexpected checksum length: {checksum.hex()}')
    sha = sha1(content).digest()
    if sha != checksum:
        raise PackBroken(f'Pack validate failed, checksum not match, sha1={sha.hex()}, checksum={checksum.hex()}')
    return True


def progressive_decompress(data, index):
    """
    Progressively decompress given `data` starting from `index`.

    We can't directly know the compressed size,
    the only way is to try to decompress and let zlib determine its end.
    But we can't directly input the entire remaining data either,
    which will copy all the remaining memoryview to `unused_data` in bytes.

    Args:
        data (memoryview):
        index (int):

    Returns:
        tuple[bytes, int]: decompressed data, total consumed
    """
    decompresser = decompressobj()

    # try 512 bytes first, because most git objects like tree, commit, delta are small
    chunk_size = 512
    end = index + chunk_size
    chunk_data = data[index:end]
    # no need to check empty chunk_data
    # it shouldn't happen and if it happens zlib.error will be raised or sha1 validation error will be raised
    content = decompresser.decompress(chunk_data)
    if decompresser.eof:
        index += len(chunk_data) - len(decompresser.unused_data)
        return content, index
    else:
        index += chunk_size

    content_queue = deque()
    content_queue.append(content)

    # try 2KB
    chunk_size = 2048
    end = index + chunk_size
    chunk_data = data[index:end]
    content = decompresser.decompress(chunk_data)
    content_queue.append(content)
    if decompresser.eof:
        index += len(chunk_data) - len(decompresser.unused_data)
        content = b''.join(content_queue)
        return content, index
    else:
        index += chunk_size

    # try 8KB
    chunk_size = 8196
    while True:
        end = index + chunk_size
        chunk_data = data[index:end]
        content = decompresser.decompress(chunk_data)
        content_queue.append(content)
        if decompresser.eof:
            index += len(chunk_data) - len(decompresser.unused_data)
            content = b''.join(content_queue)
            return content, index
        else:
            index += chunk_size


def read_first_obj(data) -> "tuple[GitObject, int]":
    """
    Args:
        data (memoryview):

    Returns:
        tuple[GitObject, int]:
    """
    # note that the `size` here is the original object data size (no compress, no delta)
    objtype, size, consumed = parse_objdata_return_info(data)
    if objtype in OBJTYPE_BASIC:
        # non DELTA
        # note that we skip decoding object here, since we just want to solve deltas and get sha1.
        # meaning that you cannot get `obj.decoded` which requires compressed `obj.data`.
        content, consumed = progressive_decompress(data, consumed)
        content = memoryview(content)
        # create git object
        obj = GitObject(type=objtype, size=size, data=content)
        return obj, consumed
    if objtype == 7:
        # REF_DELTA
        # no need to check ref length, if length < 20 decompress() would raise error
        end = consumed + 20
        ref = data[consumed:end].hex()
        consumed = end
        content, consumed = progressive_decompress(data, consumed)
        # parse delta
        source_size, result_size, all_instructions = parse_delta_object(content)
        decoded = RefDeltaObj(
            ref=ref,
            source_size=source_size,
            result_size=result_size,
            all_instructions=all_instructions,
        )
        content = memoryview(content)
    elif objtype == 6:
        # OFS_DELTA
        # first 10 bytes is enough for offset in uint64
        offset, add = parse_ofs_delta_info(data[consumed:consumed + 10])
        consumed += add
        content, consumed = progressive_decompress(data, consumed)
        # parse delta
        source_size, result_size, all_instructions = parse_delta_object(content)
        decoded = OfsDeltaObj(
            offset=offset,
            source_size=source_size,
            result_size=result_size,
            all_instructions=all_instructions,
        )
        content = memoryview(content)
    elif objtype == 5:
        raise ObjectBroken(
            f'Object type {objtype} is a preserved value, it should not be used', data)
    else:
        raise ObjectBroken(f'Unknown object type {objtype}', data)

    # create git object
    # set result_size and decoded
    obj = GitObject(type=objtype, size=result_size, data=content)
    cached_property.set(obj, 'decoded', decoded)
    return obj, consumed


class PackObjectInfo(Struct):
    sha: bytes
    offset: int
    crc: int


class GenIdx(GitObjectManager):

    def _parse_pack_info(self, data):
        """
        Args:
            data (bytes | memoryview):

        Returns:
            list[PackObjectInfo]:
        """
        if type(data) is bytes:
            data = memoryview(data)

        # check header, must be pack file and version 2
        if not data[:8] == b'PACK\x00\x00\x00\x02':
            raise PackBroken(f'Unexpected pack header {data[:8]}')
        try:
            num_objects = unpack('>I', data[8:12])[0]
        except struct_error as e:
            raise PackBroken(str(e))
        validate_pack(data)
        data = data[12:]

        # key: int offset, value: GitObject
        dict_offset_to_object = {}
        list_info = []
        index = 12
        for _ in range(num_objects):
            obj, consumed = read_first_obj(data)
            # apply delta
            objtype = obj.type
            if objtype == 7:
                # apply REF_DELTA
                ref = obj.decoded.ref
                source = self.cat(ref)  # may raise KeyError
                obj.apply_delta_from_source(source)
            elif objtype == 6:
                # apply OFS_DELTA
                offset = index - obj.decoded.offset
                try:
                    source = dict_offset_to_object[offset]
                except KeyError:
                    # this shouldn't happen
                    raise PackBroken(f'No corresponding pack object at offset={offset}. '
                                     f'index={index}, ofs_delta={obj.decoded.offset}')
                obj.apply_delta_from_source(source)

            dict_offset_to_object[index] = obj
            # prepare info
            # note that we skip decoding object here, since we just want to solve deltas and get sha1.
            # meaning that you cannot get `obj.decoded` which requires compressed `obj.data`.

            # CRC32 is calculated over the raw object entry data (header + body)
            crc = crc32(data[:consumed])
            # SHA-1 is of the final, reconstructed object data
            sha = obj.sha1()
            info = PackObjectInfo(sha=sha, offset=index, crc=crc)
            list_info.append(info)
            # next
            data = data[consumed:]
            index += consumed
            continue

        # data now should be checksum only
        if len(data) != 20:
            logger.warning('Pack file has redundant trailing data')
        # sort objects by sha1
        list_info.sort(key=lambda i: i.sha)
        return list_info

    @staticmethod
    def _iter_idx_data(list_info, data):
        """
        Args:
            list_info (list[PackObjectInfo]):
            data (bytes | memoryview):

        Yields:
            bytes:
        """
        # Version 2 pack index
        yield b'\xfftOc\x00\x00\x00\x02'

        # fanout table
        fanout = {byte: 0 for byte in range(256)}
        for info in list_info:
            byte = info.sha[0]
            fanout[byte] += 1
        # Convert to cumulative counts
        table = []
        cumsum = 0
        for count in fanout.values():
            cumsum += count
            table.append(cumsum)
        del fanout
        yield pack('>256I', *table)

        # sha1 table
        for info in list_info:
            yield info.sha

        # crc table
        table = [e.crc for e in list_info]
        yield pack(f'>{len(table)}I', *table)

        # offset table
        table = []
        table_large = []
        count_large = 0
        for info in list_info:
            offset = info.offset
            if offset >= 2147483648:
                # Set MSB to 1 and store the index into the large offset table
                count_large += 1
                table.append(2147483648 + count_large)
                table_large.append(offset)
            else:
                table.append(offset)
        yield pack(f'>{len(table)}I', *table)
        del table

        # large offset table
        if table_large:
            yield pack(f'>{len(table_large)}Q', *table_large)

        # Packfile checksum
        yield data[-20:]

    def pack_to_idx(self, data):
        """
        Args:
            data (bytes | memoryview):

        Returns:

        """
        list_info = self._parse_pack_info(data)
        idx = b''.join(self._iter_idx_data(list_info, data))
        checksum = sha1(idx).digest()
        return b''.join([idx, checksum])
