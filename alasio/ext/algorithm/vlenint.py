from collections import deque

from alasio.ext.algorithm.bit2coding import encode_bit2, decode_bit2
from alasio.ext.algorithm.unpack import unpack_little_int, pack_little_int


def vlenint_value_check(data):
    """
    Check if the values in data are valid for vlenint encoding
    
    Args:
        data (Iterable[int]): list of values to encode
    
    Raises:
        ValueError: if any value is negative
    """
    if not data:
        return
    min_val = min(data)
    max_val = max(data)
    if min_val < 0:
        raise ValueError(f"Value must be >= 0, got {min_val}")
    if max_val > 2 ** 32 - 1:
        raise ValueError(f"Value must be <= 2**32 - 1, got {max_val}")


def encode_vlenint(data):
    """
    Encode numbers to variable length int. vlenint have 2 section
    [byte_length]: bytes length of each value compressed in bit2coding
        byte_length indicates the bytes length of each value in the following section
        value=0 -> byte_length=0
        value=1..255 -> byte_length=1
        value=256..65535 -> byte_length=2
        value=65536..16777215 -> byte_length=3
        value=16777216..4294967295 -> byte_length=4
    [values]: values in little-endian

    Args:
        data (Iterable[int]): list of values to encode

    Returns:
        bytes: vlenint encoded data
    """
    if not data:
        return b''
    vlenint_value_check(data)
    lengths = deque()
    value_bytes = deque()
    for item in data:
        if item == 0:
            lengths.append(0)
        else:
            packed = pack_little_int(item)
            lengths.append(len(packed))
            value_bytes.append(packed)

    section_lengths = encode_bit2(lengths, ext8=True)
    section_values = b''.join(value_bytes)
    return b''.join([section_lengths, section_values])


def decode_vlenint(data, total):
    """
    Decode vlenint encoded data to a list of integers

    Args:
        data (memoryview | bytes): vlenint encoded data
        total (int): total number of values to decode

    Returns:
        tuple[list[int], int]: (decoded integers, bytes consumed)

    Raises:
        ValueError: If data is truncated or contains invalid opcodes
    """
    if isinstance(data, bytes):
        data = memoryview(data)
    byte_lengths, read = decode_bit2(data, total, ext8=True)
    values = []
    values_append = values.append
    for length in byte_lengths:
        if length == 0:
            values_append(0)
        else:
            value = unpack_little_int(data, read, length)
            values_append(value)
            read += length
    return values, read
