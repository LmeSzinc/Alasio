from collections import deque


def decode_vint(data):
    """
    Decode Bijective Variable-length Integer
    Note that this is not the vint in protobuf, this is the vint in git offset

    Note that python has special optimization for small int (-5~256) but bitwise operation is in int64
    You should use + - * / % instead of & | >>

    Args:
        data (bytearray | bytes):

    Returns:
        tuple[int, int]: (decoded_integer, bytes_read)
    """
    num = 0
    read = 1
    for byte in data:
        # add in the next 7 bits of data
        if byte >= 128:
            # see parse_ofs_delta()
            # byte & 0x7f + 1
            num += byte - 127
            num *= 128
            read += 1
        else:
            num += byte
            break

    return num, read


def encode_vint(num):
    """
    Encode Bijective Variable-length Integer
    Note that this is not the vint in protobuf, this is the vint in git offset

    Note that python has special optimization for small int (-5~256) but bitwise operation is in int64
    You should use + - * / % instead of & | >>

    Bijective base-128 representation:
        - Least significant digit in [0, 127], stored directly (MSB clear)
        - All other digits in [1, 128], stored as digit + 127 (MSB set)
        - When a non-least-significant digit is 0, borrow: set digit to 128 and decrement the carry

    Args:
        num (int): Non-negative integer to encode

    Returns:
        bytes: Encoded bytes
    """
    result = deque()
    result.append(num % 128)
    while num > 127:
        # num = (num >> 7) - 1
        num //= 128
        num -= 1
        # insert at head, set MSB=1
        # (num & 0x7f) | 0x80
        result.appendleft(128 + num % 128)

    return bytes(result)


def decode_vint_list(data, total):
    """
    Decode a sequence of vint-encoded integers from bytes.

    Args:
        data (bytearray | bytes): Encoded data containing total vint-encoded integers
        total (int): Number of integers to decode

    Returns:
        tuple[list[int], int]: (list[decoded_integer], bytes_read)
    """
    num_list = []
    num = 0
    read = 0
    count = 0
    for byte in data:
        # add in the next 7 bits of data
        if byte >= 128:
            # see parse_ofs_delta()
            # byte & 0x7f + 1
            num += byte - 127
            num *= 128
            read += 1
        else:
            num += byte
            read += 1
            # end of num
            count += 1
            num_list.append(num)
            num = 0
        if count >= total:
            break

    return num_list, read


def encode_vint_list(list_num):
    """
    Encode a list of integers as concatenated vint bytes.

    Args:
        list_num (list[int]): List of non-negative integers to encode

    Returns:
        bytes: Encoded bytes, concatenation of each vint-encoded integer
    """
    result_list = deque()
    for num in list_num:
        result = deque()
        result.append(num % 128)
        while num > 127:
            # num = (num >> 7) - 1
            num //= 128
            num -= 1
            # insert at head, set MSB=1
            # (num & 0x7f) | 0x80
            result.appendleft(128 + num % 128)

        result_list.extend(result)

    return bytes(result_list)
