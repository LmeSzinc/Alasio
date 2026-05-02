import re
from collections import deque


def _split_pathspec(pattern):
    """
    独立辅助函数：单次遍历完成路径块分割，保留转义字符供后续处理。

    Args:
        pattern (str):

    Returns:
        list[str]:
    """
    chunks = []
    cur = deque()
    escape = False

    for i, char in enumerate(pattern):
        if escape:
            cur.append('\\')
            cur.append(char)
            escape = False
        elif char == '\\':
            escape = True
        elif char == '/':
            chunks.append("".join(cur))
            cur.clear()
        else:
            cur.append(char)

    if escape:
        cur.append('\\')

    chunks.append("".join(cur))
    return chunks


def _convert_normal_chunk(chunk):
    """
    独立辅助函数：单次遍历将普通的 glob 字符转换为正则。
    使用状态标记和 deque 缓冲，消除嵌套循环与高频字符串拼接。

    Args:
        chunk (str):

    Returns:
        str:
    """
    res = deque()
    escape = False
    in_charset = False
    charset_buffer = deque()

    for i, char in enumerate(chunk):
        if escape:
            if in_charset:
                if char == '/':
                    # Git 不支持字符集(charset)中含 /，遇到则直接丢弃
                    pass
                else:
                    charset_buffer.append('\\')
                    charset_buffer.append(char)
            else:
                res.append(re.escape(char))
            escape = False
            continue

        if char == '\\':
            escape = True
            continue

        if in_charset:
            if char == ']':
                # 闭合字符集
                in_charset = False
                charset_str = "".join(charset_buffer)

                # 转换 Git 的 [!...] 为 Python 正则的 [^...]
                if charset_str.startswith('!'):
                    charset_body = '^' + charset_str[1:]
                else:
                    charset_body = charset_str

                # 防御性处理：避免空 [] 导致 Python re 模块崩溃
                if not charset_body or charset_body == '^':
                    res.append('(?!)')  # 负向零宽断言，永远不匹配
                else:
                    res.append('(?=[^/])[')
                    res.append(charset_body)
                    res.append(']')

                charset_buffer.clear()
            elif char == '[':
                # 字符集内遇到新的 [，回退当前未闭合的字符集，重新开始
                res.append(re.escape('['))
                for c in charset_buffer:
                    res.append(re.escape(c))
                charset_buffer.clear()
                # in_charset 保持 True，[ 不加入缓冲区，作为新字符集的开始
            elif char == '/':
                # 丢弃未转义直接混入 [] 中的 /
                continue
            else:
                # 累加到缓冲区
                charset_buffer.append(char)
        else:
            if char == '*':
                res.append('[^/]*')
            elif char == '?':
                res.append('[^/]')
            elif char == '[':
                in_charset = True
            else:
                res.append(re.escape(char))

    # 兜底处理循环结束时未完结的状态
    if escape:
        if in_charset:
            charset_buffer.append('\\')
        else:
            res.append(re.escape('\\'))

    if in_charset:
        # 如果字符集最终没有闭合，Git将其视作普通字符，进行平滑回退
        # 使用 re.escape 纯文本处理，避免通配符被二次解析
        # 注意 charset_buffer 中可能含未处理的转义序列，需要重新解释
        res.append(re.escape('['))
        fallback_escape = False
        for c in charset_buffer:
            if fallback_escape:
                res.append(re.escape(c))
                fallback_escape = False
            elif c == '\\':
                fallback_escape = True
            else:
                res.append(re.escape(c))
        if fallback_escape:
            res.append(re.escape('\\'))

    return "".join(res)


def gitattributes_to_regex(root, pathspec):
    """
    将 .gitattributes 的路径规则转换为用于完整路径匹配的正则表达式。

    Args:
        root (str): .gitattributes 所在的目录位置 (如 "" 或 "src/")
        pathspec (str): .gitattributes 路径规则 (如 "*.py")

    Returns:
        str: 编译就绪的正则表达式字符串
    """
    if not pathspec:
        return "(?s)^$"

    # 1. 规范化 Root 目录前缀
    if root:
        if not root.endswith('/'):
            root += '/'
        root_re = re.escape(root)
    else:
        root_re = ""

    s = pathspec
    is_floating = None

    # 2. 预处理头部的显式锚定符和浮动标记
    if s.startswith('**/'):
        is_floating = True
        s = s[3:]
    elif s.startswith('/'):
        is_floating = False
        s = s[1:]

    # 3. 执行分割
    chunks = _split_pathspec(s)

    # 判定是否为浮动规则
    if is_floating is None:
        if len(chunks) == 1:
            is_floating = True
        elif len(chunks) == 2 and chunks[1] == '':
            is_floating = True
        else:
            is_floating = False

    # 4. 处理末尾斜杠 (匹配目录下所有内容)
    if len(chunks) > 1 and chunks[-1] == '':
        chunks[-1] = '**'

    # 合并连续的 '**'
    filtered_chunks = []
    for c in chunks:
        if c == '**' and filtered_chunks and filtered_chunks[-1] == '**':
            continue
        filtered_chunks.append(c)
    chunks = filtered_chunks

    # 5. 组装每一块成为完整的正则主体（使用 enumerate 遍历及 deque 缓冲）
    regex_body_parts = deque()
    n = len(chunks)

    for i, chunk in enumerate(chunks):
        if chunk == '**':
            if i == n - 1:
                if i == 0:
                    regex_body_parts.append('.*')
                else:
                    regex_body_parts.append('/.*')
            else:
                if i == 0:
                    regex_body_parts.append('(?:.*/)?')
                else:
                    regex_body_parts.append('/(?:.*/)?')
        else:
            if i > 0 and chunks[i - 1] != '**':
                regex_body_parts.append('/')
            regex_body_parts.append(_convert_normal_chunk(chunk))

    regex_body = "".join(regex_body_parts)

    # 6. 处理锚定范围和目录穿透闭包
    full_regex_parts = deque(['(?s)^', root_re])

    if is_floating:
        full_regex_parts.append('(?:.*/)?')

    full_regex_parts.append(regex_body)

    # 命中规则后的同级、下级目录穿透特性
    if chunks and chunks[-1] != '**':
        full_regex_parts.append('(?:$|/.*)')

    return "".join(full_regex_parts)
