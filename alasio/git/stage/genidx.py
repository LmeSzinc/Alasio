from collections import deque
from hashlib import sha1
from struct import error as struct_error, pack, unpack
from zlib import crc32, decompressobj

from msgspec import Struct

from alasio.ext.cache import cached_property
from alasio.git.file.exception import ObjectBroken, PackBroken, PackNotSelfContained
from alasio.git.file.gitobject import GitObjectManager
from alasio.git.obj.obj import OBJTYPE_BASIC, GitObject, parse_objdata_return_info
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

    def _parse_pack_info(self, data, allow_external=False):
        """
        Parse a pack and resolve every delta chain in it.

        A base is looked up inside the pack first: a full object is its own
        base, an ofs-delta points backwards, a ref-delta by sha1. An entry
        whose base has not been parsed yet waits for a later pass, so a chain
        of any depth, and any mixture of ofs and ref deltas, is resolved. Only
        when a pass resolves nothing is the rest taken as pointing outside the
        pack, which is what a thin pack does.

        Args:
            data (bytes | memoryview): The pack, header to trailing checksum
            allow_external (bool): True to resolve a base that the pack does
                not hold through self.cat(), which needs the repo to be read
                (read_lazy or read_full). Defaults to False: a pack read back
                from the disk must be self-contained, and reading the repo for
                it hides that the pack is thin

        Returns:
            list[PackObjectInfo]: One entry per object, sorted by sha1

        Raises:
            PackNotSelfContained: If a base is outside the pack and
                allow_external is False
            PackBroken: If the header, an entry or the trailing checksum is
                broken, or allow_external is True and a base is neither in the
                pack nor in the repo
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
        body = data[12:]

        # key: int offset, value: GitObject
        offset_to_object = {}
        # key: str sha1 in hex, value: GitObject, the same objects as above
        sha1_to_object = {}
        list_info = []
        # entries whose base is not parsed yet: (offset, crc, obj, base_offset, base_sha1)
        deferred = []

        def publish(offset, crc, obj):
            """
            Record a resolved object under both of its keys.

            The sha1 is computed once: it is the key of sha1_to_object and the
            value of the idx entry, and hashing the content again for the
            second use costs a full pass over it.

            Args:
                offset (int): Offset of the entry in the pack file
                crc (int): crc32 of the entry bytes
                obj (GitObject): The object, its delta already applied
            """
            sha = obj.sha1()
            offset_to_object[offset] = obj
            sha1_to_object[sha.hex()] = obj
            list_info.append(PackObjectInfo(sha=sha, offset=offset, crc=crc))

        index = 12
        for _ in range(num_objects):
            obj, consumed = read_first_obj(body)
            crc = crc32(body[:consumed])
            objtype = obj.type
            base_offset = 0
            base_sha1 = ''
            if objtype in OBJTYPE_BASIC:
                # a full object is its own base
                resolved = obj
            elif objtype == 6:
                # OFS_DELTA, the base is earlier in the pack
                base_offset = index - obj.decoded.offset
                source = offset_to_object.get(base_offset)
                resolved = obj.resolved_from(source) if source is not None else None
            elif objtype == 7:
                # REF_DELTA, the base may be anywhere in the pack
                base_sha1 = obj.decoded.ref
                source = sha1_to_object.get(base_sha1)
                resolved = obj.resolved_from(source) if source is not None else None
            else:
                # read_first_obj() rejects every other type already
                raise PackBroken(f'Unexpected object type {objtype} at offset={index}')

            if resolved is None:
                deferred.append((index, crc, obj, base_offset, base_sha1))
            else:
                publish(index, crc, resolved)
            # next
            body = body[consumed:]
            index += consumed

        # what follows the objects is the checksum, which validate_pack() read
        if len(body) != 20:
            logger.warning('Pack file has redundant trailing data')

        # resolve what the parse pass left: an entry becomes resolvable as soon
        # as its base is, whatever the kind of that base is
        while deferred:
            remaining = []
            for offset, crc, obj, base_offset, base_sha1 in deferred:
                if base_sha1:
                    source = sha1_to_object.get(base_sha1)
                else:
                    source = offset_to_object.get(base_offset)
                if source is None:
                    remaining.append((offset, crc, obj, base_offset, base_sha1))
                    continue
                publish(offset, crc, obj.resolved_from(source))
            if len(remaining) < len(deferred):
                deferred = remaining
                continue

            # every base the pack holds is in use, so the rest needs a base the
            # pack does not hold, which is exactly what a thin pack is
            if not allow_external:
                offset, crc, obj, base_offset, base_sha1 = remaining[0]
                base = base_sha1 or f'offset {base_offset}'
                raise PackNotSelfContained(
                    f'Pack is not self-contained: entry at offset={offset} '
                    f'refers to base {base}, which the pack does not hold')

            # read the missing bases from the repo and run the loop again, the
            # entries that were waiting on them resolve there
            progressed = False
            still = []
            for offset, crc, obj, base_offset, base_sha1 in remaining:
                if not base_sha1:
                    # an ofs-delta on a ref-delta that is still waiting
                    still.append((offset, crc, obj, base_offset, base_sha1))
                    continue
                try:
                    source = self.cat(base_sha1)
                except KeyError:
                    still.append((offset, crc, obj, base_offset, base_sha1))
                    continue
                publish(offset, crc, obj.resolved_from(source))
                progressed = True
            if not progressed:
                offset, crc, obj, base_offset, base_sha1 = still[0]
                base = base_sha1 or f'offset {base_offset}'
                raise PackBroken(
                    f'Pack entry at offset={offset} cannot be resolved: '
                    f'base {base} is neither in the pack nor in the repo')
            deferred = still

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

    def pack_to_idx(self, data, allow_external=False):
        """
        Build the version 2 index of a pack file.

        Args:
            data (bytes | memoryview): The pack, header to trailing checksum
            allow_external (bool): True to resolve a delta base that the pack
                does not hold through self.cat(). Defaults to False, a pack
                read back from the disk must be self-contained, see
                _parse_pack_info

        Returns:
            bytes: The idx file, including its own trailing sha1

        Raises:
            PackNotSelfContained: If a base is outside the pack and
                allow_external is False
            PackBroken: If the pack is not readable as a pack
        """
        list_info = self._parse_pack_info(data, allow_external=allow_external)
        idx = b''.join(self._iter_idx_data(list_info, data))
        checksum = sha1(idx).digest()
        return b''.join([idx, checksum])
