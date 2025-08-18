import zlib
from collections import deque
from typing import Deque, Tuple
from zlib import decompress

import msgspec

from alasio.ext.gitpython.file.exception import ObjectBroken


class OfsDeltaObj(msgspec.Struct):
    # reversed offset, it's a positive number but lookup previous data
    offset: int
    # size of the data to copy from
    source_size: int
    # size of the result data after delta instructions applied
    result_size: int
    # deque of instructions
    # if delta instruction is copy:   offset, size, None
    # if delta instruction is append:      0,    0, append_data
    all_instructions: Deque[Tuple[int, int, memoryview]]


class RefDeltaObj(msgspec.Struct):
    # sha1 of the reference object, reference may in another pack file
    ref: str
    # size of the data to copy from
    source_size: int
    # size of the result data after delta instructions applied
    result_size: int
    # deque of instructions
    # if delta instruction is copy:   offset, size, None
    # if delta instruction is append:      0,    0, append_data
    all_instructions: Deque[Tuple[int, int, memoryview]]


def parse_ofs_delta(data):
    """
    OBJ_OFS_DELTA and OBJ_REF_DELTA are the most complex things in git
    https://www.alibabacloud.com/blog/a-detailed-explanation-of-the-underlying-data-structures-and-principles-of-git_597391
    https://awasu.com/weblog/git-guts/delta-objects/

    Args:
        data (memoryview):

    Returns:
        OfsDeltaObj:
    """
    # read reverse offset
    offset = 0
    index = 1
    for byte in data:
        # add in the next 7 bits of data
        if byte >= 128:
            # NOTE: When reading offsets for delta'fied objects, there is an additional twist :-/
            # The sequences [ 0xxxxxxx ] and [ 10000000, 0xxxxxxx ] would normally be read as
            # the same value (0xxxxxxx), so for each byte except the last one, we add 2^7,
            # which has the effect of ensuring that all 1-byte sequences are less than all 2-byte
            # sequences, which are less than all 3-byte sequences, etc. We add 1 here, but since
            # we are going to loop back and left-shift val by 7 bits, that is the same as adding 2^7.
            # Look for "offset encoding" here:
            #   https://git-scm.com/docs/pack-format
            # byte & 0x7f + 1
            offset += byte - 127
            offset *= 128
            index += 1
        else:
            # end reverse_offset, start source_size
            offset += byte
            break

    # decompress delta
    try:
        data = decompress(data[index:])
    except zlib.error as e:
        raise ObjectBroken(str(e), data)
    data = memoryview(data)

    # parse delta
    source_size, result_size, all_instructions = parse_delta_object(data)

    return OfsDeltaObj(
        offset=offset,
        source_size=source_size,
        result_size=result_size,
        all_instructions=all_instructions,
    )


def parse_ref_delta(data):
    """
    Args:
        data (memoryview):

    Returns:
        RefDeltaObj:
    """
    # 20 bytes sha1 in header
    # copy memory view to bytes
    ref = data[:20].hex()
    # no need to check ref length, if length < 20 decompress() would raise error

    # decompress delta
    try:
        data = decompress(data[20:])
    except zlib.error as e:
        raise ObjectBroken(str(e), data)
    data = memoryview(data)

    # parse delta
    source_size, result_size, all_instructions = parse_delta_object(data)

    return RefDeltaObj(
        ref=ref,
        source_size=source_size,
        result_size=result_size,
        all_instructions=all_instructions,
    )


def parse_delta_object(data):
    """
    Args:
        data (memoryview):

    Returns:
        source_size (int):
        result_size (int):
        deque[tuple[int, int, memoryview, memoryview]]:
            if delta instruction is copy:   offset, size,        None, remaining_data
            if delta instruction is append:      0,    0, append_data, remaining_data
    """
    # read source_size and result_size
    index = 1
    source_size = 0
    result_size = -1
    shift = 1
    for byte in data:
        if result_size >= 0:
            # parse result_size
            if byte >= 128:
                result_size += (byte - 128) * shift
                shift *= 128
            else:
                # end result_size
                result_size += byte * shift
                break
        else:
            # parse source_size
            if byte >= 128:
                source_size += (byte - 128) * shift
                shift *= 128
            else:
                # end source_size
                source_size += byte * shift
                # start result_size and reset shift
                result_size = 0
                shift = 1
        index += 1

    data = data[index:]
    # print(data[:50].hex())

    all_instructions = deque()
    append = all_instructions.append
    while 1:
        # read delta instructions
        try:
            header = data[0]
        except IndexError:
            raise ObjectBroken('Delta instruction has no header', data)

        # instruction: copy
        if header >= 128:
            # no need header -= 128
            # read second byte
            data_index = 1

            try:
                if header % 2:
                    offset = data[data_index]
                    data_index += 1
                else:
                    offset = 0
                if header & 0x02:
                    offset += data[data_index] * 256
                    data_index += 1
                if header & 0x04:
                    offset += data[data_index] * 65536
                    data_index += 1
                if header & 0x08:
                    offset += data[data_index] * 16777216
                    data_index += 1

                # Unrolled size calculation
                if header & 0x10:
                    size = data[data_index]
                    data_index += 1
                else:
                    size = 0
                if header & 0x20:
                    size += data[data_index] * 256
                    data_index += 1
                if header & 0x40:
                    size += data[data_index] * 65536
                    data_index += 1
            except IndexError:
                raise ObjectBroken(
                    f'Delta header wants to read data_index={data_index} but data reached end', data)

            # Handle special case
            if size == 0:
                size = 65536

            data = data[data_index:]
            append((offset, size, None))
            # return offset, size, None, data

        # instruction: insert
        elif header > 0:
            # size is header
            end = header + 1
            append_data = data[1:end]
            # validate append size
            if len(append_data) != header:
                raise ObjectBroken(
                    f'Delta instruction header {header} wants to read {header} bytes'
                    f'but gets data length of {len(append_data)}', data)
            data = data[end:]
            # return 0, 0, append_data, data
            append((0, 0, append_data))

        # instruction: reserved
        else:
            raise ObjectBroken(f'Delta instruction header {header} is reserved, it should not be used', data)

        # End
        if not data:
            break

    return source_size, result_size, all_instructions


def parse_delta_instruction(data):
    """
    Parse one delta instruction.
    For testing only, function is embedded to improve performance

    Args:
        data (memoryview):

    Returns:
        Tuple[int, int, memoryview, memoryview]:
            if delta instruction is copy:   offset, size,        None, remaining_data
            if delta instruction is append:      0,    0, append_data, remaining_data

    Raises:
        ObjectBroken:
    """
    # read delta instructions
    try:
        header = data[0]
    except IndexError:
        raise ObjectBroken('Delta instruction has no header', data)

    # instruction: copy
    if header >= 128:
        # no need header -= 128
        # read second byte
        data_index = 1

        try:
            if header % 2:
                offset = data[data_index]
                data_index += 1
            else:
                offset = 0
            if header & 0x02:
                offset += data[data_index] * 256
                data_index += 1
            if header & 0x04:
                offset += data[data_index] * 65536
                data_index += 1
            if header & 0x08:
                offset += data[data_index] * 16777216
                data_index += 1

            # Unrolled size calculation
            if header & 0x10:
                size = data[data_index]
                data_index += 1
            else:
                size = 0
            if header & 0x20:
                size += data[data_index] * 256
                data_index += 1
            if header & 0x40:
                size += data[data_index] * 65536
                data_index += 1
        except IndexError:
            raise ObjectBroken(
                f'Delta header wants to read data_index={data_index} but data reached end', data)

        # Handle special case
        if size == 0:
            size = 65536

        data = data[data_index:]
        return offset, size, None, data

    # instruction: insert
    elif header > 0:
        # size is header
        end = header + 1
        append_data = data[1:end]
        # validate append size
        if len(append_data) != header:
            raise ObjectBroken(
                f'Delta instruction header {header} wants to read {header} bytes'
                f'but gets data length of {len(append_data)}', data)
        data = data[end:]
        return 0, 0, append_data, data

    # instruction: reserved
    else:
        raise ObjectBroken(f'Delta instruction header {header} is reserved, it should not be used', data)


def apply_delta(source, delta):
    """
    Apply delta instructions to `data`, return a new data

    Args:
        source (memoryview):
        delta (OfsDeltaObj):

    Returns:
        bytes:
    """
    # validate source_size
    if len(source) != delta.source_size:
        raise ObjectBroken(f'Delta instructions expects source_size={delta.source_size} '
                           f'but data is ln length={len(source)}', source)

    # apply delta
    result = deque()
    result_append = result.append
    for offset, size, append in delta.all_instructions:
        if size:
            # instruction: copy
            data = source[offset:offset + size]
            result_append(data)
        else:
            # instruction: append
            result_append(append)
    # note that `join` is faster than `result += data` because it creates byte object once
    result = b''.join(result)

    # validate result_size
    if len(result) != delta.result_size:
        raise ObjectBroken(f'Delta instructions expects result_size={delta.source_size} '
                           f'but result is ln length={len(result)}', result)

    return result
