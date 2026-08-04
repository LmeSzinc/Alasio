import lzma


def _lzma_dictsize(length, max_dict_size=None):
    """
    Auto LZMA dictionary size depends on content length
    4KB to 64MB

    Args:
        length (int): Data length to compress.
        max_dict_size (int, optional): Maximum dictionary size cap.
            If not a power of 2, capped to the nearest power of 2 less than it.
            If less than 4096, treated as 4096.

    Returns:
        int: Chosen dictionary size.
    """
    # 2^12 = 4096 (min), 2^26 = 67108864 = 64MB (max)
    max_exp = 26
    min_exp = 12

    if max_dict_size is not None:
        cap_exp = max_dict_size.bit_length() - 1
        if cap_exp < min_exp:
            cap_exp = min_exp
        elif cap_exp > max_exp:
            cap_exp = max_exp
    else:
        cap_exp = max_exp

    if length > 0:
        exp = (length - 1).bit_length()
    else:
        exp = min_exp

    if exp < min_exp:
        exp = min_exp
    if exp > cap_exp:
        exp = cap_exp

    return 1 << exp


def lzma_compress(data, max_dict_size=None):
    """
    Compress data using lzma with the best compression ratio

    Args:
        data (bytes):
        max_dict_size (int, optional): Maximum dictionary size cap, 4096 ~ 67108864

    Returns:
        bytes:
    """
    # dict size larger than data length is meaning less
    # so having auto adjusted dict size can speed up compressor initialization and reduce memory usage
    dictsize = _lzma_dictsize(len(data), max_dict_size=max_dict_size)
    my_filters = [
        {
            "id": lzma.FILTER_LZMA2,
            "dict_size": dictsize,  # 根据文件大小动态计算的字典
            "preset": 9,  # 基础底子设为 9
            "nice_len": 273,  # 压榨最后一点空间，寻找最长匹配
            "mf": lzma.MF_BT4,  # 确保使用二叉树匹配
        }
    ]
    compressed = lzma.compress(data, format=lzma.FORMAT_RAW, filters=my_filters, check=lzma.CHECK_NONE)
    return compressed

# Note that there is no lzma_decompress()
# just use ``lzma.decompress(data)`` to decompress
# no need to set the same parameters in decompress, lzma can auto handle it
