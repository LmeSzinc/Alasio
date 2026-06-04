def match_run(data, index, min_length=1, max_length=0):
    """
    Find the longest run of identical bytes starting from index.

    Args:
        data (memoryview): data must be a memoryview of bytes
        index (int): start index
        min_length (int):
        max_length (int): 0 for no limit

    Returns:
        tuple[int, int]: (run_bytes_in_uint8, run_length)
            run_length is always >= min_length

    Raises:
        IndexError: If start index is out of data
    """
    start = data[index]
    length = 1
    if max_length > 0:
        for byte in data[index + 1:]:
            if byte != start or length >= max_length:
                break
            length += 1
    else:
        for byte in data[index + 1:]:
            if byte != start:
                break
            length += 1
    # not match min_length
    if length < min_length:
        return start, min_length
    return start, length
