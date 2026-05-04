"""
Tests for alasio.git.attr.attrfile module.

Tests cover:
- FileAttrs (msgspec struct, pending_pattern)
- TestAttrsDict (attrs_dict computation: deeper overrides shallower, later overrides earlier)
- PatternBase
- PatternRegex (including regex caching and apply method)
- PatternFileName (match_pathspec classmethod and apply method)
- PatternFileSuffix (match_pathspec classmethod and apply method)
- get_pattern() factory function
"""

import re

from alasio.git.attr.attrfile import (FileAttrs, PatternBase, PatternFileName, PatternFileSuffix, PatternRegex,
                                      get_pattern)


# ==========================================
# 1. FileAttrs 测试 — 构造与字段
# ==========================================
class TestFileAttrs:
    def test_default_construction(self):
        """测试 FileAttrs 默认构造"""
        fa = FileAttrs(path="module/xxx.py")
        assert fa.path == "module/xxx.py"
        assert fa.pending_pattern == []

    def test_with_pending_pattern(self):
        """测试 FileAttrs 携带 pending_pattern"""
        patterns = [
            PatternFileName(root="", name="test.py", data={"text": "auto"}),
            PatternFileSuffix(root="", suffix=".py", data={"eol": "lf"}),
        ]
        fa = FileAttrs(path="foo/bar.txt", pending_pattern=patterns)
        assert fa.path == "foo/bar.txt"
        assert fa.pending_pattern == patterns
        assert fa.pending_pattern is patterns  # 传入的正是同一个列表引用

    def test_pending_pattern_default_is_empty(self):
        """测试 pending_pattern 默认是空列表且互不影响"""
        fa1 = FileAttrs(path="a.py")
        fa2 = FileAttrs(path="b.py")
        fa1.pending_pattern.append(PatternFileName(root="", name="a.py", data={"key": "val"}))
        assert len(fa1.pending_pattern) == 1
        assert fa2.pending_pattern == []

    def test_path_separator_is_always_forward_slash(self):
        """git 中路径分隔符总是 /"""
        fa = FileAttrs(path="backend/api/router.py")
        assert "/" in fa.path
        assert "\\" not in fa.path


# ==========================================
# 2. TestAttrsDict 测试 — attrs_dict 计算逻辑
# ==========================================
class TestAttrsDict:
    """测试 FileAttrs.attrs_dict 的覆盖规则：
    - 深层目录覆盖浅层目录
    - 同目录下 pending_pattern 中靠后的覆盖靠前的
    - unset ('-') 支持
    """

    # --- 基础 ---
    def test_empty(self):
        """无匹配 pattern 时返回空 dict"""
        fa = FileAttrs(path="test.py")
        assert fa.attrs_dict == {}

    def test_single_pattern(self):
        """单个 pattern 直接返回其 data"""
        fa = FileAttrs(path="test.py")
        fa.pending_pattern.append(PatternFileName(root="", name="test.py", data={"text": "auto", "eol": "lf"}))
        assert fa.attrs_dict == {"text": "auto", "eol": "lf"}

    # --- 同目录覆盖 ---
    def test_same_dir_override(self):
        """同一目录下，pending_pattern 中靠后的 pattern 覆盖靠前的"""
        fa = FileAttrs(path="test.py")
        fa.pending_pattern.append(PatternFileName(root="", name="test.py", data={"text": "auto", "eol": "lf"}))
        fa.pending_pattern.append(PatternFileName(root="", name="test.py", data={"text": "override"}))
        assert fa.attrs_dict == {"text": "override", "eol": "lf"}

    def test_same_dir_partial_override(self):
        """同一目录下，后面的 pattern 只覆盖已设置的 key，未涉及的 key 保留"""
        fa = FileAttrs(path="test.py")
        fa.pending_pattern.append(PatternFileName(root="", name="test.py", data={"a": "1", "b": "2", "c": "3"}))
        fa.pending_pattern.append(PatternFileName(root="", name="test.py", data={"b": "new-b"}))
        assert fa.attrs_dict == {"a": "1", "b": "new-b", "c": "3"}

    def test_same_dir_add_new_key(self):
        """同一目录下，后面的 pattern 可以追加新 key"""
        fa = FileAttrs(path="test.py")
        fa.pending_pattern.append(PatternFileName(root="", name="test.py", data={"text": "auto"}))
        fa.pending_pattern.append(PatternFileName(root="", name="test.py", data={"eol": "lf"}))
        assert fa.attrs_dict == {"text": "auto", "eol": "lf"}

    def test_same_dir_three_layers(self):
        """同一目录下三层 pattern 覆盖"""
        fa = FileAttrs(path="test.py")
        fa.pending_pattern.append(PatternFileName(root="", name="test.py", data={"text": "first", "eol": "lf"}))
        fa.pending_pattern.append(PatternFileName(root="", name="test.py", data={"text": "second"}))
        fa.pending_pattern.append(PatternFileName(root="", name="test.py", data={"text": "third", "diff": "python"}))
        assert fa.attrs_dict == {"text": "third", "eol": "lf", "diff": "python"}

    # --- 不同目录覆盖 ---
    def test_deeper_overrides_shallower(self):
        """深层目录的 pattern 覆盖浅层目录"""
        fa = FileAttrs(path="src/main.py")
        fa.pending_pattern.append(
            PatternFileSuffix(root="", suffix=".py", data={"text": "auto", "eol": "lf", "difftype": "default"}))
        fa.pending_pattern.append(
            PatternFileSuffix(root="src/", suffix=".py", data={"text": "override", "difftype": "python"}))
        assert fa.attrs_dict == {"text": "override", "eol": "lf", "difftype": "python"}

    def test_deeper_overrides_regardless_of_insert_order(self):
        """深层目录覆盖浅层目录，无论 pending_pattern 中的插入顺序如何（排序纠正）"""
        fa = FileAttrs(path="frontend/src/abc.svelte")
        p_frontend = PatternFileSuffix(root="frontend/", suffix=".svelte", data={"text": "html", "diff": "svelte"})
        p_root = PatternFileSuffix(root="", suffix=".svelte", data={"text": "auto", "eol": "lf"})
        # 反向顺序
        fa.pending_pattern = [p_frontend, p_root]
        assert fa.attrs_dict == {"text": "html", "diff": "svelte", "eol": "lf"}
        # 正向顺序
        fa.pending_pattern = [p_root, p_frontend]
        assert fa.attrs_dict == {"text": "html", "diff": "svelte", "eol": "lf"}

    def test_three_levels_deepest_wins(self):
        """三层目录嵌套：最深层覆盖全部"""
        fa = FileAttrs(path="frontend/src/routes/app.svelte")
        p_root = PatternFileSuffix(root="", suffix=".svelte", data={"text": "root", "a": "1"})
        p_frontend = PatternFileSuffix(root="frontend/", suffix=".svelte", data={"text": "frontend", "b": "2"})
        p_routes = PatternFileSuffix(root="frontend/src/routes/", suffix=".svelte", data={"text": "routes", "c": "3"})
        fa.pending_pattern = [p_frontend, p_routes, p_root]
        assert fa.attrs_dict == {"text": "routes", "a": "1", "b": "2", "c": "3"}

    # --- unset ('-') 测试 ---
    def test_unset_value(self):
        """值为 '-' 表示 unset，从结果中移除该 key"""
        fa = FileAttrs(path="test.py")
        fa.pending_pattern.append(
            PatternFileName(root="", name="test.py", data={"text": "auto", "eol": "lf", "merge": "union"}))
        fa.pending_pattern.append(PatternFileName(root="", name="test.py", data={"text": "-"}))
        assert fa.attrs_dict == {"eol": "lf", "merge": "union"}

    def test_unset_nonexistent_key(self):
        """unset 一个不存在的 key 不应报错"""
        fa = FileAttrs(path="test.py")
        fa.pending_pattern.append(PatternFileName(root="", name="test.py", data={"text": "auto"}))
        fa.pending_pattern.append(PatternFileName(root="", name="test.py", data={"eol": "-"}))
        assert fa.attrs_dict == {"text": "auto"}

    def test_unset_then_reset(self):
        """unset 后再重新设置"""
        fa = FileAttrs(path="test.py")
        fa.pending_pattern.append(PatternFileName(root="", name="test.py", data={"text": "auto", "eol": "lf"}))
        fa.pending_pattern.append(PatternFileName(root="", name="test.py", data={"text": "-"}))
        fa.pending_pattern.append(PatternFileName(root="", name="test.py", data={"text": "re-enabled"}))
        assert fa.attrs_dict == {"text": "re-enabled", "eol": "lf"}

    def test_deeper_unset_shallower_value(self):
        """深层目录的 pattern unset 浅层的值"""
        fa = FileAttrs(path="src/main.py")
        fa.pending_pattern.append(
            PatternFileSuffix(root="", suffix=".py", data={"text": "auto", "eol": "lf", "diff": "python"}))
        fa.pending_pattern.append(PatternFileSuffix(root="src/", suffix=".py", data={"diff": "-"}))
        assert fa.attrs_dict == {"text": "auto", "eol": "lf"}

    def test_deeper_re_set_after_shallow_unset(self):
        """三层：浅层设值 → 中层 unset → 深层重新设值"""
        fa = FileAttrs(path="a/b/test.py")
        p_root = PatternFileSuffix(root="", suffix=".py", data={"text": "auto", "eol": "lf", "diff": "python"})
        p_a = PatternFileSuffix(root="a/", suffix=".py", data={"diff": "-"})
        p_ab = PatternFileSuffix(root="a/b/", suffix=".py", data={"diff": "newdriver"})
        fa.pending_pattern = [p_root, p_a, p_ab]
        assert fa.attrs_dict == {"text": "auto", "eol": "lf", "diff": "newdriver"}

    # --- 缓存行为 ---
    def test_attrs_dict_is_cached(self):
        """attrs_dict 是 cached_property，重复访问返回同一对象"""
        fa = FileAttrs(path="test.py")
        fa.pending_pattern.append(PatternFileName(root="", name="test.py", data={"text": "auto"}))
        d1 = fa.attrs_dict
        d2 = fa.attrs_dict
        assert d1 is d2

    def test_attrs_dict_not_invalidated_by_mutation(self):
        """cached_property 在首次访问后不因 pending_pattern 变更而更新（已知限制）"""
        fa = FileAttrs(path="test.py")
        fa.pending_pattern.append(PatternFileName(root="", name="test.py", data={"text": "auto"}))
        d1 = fa.attrs_dict
        fa.pending_pattern.append(PatternFileName(root="", name="test.py", data={"eol": "lf"}))
        d3 = fa.attrs_dict
        assert d3 is d1
        assert d3 == {"text": "auto"}


# ==========================================
# 3. PatternBase 测试
# ==========================================
class TestPatternBase:
    def test_construction(self):
        """测试 PatternBase 直接构造"""
        data = {"text": "auto", "eol": "lf"}
        pb = PatternBase(root="src/", data=data)
        assert pb.root == "src/"
        assert pb.data == data

    def test_empty_root(self):
        """root 为空字符串表示根目录 .gitattributes"""
        pb = PatternBase(root="", data={"binary": True})
        assert pb.root == ""
        assert pb.data == {"binary": True}


# ==========================================
# 4. PatternRegex 测试
# ==========================================
class TestPatternRegex:
    def test_construction_and_regex_caching(self):
        """测试 PatternRegex 的正则生成与缓存"""
        pr = PatternRegex(root="", pathspec="*.py", data={"text": "auto"})
        assert pr.root == ""
        assert pr.pathspec == "*.py"
        assert pr.data == {"text": "auto"}
        regex1 = pr.regex
        regex2 = pr.regex
        assert regex1 is regex2
        assert isinstance(regex1, re.Pattern)

    def test_regex_with_root(self):
        """测试带 root 的 PatternRegex"""
        pr = PatternRegex(root="src/", pathspec="*.py", data={"text": "auto"})
        assert pr.regex.pattern.startswith("(?s)^src/")

    def test_apply_matches_files_with_root(self):
        """测试 PatternRegex.apply 在 root 前缀下的匹配，往 pending_pattern 存 self"""
        pr = PatternRegex(root="backend/", pathspec="*.py", data={"type": "python"})
        files = [
            FileAttrs(path="backend/main.py"),
            FileAttrs(path="backend/api/router.py"),
            FileAttrs(path="frontend/app.ts"),
            FileAttrs(path="backend/README.md"),
        ]
        pr.apply(files)
        assert files[0].pending_pattern == [pr]
        assert files[1].pending_pattern == [pr]
        assert files[2].pending_pattern == []
        assert files[3].pending_pattern == []

    def test_apply_matches_files_without_root(self):
        """测试 PatternRegex.apply 无 root 前缀时匹配所有路径"""
        pr = PatternRegex(root="", pathspec="*.svelte", data={"text": "html"})
        files = [
            FileAttrs(path="src/routes/+page.svelte"),
            FileAttrs(path="src/lib/index.ts"),
            FileAttrs(path="src/lib/Button.svelte"),
        ]
        pr.apply(files)
        assert files[0].pending_pattern == [pr]
        assert files[1].pending_pattern == []
        assert files[2].pending_pattern == [pr]

    def test_regex_anchor_prevents_partial_match(self):
        """测试正则锚定防止部分匹配"""
        pr = PatternRegex(root="", pathspec="foo", data={"key": "val"})
        files = [
            FileAttrs(path="foo"),
            FileAttrs(path="foobar"),
            FileAttrs(path="x/foo"),
            FileAttrs(path="x/foobar"),
        ]
        pr.apply(files)
        assert files[0].pending_pattern == [pr]
        assert files[1].pending_pattern == []
        assert files[2].pending_pattern == [pr]
        assert files[3].pending_pattern == []

    def test_apply_appends_multiple_times(self):
        """测试多次 apply 追加 pattern 到 pending_pattern"""
        pr1 = PatternRegex(root="", pathspec="*.py", data={"a": "1"})
        pr2 = PatternRegex(root="", pathspec="*.py", data={"b": "2"})
        file = FileAttrs(path="test.py")
        pr1.apply([file])
        pr2.apply([file])
        assert file.pending_pattern == [pr1, pr2]


# ==========================================
# 5. PatternFileName 测试
# ==========================================
class TestPatternFileName:
    def test_match_pathspec_simple_filename(self):
        """测试 match_pathspec 匹配简单文件名"""
        assert PatternFileName.match_pathspec("package.json") is True
        assert PatternFileName.match_pathspec("README.md") is True
        assert PatternFileName.match_pathspec("Dockerfile") is True

    def test_match_pathspec_rejects_paths_with_slash(self):
        """包含 / 的不是纯文件名"""
        assert PatternFileName.match_pathspec("src/package.json") is False
        assert PatternFileName.match_pathspec("/README.md") is False

    def test_match_pathspec_rejects_wildcards(self):
        """包含通配符 * 的不是纯文件名"""
        assert PatternFileName.match_pathspec("*.py") is False
        assert PatternFileName.match_pathspec("file*") is False

    def test_match_pathspec_rejects_charset(self):
        """包含字符集 [ 的不是纯文件名"""
        assert PatternFileName.match_pathspec("file[0-9].txt") is False

    def test_apply_exact_name_match(self):
        """测试 PatternFileName.apply 精确文件名匹配，往 pending_pattern 存 self"""
        pn = PatternFileName(root="", name="package.json", data={"text": "json"})
        files = [
            FileAttrs(path="package.json"),
            FileAttrs(path="frontend/package.json"),
            FileAttrs(path="backend/package.json"),
            FileAttrs(path="package-lock.json"),
            FileAttrs(path="src/package.json.backup"),
        ]
        pn.apply(files)
        assert files[0].pending_pattern == [pn]
        assert files[1].pending_pattern == [pn]
        assert files[2].pending_pattern == [pn]
        assert files[3].pending_pattern == []
        assert files[4].pending_pattern == []

    def test_apply_with_root_prefix(self):
        """测试 PatternFileName.apply 带 root 前缀"""
        pn = PatternFileName(root="src/", name="index.ts", data={"export": "barrel"})
        files = [
            FileAttrs(path="src/index.ts"),
            FileAttrs(path="src/lib/index.ts"),
            FileAttrs(path="tests/index.ts"),
            FileAttrs(path="src/index.js"),
        ]
        pn.apply(files)
        assert files[0].pending_pattern == [pn]
        assert files[1].pending_pattern == [pn]
        assert files[2].pending_pattern == []
        assert files[3].pending_pattern == []


# ==========================================
# 6. PatternFileSuffix 测试
# ==========================================
class TestPatternFileSuffix:
    def test_match_pathspec_star_suffix(self):
        """测试 match_pathspec 匹配 *.ext 格式"""
        assert PatternFileSuffix.match_pathspec("*.py") is True
        assert PatternFileSuffix.match_pathspec("*.txt") is True
        assert PatternFileSuffix.match_pathspec("*.component.svelte") is True

    def test_match_pathspec_rejects_non_star_start(self):
        """不以 * 开头的不是后缀匹配"""
        assert PatternFileSuffix.match_pathspec("file*.py") is False
        assert PatternFileSuffix.match_pathspec(".gitignore") is False

    def test_match_pathspec_rejects_slash(self):
        """包含 / 的不匹配"""
        assert PatternFileSuffix.match_pathspec("src/*.py") is False

    def test_match_pathspec_rejects_nested_wildcard(self):
        """后缀中包含 * 的不匹配"""
        assert PatternFileSuffix.match_pathspec("*.p*") is False

    def test_match_pathspec_rejects_charset_in_suffix(self):
        """后缀中包含 [ 的不匹配"""
        assert PatternFileSuffix.match_pathspec("*.[ch]") is False

    def test_apply_suffix_match(self):
        """测试 PatternFileSuffix.apply 后缀匹配，往 pending_pattern 存 self"""
        ps = PatternFileSuffix(root="", suffix=".py", data={"text": "python"})
        files = [
            FileAttrs(path="main.py"),
            FileAttrs(path="backend/api/router.py"),
            FileAttrs(path="scripts/run.py.txt"),
            FileAttrs(path="frontend/app.ts"),
        ]
        ps.apply(files)
        assert files[0].pending_pattern == [ps]
        assert files[1].pending_pattern == [ps]
        assert files[2].pending_pattern == []
        assert files[3].pending_pattern == []

    def test_apply_with_root_prefix(self):
        """测试 PatternFileSuffix.apply 带 root 前缀"""
        ps = PatternFileSuffix(root="frontend/", suffix=".svelte", data={"text": "html"})
        files = [
            FileAttrs(path="frontend/src/App.svelte"),
            FileAttrs(path="frontend/src/Header.svelte"),
            FileAttrs(path="backend/template.svelte"),
            FileAttrs(path="frontend/src/main.ts"),
        ]
        ps.apply(files)
        assert files[0].pending_pattern == [ps]
        assert files[1].pending_pattern == [ps]
        assert files[2].pending_pattern == []
        assert files[3].pending_pattern == []

    def test_match_pathspec_star_only(self):
        """测试 match_pathspec 匹配单独的 *（匹配所有文件）"""
        assert PatternFileSuffix.match_pathspec("*") is True

    def test_apply_star_only_matches_all_files(self):
        """测试 pathspec='*' 的 PatternFileSuffix 匹配所有文件，后缀为空字符串"""
        ps = PatternFileSuffix(root="", suffix="", data={"text": "auto"})
        files = [
            FileAttrs(path="main.py"),
            FileAttrs(path="backend/api/router.py"),
            FileAttrs(path="frontend/src/App.svelte"),
            FileAttrs(path="README.md"),
            FileAttrs(path="package.json"),
            FileAttrs(path="doc/guide/tutorial.md"),
        ]
        ps.apply(files)
        for file in files:
            assert file.pending_pattern == [ps], f"File {file.path} should match '*'"

    def test_apply_star_only_with_root(self):
        """测试 pathspec='*' 带 root 前缀的 PatternFileSuffix 匹配该 root 下所有文件"""
        ps = PatternFileSuffix(root="frontend/", suffix="", data={"text": "frontend-default"})
        files = [
            FileAttrs(path="frontend/src/App.svelte"),
            FileAttrs(path="frontend/package.json"),
            FileAttrs(path="frontend/assets/logo.png"),
            FileAttrs(path="backend/main.py"),
            FileAttrs(path="README.md"),
        ]
        ps.apply(files)
        assert files[0].pending_pattern == [ps]
        assert files[1].pending_pattern == [ps]
        assert files[2].pending_pattern == [ps]
        assert files[3].pending_pattern == []
        assert files[4].pending_pattern == []


# ==========================================
# 7. get_pattern 工厂函数测试
# ==========================================
class TestGetPattern:
    def test_returns_pattern_filesuffix_for_star_suffix(self):
        """*.py 应返回 PatternFileSuffix"""
        p = get_pattern("", "*.py", {"text": "auto"})
        assert isinstance(p, PatternFileSuffix)
        assert p.suffix == ".py"
        assert p.root == ""
        assert p.data == {"text": "auto"}

    def test_returns_pattern_filesuffix_for_star_only(self):
        """单独的 * 应返回 PatternFileSuffix，后缀为空字符串，匹配所有文件"""
        p = get_pattern("", "*", {"text": "auto"})
        assert isinstance(p, PatternFileSuffix)
        assert p.suffix == ""
        assert p.root == ""
        assert p.data == {"text": "auto"}

    def test_returns_pattern_filename_for_plain_name(self):
        """纯文件名应返回 PatternFileName"""
        p = get_pattern("src/", "package.json", {"eol": "lf"})
        assert isinstance(p, PatternFileName)
        assert p.name == "package.json"
        assert p.root == "src/"
        assert p.data == {"eol": "lf"}

    def test_returns_pattern_regex_for_complex_pattern(self):
        """通配符/slash 等复杂模式应返回 PatternRegex"""
        p = get_pattern("", "**/*.py", {"text": "auto"})
        assert isinstance(p, PatternRegex)
        assert p.pathspec == "**/*.py"
        assert p.root == ""
        assert p.data == {"text": "auto"}

    def test_returns_pattern_regex_for_anchored_pattern(self):
        """以 / 开头的锚定模式应返回 PatternRegex"""
        p = get_pattern("", "/README.md", {"text": "markdown"})
        assert isinstance(p, PatternRegex)
        assert p.pathspec == "/README.md"

    def test_returns_pattern_regex_for_pattern_with_slash(self):
        """包含 / 的模式应返回 PatternRegex"""
        p = get_pattern("", "src/*.py", {"text": "auto"})
        assert isinstance(p, PatternRegex)

    def test_returns_pattern_regex_for_charset_pattern(self):
        """包含 [ 的模式应返回 PatternRegex"""
        p = get_pattern("", "*.[ch]", {"text": "auto"})
        assert isinstance(p, PatternRegex)

    def test_integration_all_pattern_types_work_together(self):
        """集成测试：三种 Pattern 类型混合使用，验证 attrs_dict 覆盖规则"""
        files = [
            FileAttrs(path="frontend/package.json"),
            FileAttrs(path="frontend/src/App.svelte"),
            FileAttrs(path="frontend/src/main.ts"),
            FileAttrs(path="frontend/src/lib/index.ts"),
            FileAttrs(path="backend/main.py"),
        ]
        # 根目录 .gitattributes patterns
        root_patterns = [
            get_pattern("", "package.json", {"type": "manifest"}),
            get_pattern("", "*.svelte", {"type": "component"}),
            get_pattern("", "*.ts", {"type": "typescript"}),
            get_pattern("", "**/*.py", {"type": "python"}),
        ]
        for pattern in root_patterns:
            pattern.apply(files)
        # frontend/ .gitattributes patterns（应覆盖根目录）
        frontend_patterns = [
            get_pattern("frontend/", "*.ts", {"lang": "typescript-strict"}),
        ]
        for pattern in frontend_patterns:
            pattern.apply(files)
        assert files[0].attrs_dict == {"type": "manifest"}
        assert files[1].attrs_dict == {"type": "component"}
        assert files[2].attrs_dict == {"type": "typescript", "lang": "typescript-strict"}
        assert files[3].attrs_dict == {"type": "typescript", "lang": "typescript-strict"}
        assert files[4].attrs_dict == {"type": "python"}

    def test_integration_override_order_respected(self):
        """验证 gitattributes 覆盖顺序：深层目录覆盖浅层目录（正常 apply 顺序）"""
        fa = FileAttrs(path="doc/readme.md")
        p_root = get_pattern("", "readme.md", {"text": "auto", "eol": "native"})
        p_sub = get_pattern("doc/", "readme.md", {"text": "override"})
        p_root.apply([fa])
        p_sub.apply([fa])
        assert fa.attrs_dict == {"text": "override", "eol": "native"}

    def test_integration_deeper_overrides_regardless_of_apply_order(self):
        """深层目录覆盖浅层目录，无论 apply 顺序如何（排序纠正）"""
        fa = FileAttrs(path="frontend/src/abc.svelte")
        p_frontend = get_pattern("frontend/", "*.svelte", {"text": "html", "diff": "svelte"})
        p_root = get_pattern("", "*.svelte", {"text": "auto", "eol": "lf"})
        fa.pending_pattern = [p_frontend, p_root]
        assert fa.attrs_dict == {"text": "html", "diff": "svelte", "eol": "lf"}
        fa.pending_pattern = [p_root, p_frontend]
        assert fa.attrs_dict == {"text": "html", "diff": "svelte", "eol": "lf"}

    def test_integration_same_depth_later_wins(self):
        """同深度 pattern 之间，pending_pattern 中靠后的覆盖靠前的"""
        fa = FileAttrs(path="test.py")
        p_early = PatternFileSuffix(root="", suffix=".py", data={"text": "auto", "eol": "lf"})
        p_late = PatternFileSuffix(root="", suffix=".py", data={"text": "override"})
        fa.pending_pattern = [p_early, p_late]
        assert fa.attrs_dict == {"text": "override", "eol": "lf"}

    def test_star_only_matches_all_files(self):
        """集成测试：'*' pathspec 匹配所有文件并设默认属性"""
        files = [
            FileAttrs(path="main.py"),
            FileAttrs(path="backend/api/router.py"),
            FileAttrs(path="frontend/src/App.svelte"),
            FileAttrs(path="README.md"),
            FileAttrs(path="package.json"),
        ]
        p_star = get_pattern("", "*", {"text": "auto", "eol": "lf"})
        p_star.apply(files)
        for file in files:
            assert file.attrs_dict == {"text": "auto", "eol": "lf"}, \
                f"File {file.path} should have default attrs from '*'"

    def test_star_only_with_deeper_override(self):
        """集成测试：'*' 设默认属性，更具体的 pattern 覆盖 '*' 的属性"""
        files = [
            FileAttrs(path="main.py"),
            FileAttrs(path="backend/main.py"),
            FileAttrs(path="frontend/app.ts"),
        ]
        # root '*' sets defaults for all files
        p_star = get_pattern("", "*", {"text": "auto", "eol": "lf"})
        p_star.apply(files)
        # *.py overrides for Python files
        p_py = get_pattern("", "*.py", {"text": "python"})
        p_py.apply(files)
        # *.ts overrides for TypeScript files
        p_ts = get_pattern("", "*.ts", {"text": "typescript"})
        p_ts.apply(files)
        # backend/ deeper pattern overrides eol for backend Python files
        p_backend_py = get_pattern("backend/", "*.py", {"eol": "crlf"})
        p_backend_py.apply(files)
        assert files[0].attrs_dict == {"text": "python", "eol": "lf"}
        assert files[1].attrs_dict == {"text": "python", "eol": "crlf"}
        assert files[2].attrs_dict == {"text": "typescript", "eol": "lf"}

    def test_star_only_unset_then_reset(self):
        """集成测试：'*' 全局设值 → 局部 unset → 局部重新设值"""
        files = [
            FileAttrs(path="main.py"),
            FileAttrs(path="doc/readme.md"),
        ]
        # '*' sets global defaults
        p_star = get_pattern("", "*", {"text": "auto", "eol": "lf", "diff": "default"})
        p_star.apply(files)
        # doc/* unset diff
        p_doc = get_pattern("doc/", "*", {"diff": "-"})
        p_doc.apply(files)
        # doc/readme.md re-sets diff
        p_readme = get_pattern("doc/", "readme.md", {"diff": "markdown"})
        p_readme.apply(files)
        assert files[0].attrs_dict == {"text": "auto", "eol": "lf", "diff": "default"}
        assert files[1].attrs_dict == {"text": "auto", "eol": "lf", "diff": "markdown"}
