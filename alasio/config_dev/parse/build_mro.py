def _merge_c3(lists_to_merge: "list[list[str]]", context_cls: str) -> "tuple[str, ...]":
    """
    C3 算法的合并核心逻辑。
    使用 list of lists 作为输入，因为需要进行 pop 操作。
    """
    result = []

    while True:
        # 1. 过滤掉已经处理完毕的空列表
        active_lists = [items for items in lists_to_merge if items]
        if not active_lists:
            return tuple(result)

        # 2. 性能核心：预先提取所有列表的 tail（非首位元素）放入 set 中
        # 这样查找 candidate 是否被阻塞只需要 O(1)
        tails = {item for items in active_lists for item in items[1:]}

        # 3. 寻找一个不在任何 tail 中的 head
        candidate = None
        for items in active_lists:
            head = items[0]
            if head not in tails:
                candidate = head
                break

        if candidate is not None:
            result.append(candidate)
            # 4. 从所有列表中移除这个 candidate
            for items in active_lists:
                if items[0] == candidate:
                    items.pop(0)
        else:
            # 5. 如果没有 candidate 产生但列表不为空，说明存在循环或无法线性化
            # 这里的失败通常指“顺序冲突”（Inconsistent hierarchy）
            # 比如 A(B, C) 和 B(C, A) 同时存在
            remaining = [list(p) for p in active_lists]
            raise TypeError(
                f"Cannot create a consistent MRO for class '{context_cls}'. "
                f"Remaining merge lists: {remaining}"
            )


def _resolve_mro(
        cls: str,
        hierarchy: "dict[str, tuple[str, ...]]",
        cache: "dict[str, tuple[str, ...]]",
        stack_dict: "dict[str, None]",
) -> "tuple[str, ...]":
    """
    递归解析单个类的 MRO，结果存入 cache。
    """
    # 1. 检查缓存
    if cls in cache:
        return cache[cls]

    # 2. 检测循环引用 (Circular Dependency)
    if cls in stack_dict:
        # 提取环的路径：从第一次出现 cls 的地方到当前
        path = list(stack_dict.keys())
        cycle_path = path[path.index(cls):] + [cls]
        raise TypeError(f"Cycle detected in inheritance hierarchy: {' -> '.join(cycle_path)}")

    # 3. 获取父类
    parents = hierarchy.get(cls, ())
    if not parents:
        res = (cls,)
        cache[cls] = res
        return res

    # 4. 进入递归：将当前类压入路径栈
    stack_dict[cls] = None
    try:
        parent_mros = [
            _resolve_mro(p, hierarchy, cache, stack_dict)
            for p in parents
        ]

        # 5. 执行 C3 Merge
        merge_input = [list(mro) for mro in parent_mros] + [list(parents)]
        cls_mro = (cls,) + _merge_c3(merge_input, cls)

        cache[cls] = cls_mro
        return cls_mro
    finally:
        # 无论成功与否，退出当前层级时必须弹出，确保不影响其他分支
        try:
            del stack_dict[cls]
        except KeyError:
            pass


def build_mro(hierarchy: "dict[str, tuple[str, ...]]") -> "dict[str, tuple[str, ...]]":
    """
    构建 hierarchy 中所有类的 MRO。

    Args:
        hierarchy (dict[str, tuple[str, ...]]):
            key: class name
            value: tuple of parents

    Returns:
        dict[str, tuple[str, ...]]:
            key: class name
            value: MRO chain
    """
    mro_cache = {}
    stack_dict = {}

    for class_name in hierarchy:
        _resolve_mro(class_name, hierarchy, mro_cache, stack_dict)

    return mro_cache
