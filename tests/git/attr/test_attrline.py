from alasio.git.attr.attrline import parse_gitattributes_line


# ============================================================
# 空行和注释行
# ============================================================
def test_empty_line():
    """空字符串返回空结果"""
    assert parse_gitattributes_line('') == ('', {})


def test_whitespace_only_line():
    """纯空白行返回空结果"""
    assert parse_gitattributes_line('   \t  ') == ('', {})


def test_comment_line():
    """注释行返回空结果"""
    assert parse_gitattributes_line('# this is a comment') == ('', {})
    assert parse_gitattributes_line('  # leading whitespace then comment') == ('', {})


# ============================================================
# 无引号、无转义的普通行
# ============================================================
def test_plain_pathspec_with_attrs():
    """普通路径模式 + 属性"""
    pathspec, attrs = parse_gitattributes_line('*.py     text diff=python')
    assert pathspec == '*.py'
    assert attrs == {'text': 'set', 'diff': 'python'}


def test_plain_pathspec_bare():
    """仅有路径模式，无属性"""
    pathspec, attrs = parse_gitattributes_line('*.py')
    assert pathspec == '*.py'
    assert attrs == {}


def test_plain_pathspec_with_whitespace_then_nothing():
    """路径模式后有空格但无属性"""
    pathspec, attrs = parse_gitattributes_line('*.py   ')
    assert pathspec == '*.py'
    assert attrs == {}


def test_plain_mixed_attrs():
    """混合多种属性"""
    pathspec, attrs = parse_gitattributes_line('package-lock.json text eol=lf -diff')
    assert pathspec == 'package-lock.json'
    assert attrs == {'text': 'set', 'eol': 'lf', 'diff': ''}


# ============================================================
# 有转义但无引号的路径（无引号 + 有 \\）
# ============================================================
def test_unquoted_with_newline_escape():
    """无引号但有 \\n 转义 — 换行符被转换为真实换行并保留在路径中"""
    line = 'hello\\nworld text'
    pathspec, attrs = parse_gitattributes_line(line)
    assert pathspec == 'hello\nworld'
    assert attrs == {'text': 'set'}


def test_unquoted_with_tab_escape():
    """无引号但有 \\t 转义 — tab 被转换"""
    line = 'file\\tname.txt text'
    pathspec, attrs = parse_gitattributes_line(line)
    assert pathspec == 'file\tname.txt'
    assert attrs == {'text': 'set'}


def test_unquoted_with_carriage_return_escape():
    """无引号但有 \\r 转义"""
    line = 'file\\rname.txt text'
    pathspec, attrs = parse_gitattributes_line(line)
    assert pathspec == 'file\rname.txt'
    assert attrs == {'text': 'set'}


def test_unquoted_unknown_escape_preserved():
    """无引号路径中的未知转义（\\ 空格）— 空格原样保留在路径中"""
    line = 'file\\ name.txt text'
    pathspec, attrs = parse_gitattributes_line(line)
    assert pathspec == 'file name.txt'
    assert attrs == {'text': 'set'}


def test_unquoted_escape_bare_pathspec():
    """无引号转义路径，仅有路径无属性，且 end 为真值"""
    line = 'hello\\nworld'
    pathspec, attrs = parse_gitattributes_line(line)
    assert pathspec == 'hello\nworld'
    assert attrs == {}


def test_unquoted_escape_only_backslash():
    """仅有转义无普通字符 — end 为假值分支"""
    line = '\\'
    pathspec, attrs = parse_gitattributes_line(line)
    assert pathspec == ''
    assert attrs == {}


def test_unquoted_escape_then_text():
    """\\n 后跟普通文本，无后续属性"""
    line = '\\nhello'
    pathspec, attrs = parse_gitattributes_line(line)
    assert pathspec == '\nhello'
    assert attrs == {}


# ============================================================
# 有引号但无转义的路径
# ============================================================
def test_quoted_simple():
    """简单带引号路径 + 属性"""
    pathspec, attrs = parse_gitattributes_line('"file name.txt" text eol=lf')
    assert pathspec == 'file name.txt'
    assert attrs == {'text': 'set', 'eol': 'lf'}


def test_quoted_bare_pathspec():
    """仅有带引号路径，无属性"""
    pathspec, attrs = parse_gitattributes_line('"file name.txt"')
    assert pathspec == 'file name.txt'
    assert attrs == {}


def test_quoted_bare_with_trailing_space():
    """带引号路径后有空格但无属性"""
    pathspec, attrs = parse_gitattributes_line('"file name.txt"   ')
    assert pathspec == 'file name.txt'
    assert attrs == {}


def test_quoted_unpaired_quote():
    """引号未配对 — pathspec 保留开头的双引号"""
    pathspec, attrs = parse_gitattributes_line('"filename.txt text eol=lf')
    assert pathspec == '"filename.txt'
    assert attrs == {'text': 'set', 'eol': 'lf'}


def test_quoted_unpaired_bare():
    """引号未配对且无后续属性"""
    pathspec, attrs = parse_gitattributes_line('"filename')
    assert pathspec == '"filename'
    assert attrs == {}


# ============================================================
# 有引号且有转义的路径
# ============================================================
def test_quoted_with_escaped_quote():
    """引号内转义双引号 — 路径中包含字面双引号"""
    line = '"my \\"quoted\\" file.txt" merge=ours'
    pathspec, attrs = parse_gitattributes_line(line)
    assert pathspec == 'my "quoted" file.txt'
    assert attrs == {'merge': 'ours'}


def test_quoted_with_n_escape():
    """引号内有 \\n 转义 — 正确解析为换行符"""
    line = '"hello\\nworld" text'
    pathspec, attrs = parse_gitattributes_line(line)
    assert pathspec == 'hello\nworld'
    assert attrs == {'text': 'set'}


def test_quoted_escape_bare_pathspec():
    """引号内转义路径，无属性"""
    line = '"a\\tb"'
    pathspec, attrs = parse_gitattributes_line(line)
    assert pathspec == 'a\tb'
    assert attrs == {}


# ============================================================
# 属性解析: text
# ============================================================
def test_attr_text_set():
    """text 属性设为 set"""
    _, attrs = parse_gitattributes_line('*.py text')
    assert attrs == {'text': 'set'}


# ============================================================
# 属性解析: binary
# ============================================================
def test_attr_binary():
    """binary 等价于 -text -diff -merge"""
    _, attrs = parse_gitattributes_line('*.bin binary')
    assert attrs == {'text': 'unset', 'diff': '', 'merge': ''}


def test_attr_binary_overwrites_previous():
    """binary 覆盖之前设置的 text"""
    _, attrs = parse_gitattributes_line('*.bin text binary')
    assert attrs == {'text': 'unset', 'diff': '', 'merge': ''}


# ============================================================
# 属性解析: -text, -diff, -merge
# ============================================================
def test_attr_neg_text():
    """-text 取消 text"""
    _, attrs = parse_gitattributes_line('*.bin -text')
    assert attrs == {'text': 'unset'}


def test_attr_neg_diff():
    """-diff 取消 diff"""
    _, attrs = parse_gitattributes_line('*.bin -diff')
    assert attrs == {'diff': ''}


def test_attr_neg_merge():
    """-merge 取消 merge"""
    _, attrs = parse_gitattributes_line('*.bin -merge')
    assert attrs == {'merge': ''}


# ============================================================
# 属性解析: eol=...
# ============================================================
def test_attr_eol_lf():
    """eol=lf"""
    _, attrs = parse_gitattributes_line('*.py eol=lf')
    assert attrs == {'eol': 'lf'}


def test_attr_eol_crlf():
    """eol=crlf"""
    _, attrs = parse_gitattributes_line('*.py eol=crlf')
    assert attrs == {'eol': 'crlf'}


def test_attr_eol_invalid():
    """eol=invalid_value — 不设 eol 键"""
    _, attrs = parse_gitattributes_line('*.py eol=native')
    assert 'text' not in attrs
    assert 'eol' not in attrs


def test_attr_eol_does_not_overwrite_text_unset():
    """eol=lf 会设 text=set，不影响之前的 -text"""
    _, attrs = parse_gitattributes_line('*.py -text eol=lf')
    assert attrs == {'text': 'unset', 'eol': 'lf'}


# ============================================================
# 属性解析: diff=..., merge=...
# ============================================================
def test_attr_diff_driver():
    """diff=python 设置 diff driver"""
    _, attrs = parse_gitattributes_line('*.py diff=python')
    assert attrs == {'diff': 'python'}


def test_attr_merge_driver():
    """merge=ours 设置 merge driver"""
    _, attrs = parse_gitattributes_line('*.lock merge=ours')
    assert attrs == {'merge': 'ours'}


# ============================================================
# 属性解析: 未知 key=value / token（应被忽略）
# ============================================================
def test_attr_unknown_key_value_ignored():
    """不认识的 key=value 被忽略"""
    _, attrs = parse_gitattributes_line('*.py foo=bar')
    assert attrs == {}


def test_attr_unknown_token_ignored():
    """不是特殊关键字也没有 = 的 token 被忽略"""
    _, attrs = parse_gitattributes_line('*.py something')
    assert attrs == {}


# ============================================================
# 属性解析: 空 token 跳过
# ============================================================
def test_attr_empty_token_skipped():
    """连续的多个空格不应产生空 token 影响结果"""
    _, attrs = parse_gitattributes_line('*.py    text    eol=lf')
    assert attrs == {'text': 'set', 'eol': 'lf'}


# ============================================================
# 组合场景
# ============================================================
def test_complex_combination():
    """复杂的组合属性 — 后出现的属性覆盖前面的"""
    _, attrs = parse_gitattributes_line('* text=auto eol=lf diff=python merge=text')
    assert attrs == {'text': 'auto', 'eol': 'lf', 'diff': 'python', 'merge': 'text'}


def test_binary_with_eol():
    """binary 后 eol=lf 会重置 text=set"""
    _, attrs = parse_gitattributes_line('*.dat binary eol=lf')
    assert attrs == {'text': 'unset', 'eol': 'lf', 'diff': '', 'merge': ''}


def test_multiple_neg_attrs():
    """多个否定属性"""
    _, attrs = parse_gitattributes_line('*.dat -text -diff -merge')
    assert attrs == {'text': 'unset', 'diff': '', 'merge': ''}


def test_pathspec_with_spaces_inside_quotes():
    """引号内路径含空格"""
    pathspec, attrs = parse_gitattributes_line('"My Documents/file.txt" text')
    assert pathspec == 'My Documents/file.txt'
    assert attrs == {'text': 'set'}


# ============================================================
# ESCAPE_CHAR / text=auto / text=unknown_value
# ============================================================
def test_escape_char_mapping():
    """验证 ESCAPE_CHAR 表中所有转义映射"""
    from alasio.git.attr.attrline import ESCAPE_CHAR
    assert ESCAPE_CHAR == {'n': '\n', 't': '\t', 'r': '\r'}
    assert ESCAPE_CHAR['n'] == '\n'
    assert ESCAPE_CHAR['t'] == '\t'
    assert ESCAPE_CHAR['r'] == '\r'

    # 未知转义字符原样保留（fallback 逻辑在 get 调用中）
    assert ESCAPE_CHAR.get('x', 'x') == 'x'
    assert ESCAPE_CHAR.get(' ', ' ') == ' '


def test_attr_text_auto():
    """text=auto 直接设置 text='auto'"""
    _, attrs = parse_gitattributes_line('*.txt text=auto')
    assert attrs == {'text': 'auto'}


def test_attr_text_unknown_value():
    """text=unknown 退化为 text='set'"""
    _, attrs = parse_gitattributes_line('*.dat text=foobar')
    assert attrs == {'text': 'set'}


# ============================================================
# 边界情况
# ============================================================
def test_only_backslash_in_unquoted():
    """无引号路径仅有反斜杠 — end 假值分支"""
    line = '\\'
    pathspec, attrs = parse_gitattributes_line(line)
    assert pathspec == ''
    assert attrs == {}


def test_escape_at_end_of_unquoted():
    """转义在无引号路径末尾"""
    line = 'file\\'
    pathspec, attrs = parse_gitattributes_line(line)
    assert pathspec == 'file'
    assert attrs == {}


def test_escape_at_end_of_unquoted_with_attrs():
    """转义在无引号路径末尾，后有属性 — 空格原样保留在路径中"""
    line = 'file\\ text'
    pathspec, attrs = parse_gitattributes_line(line)
    assert pathspec == 'file text'
    assert attrs == {}


def test_escape_then_two_spaces_first_escaped_second_separator():
    """file\\ text：第一个空格被转义保留，第二个空格作为分隔符，text 被解析为属性"""
    line = 'file\\  text'
    pathspec, attrs = parse_gitattributes_line(line)
    assert pathspec == 'file '
    assert attrs == {'text': 'set'}


def test_escape_quote_complex_end():
    line = r'file\" text'
    pathspec, attrs = parse_gitattributes_line(line)
    assert pathspec == 'file"'
    assert attrs == {'text': 'set'}

    line = r'"file\"" text'
    pathspec, attrs = parse_gitattributes_line(line)
    assert pathspec == 'file"'
    assert attrs == {'text': 'set'}

    line = r'file\\" text'
    pathspec, attrs = parse_gitattributes_line(line)
    assert pathspec == r'file\"'
    assert attrs == {'text': 'set'}
