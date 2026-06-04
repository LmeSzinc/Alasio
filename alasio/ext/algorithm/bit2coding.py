from alasio.backport.batch import batched
from alasio.ext.algorithm.lz77 import match_lz77, match_run
from alasio.ext.algorithm.unpack import unpack_little_int


def encode_bit2_opcode_iter(data):
    """
    将输入的 2-bit 数据流转换为操作元组列表

    Args:
        data (list[int]): value must be 0, 1, 2, 3

    Yields:
        tuple: operation code
            - (0, list[int]): literal values
            - (1, int, int): run value and length
            - (2, int, int): copy offset and length
    """
    pending_literals = []

    i = 0
    n = len(data)
    mv = memoryview(bytes(data))

    while i < n:
        # 1. 检测连续重复 (Run)
        run_val, run_len = match_run(mv, i, min_length=3)

        # 2. 检测历史匹配 (Copy)
        copy_offset, copy_len = match_lz77(mv, i, min_length=3)

        # 3. 决策：Run vs Copy vs Literal
        if run_len >= 3:
            # 如果 Copy 的长度远大于 Run 长度，才优先考虑 Copy
            if copy_len > run_len + 2:
                # flush_literals
                if pending_literals:
                    yield 0, pending_literals
                    pending_literals = []
                # operation: copy
                yield 2, copy_offset, copy_len
                i += copy_len
            else:
                # flush_literals
                if pending_literals:
                    yield 0, pending_literals
                    pending_literals = []
                # operation: run
                yield 1, run_val, run_len
                i += run_len

        elif copy_len >= 3:
            # 判断这次 Copy 是否合算（长偏移量需要更长的匹配长度才合算）
            if copy_offset <= 256 or copy_len >= 4:
                # flush_literals
                if pending_literals:
                    yield 0, pending_literals
                    pending_literals = []
                # operation: copy
                yield 2, copy_offset, copy_len
                i += copy_len
            else:
                pending_literals.append(mv[i])
                i += 1
        else:
            pending_literals.append(mv[i])
            i += 1

    # flush_literals
    if pending_literals:
        yield 0, pending_literals


def decode_bit2_opcode(opcodes):
    """
    将操作元组列表重新解码为原始的 list[int]

    Args:
        opcodes (Iterable[tuple])

    Returns:
        list[int]: list of 2 bits value, value must be 0, 1, 2, 3
    """
    res = []

    for opcode in opcodes:
        op_type = opcode[0]

        if op_type == 0:
            # 0: literal values (list[int])
            res.extend(opcode[1])

        elif op_type == 1:
            # 1: run value and length
            _, run_val, run_len = opcode
            res.extend([run_val] * run_len)

        elif op_type == 2:
            # 2: copy offset and length
            _, offset, length = opcode
            start = len(res) - offset

            if length <= offset:
                # 普通复制，直接切片
                res.extend(res[start: start + length])
            else:
                # 滚动复制 (Rolling Copy)，例如 offset=1, length=5
                # 利用切片乘法避免 Python 层的 for 循环
                pattern = res[start: start + offset]
                repeats = length // offset
                remainder = length % offset
                res.extend(pattern * repeats + pattern[:remainder])

    return res


def encode_length_int(length):
    """
    Encode length to bytes
    
    Args:
        length (int): length to encode
    
    Returns:
        tuple: (d, *length_bytes)
            D (0~3) indicates to read D+1 bytes of N, N is packed in little-endian
    """
    if length <= 255:
        d = 0
        return d, length
    elif length <= 65535:
        d = 1
        return d, length % 256, length // 256
    elif length <= 16777215:
        d = 2
        first = length // 256
        return d, length % 256, first % 256, first // 256
    elif length <= 4294967295:
        d = 3
        first = length // 256
        second = first // 256
        return d, length % 256, first % 256, second % 256, second // 256
    else:
        raise ValueError(f"Length is too large: {length}")


def encode_bit2_stream_iter(opcodes):
    """
    Compress operations to store a list of 2bits
    1. literal operations, op=(0, data), e.g. (0, [1, 2, 3, 4, ...])
    000000XX: 1 item
    0001XXYY: 2 item
    001NNNNN: pack N+1 items, N (0~31), indicates to read 1~8 bytes, each following bytes are AABBCCDD
              last byte may have trailing 00 to fill up to a full byte, e.g. AABB0000
    000001DD: pack N+16 items, N (0~2^32), D (0~3) indicates to read D+1 bytes of N, N is packed in little-endian
    2. run operations, op=(1, item, run), e.g. (1, 2, 35)
    1XXNNNNN: run XX for N+4 times, N (0~31)
    0110XXDD: run XX for N+36 times, N (0~2^32),
              D (0~3) indicates to read D+1 bytes of N, N is packed in little-endian
    3. copy operations, op=(2, offset, length), e.g. (2, 28, 5)
    010LLLLL: Copy from offset=F+1 length=L+1
              L (0~31),
              this indicates to read next byte as F (0~255)
    0111LLFF: Copy from offset=F+1 length=L+1
              L (0~3) indicates to read L+1 bytes of L (0~2^32), L is packed in little-endian
              F (0~3) indicates to read F+1 bytes of F (0~2^32), F is packed in little-endian

    Args:
        opcodes (Iterable[tuple]): 

    Yields:
        int: compressed data in uint8
    """
    for opcode in opcodes:
        op_type = opcode[0]

        # 0: literal values (list[int])
        if op_type == 0:
            items = opcode[1]
            literal_len = len(items)
            # 000000XX: 1 item
            if literal_len == 1:
                yield items[0]
                continue
            # 0001XXYY: 2 item
            if literal_len == 2:
                yield 16 + items[0] * 4 + items[1]
                continue
            # pack multiple items
            for item_batch in batched(items, 32):
                n = len(item_batch)
                # Use compact formats for small trailing batches
                if n == 1:
                    yield item_batch[0]
                    continue
                if n == 2:
                    yield 16 + item_batch[0] * 4 + item_batch[1]
                    continue
                # 001NNNNN: pack N+1 items, N (0~31)
                yield 31 + n
                stack_count = 0
                stack_val = 0
                for item in item_batch:
                    stack_val = stack_val * 4 + item
                    stack_count += 1
                    # each following bytes are AABBCCDD
                    if stack_count == 4:
                        yield stack_val
                        stack_val = 0
                        stack_count = 0
                # trailing 00 to fill up to a full byte, e.g. AABB0000
                if stack_count > 0:
                    trailing = (4 - stack_count) * 2
                    trailing = 2 ** trailing
                    yield stack_val * trailing

        # 1: run value and length
        elif op_type == 1:
            item = opcode[1]
            run = opcode[2]
            # 1XXNNNNN: run XX for N+4 times, N (0~31)
            if run < 36:
                yield 128 + item * 32 + (run - 4)
                continue
            # 0110XXDD: run XX for N+36 times, N (0~2^32),
            #           D (0~3) indicates to read D+1 bytes of N, N is packed in little-endian
            d, *length_bytes = encode_length_int(run - 36)
            yield 96 + item * 4 + d
            yield from length_bytes

        # 2: copy offset and length
        elif op_type == 2:
            offset = opcode[1]
            length = opcode[2]
            # 010LLLLL: Copy from offset=F+1 length=L+1
            #           L (0~31),
            #           this indicates to read next byte as F (0~255)
            if length <= 32 and offset <= 256:
                yield 63 + length
                yield offset - 1
                continue
            # 0111LLFF: Copy from offset=F+1 length=L+1
            #           L (0~3) indicates to read L+1 bytes of L (0~2^32), L is packed in little-endian
            #           F (0~3) indicates to read F+1 bytes of F (0~2^32), F is packed in little-endian
            l_d, *l_bytes = encode_length_int(length - 1)
            f_d, *f_bytes = encode_length_int(offset - 1)
            yield 112 + l_d * 4 + f_d
            yield from l_bytes
            yield from f_bytes

        # this shouldn't happen
        else:
            raise ValueError(f"Invalid opcode: {opcode}")


def decode_bit2_stream_iter(data, total):
    """
    Decode compressed operations to opcodes list
    See encode_bit2_stream_iter for more information

    Args:
        data (memoryview): compressed data
        total (int): Total numbers

    Returns:
        tuple[list[int], int]: (list of opcodes, read bytes count)
    """
    count = 0
    read = 0
    opcodes = []
    if total == 0:
        return opcodes, read
    while True:
        try:
            byte = data[read]
        except IndexError:
            raise ValueError(f"Data truncated, expected {total} numbers, got {count}")
        read += 1
        # 1XXNNNNN: run XX for N+4 times, N (0~31)
        if byte >= 128:
            item = (byte // 32) % 4
            run = (byte % 32) + 4
            opcodes.append((1, item, run))
            count += run
        # 0111LLFF: copy offset and length
        #           L (0~3) indicates to read L+1 bytes, F (0~3) indicates to read F+1 bytes
        elif byte >= 112:
            l_d = (byte % 16) // 4
            f_d = byte % 4
            length = unpack_little_int(data, read, l_d + 1) + 1
            read += l_d + 1
            offset = unpack_little_int(data, read, f_d + 1) + 1
            read += f_d + 1
            opcodes.append((2, offset, length))
            count += length
        # 0110XXDD: run XX for N+36 times, N (0~2^32),
        #           D (0~3) indicates to read D+1 bytes of N, N is packed in little-endian
        elif byte >= 96:
            item = (byte % 16) // 4
            d = byte % 4 + 1
            length = unpack_little_int(data, read, d) + 36
            opcodes.append((1, item, length))
            count += length
            read += d
        # 010LLLLL: Copy from offset=F+1 length=L+1
        #           L (0~31),
        #           this indicates to read next byte as F (0~255)
        elif byte >= 64:
            length = (byte % 32) + 1
            offset = data[read] + 1
            read += 1
            opcodes.append((2, offset, length))
            count += length
        # 001NNNNN: pack N+1 items, N (0~31)
        elif byte >= 32:
            n = byte - 31  # 1-32 items
            packed_count = (n + 3) // 4
            remain_n = n
            items = []
            for _ in range(packed_count):
                packed = data[read]
                read += 1
                # each following bytes are AABBCCDD
                if remain_n >= 4:
                    item1 = packed // 64
                    item2 = (packed // 16) % 4
                    item3 = (packed // 4) % 4
                    item4 = packed % 4
                    items.extend([item1, item2, item3, item4])
                    remain_n -= 4
                # trailing 00 to fill up to a full byte, e.g. AABB0000
                elif remain_n == 3:
                    item1 = packed // 64
                    item2 = (packed // 16) % 4
                    item3 = (packed // 4) % 4
                    items.extend([item1, item2, item3])
                    remain_n = 0
                elif remain_n == 2:
                    item1 = packed // 64
                    item2 = (packed // 16) % 4
                    items.extend([item1, item2])
                    remain_n = 0
                elif remain_n == 1:
                    item1 = packed // 64
                    items.append(item1)
                    remain_n = 0
            opcodes.append((0, items))
            count += n
        # 0001XXYY: 2 item
        elif byte >= 16:
            first = (byte % 16) // 4
            second = byte % 4
            opcodes.append((0, [first, second]))
            count += 2
        # invalid: byte>=000000XX
        elif byte >= 4:
            raise ValueError(f"Invalid opcode: {byte}")
        # 000000XX: 1 item
        elif byte >= 0:
            opcodes.append((0, [byte]))
            count += 1

        # check total
        if count >= total:
            break

    return opcodes, read
