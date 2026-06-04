def match_lz77(data, index, window=0, min_length=3):
    """
    An optimized LZ77 match

    Args:
        data (memoryview):
            Note that memoryview must be a memoryview of bytes, must not be sliced
        index (int):
        window (int): Default to 0 for full match
        min_length (int):

    Returns:
        tuple[int, int]: offset, length
            meaning that data[index-offset:index-offset+length] == data[index:index+length]
            length might greater than offset for rolling copy
            length is 0 if no match
    """
    if window > 0:
        start = index - window
        if start < 0:
            start = 0
    else:
        start = 0
    max_length = len(data) - index
    if max_length < min_length:
        return 0, 0

    # 1. quick failure, no match
    obj: bytes = data.obj
    target = data[index: index + min_length]
    # actually iis rfind(end=index - min_length + 1)
    idx = obj.rfind(target, start, index)
    if idx == -1:
        return 0, 0

    # 2. 指数增长探测区间
    best_len = min_length
    best_idx = idx

    # 理论上，匹配起点为 best_idx 时，在历史区间内的最大可能匹配长度
    # limit_len = min(max_length, index - best_idx)
    limit_len = index - best_idx
    if limit_len > max_length:
        limit_len = max_length

    curr_len = min_length * 2
    while curr_len <= limit_len:
        target = data[index: index + curr_len]
        idx_new = obj.rfind(target, start, best_idx + curr_len)
        if idx_new != -1:
            best_len = curr_len
            best_idx = idx_new
            # 倍增
            curr_len *= 2
            # 更新限制
            # limit_len = min(max_length, index - best_idx)
            limit_len = index - best_idx
            if limit_len > max_length:
                limit_len = max_length
        else:
            break

    # 3. 在窄区间 [best_len + 1, min(curr_len - 1, limit_len)] 内进行二分微调
    low = best_len + 1
    # high = min(curr_len - 1, limit_len)
    high = curr_len - 1
    if high > limit_len:
        high = limit_len

    while low <= high:
        mid = (low + high) // 2
        target = data[index: index + mid]
        idx_new = obj.rfind(target, start, best_idx + mid)
        if idx_new != -1:
            best_len = mid
            best_idx = idx_new
            # When a farther-left match is found, the potential max length
            # (bounded by history depth) increases. Expand the search upper
            # bound so binary search can reach the longer match.
            new_limit = index - best_idx
            if new_limit > max_length:
                new_limit = max_length
            if new_limit > high:
                high = new_limit
            low = mid + 1
        else:
            high = mid - 1

    # 4. 滚动复制检测（Rolling Copy）
    # 当最长匹配恰好延伸至历史终点（即 best_idx + best_len == i），开启滚动扩展
    offset = index - best_idx
    if offset == best_len:
        match_idx = index - best_len
        while best_len < max_length and data[match_idx + best_len] == data[index + best_len]:
            best_len += 1

    return offset, best_len
