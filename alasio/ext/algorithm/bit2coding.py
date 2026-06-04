from alasio.ext.algorithm.lz77 import match_lz77, match_run


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
        run_val, run_len = match_run(mv, i)

        # 2. 检测历史匹配 (Copy)
        copy_offset, copy_len = match_lz77(mv, i)

        # 3. 决策：Run vs Copy vs Literal
        if run_len >= 4:
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
